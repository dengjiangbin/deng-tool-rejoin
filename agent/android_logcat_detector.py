"""Per-package Roblox logcat signals (UID-attributed, Termux-safe polling)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import android

_UID_RE = re.compile(r"userId=(\d+)")
_LOGCAT_UID_RE = re.compile(r"uid=(\d+)")
_DO_TELEPORT_RE = re.compile(r"doTeleport", re.IGNORECASE)
_WITH_REASON_RE = re.compile(r"with reason", re.IGNORECASE)
_GAME_JOIN_RE = re.compile(r"gamejoinloadtime", re.IGNORECASE)
_PROCESS_DIED_RE = re.compile(
    r"(?:Process\s+\S+\s+has died|has died|force stopping|Force stopping|"
    r"Killing\s+\d+:\S+|ActivityManager.*died)",
    re.IGNORECASE,
)


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


def _parse_logcat_uid(line: str) -> str | None:
    match = _LOGCAT_UID_RE.search(line)
    if match:
        return match.group(1)
    return None


def _uid_to_package(uid_map: dict[str, str], uid: str) -> str | None:
    for pkg, mapped in uid_map.items():
        if mapped == uid:
            return pkg
    return None


def poll_logcat_events(
    packages: list[str],
    *,
    uid_map: dict[str, str] | None = None,
    max_lines: int = 120,
) -> tuple[list[LogcatPackageEvent], LogcatDetectorState]:
    """Read recent logcat lines and attribute Roblox lifecycle hints per package."""
    state = LogcatDetectorState(started=True)
    state.uid_map = dict(uid_map or package_uid_map(packages))
    state.last_poll_at = time.time()
    events: list[LogcatPackageEvent] = []
    try:
        result = android.run_command(
            ["logcat", "-d", "-v", "uid", "-t", str(max(10, int(max_lines)))],
            timeout=8,
        )
        if not result.ok:
            state.permission_ok = False
            state.error = (result.stderr or result.stdout or "logcat_failed")[:160]
            return events, state
        state.permission_ok = True
        for line in (result.stdout or "").splitlines():
            uid = _parse_logcat_uid(line)
            pkg = _uid_to_package(state.uid_map, uid) if uid else None
            if not pkg and packages:
                for candidate in packages:
                    if candidate in line:
                        pkg = candidate
                        break
            if not pkg:
                continue
            now = time.time()
            if _GAME_JOIN_RE.search(line):
                events.append(LogcatPackageEvent(pkg, "package_logcat_game_join_loaded", line, now))
            elif _WITH_REASON_RE.search(line):
                events.append(LogcatPackageEvent(pkg, "package_logcat_reason", line, now))
            elif _DO_TELEPORT_RE.search(line):
                events.append(LogcatPackageEvent(pkg, "package_logcat_teleport", line, now))
            elif _PROCESS_DIED_RE.search(line) and pkg in line:
                events.append(LogcatPackageEvent(pkg, "package_process_missing", line, now))
    except Exception as exc:  # noqa: BLE001
        state.permission_ok = False
        state.error = str(exc)[:160]
    state.last_events = [
        {"package": e.package, "event": e.event, "at": e.at, "line": e.line[:240]}
        for e in events[-32:]
    ]
    return events, state
