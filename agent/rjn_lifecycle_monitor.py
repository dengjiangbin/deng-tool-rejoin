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

WATCHED_PHRASES = (
    "gamejoinloadtime",
    "doTeleport",
    "with reason",
    "PlaceLauncher",
    "joinGameSuccess",
    "in_experience",
)
DEFAULT_LAUNCH_WATCHDOG_SECONDS = 120.0
PROCESS_MISSING_CONFIRM = 2
DISCONNECT_SCAN_INTERVAL_SECONDS = 5.0
LAUNCH_ONLINE_FALLBACK_MIN_AGE_SECONDS = 20.0

_UID_OPTIONAL_ONLINE_SOURCES = frozenset({
    "presence_in_experience",
    "activity_in_game",
    "online_evidence",
    "logcat_join_hint",
})

STATE_STOPPED = "STOPPED"
STATE_LAUNCHING = "LAUNCHING"
STATE_TELEPORTING = "TELEPORTING"
STATE_ONLINE_CONFIRMED = "ONLINE_CONFIRMED"
STATE_DISCONNECTED = "DISCONNECTED"
STATE_DEAD = "DEAD"
STATE_RELAUNCHING = "RELAUNCHING"
STATE_FAILED = "FAILED"

_ACTIVE_MONITOR_STATES = frozenset({
    STATE_LAUNCHING,
    STATE_RELAUNCHING,
    STATE_ONLINE_CONFIRMED,
    STATE_TELEPORTING,
    STATE_DISCONNECTED,
    STATE_FAILED,
})

_UID_RE = re.compile(r"userId=(\d+)")
_LOGCAT_HEADER_RE = re.compile(
    r"^\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}\s+(\d+)\s+(\d+)\s+(\d+)\s"
)
_LOGCAT_UID_RE = re.compile(r"uid=(\d+)")
_GAME_JOIN_RE = re.compile(r"gamejoinloadtime", re.I)
_DO_TELEPORT_RE = re.compile(r"doTeleport", re.I)
_WITH_REASON_RE = re.compile(r"with reason", re.I)
_IDLE_DISCONNECT_RE = re.compile(
    r"(disconnected for being idle|Error Code:\s*278|idle\s+\d+\s+minutes|You were disconnected.*idle)",
    re.I,
)
_POSITIVE_ONLINE_RES: list[tuple[str, re.Pattern[str]]] = [
    ("gamejoinloadtime", re.compile(r"gamejoinloadtime", re.I)),
    (
        "logcat_place_launcher_join",
        re.compile(r"\bPlaceLauncher\b.*\b(join|joined|connected)\b", re.I),
    ),
    ("logcat_join_game_success", re.compile(r"\bjoinGameSuccess\b", re.I)),
    (
        "logcat_joined_experience",
        re.compile(r"\bJoined\s+(game|experience|place)\b", re.I),
    ),
    ("logcat_in_experience", re.compile(r"\bin[_ ]experience\b", re.I)),
    ("logcat_game_loaded", re.compile(r"\bGame\s+loaded\b", re.I)),
    (
        "logcat_experience_started",
        re.compile(r"\bExperience\s+started\b", re.I),
    ),
]


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
    relaunching: bool = False
    force_close_detected: bool = False
    process_missing_streak: int = 0
    last_transition_at: float = 0.0
    last_transition_reason: str = ""
    last_online_evidence_at: float = 0.0
    last_positive_online_evidence_at: float = 0.0
    online_evidence_source: str = ""
    last_offline_evidence_at: float = 0.0
    last_dead_detected_at: float = 0.0
    last_dead_reason: str = ""
    last_disconnect_scan_at: float = 0.0
    disconnect_prompt_text: str = ""


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


