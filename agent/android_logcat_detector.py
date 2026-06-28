"""Per-package Roblox logcat signals (UID/PID-attributed, Termux-safe polling)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import android

_UID_RE = re.compile(r"userId=(\d+)")
_LOGCAT_UID_RE = re.compile(r"uid=(\d+)")
_LOGCAT_HEADER_RE = re.compile(
    r"^\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}\s+(\d+)\s+(\d+)\s+(\d+)\s"
)
_DO_TELEPORT_RE = re.compile(r"doTeleport", re.IGNORECASE)
_WITH_REASON_RE = re.compile(r"with reason", re.IGNORECASE)
_IDLE_DISCONNECT_RE = re.compile(
    r"(disconnected for being idle|Error Code:\s*278|idle\s+\d+\s+minutes|You were disconnected.*idle)",
    re.IGNORECASE,
)
_GAME_JOIN_RE = re.compile(r"gamejoinloadtime", re.IGNORECASE)
_PROCESS_DIED_RE = re.compile(
    r"(?:Process\s+\S+\s+has died|has died|force stopping|Force stopping|"
    r"Killing\s+\d+:\S+|ActivityManager.*died|am_kill|proc_died)",
    re.IGNORECASE,
)
_POSITIVE_JOIN_RES: list[tuple[str, re.Pattern[str]]] = [
    ("logcat_place_launcher_join", re.compile(r"\bPlaceLauncher\b.*\b(join|joined|connected)\b", re.I)),
    ("logcat_join_game_success", re.compile(r"\bjoinGameSuccess\b", re.I)),
    ("logcat_in_experience", re.compile(r"\bin[_ ]experience\b", re.I)),
    ("logcat_game_loaded", re.compile(r"\bGame\s+loaded\b", re.I)),
]


@dataclass
class LogcatPackageEvent:
    package: str
    event: str
    line: str
    at: float


@dataclass
class LogcatDetectorState:
    started: bool = False
    permission_ok: bool = False
    error: str = ""
    uid_map: dict[str, str] = field(default_factory=dict)
    pid_map: dict[str, str] = field(default_factory=dict)
    last_events: list[dict[str, Any]] = field(default_factory=list)
    last_poll_at: float = 0.0


def package_uid_map(packages: list[str]) -> dict[str, str]:
    """Map package name → Android UID string via dumpsys package."""
    out: dict[str, str] = {}
    for pkg in packages:
        pkg = str(pkg or "").strip()
        if not pkg:
            continue
        try:
            result = android.run_android_command(
                ["dumpsys", "package", pkg],
                timeout=6,
                prefer_root=True,
            )
            text = result.stdout if result.ok else ""
            match = _UID_RE.search(text)
            if match:
                out[pkg] = match.group(1)
        except Exception:  # noqa: BLE001
            continue
    return out


def package_pid_map(packages: list[str]) -> dict[str, str]:
    """Map package name → primary PID string via pidof."""
    out: dict[str, str] = {}
    for pkg in packages:
        pkg = str(pkg or "").strip()
        if not pkg:
            continue
        try:
            res = android.run_command(["pidof", pkg], timeout=2)
            if res.ok and (res.stdout or "").strip():
                out[pkg] = res.stdout.strip().split()[0]
        except Exception:  # noqa: BLE001
            continue
    return out


def _parse_logcat_uid(line: str) -> str | None:
    match = _LOGCAT_HEADER_RE.match(line.strip())
    if match:
        return match.group(1)
    match = _LOGCAT_UID_RE.search(line)
    return match.group(1) if match else None


def _parse_logcat_pid(line: str) -> str | None:
    match = _LOGCAT_HEADER_RE.match(line.strip())
    return match.group(2) if match else None


def _uid_to_package(uid_map: dict[str, str], uid: str) -> str | None:
    for pkg, mapped in uid_map.items():
        if mapped == uid:
            return pkg
    return None


def _pid_to_package(pid_map: dict[str, str], pid: str) -> str | None:
    return pid_map.get(str(pid or "").strip())


def _match_line_events(package: str, line: str, now: float) -> list[LogcatPackageEvent]:
    events: list[LogcatPackageEvent] = []
    if _GAME_JOIN_RE.search(line):
        events.append(LogcatPackageEvent(package, "package_logcat_game_join_loaded", line, now))
    elif _WITH_REASON_RE.search(line):
        events.append(LogcatPackageEvent(package, "package_logcat_reason", line, now))
    elif _IDLE_DISCONNECT_RE.search(line):
        events.append(LogcatPackageEvent(package, "package_logcat_idle_disconnect", line, now))
    elif _DO_TELEPORT_RE.search(line):
        events.append(LogcatPackageEvent(package, "package_logcat_teleport", line, now))
    elif _PROCESS_DIED_RE.search(line):
        events.append(LogcatPackageEvent(package, "package_process_missing", line, now))
    else:
        for source, pattern in _POSITIVE_JOIN_RES:
            if pattern.search(line):
                events.append(
                    LogcatPackageEvent(package, "package_logcat_join_hint", line, now)
                )
                break
    return events


def poll_pid_logcat_events(
    package: str,
    pid: str,
    *,
    max_lines: int = 320,
) -> list[LogcatPackageEvent]:
    """Dump recent logcat scoped to one PID — strongest attribution when UID map fails."""
    pkg = str(package or "").strip()
    pid_s = str(pid or "").strip()
    if not pkg or not pid_s:
        return []
    events: list[LogcatPackageEvent] = []
    try:
        result = android.run_command(
            ["logcat", "-d", "-t", str(max(20, int(max_lines))), "--pid", pid_s],
            timeout=10,
        )
        if not result.ok:
            return events
        now = time.time()
        for line in (result.stdout or "").splitlines():
            if not line.strip():
                continue
            events.extend(_match_line_events(pkg, line, now))
    except Exception:  # noqa: BLE001
        return events
    return events


def poll_logcat_events(
    packages: list[str],
    *,
    uid_map: dict[str, str] | None = None,
    pid_map: dict[str, str] | None = None,
    max_lines: int = 400,
) -> tuple[list[LogcatPackageEvent], LogcatDetectorState]:
    """Read recent logcat lines and attribute Roblox lifecycle hints per package."""
    state = LogcatDetectorState(started=True)
    state.uid_map = dict(uid_map if uid_map is not None else package_uid_map(packages))
    state.pid_map = dict(pid_map if pid_map is not None else package_pid_map(packages))
    pid_to_pkg = {pid: pkg for pkg, pid in state.pid_map.items() if pid}
    state.last_poll_at = time.time()
    events: list[LogcatPackageEvent] = []
    seen: set[tuple[str, str, str]] = set()

    def _add(event: LogcatPackageEvent) -> None:
        key = (event.package, event.event, event.line[:180])
        if key in seen:
            return
        seen.add(key)
        events.append(event)

    try:
        result = android.run_command(
            ["logcat", "-d", "-v", "uid", "-t", str(max(20, int(max_lines)))],
            timeout=10,
        )
        if not result.ok:
            state.permission_ok = False
            state.error = (result.stderr or result.stdout or "logcat_failed")[:160]
        else:
            state.permission_ok = True
            now = time.time()
            for line in (result.stdout or "").splitlines():
                uid = _parse_logcat_uid(line)
                pid = _parse_logcat_pid(line)
                pkg = _uid_to_package(state.uid_map, uid) if uid else None
                if not pkg and pid:
                    pkg = pid_to_pkg.get(pid)
                if not pkg and packages:
                    matches = [candidate for candidate in packages if candidate in line]
                    if len(matches) == 1:
                        pkg = matches[0]
                if not pkg:
                    continue
                for event in _match_line_events(pkg, line, now):
                    _add(event)
    except Exception as exc:  # noqa: BLE001
        state.permission_ok = False
        state.error = str(exc)[:160]

    for pkg, pid in state.pid_map.items():
        for event in poll_pid_logcat_events(pkg, pid, max_lines=max(120, int(max_lines // 2))):
            _add(event)

    state.last_events = [
        {"package": e.package, "event": e.event, "at": e.at, "line": e.line[:240]}
        for e in events[-48:]
    ]
    return events, state
