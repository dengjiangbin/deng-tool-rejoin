"""rjn.txt-style Roblox lifecycle detection: UID logcat + process watchdog.

Source of truth for package ONLINE_CONFIRMED, disconnect, force-close, and runtime.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from . import android
from .constants import DATA_DIR

WATCHED_PHRASES = ("gamejoinloadtime", "doTeleport", "with reason")
DEFAULT_LAUNCH_WATCHDOG_SECONDS = 120.0
PROCESS_MISSING_CONFIRM = 2

STATE_STOPPED = "STOPPED"
STATE_LAUNCHING = "LAUNCHING"
STATE_TELEPORTING = "TELEPORTING"
STATE_ONLINE_CONFIRMED = "ONLINE_CONFIRMED"
STATE_DISCONNECTED = "DISCONNECTED"
STATE_DEAD = "DEAD"
STATE_RELAUNCHING = "RELAUNCHING"
STATE_FAILED = "FAILED"

_UID_RE = re.compile(r"userId=(\d+)")
_LOGCAT_UID_RE = re.compile(r"uid=(\d+)")
_GAME_JOIN_RE = re.compile(r"gamejoinloadtime", re.I)
_DO_TELEPORT_RE = re.compile(r"doTeleport", re.I)
_WITH_REASON_RE = re.compile(r"with reason", re.I)


@dataclass
class UidResolution:
    package: str
    uid: str | None = None
    resolved_at: float = 0.0
    command_output_sample: str = ""
    error: str | None = None


@dataclass
class LogcatEvent:
    package: str
    uid: str
    phrase: str
    raw_line_sanitized: str
    seen_at: float
    action_taken: str = ""


@dataclass
class PackageRjnState:
    package: str
    uid: str = ""
    uid_error: str = ""
    internal_state: str = STATE_STOPPED
    online_since: float = 0.0
    runtime_source: str = ""
    launch_started_at: float = 0.0
    watchdog_active: bool = False
    launch_failed_reason: str = ""
    error_count: int = 0
    last_gamejoinloadtime_at: float = 0.0
    last_doteleport_at: float = 0.0
    last_with_reason_at: float = 0.0
    last_logcat_event_at: float = 0.0
    last_process_check_at: float = 0.0
    process_exists: bool = False
    pids: list[str] = field(default_factory=list)
    force_close_detected: bool = False
    process_missing_streak: int = 0
    last_transition_at: float = 0.0
    last_transition_reason: str = ""
    last_online_evidence_at: float = 0.0
    last_offline_evidence_at: float = 0.0


@dataclass
class PackageEvaluateResult:
    package: str
    internal_state: str
    public_status: str
    reason: str
    is_online_confirmed: bool
    failed_checks: list[str]
    process_exists: bool
    detail: dict[str, Any]


def _sanitize_line(line: str) -> str:
    text = str(line or "")[:240]
    text = re.sub(r"(?i)ROBLOSECURITY[^\s]*", "<masked>", text)
    return text


def resolve_package_uid(package: str) -> UidResolution:
    pkg = android.validate_package_name(package)
    now = time.time()
    try:
        result = android.run_android_command(
            ["dumpsys", "package", pkg],
            timeout=6,
            prefer_root=True,
        )
        text = result.stdout if result.ok else (result.stderr or "")
        match = _UID_RE.search(text)
        if match:
            return UidResolution(
                package=pkg,
                uid=match.group(1),
                resolved_at=now,
                command_output_sample=text[:400],
            )
        return UidResolution(
            package=pkg,
            uid=None,
            resolved_at=now,
            command_output_sample=text[:400],
            error="userId not found in dumpsys package",
        )
    except Exception as exc:  # noqa: BLE001
        return UidResolution(
            package=pkg,
            uid=None,
            resolved_at=now,
            error=str(exc)[:160],
        )


class RjnLifecycleMonitor:
    """Per-package lifecycle from UID-filtered logcat + process watchdog."""

    def __init__(
        self,
        packages: list[str],
        *,
        root_info: Any = None,
        stop_event: threading.Event | None = None,
        launch_watchdog_seconds: float = DEFAULT_LAUNCH_WATCHDOG_SECONDS,
    ) -> None:
        self.packages = [str(p).strip() for p in packages if str(p).strip()]
        self._root_info = root_info
        self._stop_event = stop_event or threading.Event()
        self._launch_watchdog_seconds = float(launch_watchdog_seconds)
        self._lock = threading.RLock()
        self._states: dict[str, PackageRjnState] = {
            pkg: PackageRjnState(package=pkg) for pkg in self.packages
        }
        self._uid_map: dict[str, str] = {}
        self._uid_to_package: dict[str, str] = {}
        self._uid_resolutions: dict[str, UidResolution] = {}
        self._recent_events: list[LogcatEvent] = []
        self._monitor_started_at: float = 0.0
        self._logcat_cleared_at: float = 0.0
        self._logcat_started_at: float = 0.0
        self._logcat_stream_alive: bool = False
        self._logcat_error: str = ""
        self._logcat_thread: threading.Thread | None = None
        self._logcat_proc: subprocess.Popen[str] | None = None
        self._session_started: bool = False

    def refresh_uid_map(self) -> dict[str, str]:
        with self._lock:
            for pkg in self.packages:
                res = resolve_package_uid(pkg)
                self._uid_resolutions[pkg] = res
                row = self._states.setdefault(pkg, PackageRjnState(package=pkg))
                if res.uid:
                    self._uid_map[pkg] = res.uid
                    self._uid_to_package[res.uid] = pkg
                    row.uid = res.uid
                    row.uid_error = ""
                else:
                    row.uid = ""
                    row.uid_error = res.error or "uid_unresolved"
                    if row.internal_state not in {STATE_DEAD, STATE_STOPPED}:
                        row.internal_state = STATE_FAILED
                        row.last_transition_reason = row.uid_error
            return dict(self._uid_map)

    def clear_logcat(self) -> bool:
        try:
            res = android.run_command(["logcat", "-c"], timeout=6)
            self._logcat_cleared_at = time.time()
            return res.ok
        except Exception as exc:  # noqa: BLE001
            self._logcat_error = str(exc)[:160]
            return False

    def start_session(self) -> None:
        with self._lock:
            if self._session_started:
                return
            self._session_started = True
            self._monitor_started_at = time.time()
            self.clear_logcat()
            self.refresh_uid_map()
            for pkg in self.packages:
                row = self._states[pkg]
                if row.internal_state == STATE_STOPPED:
                    row.internal_state = STATE_LAUNCHING
                    row.last_transition_at = self._monitor_started_at
                    row.last_transition_reason = "session_start"
            self._start_logcat_thread()

    def stop_session(self) -> None:
        self._stop_event.set()
        proc = self._logcat_proc
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
        self._logcat_stream_alive = False

    def _start_logcat_thread(self) -> None:
        if self._logcat_thread and self._logcat_thread.is_alive():
            return
        self._logcat_started_at = time.time()
        self._logcat_thread = threading.Thread(
            target=self._logcat_reader_loop,
            name="rjn-logcat-uid",
            daemon=True,
        )
        self._logcat_thread.start()

    def _logcat_reader_loop(self) -> None:
        try:
            proc = subprocess.Popen(
                ["logcat", "-v", "uid"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            self._logcat_proc = proc
            self._logcat_stream_alive = True
            while not self._stop_event.is_set():
                if proc.stdout is None:
                    break
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)
                    continue
                self._handle_logcat_line(line.strip())
        except Exception as exc:  # noqa: BLE001
            self._logcat_error = str(exc)[:160]
            self._logcat_stream_alive = False

    def _uid_for_line(self, line: str) -> str | None:
        match = _LOGCAT_UID_RE.search(line)
        return match.group(1) if match else None

    def _handle_logcat_line(self, line: str) -> None:
        if not line:
            return
        seen_at = time.time()
        if self._monitor_started_at and seen_at < self._monitor_started_at:
            return
        uid = self._uid_for_line(line)
        if not uid:
            return
        with self._lock:
            pkg = self._uid_to_package.get(uid)
            if not pkg:
                return
            phrase = ""
            if _GAME_JOIN_RE.search(line):
                phrase = "gamejoinloadtime"
            elif _WITH_REASON_RE.search(line):
                phrase = "with reason"
            elif _DO_TELEPORT_RE.search(line):
                phrase = "doTeleport"
            else:
                return

            event = LogcatEvent(
                package=pkg,
                uid=uid,
                phrase=phrase,
                raw_line_sanitized=_sanitize_line(line),
                seen_at=seen_at,
            )
            self._apply_phrase(pkg, phrase, seen_at, event)
            event.action_taken = self._states[pkg].internal_state
            self._recent_events.append(event)
            if len(self._recent_events) > 128:
                self._recent_events = self._recent_events[-128:]

    def _transition(
        self,
        pkg: str,
        new_state: str,
        reason: str,
        *,
        at: float,
        offline: bool = False,
    ) -> None:
        row = self._states[pkg]
        row.internal_state = new_state
        row.last_transition_at = at
        row.last_transition_reason = reason
        if offline:
            row.online_since = 0.0
            row.runtime_source = ""
            row.last_offline_evidence_at = at
            from .status_monitor_runtime import clear_online_since, record_lifecycle_transition

            clear_online_since(pkg)
            record_lifecycle_transition(pkg, new_state, reason, now=at, offline=True)
        else:
            from .status_monitor_runtime import record_lifecycle_transition

            record_lifecycle_transition(pkg, new_state, reason, now=at)

    def _apply_phrase(self, pkg: str, phrase: str, at: float, event: LogcatEvent) -> None:
        row = self._states[pkg]
        row.last_logcat_event_at = at
        if phrase == "gamejoinloadtime":
            row.last_gamejoinloadtime_at = at
            row.watchdog_active = False
            row.launch_failed_reason = ""
            prev = row.internal_state
            if prev != STATE_ONLINE_CONFIRMED:
                row.online_since = at
            row.runtime_source = "gamejoinloadtime"
            row.last_online_evidence_at = at
            row.internal_state = STATE_ONLINE_CONFIRMED
            row.last_transition_at = at
            row.last_transition_reason = "gamejoinloadtime"
            row.process_missing_streak = 0
            row.force_close_detected = False
            from .status_monitor_runtime import mark_online_confirmed_gamejoin

            mark_online_confirmed_gamejoin(pkg, at, previous_state=prev)
            event.action_taken = "ONLINE_CONFIRMED"
        elif phrase == "with reason":
            row.last_with_reason_at = at
            self._transition(
                pkg,
                STATE_DISCONNECTED,
                "logcat_with_reason",
                at=at,
                offline=True,
            )
            event.action_taken = "DISCONNECTED"
        elif phrase == "doTeleport":
            row.last_doteleport_at = at
            if row.internal_state == STATE_ONLINE_CONFIRMED:
                row.internal_state = STATE_TELEPORTING
                row.last_transition_at = at
                row.last_transition_reason = "doTeleport"
            event.action_taken = "TELEPORTING"

    def begin_launch_watchdog(self, package: str, *, relaunch: bool = False) -> None:
        pkg = str(package or "").strip()
        if not pkg:
            return
        now = time.time()
        with self._lock:
            row = self._states.setdefault(pkg, PackageRjnState(package=pkg))
            row.launch_started_at = now
            row.watchdog_active = True
            row.launch_failed_reason = ""
            new_state = STATE_RELAUNCHING if relaunch else STATE_LAUNCHING
            row.internal_state = new_state
            row.last_transition_at = now
            row.last_transition_reason = "launch_started"
            if relaunch:
                row.online_since = 0.0
                row.runtime_source = ""

    def _process_check(self, package: str) -> tuple[bool, list[str]]:
        pkg = android.validate_package_name(package)
        pids: list[str] = []
        root_tool = getattr(self._root_info, "tool", None) if self._root_info else None
        try:
            res = android.run_command(["pgrep", "-f", pkg], timeout=3)
            if res.ok and (res.stdout or "").strip():
                pids = [p for p in res.stdout.strip().split() if p.isdigit()]
        except Exception:  # noqa: BLE001
            pass
        if not pids:
            try:
                res = android.run_command(["pidof", pkg], timeout=2)
                if res.ok and (res.stdout or "").strip():
                    pids = res.stdout.strip().split()
            except Exception:  # noqa: BLE001
                pass
        if not pids and getattr(self._root_info, "available", False) and root_tool:
            try:
                res = android.run_root_command(["pidof", pkg], root_tool=root_tool, timeout=2)
                if res.ok and (res.stdout or "").strip():
                    pids = res.stdout.strip().split()
            except Exception:  # noqa: BLE001
                pass
        return bool(pids), pids

    def evaluate_package(self, package: str) -> PackageEvaluateResult:
        pkg = str(package or "").strip()
        now = time.time()
        failed: list[str] = []
        with self._lock:
            row = self._states.setdefault(pkg, PackageRjnState(package=pkg))
            effective_uid = row.uid or self._uid_map.get(pkg) or ""
            if not effective_uid and pkg in self._uid_resolutions:
                res = self._uid_resolutions[pkg]
                if not res.uid:
                    failed.append("uid_not_resolved")
            process_exists, pids = self._process_check(pkg)
            row.process_exists = process_exists
            row.pids = list(pids)
            row.last_process_check_at = now

            if row.watchdog_active and row.launch_started_at > 0:
                age = now - row.launch_started_at
                if age > self._launch_watchdog_seconds:
                    if row.last_gamejoinloadtime_at < row.launch_started_at:
                        row.watchdog_active = False
                        row.launch_failed_reason = "launch_watchdog_timeout"
                        row.error_count += 1
                        self._transition(
                            pkg,
                            STATE_FAILED,
                            "launch_watchdog_timeout",
                            at=now,
                            offline=True,
                        )

            if not process_exists:
                row.process_missing_streak += 1
                if row.process_missing_streak >= PROCESS_MISSING_CONFIRM:
                    row.force_close_detected = True
                    if row.internal_state in {
                        STATE_ONLINE_CONFIRMED,
                        STATE_TELEPORTING,
                        STATE_LAUNCHING,
                        STATE_RELAUNCHING,
                    }:
                        self._transition(
                            pkg,
                            STATE_DEAD,
                            "process_missing",
                            at=now,
                            offline=True,
                        )
            else:
                row.process_missing_streak = 0
                row.force_close_detected = False

            internal = row.internal_state
            is_online = internal == STATE_ONLINE_CONFIRMED and process_exists
            if internal == STATE_ONLINE_CONFIRMED and not process_exists:
                failed.append("process_missing")
                is_online = False
            if internal == STATE_ONLINE_CONFIRMED and row.last_with_reason_at > row.last_gamejoinloadtime_at:
                failed.append("with_reason_after_join")
                is_online = False
            if not effective_uid:
                failed.append("uid_not_resolved")
                is_online = False
            if internal != STATE_ONLINE_CONFIRMED:
                failed.append("no_uid_matched_gamejoinloadtime")
                is_online = False

            public = self._map_public_status(internal, is_online)
            reason = self._decision_reason(row, is_online, failed)

            detail = {
                "internal_state": internal,
                "online_confirmed": str(is_online).lower(),
                "runtime_source": row.runtime_source or "none",
                "online_since": row.online_since or "",
                "process_running": str(process_exists).lower(),
                "pids": ",".join(pids),
                "reason": reason,
                "dead_reason": row.last_transition_reason if not is_online else "",
                "launch_watchdog_active": str(row.watchdog_active).lower(),
                "launch_watchdog_age_seconds": round(
                    max(0.0, now - row.launch_started_at) if row.launch_started_at else 0.0,
                    1,
                ),
                "last_gamejoinloadtime_at": row.last_gamejoinloadtime_at or "",
                "last_with_reason_at": row.last_with_reason_at or "",
                "last_doteleport_at": row.last_doteleport_at or "",
            }
            return PackageEvaluateResult(
                package=pkg,
                internal_state=internal,
                public_status=public,
                reason=reason,
                is_online_confirmed=is_online,
                failed_checks=list(failed),
                process_exists=process_exists,
                detail=detail,
            )

    def _map_public_status(self, internal: str, is_online: bool) -> str:
        if is_online:
            return "Online"
        if internal == STATE_DISCONNECTED:
            return "Disconnected"
        if internal in {STATE_LAUNCHING, STATE_TELEPORTING}:
            return "Launching"
        if internal == STATE_RELAUNCHING:
            return "Relaunching"
        if internal in {STATE_DEAD, STATE_FAILED}:
            return "Dead"
        if internal == STATE_STOPPED:
            return "Launching"
        return "Dead"

    def _decision_reason(
        self,
        row: PackageRjnState,
        is_online: bool,
        failed: list[str],
    ) -> str:
        if is_online:
            return "online because UID-matched gamejoinloadtime was seen and process exists"
        if row.force_close_detected or "process_missing" in failed:
            return "process_missing"
        if row.last_with_reason_at and row.last_with_reason_at >= row.last_gamejoinloadtime_at:
            return "UID-matched logcat line contained with reason"
        if row.launch_failed_reason == "launch_watchdog_timeout":
            return "launch_watchdog_timeout"
        if "uid_not_resolved" in failed:
            return row.uid_error or "uid_not_resolved"
        return "no UID-matched gamejoinloadtime after launch"

    def probe_snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            uid_map_out: dict[str, Any] = {}
            for pkg, res in self._uid_resolutions.items():
                uid_map_out[pkg] = {
                    "uid": res.uid,
                    "resolved_at": res.resolved_at,
                    "resolve_error": res.error,
                    "sample": res.command_output_sample[:200] if res.command_output_sample else "",
                }
            packages_out: dict[str, Any] = {}
            for pkg, row in self._states.items():
                age = max(0.0, now - row.launch_started_at) if row.launch_started_at else 0.0
                ev = self.evaluate_package(pkg)
                packages_out[pkg] = {
                    "state": row.internal_state,
                    "online_since": row.online_since or None,
                    "runtime_source": row.runtime_source or "gamejoinloadtime",
                    "process_exists": row.process_exists,
                    "pids": list(row.pids),
                    "last_gamejoinloadtime_at": row.last_gamejoinloadtime_at or None,
                    "last_doteleport_at": row.last_doteleport_at or None,
                    "last_with_reason_at": row.last_with_reason_at or None,
                    "last_logcat_event_at": row.last_logcat_event_at or None,
                    "launch_started_at": row.launch_started_at or None,
                    "launch_watchdog_active": row.watchdog_active,
                    "launch_watchdog_age_seconds": round(age, 1),
                    "launch_watchdog_timeout_seconds": self._launch_watchdog_seconds,
                    "launch_failed_reason": row.launch_failed_reason or None,
                    "decision": ev.reason,
                    "failed_checks": list(ev.failed_checks),
                    "is_online_confirmed": ev.is_online_confirmed,
                }
            recent = [
                {
                    "package": e.package,
                    "uid": e.uid,
                    "phrase": e.phrase,
                    "seen_at": e.seen_at,
                    "raw_line_sanitized": e.raw_line_sanitized,
                    "action_taken": e.action_taken,
                }
                for e in self._recent_events[-32:]
            ]
            return {
                "enabled": self._session_started,
                "logcat_stream_alive": self._logcat_stream_alive,
                "logcat_started_at": self._logcat_started_at or None,
                "logcat_cleared_at": self._logcat_cleared_at or None,
                "logcat_error": self._logcat_error or None,
                "monitor_started_at": self._monitor_started_at or None,
                "watched_phrases": list(WATCHED_PHRASES),
                "uid_map": uid_map_out,
                "packages": packages_out,
                "recent_events": recent,
            }

    def write_probe_file(self) -> None:
        path = DATA_DIR / "rjn-style-detection.json"
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"rjn_style_detection": self.probe_snapshot()}, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