def _package_force_stopped_quick(package: str) -> bool:
    try:
        from .package_online_evidence import _package_force_stopped

        return bool(_package_force_stopped(package))
    except Exception:  # noqa: BLE001
        return False


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
        try:
            from .android_logcat_detector import package_uid_map

            mapped = package_uid_map([pkg]).get(pkg)
            if mapped:
                return UidResolution(
                    package=pkg,
                    uid=str(mapped),
                    resolved_at=now,
                    command_output_sample=text[:400],
                )
        except Exception:  # noqa: BLE001
            pass
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
        self._pid_map: dict[str, str] = {}
        self._pid_to_package: dict[str, str] = {}
        self._last_pid_refresh_at: float = 0.0
        self._uid_resolutions: dict[str, UidResolution] = {}
        self._recent_events: list[LogcatEvent] = []
        self._monitor_started_at: float = 0.0
        self._logcat_cleared_at: float = 0.0
        self._logcat_started_at: float = 0.0
        self._logcat_stream_alive: bool = False
        self._logcat_error: str = ""
        self._logcat_thread: threading.Thread | None = None
        self._logcat_proc: subprocess.Popen[str] | None = None
        self._logcat_pid: int = 0
        self._logcat_last_line_at: float = 0.0
        self._logcat_last_uid_matched_at: float = 0.0
        self._ignored_uid_lines: list[dict[str, Any]] = []
        self._detector_errors: list[str] = []
        self._session_started: bool = False
        self._last_logcat_poll_at: float = 0.0

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
            return dict(self._uid_map)

    def refresh_pid_map(self) -> dict[str, str]:
        with self._lock:
            from .android_logcat_detector import package_pid_map

            self._pid_map = package_pid_map(self.packages)
            self._pid_to_package = {
                pid: pkg for pkg, pid in self._pid_map.items() if pid
            }
            for pkg, row in self._states.items():
                _exists, pids = self._process_check(pkg)
                for pid in pids:
                    self._pid_map[pkg] = pid
                    self._pid_to_package[pid] = pkg
            self._last_pid_refresh_at = time.time()
            return dict(self._pid_map)

    def clear_logcat(self) -> bool:
        try:
            res = android.run_command(["logcat", "-c"], timeout=6)
            self._logcat_cleared_at = time.time()
            return res.ok
        except Exception as exc:  # noqa: BLE001
            self._logcat_error = str(exc)[:160]
            return False

    def start_session(self) -> None:
        """Detection-only session start: clear logcat, build UID map, start reader."""
        with self._lock:
            if self._session_started:
                return
            self._session_started = True
            self._monitor_started_at = time.time()
            self.clear_logcat()
            self.refresh_uid_map()
            self.refresh_pid_map()
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

    def _ensure_logcat_stream(self) -> None:
        """Restart logcat reader only — never Termux or the main monitor process."""
        with self._lock:
            alive = bool(
                self._logcat_thread
                and self._logcat_thread.is_alive()
                and self._logcat_stream_alive
            )
            if alive:
                return
            if self._logcat_proc is not None:
                try:
                    self._logcat_proc.kill()
                except OSError:
                    pass
                self._logcat_proc = None
            self._logcat_stream_alive = False
            self._start_logcat_thread()

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
            self._logcat_pid = int(proc.pid or 0)
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
                self._logcat_last_line_at = time.time()
                self._handle_logcat_line(line.strip())
        except Exception as exc:  # noqa: BLE001
            self._logcat_error = str(exc)[:160]
            self._detector_errors.append(self._logcat_error)
            if len(self._detector_errors) > 16:
                self._detector_errors = self._detector_errors[-16:]
        finally:
            self._logcat_stream_alive = False
            self._logcat_pid = 0

    def _uid_for_line(self, line: str) -> str | None:
        match = _LOGCAT_HEADER_RE.match(line.strip())
        if match:
            return match.group(1)
        match = _LOGCAT_UID_RE.search(line)
        return match.group(1) if match else None

    def _pid_for_line(self, line: str) -> str | None:
        match = _LOGCAT_HEADER_RE.match(line.strip())
        return match.group(2) if match else None

    def _package_for_line(self, line: str, uid: str | None) -> str | None:
        if uid:
            pkg = self._uid_to_package.get(uid)
            if pkg:
                return pkg
        pid = self._pid_for_line(line)
        if pid:
            pkg = self._pid_to_package.get(pid)
            if pkg:
                return pkg
        matches = [pkg for pkg in self.packages if pkg and pkg in line]
        if len(matches) == 1:
            return matches[0]
        return None

    def _match_positive_online(self, line: str) -> str | None:
        for source, pattern in _POSITIVE_ONLINE_RES:
            if pattern.search(line):
                return source
        return None

    def _poll_recent_logcat(self) -> None:
        """Backfill join/disconnect hints from recent logcat when stream misses lines."""
        now = time.time()
        if now - self._last_logcat_poll_at < 5.0:
            return
        self._last_logcat_poll_at = now
        if now - self._last_pid_refresh_at >= 8.0:
            self.refresh_pid_map()
        try:
            from .android_logcat_detector import poll_logcat_events

            events, _state = poll_logcat_events(
                self.packages,
                uid_map=dict(self._uid_map),
                pid_map=dict(self._pid_map),
                max_lines=400,
            )
        except Exception as exc:  # noqa: BLE001
            self._detector_errors.append(f"logcat_poll:{exc}"[:120])
            return
        for event in events:
            if event.event == "package_logcat_game_join_loaded":
                self._confirm_online_evidence(
                    event.package,
                    event.at,
                    source="gamejoinloadtime",
                )
            elif event.event == "package_logcat_join_hint":
                self._confirm_online_evidence(
                    event.package,
                    event.at,
                    source="logcat_join_hint",
                )
            elif event.event == "package_logcat_reason":
                self._apply_phrase(
                    event.package,
                    "with reason",
                    event.at,
                    LogcatEvent(
                        package=event.package,
                        uid=self._uid_map.get(event.package, ""),
                        phrase="with reason",
                        raw_line_sanitized=_sanitize_line(event.line),
                        seen_at=event.at,
                    ),
                )
            elif event.event == "package_logcat_idle_disconnect":
                self._apply_phrase(
                    event.package,
                    "idle_disconnect_278",
                    event.at,
                    LogcatEvent(
                        package=event.package,
                        uid=self._uid_map.get(event.package, ""),
                        phrase="idle_disconnect_278",
                        raw_line_sanitized=_sanitize_line(event.line),
                        seen_at=event.at,
                    ),
                )
            elif event.event == "package_process_missing":
                row = self._states.get(event.package)
                if row and self._was_ever_online_confirmed(row):
                    row.process_missing_streak = PROCESS_MISSING_CONFIRM
                    row.force_close_detected = True
                    self._transition(
                        event.package,
                        STATE_DEAD,
                        "process_missing",
                        at=event.at,
                        offline=True,
                    )
            elif event.event == "package_logcat_teleport":
                self._apply_phrase(
                    event.package,
                    "doTeleport",
                    event.at,
                    LogcatEvent(
                        package=event.package,
                        uid=self._uid_map.get(event.package, ""),
                        phrase="doTeleport",
                        raw_line_sanitized=_sanitize_line(event.line),
                        seen_at=event.at,
                    ),
                )

    def _handle_logcat_line(self, line: str) -> None:
        if not line:
            return
        seen_at = time.time()
        if self._monitor_started_at and seen_at < self._monitor_started_at:
            return
        uid = self._uid_for_line(line)
        with self._lock:
            pkg = self._package_for_line(line, uid)
            if not pkg:
                if uid:
                    self._ignored_uid_lines.append({
                        "uid": uid,
                        "line": _sanitize_line(line),
                        "at": seen_at,
                        "reason": "uid_not_mapped",
                    })
                    if len(self._ignored_uid_lines) > 32:
                        self._ignored_uid_lines = self._ignored_uid_lines[-32:]
                return
            if uid:
                self._logcat_last_uid_matched_at = seen_at
            effective_uid = uid or self._uid_map.get(pkg) or ""
            phrase = ""
            if _WITH_REASON_RE.search(line):
                phrase = "with reason"
            elif _IDLE_DISCONNECT_RE.search(line):
                phrase = "idle_disconnect_278"
            elif _DO_TELEPORT_RE.search(line):
                phrase = "doTeleport"
            else:
                positive = self._match_positive_online(line)
                if positive:
                    phrase = positive
                elif any(
                    hint in line.lower()
                    for hint in ("placelauncher", "joingame", "in_experience", "game loaded")
                ):
                    phrase = "logcat_join_hint"
                else:
                    return

            event = LogcatEvent(
                package=pkg,
                uid=effective_uid,
                phrase=phrase,
                raw_line_sanitized=_sanitize_line(line),
                seen_at=seen_at,
            )
            if phrase == "with reason":
                self._apply_phrase(pkg, phrase, seen_at, event)
            elif phrase == "idle_disconnect_278":
                self._apply_phrase(pkg, phrase, seen_at, event)
            elif phrase == "doTeleport":
                self._apply_phrase(pkg, phrase, seen_at, event)
            else:
                self._confirm_online_evidence(pkg, seen_at, source=phrase, event=event)
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
        if new_state in {STATE_DEAD, STATE_DISCONNECTED, STATE_FAILED}:
            row.last_dead_detected_at = at
            row.last_dead_reason = reason
            row.last_positive_online_evidence_at = 0.0
            row.last_gamejoinloadtime_at = 0.0
            row.online_evidence_source = ""
            row.watchdog_active = False
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

    def _confirm_online_evidence(
        self,
        pkg: str,
        at: float,
        *,
        source: str,
        event: LogcatEvent | None = None,
    ) -> None:
        row = self._states[pkg]
        source_norm = str(source or "").strip()
        if row.launch_started_at > 0:
            if source_norm != "gamejoinloadtime" and at < row.launch_started_at:
                return
            if (
                source_norm != "gamejoinloadtime"
                and (at - row.launch_started_at) < LAUNCH_ONLINE_FALLBACK_MIN_AGE_SECONDS
            ):
                return
        row.last_logcat_event_at = at
        row.watchdog_active = False
        row.launch_failed_reason = ""
        prev = row.internal_state
        if prev != STATE_ONLINE_CONFIRMED:
            row.online_since = at
        row.runtime_source = source
        row.last_online_evidence_at = at
        row.last_positive_online_evidence_at = at
        row.online_evidence_source = source
        row.internal_state = STATE_ONLINE_CONFIRMED
        row.last_transition_at = at
        row.last_transition_reason = source
        row.process_missing_streak = 0
        row.force_close_detected = False
        row.relaunching = False
        if source == "gamejoinloadtime":
            row.last_gamejoinloadtime_at = at
            from .status_monitor_runtime import mark_online_confirmed_gamejoin

            mark_online_confirmed_gamejoin(pkg, at, previous_state=prev)
        else:
            from .status_monitor_runtime import mark_online_confirmed_evidence

            mark_online_confirmed_evidence(pkg, at, source=source, previous_state=prev)
        if event is not None:
            event.action_taken = "ONLINE_CONFIRMED"

    def confirm_online_evidence(
        self,
        package: str,
        at: float,
        *,
        source: str,
    ) -> None:
        """External online proof (e.g. Roblox Presence in_experience)."""
        pkg = str(package or "").strip()
        if not pkg:
            return
        with self._lock:
            self._confirm_online_evidence(pkg, float(at), source=str(source or "online_evidence"))

    def apply_disconnect(
        self,
        package: str,
        at: float,
        *,
        reason: str,
        matched_text: str | None = None,
    ) -> None:
        """External disconnect proof (idle UI, logcat, etc.)."""
        pkg = str(package or "").strip()
        internal_reason = str(reason or "ui_disconnect").strip() or "ui_disconnect"
        if not pkg:
            return
        with self._lock:
            self._states.setdefault(pkg, PackageRjnState(package=pkg))
            row = self._states[pkg]
            row.last_with_reason_at = float(at)
            prompt = str(matched_text or "").strip()
            if prompt:
                row.disconnect_prompt_text = prompt[:240]
            self._transition(
                pkg,
                STATE_DISCONNECTED,
                internal_reason,
                at=float(at),
                offline=True,
            )

    def _was_ever_online_confirmed(self, row: PackageRjnState) -> bool:
        return row.last_positive_online_evidence_at > 0

    def _try_confirm_launch_online(self, pkg: str, now: float) -> bool:
        """Best-effort in-game proof during launch before watchdog marks join failed."""
        try:
            from .package_online_evidence import (
                collect_online_evidence,
                evaluate_online_confirmed,
            )

            scan = collect_online_evidence(pkg, root_info=self._root_info)
            decision = evaluate_online_confirmed(scan)
            if bool(getattr(decision, "is_disconnected", False)):
                return False
            if decision.is_online_confirmed:
                self._confirm_online_evidence(pkg, now, source="activity_in_game")
                return True
        except Exception as exc:  # noqa: BLE001
            self._detector_errors.append(f"launch_online_fallback:{exc}"[:120])
        return False

    def _detect_live_disconnect(self, package: str) -> tuple[str | None, str | None]:
        try:
            from .package_online_evidence import detect_live_disconnect

            reason, matched = detect_live_disconnect(
                package,
                root_info=getattr(self, "_root_info", None),
            )
            return reason, matched
        except Exception as exc:  # noqa: BLE001
            self._detector_errors.append(f"disconnect_scan:{exc}"[:120])
            return None, None

    def _apply_phrase(self, pkg: str, phrase: str, at: float, event: LogcatEvent) -> None:
        row = self._states[pkg]
        row.last_logcat_event_at = at
        if phrase == "gamejoinloadtime":
            self._confirm_online_evidence(pkg, at, source="gamejoinloadtime", event=event)
        elif phrase == "with reason":
            row.last_with_reason_at = at
            prompt = getattr(event, "raw_line_sanitized", "") if event is not None else ""
            if prompt:
                row.disconnect_prompt_text = str(prompt)[:240]
            self._transition(
                pkg,
                STATE_DISCONNECTED,
                "logcat_with_reason",
                at=at,
                offline=True,
            )
            event.action_taken = "DISCONNECTED"
        elif phrase == "idle_disconnect_278":
            row.last_with_reason_at = at
            prompt = getattr(event, "raw_line_sanitized", "") if event is not None else ""
            if prompt:
                row.disconnect_prompt_text = str(prompt)[:240]
            self._transition(
                pkg,
                STATE_DISCONNECTED,
                "idle_disconnect_278",
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

    def note_launch_watchdog(self, package: str, *, relaunch: bool = False) -> None:
        """Detection-only launch timer — does not launch/relaunch or change supervisor state."""
        pkg = str(package or "").strip()
        if not pkg:
            return
        now = time.time()
        with self._lock:
            row = self._states.setdefault(pkg, PackageRjnState(package=pkg))
            row.launch_started_at = now
            row.watchdog_active = True
            row.launch_failed_reason = ""
            row.relaunching = bool(relaunch)
            if relaunch:
                row.internal_state = STATE_RELAUNCHING
                row.last_transition_at = now
                row.last_transition_reason = "relaunch_watchdog_started"
            elif row.internal_state in {STATE_STOPPED, STATE_DEAD, STATE_FAILED}:
                row.internal_state = STATE_LAUNCHING
                row.last_transition_at = now
                row.last_transition_reason = "launch_watchdog_started"

    def begin_launch_watchdog(self, package: str, *, relaunch: bool = False) -> None:
        """Backward-compatible alias — detection only."""
        self.note_launch_watchdog(package, relaunch=relaunch)

    def _process_check(self, package: str) -> tuple[bool, list[str]]:
        """Roblox package PID check — pidof/is_process_running only (no pgrep -f)."""
        pkg = android.validate_package_name(package)
        pids: list[str] = []
        root_tool = getattr(self._root_info, "tool", None) if self._root_info else None
        try:
            if getattr(self._root_info, "available", False) and root_tool:
                res = android.run_root_command(["pidof", pkg], root_tool=root_tool, timeout=2)
                if res.ok and (res.stdout or "").strip():
                    pids = res.stdout.strip().split()
            elif android.is_process_running(pkg):
                res = android.run_command(["pidof", pkg], timeout=2)
                if res.ok and (res.stdout or "").strip():
                    pids = res.stdout.strip().split()
        except Exception:  # noqa: BLE001
            pass
        return bool(pids), pids

    def evaluate_package(self, package: str) -> PackageEvaluateResult:
        pkg = str(package or "").strip()
        now = time.time()
        failed: list[str] = []
        self._ensure_logcat_stream()
        self._poll_recent_logcat()
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
                if (
                    age >= LAUNCH_ONLINE_FALLBACK_MIN_AGE_SECONDS
                    and row.last_positive_online_evidence_at < row.launch_started_at
                    and process_exists
                ):
                    self._try_confirm_launch_online(pkg, now)
                if age > self._launch_watchdog_seconds:
                    had_positive = row.last_positive_online_evidence_at >= row.launch_started_at
                    if not had_positive and row.last_gamejoinloadtime_at < row.launch_started_at:
                        row.watchdog_active = False
                        row.launch_failed_reason = (
                            "no_online_confirmation"
                            if row.last_positive_online_evidence_at <= 0
                            else "launch_watchdog_timeout"
                        )
                        row.error_count += 1
                        row.internal_state = STATE_FAILED
                        row.last_transition_at = now
                        row.last_transition_reason = row.launch_failed_reason
                        row.last_dead_detected_at = now
                        row.last_dead_reason = row.launch_failed_reason

            if not process_exists:
                row.process_missing_streak += 1
                if row.process_missing_streak >= PROCESS_MISSING_CONFIRM:
                    if self._was_ever_online_confirmed(row):
                        row.force_close_detected = True
                        self._transition(
                            pkg,
                            STATE_DEAD,
                            "process_missing",
                            at=now,
                            offline=True,
                        )
                    elif (
                        row.watchdog_active
                        or row.internal_state in {STATE_LAUNCHING, STATE_RELAUNCHING}
                    ):
                        row.process_missing_streak = 0
                    elif row.internal_state == STATE_ONLINE_CONFIRMED:
                        row.force_close_detected = True
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
                if (
                    row.internal_state in {STATE_ONLINE_CONFIRMED, STATE_TELEPORTING}
                    and now - row.last_disconnect_scan_at >= DISCONNECT_SCAN_INTERVAL_SECONDS
                ):
                    row.last_disconnect_scan_at = now
                    disconnect_reason, matched_text = self._detect_live_disconnect(pkg)
                    if disconnect_reason:
                        row.last_with_reason_at = now
                        if matched_text:
                            row.disconnect_prompt_text = str(matched_text)[:240]
                        self._transition(
                            pkg,
                            STATE_DISCONNECTED,
                            disconnect_reason,
                            at=now,
                            offline=True,
                        )

            internal = row.internal_state
            has_positive_evidence = (
                internal == STATE_ONLINE_CONFIRMED
                and row.last_positive_online_evidence_at > 0
            )
            is_online = has_positive_evidence and process_exists
            if process_exists and _package_force_stopped_quick(pkg):
                failed.append("force_stopped")
                is_online = False
            if internal == STATE_ONLINE_CONFIRMED and not process_exists:
                failed.append("process_missing")
                is_online = False
            if internal == STATE_ONLINE_CONFIRMED and row.last_with_reason_at > row.last_positive_online_evidence_at:
                failed.append("with_reason_after_join")
                is_online = False
            if not effective_uid:
                failed.append("uid_not_resolved")
                if row.online_evidence_source not in _UID_OPTIONAL_ONLINE_SOURCES:
                    is_online = False
            if internal != STATE_ONLINE_CONFIRMED:
                failed.append("no_positive_online_evidence")
                is_online = False
            elif not row.last_positive_online_evidence_at:
                failed.append("no_uid_matched_gamejoinloadtime")
                is_online = False

            public = self._map_public_status(
                internal,
                is_online,
                relaunching=bool(row.relaunching or internal == STATE_RELAUNCHING),
            )
            reason = self._decision_reason(row, is_online, failed)
            from .roblox_disconnect_reasons import format_lifecycle_dead_reason

            dead_reason_key = row.last_transition_reason or row.launch_failed_reason or row.last_dead_reason
            if is_online:
                reason_user_friendly = ""
            elif internal in {STATE_DISCONNECTED, STATE_DEAD, STATE_FAILED}:
                reason_user_friendly = format_lifecycle_dead_reason(
                    dead_reason_key,
                    row.disconnect_prompt_text or None,
                )
            else:
                reason_user_friendly = format_lifecycle_dead_reason(
                    row.launch_failed_reason or row.last_transition_reason or reason,
                    row.disconnect_prompt_text or None,
                )

            detail = {
                "internal_state": internal,
                "online_confirmed": str(is_online).lower(),
                "runtime_source": row.runtime_source or "none",
                "online_since": row.online_since or "",
                "online_evidence_source": row.online_evidence_source or "",
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
                "last_positive_online_evidence_at": row.last_positive_online_evidence_at or "",
                "last_with_reason_at": row.last_with_reason_at or "",
                "launch_failed_reason": row.launch_failed_reason or "",
                "reason_internal": row.last_transition_reason or row.launch_failed_reason or reason,
                "reason_user_friendly": reason_user_friendly,
                "disconnect_prompt_text": row.disconnect_prompt_text or "",
                "matched_disconnect_text": row.disconnect_prompt_text or "",
                "why_still_launching": (
                    reason
                    if internal in {STATE_LAUNCHING, STATE_RELAUNCHING} and not is_online
                    else ""
                ),
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

    def _map_public_status(self, internal: str, is_online: bool, *, relaunching: bool = False) -> str:
        if is_online:
            return "Online"
        if internal == STATE_DISCONNECTED:
            return "Disconnected"
        if internal == STATE_FAILED:
            return "Join Failed"
        if relaunching or internal == STATE_RELAUNCHING:
            return "Relaunching"
        if internal in {STATE_LAUNCHING, STATE_TELEPORTING, STATE_STOPPED}:
            return "Launching"
        if internal in {STATE_DEAD}:
            return "Dead"
        return "Dead"

    def _decision_reason(
        self,
        row: PackageRjnState,
        is_online: bool,
        failed: list[str],
    ) -> str:
        if is_online:
            src = row.online_evidence_source or row.runtime_source or "gamejoinloadtime"
            return f"online because UID-matched {src} and process exists"
        if row.force_close_detected or "process_missing" in failed:
            return "process_missing"
        if row.last_with_reason_at and row.last_with_reason_at >= row.last_positive_online_evidence_at:
            if row.last_transition_reason == "idle_disconnect_278":
                return "idle_disconnect_278"
            if row.last_transition_reason in {"ui_disconnect", "logcat_disconnect"}:
                return row.last_transition_reason
            return "UID-matched logcat line contained with reason"
        if row.launch_failed_reason in {"launch_watchdog_timeout", "no_online_confirmation"}:
            return row.launch_failed_reason
        if "uid_not_resolved" in failed:
            return row.uid_error or "uid_not_resolved"
        if row.internal_state in {STATE_LAUNCHING, STATE_RELAUNCHING}:
            if not self._logcat_stream_alive:
                return "logcat stream not alive"
            if row.uid_error:
                return row.uid_error
            return "no positive online evidence after launch"
        return "no UID-matched positive online evidence after launch"

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
                    "last_transition_reason": row.last_transition_reason or None,
                    "last_dead_reason": row.last_dead_reason or None,
                    "decision": ev.reason,
                    "reason_user_friendly": ev.detail.get("reason_user_friendly") or ev.reason,
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
                "detection_only": True,
                "logcat_stream_alive": self._logcat_stream_alive,
                "logcat_pid": self._logcat_pid or None,
                "logcat_last_line_at": self._logcat_last_line_at or None,
                "logcat_last_uid_matched_line_at": self._logcat_last_uid_matched_at or None,
                "logcat_started_at": self._logcat_started_at or None,
                "logcat_cleared_at": self._logcat_cleared_at or None,
                "logcat_error": self._logcat_error or None,
                "detector_errors": list(self._detector_errors),
                "ignored_uid_lines": list(self._ignored_uid_lines[-16:]),
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
