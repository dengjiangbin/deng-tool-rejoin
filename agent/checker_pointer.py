"""Global checker pointer / lifecycle state (single source of truth).

This module holds the *one* authoritative snapshot of the focused
round-robin checker: the global State-column pointer text shown in the
runtime table, the currently focused package, first-launch progress,
recovery ownership, and per-package no-heartbeat focus counters.

It is deliberately tiny, dependency-free, and thread-safe so that:

* the checker/scheduler can publish state from its own thread,
* the Termux table renderer can read the pointer text cheaply, and
* the dev-probe can serialise every field for offline debugging.

There is exactly one process-wide instance (:func:`get`).  The renderer
and probe must never mutate it; only the checker writes.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

# ── Cross-process persistence ──────────────────────────────────────────
# The live checker runs inside the Start/watchdog process, but the
# ``dev-probe`` command runs as a *separate* process.  A bare in-memory
# singleton is therefore always ``idle`` when read by the probe.  To make
# the probe reflect the real running lifecycle we mirror the snapshot to a
# small JSON file (exactly like RjnLifecycleMonitor.write_probe_file) and
# have the probe read it when its own singleton is idle.
_STATE_FILENAME = "focused-checker-state.json"
_STATE_MAX_AGE_S = 20.0
_PERSIST_MIN_INTERVAL_S = 0.4


def _state_file_path():
    try:
        from .constants import DATA_DIR

        return DATA_DIR / _STATE_FILENAME
    except Exception:  # noqa: BLE001
        return None

# ── Checker mode / state-machine states ────────────────────────────────
MODE_IDLE = "idle"
MODE_GETTING_READY = "getting_ready"
MODE_FIRST_LAUNCHING = "first_launching"
MODE_CHECKING = "checking"
MODE_DEAD_DETECTED = "dead_detected"
MODE_RECOVERY_CLEAR_CACHE = "recovery_clear_cache"
MODE_RECOVERY_REOPENING = "recovery_reopening"
MODE_RECOVERY_RELAUNCHING = "recovery_relaunching"
MODE_RECOVERY_WAIT_ONLINE = "recovery_wait_online"
MODE_RESUME_CHECKING = "resume_checking"

# ── Pointer text labels (State column, row 2) ──────────────────────────
POINTER_GETTING_READY = "Getting Ready.."
POINTER_OPENING = "Opening.."
POINTER_CHECKING = "Checking.."
POINTER_DEAD_DETECTED = "Dead Detected"
POINTER_START_RECOVERY = "Start Recovery"
POINTER_CLEARING_CACHE = "Clearing Cache.."
POINTER_REOPENING = "Reopening"
POINTER_RELAUNCHING = "Relaunching"
POINTER_ONLINE = "Online"
POINTER_RESUME_CHECKING = "Resume Checking.."


@dataclass
class _PackagePointer:
    consecutive_no_heartbeat_focus_count: int = 0
    last_online_evidence_source: str = ""
    last_online_evidence_age_ms: float | None = None
    last_pid: str = ""
    pid_missing_since: float | None = None
    # ``display_state`` is what the table row should show right now
    # (e.g. "Checking" while the package is the active focus); once the
    # focus window resolves it is set to the real result and mirrored to
    # ``last_real_state`` so other renders keep the last known real value.
    display_state: str = ""
    last_real_state: str = ""
    # ── Single-relay presence gating ──────────────────────────────────
    # ``committed_presence_state`` is the ONLY authoritative visible
    # presence value (Online / No Heartbeat / Dead).  It is written
    # exclusively by the focused checker relay.  Raw detectors only set the
    # ``raw_*_evidence_pending`` flags; the visible row never flips to a
    # final presence state until the checker focuses the package and commits.
    committed_presence_state: str = ""
    raw_online_evidence_pending: bool = False
    raw_dead_evidence_pending: bool = False


@dataclass
class CheckerPointerState:
    """Thread-safe holder for the global checker pointer + per-package data."""

    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    checker_mode: str = MODE_IDLE
    state_pointer_text: str = ""

    active_focus_package: str = ""
    active_focus_index: int = 0
    focus_started_at: float | None = None
    focus_window_s: float = 10.0

    first_launch_phase: str = ""
    first_launch_next_package_at: float | None = None
    first_launch_started_packages: list[str] = field(default_factory=list)
    first_launch_supposedly_launched_packages: list[str] = field(default_factory=list)

    recovery_in_progress: bool = False
    active_recovery_package: str = ""
    recovery_stage: str = ""

    last_dead_reason: str = ""
    last_dead_source: str = ""
    last_dead_evidence: str = ""

    logcat_reader_alive: bool = False
    checker_loop_alive: bool = False
    duplicate_loop_guard_status: str = "ok"

    # ── Single-relay architecture metadata (probe-visible) ────────────
    first_launch_interval_s: float = 30.0
    last_launch_interval_s: float | None = None
    launch_waiting_for_online: bool = False
    launch_blocked_reason: str = ""

    # ── Session / liveness / heartbeat (probe) ────────────────────────
    session_id: str = ""
    checker_pid: int | None = None
    start_pressed_at: float | None = None            # wall-clock (time.time)
    checker_last_heartbeat_at: float | None = None   # wall-clock (time.time)
    checker_dead_reason: str = ""

    # ── Bounded recovery stage timing (probe) ─────────────────────────
    recovery_stage_started_at: float | None = None   # wall-clock
    recovery_stage_deadline_s: float | None = None

    # ── Cache-clear result (probe) ────────────────────────────────────
    cache_clear_started_at: float | None = None
    cache_clear_finished_at: float | None = None
    cache_clear_duration_ms: float | None = None
    cache_clear_command_kind: str = ""
    cache_clear_exit_code: int | None = None
    cache_clear_timed_out: bool = False
    cache_clear_error: str = ""
    cache_clear_status: str = ""

    # ── Transition / pointer history (probe) ──────────────────────────
    last_state_transition: str = ""
    last_ui_pointer_history: list[str] = field(default_factory=list)
    _last_history_mode: str = ""
    _last_history_pointer: str = ""

    _packages: dict[str, _PackagePointer] = field(default_factory=dict)

    # Persistence is opt-in: only the Start/watchdog process enables it so a
    # read-only probe process can never overwrite the file with idle state.
    _persist_enabled: bool = False
    _last_persist_at: float = 0.0

    # ── internal helpers ──────────────────────────────────────────────
    def _pkg(self, package: str) -> _PackagePointer:
        row = self._packages.get(package)
        if row is None:
            row = _PackagePointer()
            self._packages[package] = row
        return row

    # ── writers (checker thread only) ─────────────────────────────────
    def reset(self) -> None:
        with self._lock:
            self.checker_mode = MODE_IDLE
            self.state_pointer_text = ""
            self.active_focus_package = ""
            self.active_focus_index = 0
            self.focus_started_at = None
            self.first_launch_phase = ""
            self.first_launch_next_package_at = None
            self.first_launch_started_packages = []
            self.first_launch_supposedly_launched_packages = []
            self.recovery_in_progress = False
            self.active_recovery_package = ""
            self.recovery_stage = ""

    def reset_for_new_start(
        self,
        *,
        session_id: str,
        pid: int | None = None,
        start_pressed_at: float | None = None,
    ) -> None:
        """Clear ALL stale state from a previous Start and stamp a new session.

        Every probe field that could leak old-session data (pointer text,
        recovery stage, cache-clear result, first-launch lists) is wiped so a
        fresh ``3. Start`` never shows a prior recovery's pid/stage as current.
        """
        with self._lock:
            self.reset()
            self.session_id = str(session_id or "")
            self.checker_pid = int(pid) if pid is not None else None
            now = time.time()
            self.start_pressed_at = start_pressed_at if start_pressed_at is not None else now
            self.checker_last_heartbeat_at = now
            self.checker_dead_reason = ""
            self.checker_loop_alive = True
            # Wipe stale recovery + cache-clear result.
            self.recovery_stage_started_at = None
            self.recovery_stage_deadline_s = None
            self.cache_clear_started_at = None
            self.cache_clear_finished_at = None
            self.cache_clear_duration_ms = None
            self.cache_clear_command_kind = ""
            self.cache_clear_exit_code = None
            self.cache_clear_timed_out = False
            self.cache_clear_error = ""
            self.cache_clear_status = ""
            self.last_state_transition = ""
            self.last_ui_pointer_history = []
            self._last_history_mode = ""
            self._last_history_pointer = ""
            self.last_launch_interval_s = None
            self._persist(force=True)

    def heartbeat(self, *, reason: str = "") -> None:
        """Refresh the liveness timestamp so the probe can tell alive vs dead.

        Called from the watchdog thread every round — independent of the main
        Start thread — so a blocking launch/cache-clear can never make the
        probe report the checker as idle/dead while the session is running.
        """
        with self._lock:
            self.checker_last_heartbeat_at = time.time()
            self.checker_loop_alive = True
            if reason:
                self.checker_dead_reason = ""
            self._persist()

    def set_mode(self, mode: str, pointer_text: str | None = None) -> None:
        with self._lock:
            self.checker_mode = mode
            if pointer_text is not None:
                self.state_pointer_text = pointer_text
            self._persist(force=True)

    def set_pointer_text(self, text: str) -> None:
        with self._lock:
            self.state_pointer_text = text
            self._persist()

    def begin_getting_ready(
        self, packages: list[str], *, interval_s: float | None = None
    ) -> None:
        with self._lock:
            self.checker_mode = MODE_GETTING_READY
            self.state_pointer_text = POINTER_GETTING_READY
            self.first_launch_phase = "getting_ready"
            self.first_launch_started_packages = []
            self.first_launch_supposedly_launched_packages = []
            self.launch_waiting_for_online = False
            self.launch_blocked_reason = ""
            if interval_s is not None:
                self.first_launch_interval_s = float(interval_s)
            for pkg in packages:
                self._pkg(pkg)
            self._persist(force=True)

    def begin_opening(self, package: str, *, next_package_at: float | None = None) -> None:
        with self._lock:
            self.checker_mode = MODE_FIRST_LAUNCHING
            self.state_pointer_text = POINTER_OPENING
            self.first_launch_phase = "first_launching"
            self.first_launch_next_package_at = next_package_at
            if package and package not in self.first_launch_started_packages:
                self.first_launch_started_packages.append(package)
            self._persist(force=True)

    def mark_supposedly_launched(self, package: str) -> None:
        with self._lock:
            if package and package not in self.first_launch_supposedly_launched_packages:
                self.first_launch_supposedly_launched_packages.append(package)
            self._persist(force=True)

    def note_launch_interval(self, interval_s: float) -> None:
        with self._lock:
            self.last_launch_interval_s = float(interval_s)
            self._persist()

    def touch_persist(self) -> None:
        """Force a fresh state-file write.

        Used during the first-launch 30s gaps so the separate probe process
        never sees a stale (>20s) file and mislabels the checker as idle.
        """
        with self._lock:
            self._persist(force=True)

    def begin_focus(self, package: str, index: int, *, now: float, window_s: float | None = None) -> None:
        with self._lock:
            self.checker_mode = MODE_CHECKING
            self.active_focus_package = package
            self.active_focus_index = index
            self.focus_started_at = now
            if window_s is not None:
                self.focus_window_s = float(window_s)
            self.state_pointer_text = POINTER_CHECKING
            # Only the focused package is "Checking"; clear any stale
            # Checking marker left on a previously focused package.
            for pkg, row in self._packages.items():
                if row.display_state == "Checking" and pkg != package:
                    row.display_state = row.last_real_state
            self._pkg(package).display_state = "Checking"
            self._persist(force=True)

    def update_focus_timer(self, elapsed_s: float) -> None:
        """Publish the ``1s``..``Ns`` countdown text during a focus window."""
        with self._lock:
            secs = max(0, int(elapsed_s))
            self.state_pointer_text = f"{secs}s"
            self._persist()

    def mark_dead_detected(self, package: str, reason: str, source: str, evidence: str) -> None:
        with self._lock:
            self.checker_mode = MODE_DEAD_DETECTED
            self.state_pointer_text = POINTER_DEAD_DETECTED
            self.last_dead_reason = reason
            self.last_dead_source = source
            self.last_dead_evidence = evidence
            if package:
                row = self._pkg(package)
                row.committed_presence_state = "Dead"
                row.last_real_state = "Dead"
                row.display_state = "Dead"
                row.raw_dead_evidence_pending = False
            self._persist(force=True)

    # Per-stage watchdog deadlines (seconds). No stage may be infinite.
    RECOVERY_STAGE_DEADLINES = {
        "clear_cache": 30.0,
        "reopening": 15.0,
        "relaunching": 30.0,
        "wait_online": 30.0,
    }

    def begin_recovery(self, package: str, *, reason: str = "") -> None:
        with self._lock:
            self.recovery_in_progress = True
            self.active_recovery_package = package
            self.recovery_stage = "start"
            self.recovery_stage_started_at = time.time()
            self.recovery_stage_deadline_s = None
            self.checker_mode = MODE_DEAD_DETECTED
            self.state_pointer_text = POINTER_START_RECOVERY
            if reason:
                self.last_dead_reason = reason
            self._persist(force=True)

    def set_recovery_stage(self, stage: str, *, deadline_s: float | None = None) -> None:
        _map = {
            "clear_cache": (MODE_RECOVERY_CLEAR_CACHE, POINTER_CLEARING_CACHE),
            "reopening": (MODE_RECOVERY_REOPENING, POINTER_REOPENING),
            "relaunching": (MODE_RECOVERY_RELAUNCHING, POINTER_RELAUNCHING),
            "wait_online": (MODE_RECOVERY_WAIT_ONLINE, POINTER_RELAUNCHING),
            "online": (MODE_RECOVERY_WAIT_ONLINE, POINTER_ONLINE),
            "recovery_failed": (MODE_RESUME_CHECKING, POINTER_RESUME_CHECKING),
        }
        with self._lock:
            self.recovery_stage = stage
            self.recovery_stage_started_at = time.time()
            self.recovery_stage_deadline_s = (
                deadline_s
                if deadline_s is not None
                else self.RECOVERY_STAGE_DEADLINES.get(stage)
            )
            mode_text = _map.get(stage)
            if mode_text is not None:
                self.checker_mode, self.state_pointer_text = mode_text
            self._persist(force=True)

    def recovery_stage_elapsed_s(self) -> float | None:
        with self._lock:
            if self.recovery_stage_started_at is None:
                return None
            return max(0.0, time.time() - self.recovery_stage_started_at)

    def recovery_stage_expired(self) -> bool:
        """True when the active recovery stage has blown its deadline."""
        with self._lock:
            if not self.recovery_in_progress:
                return False
            if self.recovery_stage_started_at is None or self.recovery_stage_deadline_s is None:
                return False
            return (time.time() - self.recovery_stage_started_at) > self.recovery_stage_deadline_s

    def begin_cache_clear(self, *, command_kind: str = "") -> None:
        with self._lock:
            self.cache_clear_started_at = time.time()
            self.cache_clear_finished_at = None
            self.cache_clear_duration_ms = None
            self.cache_clear_command_kind = command_kind
            self.cache_clear_exit_code = None
            self.cache_clear_timed_out = False
            self.cache_clear_error = ""
            self.cache_clear_status = "running"
            self._persist(force=True)

    def record_cache_clear_result(
        self,
        *,
        status: str,
        exit_code: int | None = None,
        timed_out: bool = False,
        error: str = "",
        command_kind: str | None = None,
        finished_at: float | None = None,
    ) -> None:
        with self._lock:
            fin = finished_at if finished_at is not None else time.time()
            self.cache_clear_finished_at = fin
            if self.cache_clear_started_at is not None:
                self.cache_clear_duration_ms = round(
                    (fin - self.cache_clear_started_at) * 1000.0, 1
                )
            if command_kind is not None:
                self.cache_clear_command_kind = command_kind
            self.cache_clear_exit_code = exit_code
            self.cache_clear_timed_out = bool(timed_out)
            self.cache_clear_error = str(error or "")[:200]
            self.cache_clear_status = status
            self._persist(force=True)

    def end_recovery(self, *, failed: bool = False, reason: str = "") -> None:
        with self._lock:
            self.recovery_in_progress = False
            self.active_recovery_package = ""
            self.recovery_stage = "recovery_failed" if failed else ""
            self.recovery_stage_started_at = None
            self.recovery_stage_deadline_s = None
            if failed and reason:
                self.checker_dead_reason = ""  # process is alive; this is a stage failure
                self.last_dead_reason = reason
            self._persist(force=True)

    def resume_checking(self) -> None:
        with self._lock:
            self.checker_mode = MODE_RESUME_CHECKING
            self.state_pointer_text = POINTER_RESUME_CHECKING
            self._persist(force=True)

    # ── per-package no-heartbeat counter ──────────────────────────────
    def increment_no_heartbeat(self, package: str) -> int:
        with self._lock:
            row = self._pkg(package)
            row.consecutive_no_heartbeat_focus_count += 1
            return row.consecutive_no_heartbeat_focus_count

    def reset_no_heartbeat(self, package: str) -> None:
        with self._lock:
            self._pkg(package).consecutive_no_heartbeat_focus_count = 0

    def get_no_heartbeat(self, package: str) -> int:
        with self._lock:
            return self._pkg(package).consecutive_no_heartbeat_focus_count

    def commit_presence_state(self, package: str, state: str) -> None:
        """THE single writer of a package's visible presence state.

        Only the focused checker relay calls this.  Committing clears the
        transient ``Checking`` marker and the raw-evidence-pending flags for
        that package (the evidence has now been consumed by the relay).
        """
        with self._lock:
            row = self._pkg(package)
            row.committed_presence_state = state
            row.last_real_state = state
            row.display_state = state
            row.raw_online_evidence_pending = False
            row.raw_dead_evidence_pending = False
            self._persist(force=True)

    # Backwards-compatible alias: the resolved real state IS the committed
    # presence state under the single-relay model.
    def set_real_state(self, package: str, state: str) -> None:
        self.commit_presence_state(package, state)

    def committed_presence_state(self, package: str) -> str:
        with self._lock:
            row = self._packages.get(package)
            return "" if row is None else row.committed_presence_state

    def cache_online_evidence_pending(self, package: str, pending: bool = True) -> None:
        """Raw producer hook: mark that Online evidence exists but is NOT yet
        committed (only the relay may commit it)."""
        with self._lock:
            self._pkg(package).raw_online_evidence_pending = bool(pending)
            self._persist()

    def cache_dead_evidence_pending(self, package: str, pending: bool = True) -> None:
        """Raw producer hook: queue Dead/force-stop evidence for a package that
        is not currently focused; applied when the relay focuses it."""
        with self._lock:
            self._pkg(package).raw_dead_evidence_pending = bool(pending)
            self._persist()

    def has_pending_dead(self, package: str) -> bool:
        with self._lock:
            row = self._packages.get(package)
            return bool(row and row.raw_dead_evidence_pending)

    def display_state(self, package: str) -> str:
        """Return what the row should show now (Checking while focused)."""
        with self._lock:
            row = self._packages.get(package)
            if row is None:
                return ""
            return row.display_state or row.last_real_state

    def set_online_evidence(self, package: str, source: str, age_ms: float | None) -> None:
        with self._lock:
            row = self._pkg(package)
            row.last_online_evidence_source = source
            row.last_online_evidence_age_ms = age_ms

    def set_pid(self, package: str, pid: str, *, missing_since: float | None) -> None:
        with self._lock:
            row = self._pkg(package)
            row.last_pid = pid
            row.pid_missing_since = missing_since

    def set_loop_health(
        self,
        *,
        checker_loop_alive: bool | None = None,
        logcat_reader_alive: bool | None = None,
        duplicate_loop_guard_status: str | None = None,
    ) -> None:
        with self._lock:
            if checker_loop_alive is not None:
                self.checker_loop_alive = checker_loop_alive
            if logcat_reader_alive is not None:
                self.logcat_reader_alive = logcat_reader_alive
            if duplicate_loop_guard_status is not None:
                self.duplicate_loop_guard_status = duplicate_loop_guard_status
            self._persist(force=True)

    # ── persistence (Start/watchdog process only) ─────────────────────
    def enable_persistence(self) -> None:
        with self._lock:
            self._persist_enabled = True
        self.write_state_file(force=True)

    def _record_transition_locked(self) -> None:
        """Track mode/pointer transitions for the probe (called under lock)."""
        mode = self.checker_mode
        ptr = self.state_pointer_text
        if mode != self._last_history_mode:
            ts = round(time.time(), 3)
            self.last_state_transition = f"{self._last_history_mode or 'none'}->{mode}@{ts}"
            self._last_history_mode = mode
        if ptr and ptr != self._last_history_pointer:
            self._last_history_pointer = ptr
            self.last_ui_pointer_history.append(ptr)
            # Keep only the most recent 16 pointer labels.
            if len(self.last_ui_pointer_history) > 16:
                self.last_ui_pointer_history = self.last_ui_pointer_history[-16:]

    def _persist(self, *, force: bool = False) -> None:
        """Throttled disk mirror; called while ``self._lock`` is held."""
        self._record_transition_locked()
        if not self._persist_enabled:
            return
        now = time.time()
        if not force and (now - self._last_persist_at) < _PERSIST_MIN_INTERVAL_S:
            return
        self._last_persist_at = now
        self._write_state_file_locked()

    def write_state_file(self, *, force: bool = False) -> None:
        with self._lock:
            if force:
                self._last_persist_at = time.time()
            self._write_state_file_locked()

    def _write_state_file_locked(self) -> None:
        path = _state_file_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "written_at": time.time(),
                "pid": os.getpid(),
                "snapshot": self.probe_snapshot(),
            }
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001 — persistence must never crash a caller
            pass

    # ── readers (any thread) ──────────────────────────────────────────
    def pointer_text(self) -> str:
        with self._lock:
            return self.state_pointer_text

    def focus_elapsed_s(self, *, now: float | None = None) -> float | None:
        with self._lock:
            if self.focus_started_at is None:
                return None
            return max(0.0, (now if now is not None else time.time()) - self.focus_started_at)

    def probe_snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        now = now if now is not None else time.time()
        with self._lock:
            focus_elapsed = (
                None
                if self.focus_started_at is None
                else round(max(0.0, now - self.focus_started_at), 2)
            )
            per_package = {
                pkg: {
                    "consecutive_no_heartbeat_focus_count": row.consecutive_no_heartbeat_focus_count,
                    "last_online_evidence_source": row.last_online_evidence_source or None,
                    "last_online_evidence_age_ms": row.last_online_evidence_age_ms,
                    "last_pid": row.last_pid or None,
                    "pid_missing_since": row.pid_missing_since,
                    "display_state": row.display_state or None,
                    "last_real_state": row.last_real_state or None,
                    "last_committed_presence_state": row.committed_presence_state or None,
                    "raw_online_evidence_pending": row.raw_online_evidence_pending,
                    "raw_dead_evidence_pending": row.raw_dead_evidence_pending,
                }
                for pkg, row in self._packages.items()
            }
            return {
                "checker_mode": self.checker_mode,
                "state_pointer_text": self.state_pointer_text,
                "active_focus_package": self.active_focus_package or None,
                "active_focus_index": self.active_focus_index,
                "focus_started_at": self.focus_started_at,
                "focus_elapsed_s": focus_elapsed,
                "focus_window_s": self.focus_window_s,
                "first_launch_phase": self.first_launch_phase or None,
                "first_launch_next_package_at": self.first_launch_next_package_at,
                "first_launch_started_packages": list(self.first_launch_started_packages),
                "first_launch_supposedly_launched_packages": list(
                    self.first_launch_supposedly_launched_packages
                ),
                "recovery_in_progress": self.recovery_in_progress,
                "active_recovery_package": self.active_recovery_package or None,
                "recovery_stage": self.recovery_stage or None,
                "recovery_stage_started_at": self.recovery_stage_started_at,
                "recovery_stage_elapsed_s": (
                    None
                    if self.recovery_stage_started_at is None
                    else round(max(0.0, time.time() - self.recovery_stage_started_at), 2)
                ),
                "recovery_stage_deadline_s": self.recovery_stage_deadline_s,
                "last_dead_reason": self.last_dead_reason or None,
                "last_dead_source": self.last_dead_source or None,
                "last_dead_evidence": self.last_dead_evidence or None,
                "logcat_reader_alive": self.logcat_reader_alive,
                "checker_loop_alive": self.checker_loop_alive,
                "duplicate_loop_guard_status": self.duplicate_loop_guard_status,
                # ── Session / liveness / heartbeat ────────────────────
                "session_id": self.session_id or None,
                "checker_pid": self.checker_pid,
                "start_pressed_at": self.start_pressed_at,
                "checker_last_heartbeat_at": self.checker_last_heartbeat_at,
                "checker_dead_reason": self.checker_dead_reason or None,
                # ── Cache-clear result (bounded recovery) ─────────────
                "cache_clear_started_at": self.cache_clear_started_at,
                "cache_clear_finished_at": self.cache_clear_finished_at,
                "cache_clear_duration_ms": self.cache_clear_duration_ms,
                "cache_clear_command_kind": self.cache_clear_command_kind or None,
                "cache_clear_exit_code": self.cache_clear_exit_code,
                "cache_clear_timed_out": self.cache_clear_timed_out,
                "cache_clear_error": self.cache_clear_error or None,
                "cache_clear_status": self.cache_clear_status or None,
                # ── Transition / pointer history ──────────────────────
                "last_state_transition": self.last_state_transition or None,
                "last_ui_pointer_history": list(self.last_ui_pointer_history),
                # ── Single-relay architecture proof ───────────────────
                "valid_state_writer": "focused_checker_only",
                "non_focused_evidence_cached": True,
                "first_launch_interval_s": self.first_launch_interval_s,
                "last_launch_interval_s": self.last_launch_interval_s,
                "launch_waiting_for_online": self.launch_waiting_for_online,
                "launch_blocked_reason": self.launch_blocked_reason or None,
                "per_package": per_package,
            }


_INSTANCE: CheckerPointerState | None = None
_INSTANCE_LOCK = threading.Lock()


def get() -> CheckerPointerState:
    """Return the process-wide checker pointer state (creating it once)."""
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = CheckerPointerState()
    return _INSTANCE


def _read_state_file_raw() -> tuple[dict[str, Any], float, Any] | None:
    """Return ``(snapshot, age_s, pid)`` from the state file regardless of age."""
    path = _state_file_path()
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    written_at = data.get("written_at")
    try:
        age = time.time() - float(written_at)
    except (TypeError, ValueError):
        return None
    snap = data.get("snapshot")
    if not isinstance(snap, dict):
        return None
    return dict(snap), age, data.get("pid")


def read_state_file(*, max_age_s: float = _STATE_MAX_AGE_S) -> dict[str, Any] | None:
    """Read the persisted checker snapshot if it is fresh, else ``None``.

    Used by the dev-probe process (which does not run the checker) so it can
    reflect the real lifecycle owned by the live Start/watchdog process.
    """
    raw = _read_state_file_raw()
    if raw is None:
        return None
    snap, age, pid = raw
    if age > max_age_s:
        return None
    snap["_source"] = "state_file"
    snap["_state_file_age_s"] = round(age, 2)
    snap["_state_file_pid"] = pid
    snap["checker_state_file_age_s"] = round(age, 2)
    return snap


def probe_snapshot() -> dict[str, Any]:
    """Probe entry point: serialise the global checker pointer state.

    Prefers the live in-process singleton when it is actually running; when
    idle (the common case in a separate probe process) it falls back to the
    persisted state file.  A STALE state file means the Start/watchdog process
    hung or died — surface that as a dead checker (with the last known session /
    pid / stage) instead of a misleading ``idle``.
    """
    live = get().probe_snapshot()
    if live.get("checker_loop_alive") or live.get("checker_mode") != MODE_IDLE:
        live["_source"] = "live"
        live["checker_state_file_age_s"] = 0.0
        return live
    fresh = read_state_file()
    if fresh is not None:
        return fresh
    raw = _read_state_file_raw()
    if raw is not None:
        snap, age, pid = raw
        # File exists but is stale → the checker is not heartbeating.
        snap["_source"] = "state_file_stale"
        snap["_state_file_age_s"] = round(age, 2)
        snap["_state_file_pid"] = pid
        snap["checker_state_file_age_s"] = round(age, 2)
        snap["checker_loop_alive"] = False
        snap["checker_dead_reason"] = (
            f"state_file_stale_{round(age, 1)}s_no_heartbeat"
        )
        return snap
    live["_source"] = "live_idle"
    live["checker_state_file_age_s"] = None
    return live
