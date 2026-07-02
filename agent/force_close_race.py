"""Force-close / crash detector race diagnostics (test channel).

Compares adb shell, logcat crash monitor, process poll, and the existing
lifecycle detector. Runs only while the RJN watchdog session is active.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .constants import DATA_DIR

RACE_TRACE_PATH = DATA_DIR / "force-close-detector-race.jsonl"
# Cross-process mirror of the live detector snapshot: the watchdog process
# owns the detector, but the dev-probe runs separately and would otherwise
# always report enabled=false / session_inactive.  The detector writes this
# small file while a session is active and the probe reads it when fresh.
RACE_STATE_PATH = DATA_DIR / "force-close-detector-state.json"
RACE_STATE_MAX_AGE_SECONDS = 20.0
RACE_STATE_WRITE_MIN_INTERVAL_SECONDS = 1.0
MAX_EVENTS_PER_PACKAGE = 200
PROCESS_POLL_INTERVAL_SECONDS = float(
    os.environ.get("DENG_REJOIN_FORCE_CLOSE_RACE_POLL_SEC", "0.5") or "0.5"
)
SAMPLE_WINDOW_SECONDS = float(
    os.environ.get("DENG_REJOIN_FORCE_CLOSE_RACE_WINDOW_SEC", "600") or "600"
)
ADB_POLL_EVERY_N = max(1, int(os.environ.get("DENG_REJOIN_FORCE_CLOSE_RACE_ADB_EVERY", "2") or "2"))
RECENT_EVENTS_PROBE_LIMIT = 30

_LOGCAT_HEADER_RE = re.compile(
    r"^(?:(\d+\.\d+)\s+)?(\d{2}-\d{2})\s+(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s+(\d+)\s+(\d+)\s+(\d+)\s"
)
_FORCE_STOP_RE = re.compile(r"Force stopping\s+(\S+)", re.I)
_KILLING_PKG_RE = re.compile(r"Killing\s+\d+:(\S+)", re.I)
_PROC_DIED_RE = re.compile(r"Process\s+(\S+)\s+.*has died", re.I)
_AM_CRASH_RE = re.compile(r"\bam_crash\b", re.I)
_AM_PROC_DIED_RE = re.compile(r"\bam_proc_died\b", re.I)
_FATAL_EXCEPTION_RE = re.compile(r"FATAL EXCEPTION", re.I)
_ANDROID_RUNTIME_RE = re.compile(r"AndroidRuntime", re.I)
_PROCESS_LINE_RE = re.compile(r"Process:\s*(\S+)", re.I)

_NOISE_PACKAGES = frozenset(
    {
        "com.android.systemui",
        "com.android.launcher3",
        "com.google.android.apps.nexuslauncher",
        "com.miui.home",
        "com.sec.android.app.launcher",
    }
)

_active_detector: "ForceCloseRaceDetector | None" = None
_active_lock = threading.Lock()


def get_active_force_close_race_detector() -> "ForceCloseRaceDetector | None":
    with _active_lock:
        return _active_detector


def set_active_force_close_race_detector(detector: "ForceCloseRaceDetector | None") -> None:
    global _active_detector
    with _active_lock:
        _active_detector = detector


@dataclass
class MethodRaceRecord:
    available: bool = True
    first_at: float = 0.0
    latency_ms: float | None = None
    evidence: str = ""
    error: str = ""
    raw_sample: str = ""
    source: str = ""
    state: str = ""
    reason: str = ""
    not_seen_reason: str = ""


@dataclass
class PackageRaceState:
    package: str
    uid: str = ""
    status: str = "tracking"
    current_pids: list[str] = field(default_factory=list)
    last_online_at: float = 0.0
    last_process_present_at: float = 0.0
    last_process_absent_at: float = 0.0
    process_poll: MethodRaceRecord = field(default_factory=MethodRaceRecord)
    adb_shell: MethodRaceRecord = field(default_factory=lambda: MethodRaceRecord(available=False))
    logcat_crash: MethodRaceRecord = field(default_factory=MethodRaceRecord)
    current_detector: MethodRaceRecord = field(default_factory=MethodRaceRecord)
    winner_method: str = ""
    winner_at: float = 0.0
    winner_delta_ms: float | None = None
    suspect_dead_at: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)


class ForceCloseRaceDetector:
    """Low-CPU race diagnostics for force-close detection methods."""

    def __init__(
        self,
        packages: list[str],
        *,
        monitor: Any,
        clock: Callable[[], float] | None = None,
        trace_path: Any | None = None,
    ) -> None:
        self.packages = [str(p).strip() for p in packages if str(p).strip()]
        self._monitor = monitor
        self._clock = clock or time.time
        self._trace_path = trace_path or RACE_TRACE_PATH
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._started_at = 0.0
        self._session_active = False
        self._poll_thread: threading.Thread | None = None
        self._logcat_thread: threading.Thread | None = None
        self._logcat_proc: subprocess.Popen[str] | None = None
        self._adb_available: bool | None = None
        self._adb_error: str = ""
        self._logcat_available = True
        self._logcat_error = ""
        self._poll_count = 0
        self._packages: dict[str, PackageRaceState] = {
            pkg: PackageRaceState(package=pkg) for pkg in self.packages
        }
        self._fatal_block_pkg: str | None = None
        self._fatal_block_pid: str | None = None
        self._fatal_block_lines: list[str] = []
        self._last_state_write_at = 0.0

    def start(self) -> None:
        with self._lock:
            if self._session_active:
                return
            self._session_active = True
            self._started_at = self._clock()
            self._probe_adb_once()
            self._poll_thread = threading.Thread(
                target=self._process_poll_loop,
                name="force-close-race-poll",
                daemon=True,
            )
            self._poll_thread.start()
            self._logcat_thread = threading.Thread(
                target=self._logcat_crash_loop,
                name="force-close-race-logcat",
                daemon=True,
            )
            self._logcat_thread.start()
            set_active_force_close_race_detector(self)
        self._write_state_file(force=True)

    def stop(self) -> None:
        self._stop_event.set()
        proc = self._logcat_proc
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
        with self._lock:
            self._session_active = False
        if get_active_force_close_race_detector() is self:
            set_active_force_close_race_detector(None)
        # Remove the cross-process mirror so a stale file can never make the
        # probe believe a session is still active after Start exits.
        try:
            RACE_STATE_PATH.unlink()
        except OSError:
            pass

    def _write_state_file(self, *, force: bool = False) -> None:
        """Throttled cross-process mirror of the live snapshot."""
        now = self._clock()
        if not force and (now - self._last_state_write_at) < RACE_STATE_WRITE_MIN_INTERVAL_SECONDS:
            return
        self._last_state_write_at = now
        try:
            RACE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "written_at": time.time(),
                "pid": os.getpid(),
                "snapshot": self.probe_snapshot(),
            }
            tmp = RACE_STATE_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, RACE_STATE_PATH)
        except OSError:
            pass

    def _probe_adb_once(self) -> None:
        adb = shutil.which("adb")
        if not adb:
            self._adb_available = False
            self._adb_error = "adb_not_in_path"
            return
        try:
            res = subprocess.run(
                [adb, "get-state"],
                capture_output=True,
                text=True,
                timeout=2,
                errors="replace",
            )
            if res.returncode == 0 and (res.stdout or "").strip().lower() == "device":
                self._adb_available = True
                self._adb_error = ""
            else:
                self._adb_available = False
                detail = (res.stderr or res.stdout or "adb_not_ready").strip()[:120]
                self._adb_error = detail or "adb_not_ready"
        except Exception as exc:  # noqa: BLE001
            self._adb_available = False
            self._adb_error = str(exc)[:120]

    def _append_event(self, pkg: str, event: dict[str, Any]) -> None:
        with self._lock:
            row = self._packages.setdefault(pkg, PackageRaceState(package=pkg))
            row.events.append(event)
            if len(row.events) > MAX_EVENTS_PER_PACKAGE:
                row.events = row.events[-MAX_EVENTS_PER_PACKAGE:]
        try:
            self._trace_path.parent.mkdir(parents=True, exist_ok=True)
            with self._trace_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, separators=(",", ":")) + "\n")
        except OSError:
            pass

    def _latency_ms(self, pkg: str, at: float) -> float | None:
        row = self._packages.get(pkg)
        if row is None or row.last_process_present_at <= 0:
            return None
        return round(max(0.0, (at - row.last_process_present_at) * 1000.0), 1)

    def _maybe_set_winner(self, pkg: str, method: str, at: float) -> None:
        with self._lock:
            row = self._packages.setdefault(pkg, PackageRaceState(package=pkg))
            if row.winner_method:
                return
            row.winner_method = method
            row.winner_at = at
            baseline = row.current_detector.first_at
            if baseline > 0:
                row.winner_delta_ms = round((at - baseline) * 1000.0, 1)
            else:
                row.winner_delta_ms = None

    def note_online(self, package: str, *, at: float | None = None) -> None:
        pkg = str(package or "").strip()
        if not pkg:
            return
        now = float(at if at is not None else self._clock())
        with self._lock:
            row = self._packages.setdefault(pkg, PackageRaceState(package=pkg))
            row.last_online_at = now
            row.status = "online"

    def note_current_detector(
        self,
        package: str,
        *,
        at: float | None = None,
        state: str,
        reason: str,
        source: str = "current_lifecycle",
    ) -> None:
        pkg = str(package or "").strip()
        if not pkg:
            return
        now = float(at if at is not None else self._clock())
        with self._lock:
            row = self._packages.setdefault(pkg, PackageRaceState(package=pkg))
            rec = row.current_detector
            if rec.first_at <= 0:
                rec.first_at = now
                rec.latency_ms = self._latency_ms(pkg, now)
                rec.state = state
                rec.reason = reason
                rec.source = source
                rec.evidence = f"{state}:{reason}"
                if state in {"DEAD", "DISCONNECTED"} and reason in {
                    "process_missing",
                    "heartbeat_lost",
                }:
                    row.status = "dead"
        self._append_event(
            pkg,
            {
                "at": now,
                "package": pkg,
                "method": "current_detector",
                "state": state,
                "reason": reason,
                "source": source,
            },
        )

    def _process_poll_once(self, pkg: str, now: float) -> None:
        from .rjn_lifecycle_monitor import (
            STATE_ONLINE_CONFIRMED,
            _unpack_process_check,
        )

        monitor = self._monitor
        exists, pids, definitive, source = self._poll_process(pkg)
        uid = ""
        with monitor._lock:
            row = monitor._states.get(pkg)
            if row is not None:
                uid = str(row.uid or monitor._uid_map.get(pkg) or "")
                if row.internal_state == STATE_ONLINE_CONFIRMED:
                    self._packages.setdefault(pkg, PackageRaceState(package=pkg)).last_online_at = (
                        row.online_since or row.last_positive_online_evidence_at or now
                    )

        race = self._packages.setdefault(pkg, PackageRaceState(package=pkg))
        race.uid = uid
        race.current_pids = list(pids)

        if exists:
            race.last_process_present_at = now
            race.last_process_absent_at = 0.0
            if race.status not in {"dead", "suspect_dead"}:
                race.status = "online" if race.last_online_at > 0 else "tracking"
            return

        if race.last_process_present_at <= 0:
            race.process_poll.not_seen_reason = "never_seen_present_this_session"
            return

        rec = race.process_poll
        if rec.first_at <= 0 and definitive:
            rec.available = True
            rec.first_at = now
            rec.latency_ms = self._latency_ms(pkg, now)
            rec.source = source
            rec.evidence = f"process_absent:{source}"
            race.last_process_absent_at = now
            race.status = "dead"
            self._maybe_set_winner(pkg, "process_poll", now)
            self._append_event(
                pkg,
                {
                    "at": now,
                    "package": pkg,
                    "method": "process_poll",
                    "source": source,
                    "definitive": True,
                    "latency_ms": rec.latency_ms,
                },
            )
            was_online = False
            with monitor._lock:
                mrow = monitor._states.get(pkg)
                was_online = bool(
                    mrow
                    and (
                        mrow.internal_state == STATE_ONLINE_CONFIRMED
                        or mrow.ingame_hb_ever
                        or mrow.online_since > 0
                    )
                )
            if was_online:
                try:
                    monitor.try_mark_force_close_dead(pkg, at=now)
                except Exception:  # noqa: BLE001
                    pass

        if rec.first_at <= 0 and not definitive:
            rec.not_seen_reason = "absent_but_not_definitive"

    def _poll_process(self, package: str) -> tuple[bool, list[str], bool, str]:
        from . import android
        from .rjn_lifecycle_monitor import _unpack_process_check

        monitor = self._monitor
        pkg = android.validate_package_name(package)
        try:
            exists, pids, definitive = _unpack_process_check(monitor._process_check(pkg))
            if exists:
                return True, pids, False, "proc_cmdline"
            root_tool = getattr(monitor._root_info, "tool", None) if monitor._root_info else None
            if getattr(monitor._root_info, "available", False) and root_tool:
                res = android.run_root_command(["pidof", pkg], root_tool=root_tool, timeout=2)
                if res.ok and (res.stdout or "").strip():
                    return True, res.stdout.strip().split(), False, "su_proc"
            return False, [], definitive, "proc_cmdline"
        except Exception as exc:  # noqa: BLE001
            return False, [], False, f"fallback:{exc}"[:40]

    def _adb_poll_once(self, pkg: str, now: float) -> None:
        race = self._packages.setdefault(pkg, PackageRaceState(package=pkg))
        rec = race.adb_shell
        rec.available = bool(self._adb_available)
        if not self._adb_available:
            rec.error = self._adb_error or "adb_unavailable"
            rec.not_seen_reason = "adb_unavailable"
            return
        adb = shutil.which("adb")
        if not adb:
            rec.available = False
            rec.error = "adb_not_in_path"
            return
        try:
            res = subprocess.run(
                [adb, "shell", "pidof", pkg],
                capture_output=True,
                text=True,
                timeout=2,
                errors="replace",
            )
            stdout = (res.stdout or "").strip()
            if stdout:
                if race.last_process_present_at <= 0:
                    rec.not_seen_reason = "adb_seen_alive_before_baseline"
                return
            if race.last_process_present_at <= 0:
                rec.not_seen_reason = "never_seen_present_this_session"
                return
            if rec.first_at <= 0:
                rec.first_at = now
                rec.latency_ms = self._latency_ms(pkg, now)
                rec.evidence = "adb_pidof_empty"
                self._append_event(
                    pkg,
                    {
                        "at": now,
                        "package": pkg,
                        "method": "adb_shell",
                        "evidence": rec.evidence,
                        "latency_ms": rec.latency_ms,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            rec.available = False
            rec.error = str(exc)[:120]

    def _process_poll_loop(self) -> None:
        interval = max(0.25, PROCESS_POLL_INTERVAL_SECONDS)
        while not self._stop_event.is_set():
            now = self._clock()
            self._poll_count += 1
            pkgs = list(self.packages)
            for pkg in pkgs:
                if self._stop_event.is_set():
                    break
                try:
                    self._process_poll_once(pkg, now)
                    if self._poll_count % ADB_POLL_EVERY_N == 0:
                        self._adb_poll_once(pkg, now)
                except Exception:  # noqa: BLE001
                    pass
            self._write_state_file()
            self._stop_event.wait(interval)

    def _parse_logcat_epoch(self, line: str) -> float:
        match = _LOGCAT_HEADER_RE.match(line.strip())
        if not match:
            return self._clock()
        epoch_s = match.group(1)
        if epoch_s:
            try:
                return float(epoch_s)
            except ValueError:
                pass
        return self._clock()

    def _package_for_logcat_line(
        self,
        line: str,
        *,
        uid: str | None,
        pid: str | None,
        explicit_pkg: str | None = None,
    ) -> str | None:
        if explicit_pkg and explicit_pkg in self.packages:
            return explicit_pkg
        monitor = self._monitor
        if uid:
            with monitor._lock:
                pkg = monitor._uid_to_package.get(str(uid))
            if pkg:
                return pkg
        if pid:
            with monitor._lock:
                pkg = monitor._pid_to_package.get(str(pid))
            if pkg:
                return pkg
        matches = [p for p in self.packages if p and p in line]
        if len(matches) == 1:
            return matches[0]
        return None

    def _is_noise_line(self, line: str) -> bool:
        lower = line.lower()
        if "com.android.systemui" in lower or "recents" in lower:
            return True
        for pkg in _NOISE_PACKAGES:
            if pkg in line:
                return True
        return False

    def _handle_logcat_line(self, line: str) -> None:
        if not line.strip() or self._is_noise_line(line):
            return
        now = self._parse_logcat_epoch(line)
        match = _LOGCAT_HEADER_RE.match(line.strip())
        uid = match.group(7) if match else None
        pid = match.group(8) if match else None

        explicit_pkg: str | None = None
        evidence = ""
        force_stop = False

        m = _FORCE_STOP_RE.search(line)
        if m:
            explicit_pkg = m.group(1)
            evidence = "force_stop"
            force_stop = True
        if not explicit_pkg:
            m = _KILLING_PKG_RE.search(line)
            if m:
                explicit_pkg = m.group(1)
                evidence = evidence or "killing"
                force_stop = True
        if not explicit_pkg:
            m = _PROC_DIED_RE.search(line)
            if m:
                explicit_pkg = m.group(1)
                evidence = evidence or "proc_died"
                force_stop = True
        if _FATAL_EXCEPTION_RE.search(line) or _ANDROID_RUNTIME_RE.search(line):
            self._fatal_block_lines.append(line[:240])
            self._fatal_block_pid = pid
            proc_match = _PROCESS_LINE_RE.search(line)
            if proc_match:
                self._fatal_block_pkg = proc_match.group(1)
            if len(self._fatal_block_lines) > 8:
                self._fatal_block_lines = self._fatal_block_lines[-8:]
            return
        if self._fatal_block_lines and _PROCESS_LINE_RE.search(line):
            proc_match = _PROCESS_LINE_RE.search(line)
            if proc_match:
                self._fatal_block_pkg = proc_match.group(1)
        if self._fatal_block_lines and (
            _AM_CRASH_RE.search(line) or _AM_PROC_DIED_RE.search(line)
        ):
            explicit_pkg = explicit_pkg or self._fatal_block_pkg
            evidence = evidence or "am_crash"
        if self._fatal_block_lines and self._fatal_block_pkg:
            pkg = self._package_for_logcat_line(
                line,
                uid=uid,
                pid=pid or self._fatal_block_pid,
                explicit_pkg=self._fatal_block_pkg,
            )
            if pkg:
                sample = " | ".join(self._fatal_block_lines[-3:])[:240]
                block_evidence = (
                    "fatal_exception"
                    if any(_FATAL_EXCEPTION_RE.search(x) for x in self._fatal_block_lines)
                    else (evidence or "am_crash")
                )
                self._record_logcat_crash(pkg, now, block_evidence, sample, force_stop)
            self._fatal_block_lines = []
            self._fatal_block_pkg = None
            self._fatal_block_pid = None
            return

        if _AM_CRASH_RE.search(line) or _AM_PROC_DIED_RE.search(line):
            evidence = evidence or ("am_crash" if _AM_CRASH_RE.search(line) else "am_proc_died")

        pkg = self._package_for_logcat_line(line, uid=uid, pid=pid, explicit_pkg=explicit_pkg)
        if not pkg:
            return
        if evidence:
            sample = line.strip()[:240]
            self._record_logcat_crash(pkg, now, evidence, sample, force_stop)

    def _record_logcat_crash(
        self,
        pkg: str,
        at: float,
        evidence: str,
        raw_sample: str,
        force_stop: bool,
    ) -> None:
        race = self._packages.setdefault(pkg, PackageRaceState(package=pkg))
        rec = race.logcat_crash
        if rec.first_at <= 0:
            rec.available = True
            rec.first_at = at
            rec.latency_ms = self._latency_ms(pkg, at)
            rec.evidence = evidence
            rec.raw_sample = raw_sample
            if force_stop:
                race.suspect_dead_at = at
                if race.status != "dead":
                    race.status = "suspect_dead"
            self._append_event(
                pkg,
                {
                    "at": at,
                    "package": pkg,
                    "method": "logcat_crash",
                    "evidence": evidence,
                    "raw_sample": raw_sample[:160],
                    "latency_ms": rec.latency_ms,
                },
            )
        elif force_stop and race.status != "dead":
            race.suspect_dead_at = at

    def _logcat_crash_loop(self) -> None:
        cmd_variants = [
            ["logcat", "-v", "epoch", "-v", "uid"],
            ["logcat", "-v", "uid"],
        ]
        proc: subprocess.Popen[str] | None = None
        for cmd in cmd_variants:
            if self._stop_event.is_set():
                return
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                self._logcat_proc = proc
                self._logcat_available = True
                self._logcat_error = ""
                break
            except Exception as exc:  # noqa: BLE001
                self._logcat_available = False
                self._logcat_error = str(exc)[:120]
                proc = None
        if proc is None or proc.stdout is None:
            return
        while not self._stop_event.is_set():
            try:
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)
                    continue
                self._handle_logcat_line(line.rstrip("\n"))
            except Exception:  # noqa: BLE001
                if proc.poll() is not None:
                    break
                continue
        self._logcat_available = False

    def probe_snapshot(self) -> dict[str, Any]:
        now = self._clock()
        window_start = now - SAMPLE_WINDOW_SECONDS
        packages_out: dict[str, Any] = {}
        recent_all: list[dict[str, Any]] = []

        with self._lock:
            for pkg, row in self._packages.items():
                pp = row.process_poll
                adb = row.adb_shell
                lc = row.logcat_crash
                cd = row.current_detector
                if pp.first_at <= 0 and pp.not_seen_reason:
                    pp_reason = pp.not_seen_reason
                elif pp.first_at <= 0:
                    pp_reason = "no_absent_yet"
                else:
                    pp_reason = ""
                if lc.first_at <= 0:
                    lc_reason = lc.not_seen_reason or "no_crash_line_matched"
                else:
                    lc_reason = ""
                winner_method = row.winner_method
                winner_at = row.winner_at
                winner_delta = row.winner_delta_ms
                candidates: list[tuple[float, str]] = []
                for name, rec in (
                    ("process_poll", pp),
                    ("adb_shell", adb),
                    ("logcat_crash", lc),
                    ("current_detector", cd),
                ):
                    if rec.first_at > 0:
                        candidates.append((rec.first_at, name))
                if candidates:
                    winner_at, winner_method = min(candidates, key=lambda x: x[0])
                    winner_delta = (
                        round((winner_at - cd.first_at) * 1000.0, 1) if cd.first_at > 0 else None
                    )
                packages_out[pkg] = {
                    "status": row.status,
                    "uid": row.uid or None,
                    "current_pids": list(row.current_pids),
                    "last_online_at": row.last_online_at or None,
                    "last_process_present_at": row.last_process_present_at or None,
                    "last_process_absent_at": row.last_process_absent_at or None,
                    "methods": {
                        "adb_shell": {
                            "available": adb.available if adb.first_at else bool(self._adb_available),
                            "first_dead_at": adb.first_at or None,
                            "latency_ms_from_last_present": adb.latency_ms,
                            "evidence": adb.evidence or None,
                            "error": adb.error or self._adb_error or None,
                            "not_seen_reason": adb.not_seen_reason or None,
                        },
                        "logcat_crash": {
                            "available": self._logcat_available,
                            "first_event_at": lc.first_at or None,
                            "latency_ms_from_last_present": lc.latency_ms,
                            "evidence": lc.evidence or None,
                            "raw_sample": lc.raw_sample or None,
                            "not_seen_reason": lc_reason or None,
                        },
                        "process_poll": {
                            "available": pp.available,
                            "first_absent_at": pp.first_at or None,
                            "latency_ms_from_last_present": pp.latency_ms,
                            "interval_ms": round(PROCESS_POLL_INTERVAL_SECONDS * 1000.0, 0),
                            "evidence": pp.evidence or None,
                            "source": pp.source or None,
                            "not_seen_reason": pp_reason or None,
                        },
                        "current_detector": {
                            "first_dead_at": cd.first_at or None,
                            "latency_ms_from_last_present": cd.latency_ms,
                            "state": cd.state or None,
                            "reason": cd.reason or None,
                            "source": cd.source or "current_lifecycle",
                        },
                    },
                    "winner": {
                        "method": winner_method or None,
                        "at": winner_at or None,
                        "delta_ms_vs_current_detector": winner_delta,
                    },
                }
                for ev in row.events:
                    if float(ev.get("at") or 0) >= window_start:
                        recent_all.append(ev)

        recent_all.sort(key=lambda e: float(e.get("at") or 0), reverse=True)
        return {
            "enabled": self._session_active,
            "sample_window_seconds": SAMPLE_WINDOW_SECONDS,
            "poll_interval_ms": round(PROCESS_POLL_INTERVAL_SECONDS * 1000.0, 0),
            "adapters": {
                "adb_shell": {
                    "available": bool(self._adb_available),
                    "error": self._adb_error or None,
                },
                "logcat_crash": {
                    "available": self._logcat_available,
                    "error": self._logcat_error or None,
                },
                "process_poll": {"available": True},
            },
            "packages": packages_out,
            "recent_events": recent_all[:RECENT_EVENTS_PROBE_LIMIT],
        }


def load_recent_trace_events(
    *,
    limit: int = RECENT_EVENTS_PROBE_LIMIT,
    window_seconds: float = SAMPLE_WINDOW_SECONDS,
    clock: Callable[[], float] | None = None,
) -> list[dict[str, Any]]:
    """Load recent JSONL events when the live detector is not active."""
    now = (clock or time.time)()
    cutoff = now - window_seconds
    if not RACE_TRACE_PATH.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        lines = RACE_TRACE_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in reversed(lines[-2000:]):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if float(ev.get("at") or 0) >= cutoff:
            out.append(ev)
        if len(out) >= limit:
            break
    out.reverse()
    return out


def read_race_state_file(
    *, max_age_s: float = RACE_STATE_MAX_AGE_SECONDS
) -> dict[str, Any] | None:
    """Return the persisted force-close snapshot if fresh, else ``None``.

    Lets the separate dev-probe process report the live detector state owned
    by the running Start/watchdog process.
    """
    try:
        data = json.loads(RACE_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        age = time.time() - float(data.get("written_at"))
    except (TypeError, ValueError):
        return None
    if age > max_age_s:
        return None
    snap = data.get("snapshot")
    if not isinstance(snap, dict):
        return None
    snap = dict(snap)
    snap["source"] = "state_file"
    snap["state_file_age_s"] = round(age, 2)
    snap["state_file_pid"] = data.get("pid")
    return snap


def probe_force_close_race_snapshot(clock: Callable[[], float] | None = None) -> dict[str, Any]:
    """Probe entry: prefer live detector, else the persisted cross-process
    state file, else reconstruct from the JSONL trace."""
    live = get_active_force_close_race_detector()
    if live is not None and live._session_active:
        return live.probe_snapshot()
    disk = read_race_state_file()
    if disk is not None:
        return disk
    events = load_recent_trace_events(clock=clock)
    return {
        "enabled": False,
        "sample_window_seconds": SAMPLE_WINDOW_SECONDS,
        "poll_interval_ms": round(PROCESS_POLL_INTERVAL_SECONDS * 1000.0, 0),
        "adapters": {
            "adb_shell": {"available": bool(shutil.which("adb")), "error": None},
            "logcat_crash": {"available": None, "error": "session_inactive"},
            "process_poll": {"available": None},
        },
        "packages": {},
        "recent_events": events,
        "reason": "watchdog_session_not_active",
    }
