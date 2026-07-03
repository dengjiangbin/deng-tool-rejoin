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
_STATE_MAX_AGE_S = 10.0
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
POINTER_CHECKING = "Monitoring.."
POINTER_MONITORING = POINTER_CHECKING
POINTER_DEAD_DETECTED = "Dead Detected"
POINTER_START_RECOVERY = "Start Recovery"
POINTER_CLEARING_CACHE = "Clearing Cache.."
POINTER_REOPENING = "Reopening"
POINTER_RELAUNCHING = "Relaunching"
POINTER_ONLINE = "Online"
POINTER_RESUME_CHECKING = "Resume Monitoring.."
POINTER_RESUME_MONITORING = POINTER_RESUME_CHECKING
POINTER_RESUME_CHECKER = "Resume Monitoring"

_STATIC_MONITORING_LABELS = frozenset(
    {POINTER_CHECKING, "Monitoring..", "Checking..", POINTER_MONITORING}
)


def _is_monitoring_timer_text(text: str) -> bool:
    t = str(text or "").strip()
    if t.startswith("Monitoring ") and t.endswith("s"):
        suffix = t[len("Monitoring ") : -1].strip()
        if suffix.isdigit():
            return True
        if "/" in suffix:
            parts = suffix.split("/", 1)
            return len(parts) == 2 and parts[0].isdigit() and parts[1].endswith("s")
    return (
        (t.startswith("Monitoring ") or t.startswith("Checking "))
        and "/" in t
        and t.endswith("s")
    )


def _format_monitoring_timer(elapsed_ms: float, deadline_ms: float) -> str:
    cap = max(1, int((deadline_ms or 7000.0) / 1000.0))
    shown = min(cap, int((elapsed_ms or 0.0) / 1000.0))
    return f"Monitoring {shown}s"


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
    # ── Lifecycle / recovery tracking (probe + self-heal) ─────────────
    launch_requested_at: float | None = None
    launch_dispatched_at: float | None = None
    waiting_entered_at: float | None = None
    waiting_reason: str = ""
    last_state_transition_reason: str = ""
    dead_detected_at: float | None = None
    process_dead_detected_at: float | None = None
    logcat_dead_detected_at: float | None = None
    ocr_dead_detected_at: float | None = None
    online_evidence_at: float | None = None
    checking_committed_state_at: float | None = None
    detection_latency_ms: float | None = None
    recovery_requested_at: float | None = None
    recovery_started_at: float | None = None
    recovery_finished_at: float | None = None
    recovery_attempt: int = 0
    recovery_last_error: str = ""


@dataclass
class CheckerPointerState:
    """Thread-safe holder for the global checker pointer + per-package data."""

    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    checker_mode: str = MODE_IDLE
    state_pointer_text: str = ""

    active_focus_package: str = ""
    active_focus_index: int = 0
    focus_started_at: float | None = None
    focus_window_s: float = 7.0

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
    getting_ready_at: float | None = None            # wall-clock (time.time)
    checking_system_started_at: float | None = None  # wall-clock (time.time)
    monitoring_started_at: float | None = None
    lifecycle_blocker: str = ""
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

    # ── Bounded checking (7s cap) ─────────────────────────────────────
    checking_active_package: str = ""
    checking_package_started_at: float | None = None
    checking_elapsed_ms: float | None = None
    checking_deadline_ms: float = 7000.0
    checking_over_deadline: bool = False
    checking_timeout_action: str = ""
    checking_last_decision_package: str = ""
    checking_last_decision_state: str = ""
    checking_last_decision_reason: str = ""

    # ── Presence ownership proof ──────────────────────────────────────
    presence_state_writer: str = "checking_system"
    invalid_presence_write_attempts: int = 0

    # ── Recovery queue / handoff ────────────────────────────────────────
    recovery_queue: list[str] = field(default_factory=list)
    recovery_pause_checking: bool = False
    recovery_resume_package: str = ""
    recovery_last_result: str = ""
    recovery_last_error: str = ""
    unrecoverable_dead_packages: dict[str, str] = field(default_factory=dict)
    dead_without_recovery_queue: list[str] = field(default_factory=list)

    # ── Checker / logcat watchdog ───────────────────────────────────────
    checker_watchdog_alive: bool = False
    checker_restart_count: int = 0
    checker_last_restart_at: float | None = None
    checker_stale_detected_at: float | None = None
    checker_stale_age_s: float | None = None
    checker_restart_reason: str = ""
    logcat_restart_count: int = 0
    logcat_last_restart_at: float | None = None
    logcat_unavailable_fallback_active: bool = False

    checker_status: str = "idle"
    checker_idle_reason: str = ""
    checker_paused_reason: str = ""

    header_action_source: str = ""
    header_action_label: str = ""
    last_state_transition_reason: str = ""

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
            self.getting_ready_at = None
            self.checking_system_started_at = None
            self.monitoring_started_at = None
            self.lifecycle_blocker = ""
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
            self.checking_active_package = ""
            self.checking_package_started_at = None
            self.checking_elapsed_ms = None
            self.checking_deadline_ms = 7000.0
            self.checking_over_deadline = False
            self.checking_timeout_action = ""
            self.checking_last_decision_package = ""
            self.checking_last_decision_state = ""
            self.checking_last_decision_reason = ""
            self.presence_state_writer = "checking_system"
            self.invalid_presence_write_attempts = 0
            self.recovery_queue = []
            self.recovery_pause_checking = False
            self.recovery_resume_package = ""
            self.recovery_last_result = ""
            self.recovery_last_error = ""
            self.unrecoverable_dead_packages = {}
            self.dead_without_recovery_queue = []
            self.checker_watchdog_alive = False
            self.checker_restart_count = 0
            self.checker_last_restart_at = None
            self.checker_stale_detected_at = None
            self.checker_stale_age_s = None
            self.checker_restart_reason = ""
            self.logcat_restart_count = 0
            self.logcat_last_restart_at = None
            self.logcat_unavailable_fallback_active = False
            self.checker_status = "starting"
            self.checker_idle_reason = ""
            self.checker_paused_reason = ""
            self.header_action_source = ""
            self.header_action_label = ""
            self.last_state_transition_reason = ""
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
            self.getting_ready_at = time.time()
            self.first_launch_started_packages = []
            self.first_launch_supposedly_launched_packages = []
            self.launch_waiting_for_online = False
            self.launch_blocked_reason = ""
            self.lifecycle_blocker = ""
            if interval_s is not None:
                self.first_launch_interval_s = float(interval_s)
            for pkg in packages:
                self._pkg(pkg)
            self._refresh_header_action_locked()
            self._persist(force=True)

    def begin_preparing(self, packages: list[str]) -> None:
        """Header/table enter Preparing immediately after Start is pressed."""
        with self._lock:
            self.checker_mode = MODE_GETTING_READY
            self.state_pointer_text = "Preparing.."
            self.first_launch_phase = "preparing"
            self.getting_ready_at = None
            self.launch_waiting_for_online = False
            self.launch_blocked_reason = ""
            self.lifecycle_blocker = ""
            for pkg in packages:
                self._pkg(pkg)
            self.header_action_label = "Preparing.."
            self.header_action_source = "start_preparing"
            self._persist(force=True)

    def mirror_clear_cache_phase(self) -> None:
        """Display-only mirror of Start clear-cache phase."""
        with self._lock:
            self.state_pointer_text = "Clear Cache.."
            self.header_action_label = "Clear Cache.."
            self.header_action_source = "start_clear_cache"
            self._persist(force=True)

    def mirror_start_launch_phase(
        self,
        package: str = "",
        *,
        next_package_at: float | None = None,
        reason: str = "",
    ) -> None:
        """Display-only mirror of Start launch phase — never gates lifecycle."""
        with self._lock:
            self.checker_status = "launching"
            self.checker_idle_reason = ""
            self.checker_loop_alive = True
            self.checker_dead_reason = ""
            if package:
                self.checker_mode = MODE_FIRST_LAUNCHING
                self.state_pointer_text = POINTER_OPENING
                self.header_action_label = POINTER_OPENING
                self.header_action_source = "start_launch"
                self.first_launch_phase = "first_launching"
                self.first_launch_next_package_at = next_package_at
                if package not in self.first_launch_started_packages:
                    self.first_launch_started_packages.append(package)
            elif reason:
                self.last_state_transition_reason = str(reason)[:200]
            self._persist(force=True)

    def set_checker_idle_during_first_launch(self, *, reason: str = "") -> None:
        """Deprecated alias — display-only; Start owns lifecycle, not checker."""
        self.mirror_start_launch_phase("", reason=reason)

    def mark_checking_system_started(self) -> None:
        with self._lock:
            now = time.time()
            if self.checking_system_started_at is None:
                self.checking_system_started_at = now
            if self.monitoring_started_at is None:
                self.monitoring_started_at = now
            self.checker_mode = MODE_CHECKING
            self.checker_status = "monitoring"
            self.checker_idle_reason = ""
            self._refresh_header_action_locked()
            self._persist(force=True)

    def mark_monitoring_started(self) -> None:
        """User-facing alias for the monitoring phase after all launches dispatch."""
        self.mark_checking_system_started()

    def set_lifecycle_blocker(self, reason: str) -> None:
        with self._lock:
            self.lifecycle_blocker = str(reason or "")[:200]
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
        deadline = float(window_s if window_s is not None else self.focus_window_s)
        self.begin_checking_package(package, index, now=now, deadline_s=deadline)

    def begin_checking_package(
        self,
        package: str,
        index: int,
        *,
        now: float,
        deadline_s: float | None = None,
    ) -> None:
        with self._lock:
            deadline = float(deadline_s if deadline_s is not None else self.focus_window_s)
            self.focus_window_s = deadline
            self.checking_deadline_ms = round(deadline * 1000.0, 1)
            self.checker_mode = MODE_CHECKING
            self.active_focus_package = package
            self.checking_active_package = package
            self.active_focus_index = index
            self.focus_started_at = now
            self.checking_package_started_at = now
            self.checking_elapsed_ms = 0.0
            self.checking_over_deadline = False
            self.checking_timeout_action = ""
            timer = _format_monitoring_timer(0.0, self.checking_deadline_ms)
            self.state_pointer_text = timer
            self.header_action_label = timer
            self.header_action_source = "checking_active"
            for pkg, row in self._packages.items():
                if row.display_state in ("Checking", "Monitoring") and pkg != package:
                    row.display_state = row.committed_presence_state or row.last_real_state
            self._pkg(package).display_state = "Checking"
            self._persist(force=True)

    def update_focus_timer(self, elapsed_s: float) -> None:
        """Legacy alias — publishes ``Checking N/Ns`` timer text."""
        self.update_checking_timer(elapsed_s)

    def update_checking_timer(
        self, elapsed_s: float, *, deadline_s: float | None = None
    ) -> None:
        with self._lock:
            deadline = float(deadline_s if deadline_s is not None else self.focus_window_s)
            secs = max(0, int(elapsed_s))
            cap = max(1, int(deadline))
            shown = min(secs, cap)
            timer = f"Monitoring {shown}s"
            self.state_pointer_text = timer
            self.header_action_label = timer
            self.header_action_source = "checking_active"
            self.checking_elapsed_ms = round(max(0.0, elapsed_s) * 1000.0, 1)
            self.checking_over_deadline = elapsed_s >= deadline
            self._persist(force=True)

    def finish_checking_decision(
        self,
        package: str,
        state: str,
        reason: str,
        *,
        timeout_action: str = "",
    ) -> None:
        with self._lock:
            self.checking_last_decision_package = package
            self.checking_last_decision_state = state
            self.checking_last_decision_reason = str(reason or "")[:200]
            self.checking_timeout_action = str(timeout_action or "")[:120]
            self.presence_state_writer = "checking_system"
            row = self._pkg(package)
            row.committed_presence_state = state
            row.last_real_state = state
            row.display_state = state
            row.raw_online_evidence_pending = False
            row.raw_dead_evidence_pending = False
            self._persist(force=True)

    def end_checking_focus(self, package: str) -> None:
        with self._lock:
            if self.active_focus_package == package:
                self.active_focus_package = ""
            if self.checking_active_package == package:
                self.checking_active_package = ""
            row = self._packages.get(package)
            if row is not None and row.display_state in ("Checking", "Monitoring"):
                if row.committed_presence_state:
                    row.display_state = row.committed_presence_state
                elif row.launch_dispatched_at is not None:
                    row.display_state = "Waiting"
                else:
                    row.display_state = row.last_real_state or "Waiting"
            self._refresh_header_action_locked()
            self._persist(force=True)

    def mark_dead_detected(self, package: str, reason: str, source: str, evidence: str) -> None:
        with self._lock:
            self.checker_mode = MODE_DEAD_DETECTED
            self.state_pointer_text = POINTER_DEAD_DETECTED
            self.last_dead_reason = reason
            self.last_dead_source = source
            self.last_dead_evidence = evidence
            if package:
                row = self._pkg(package)
                if row.dead_detected_at is None:
                    row.dead_detected_at = time.time()
                self.commit_presence_state(package, "Dead")
                self.enqueue_recovery(package, reason=reason, persist=False)
            self._refresh_header_action_locked()
            self._persist(force=True)

    def enqueue_recovery(
        self, package: str, *, reason: str = "", persist: bool = True
    ) -> None:
        pkg = str(package or "").strip()
        if not pkg:
            return
        with self._lock:
            if pkg in self.unrecoverable_dead_packages:
                return
            row = self._pkg(pkg)
            if row.recovery_requested_at is None:
                row.recovery_requested_at = time.time()
            if reason:
                row.last_state_transition_reason = str(reason)[:200]
            if pkg not in self.recovery_queue and self.active_recovery_package != pkg:
                self.recovery_queue.append(pkg)
            if reason:
                self.last_dead_reason = str(reason)[:200]
            self._recompute_dead_without_recovery_locked()
            if persist:
                self._refresh_header_action_locked()
                self._persist(force=True)
        try:
            from .lime_detection_speed import get_active_lime_tracker

            lime = get_active_lime_tracker()
            if lime is not None:
                lime.note_recovery_requested(pkg)
        except Exception:  # noqa: BLE001
            pass

    def dequeue_recovery(self) -> str:
        with self._lock:
            while self.recovery_queue:
                pkg = self.recovery_queue.pop(0)
                if pkg in self.unrecoverable_dead_packages:
                    continue
                return pkg
            return ""

    @property
    def recovery_queue_size(self) -> int:
        with self._lock:
            return len(self.recovery_queue)

    def is_unrecoverable(self, package: str) -> bool:
        with self._lock:
            return str(package or "").strip() in self.unrecoverable_dead_packages

    def mark_unrecoverable(self, package: str, reason: str) -> None:
        pkg = str(package or "").strip()
        if not pkg:
            return
        with self._lock:
            self.unrecoverable_dead_packages[pkg] = str(reason or "unrecoverable")[:200]
            self.recovery_queue = [p for p in self.recovery_queue if p != pkg]
            self._recompute_dead_without_recovery_locked()
            self._persist(force=True)

    def sync_dead_packages_into_recovery_queue(self) -> None:
        with self._lock:
            for pkg, row in self._packages.items():
                if row.committed_presence_state != "Dead":
                    continue
                if pkg in self.unrecoverable_dead_packages:
                    continue
                if self.recovery_in_progress and self.active_recovery_package == pkg:
                    continue
                if pkg not in self.recovery_queue:
                    self.recovery_queue.append(pkg)
            self._recompute_dead_without_recovery_locked()
            self._persist(force=True)

    def _recompute_dead_without_recovery_locked(self) -> None:
        missing: list[str] = []
        for pkg, row in self._packages.items():
            if row.committed_presence_state != "Dead":
                continue
            if pkg in self.unrecoverable_dead_packages:
                continue
            if self.recovery_in_progress and self.active_recovery_package == pkg:
                continue
            if pkg in self.recovery_queue:
                continue
            missing.append(pkg)
        self.dead_without_recovery_queue = missing

    def record_invalid_presence_write(
        self, *, source: str, package: str, attempted_state: str
    ) -> None:
        with self._lock:
            self.invalid_presence_write_attempts += 1
            self.lifecycle_blocker = (
                f"invalid_presence_write:{source}:{package}:{attempted_state}"[:200]
            )
            self._persist(force=True)

    def record_checker_restart(self, reason: str) -> None:
        with self._lock:
            self.checker_restart_count += 1
            self.checker_last_restart_at = time.time()
            self.checker_restart_reason = str(reason or "")[:200]
            self.checker_loop_alive = True
            self.checker_watchdog_alive = True
            self._persist(force=True)

    def record_logcat_restart(self) -> None:
        with self._lock:
            self.logcat_restart_count += 1
            self.logcat_last_restart_at = time.time()
            self._persist(force=True)

    def set_recovery_pointer(self, package: str, *, stage: str = "") -> None:
        with self._lock:
            label = str(stage or self.recovery_stage or "recovery").strip()
            self.state_pointer_text = f"Recovery {package} {label}"[:120]
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
            row = self._pkg(package)
            row.recovery_attempt += 1
            if row.recovery_started_at is None:
                row.recovery_started_at = time.time()
            if row.recovery_requested_at is None:
                row.recovery_requested_at = time.time()
            self.checker_mode = MODE_DEAD_DETECTED
            self.state_pointer_text = POINTER_START_RECOVERY
            if reason:
                self.last_dead_reason = reason
            self._refresh_header_action_locked()
            self._persist(force=True)

    def set_recovery_stage(self, stage: str, *, deadline_s: float | None = None) -> None:
        _map = {
            "clear_cache": (MODE_RECOVERY_CLEAR_CACHE, POINTER_CLEARING_CACHE),
            "reopening": (MODE_RECOVERY_REOPENING, POINTER_REOPENING),
            "relaunching": (MODE_RECOVERY_RELAUNCHING, POINTER_RELAUNCHING),
            "wait_online": (MODE_RECOVERY_WAIT_ONLINE, POINTER_RELAUNCHING),
            "online": (MODE_RECOVERY_WAIT_ONLINE, POINTER_ONLINE),
            "recovery_failed": (MODE_DEAD_DETECTED, POINTER_START_RECOVERY),
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
            self.header_action_label = self.state_pointer_text
            self.header_action_source = "recovery_stage"
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

    def end_recovery(self, *, failed: bool = False, reason: str = "", resume: bool = True) -> None:
        with self._lock:
            failed_pkg = self.active_recovery_package
            self.recovery_in_progress = False
            self.active_recovery_package = ""
            self.recovery_stage = "recovery_failed" if failed else ""
            self.recovery_stage_started_at = None
            self.recovery_stage_deadline_s = None
            self.recovery_last_result = "failed" if failed else "success"
            self.recovery_last_error = str(reason or "")[:200] if failed else ""
            if failed_pkg:
                row = self._pkg(failed_pkg)
                row.recovery_finished_at = time.time()
                if failed and reason:
                    row.recovery_last_error = str(reason)[:200]
                elif not failed:
                    row.recovery_last_error = ""
                    row.committed_presence_state = ""
                    row.display_state = "Waiting"
                    row.waiting_entered_at = time.time()
                    row.waiting_reason = "recovery_handoff_waiting_for_checker"
                    row.last_state_transition_reason = row.waiting_reason
                    self.recovery_queue = [
                        p for p in self.recovery_queue if p != failed_pkg
                    ]
                self.end_checking_focus(failed_pkg)
            if failed and reason:
                self.last_dead_reason = str(reason)[:200]
            if resume:
                self.resume_checking_if_safe()
            else:
                self._refresh_header_action_locked()
            self._recompute_dead_without_recovery_locked()
            self._persist(force=True)

    def _count_unrecovered_dead_locked(self) -> int:
        count = 0
        for pkg, row in self._packages.items():
            if row.committed_presence_state != "Dead":
                continue
            if pkg in self.unrecoverable_dead_packages:
                continue
            if row.recovery_finished_at is not None and not row.recovery_last_error:
                continue
            count += 1
        return count

    def _refresh_header_action_locked(self) -> tuple[str, str]:
        """Derive header label from recovery/dead state — never stale Resume alone."""
        if self.recovery_in_progress and self.active_recovery_package:
            stage = str(self.recovery_stage or "recovery").strip()
            if stage == "start":
                label = POINTER_START_RECOVERY
            else:
                label = f"Recovery {self.active_recovery_package} {stage}"[:120]
            self.header_action_label = label
            self.header_action_source = "recovery_in_progress"
            self.state_pointer_text = label
            return label, self.header_action_source

        unrecovered = self._count_unrecovered_dead_locked()
        pending = len(self.recovery_queue)
        running = 1 if self.recovery_in_progress else 0

        if pending > 0 or self.dead_without_recovery_queue or unrecovered > 0:
            if pending > 0 or self.dead_without_recovery_queue:
                label = POINTER_START_RECOVERY
                source = "recovery_pending"
            else:
                label = POINTER_DEAD_DETECTED
                source = "unrecovered_dead"
            self.header_action_label = label
            self.header_action_source = source
            self.state_pointer_text = label
            self.checker_mode = MODE_DEAD_DETECTED
            self.checker_paused_reason = source
            return label, source

        if self.recovery_pause_checking:
            label = POINTER_RESUME_CHECKING
            source = "checker_paused_no_unrecovered_dead"
            self.header_action_label = label
            self.header_action_source = source
            self.state_pointer_text = label
            self.checker_mode = MODE_RESUME_CHECKING
            self.checker_paused_reason = source
            return label, source

        if self.checker_mode == MODE_CHECKING or self.checking_active_package:
            if self.checking_active_package:
                timer = str(self.state_pointer_text or "").strip()
                if not _is_monitoring_timer_text(timer):
                    timer = _format_monitoring_timer(
                        self.checking_elapsed_ms, self.checking_deadline_ms
                    )
                label = timer
                source = "checking_active"
                self.header_action_label = label
                self.header_action_source = source
                self.state_pointer_text = label
                return label, source
            label = ""
            source = "checking_idle"
            self.header_action_label = label
            self.header_action_source = source
            return label, source

        label = str(self.state_pointer_text or "").strip()
        if label in _STATIC_MONITORING_LABELS:
            label = ""
        source = "default"
        self.header_action_label = label
        self.header_action_source = source
        return label, source

    def resume_checking_if_safe(self) -> bool:
        """Resume checking when no unrecovered Dead packages remain.

        Automated recovery must never leave a stale ``Resume Monitoring..``
        label blocking the round-robin (probe p-fe3653d07a): when it is safe to
        resume, flip straight back to ``MODE_CHECKING``.
        """
        with self._lock:
            self.recovery_pause_checking = False
            safe = (
                not self.recovery_in_progress
                and not self.recovery_queue
                and not self.dead_without_recovery_queue
                and self._count_unrecovered_dead_locked() == 0
            )
            if safe:
                self.checker_mode = MODE_CHECKING
                self.checker_paused_reason = ""
                self._refresh_header_action_locked()
                self._persist(force=True)
                return True
            self._refresh_header_action_locked()
            self._persist(force=True)
            return False

    def resume_checking(self) -> None:
        self.resume_checking_if_safe()

    def mark_launch_requested(self, package: str) -> None:
        pkg = str(package or "").strip()
        if not pkg:
            return
        with self._lock:
            row = self._pkg(pkg)
            if row.launch_requested_at is None:
                row.launch_requested_at = time.time()
            row.display_state = "Launching"
            self._persist()

    def mark_launch_command_sent(
        self, package: str, *, reason: str = "launch_command_dispatched"
    ) -> None:
        """Record launch dispatch timestamps while package row stays Launching."""
        pkg = str(package or "").strip()
        if not pkg:
            return
        now = time.time()
        with self._lock:
            row = self._pkg(pkg)
            if row.launch_requested_at is None:
                row.launch_requested_at = now
            if row.launch_dispatched_at is None:
                row.launch_dispatched_at = now
            row.display_state = "Launching"
            row.last_state_transition_reason = str(reason or "")[:200]
            self.last_state_transition = f"{pkg}:launch_command_sent"
            if pkg and pkg not in self.first_launch_started_packages:
                self.first_launch_started_packages.append(pkg)
            self._persist(force=True)

    def mark_launch_dispatched(
        self, package: str, *, reason: str = "launch_dispatched_waiting_for_checker"
    ) -> None:
        pkg = str(package or "").strip()
        if not pkg:
            return
        now = time.time()
        with self._lock:
            row = self._pkg(pkg)
            if row.launch_requested_at is None:
                row.launch_requested_at = now
            row.launch_dispatched_at = now
            row.waiting_entered_at = now
            row.waiting_reason = str(reason or "")[:200]
            row.last_state_transition_reason = row.waiting_reason
            if row.committed_presence_state == "Dead":
                row.display_state = "Dead"
            else:
                row.display_state = "Waiting"
                row.committed_presence_state = ""
            self.last_state_transition_reason = row.waiting_reason
            self._persist(force=True)

    def self_heal_missing_recovery_requests(self) -> list[str]:
        """Dead packages without recovery_requested_at must enqueue recovery."""
        healed: list[str] = []
        with self._lock:
            for pkg, row in self._packages.items():
                if row.committed_presence_state != "Dead":
                    continue
                if pkg in self.unrecoverable_dead_packages:
                    continue
                if row.recovery_requested_at is not None:
                    continue
                if pkg in self.recovery_queue or self.active_recovery_package == pkg:
                    row.recovery_requested_at = time.time()
                    healed.append(pkg)
                    continue
                row.recovery_requested_at = time.time()
                row.last_state_transition_reason = "self_heal_missing_recovery_request"
                self.recovery_queue.append(pkg)
                healed.append(pkg)
            if healed:
                self._recompute_dead_without_recovery_locked()
                self._refresh_header_action_locked()
                self._persist(force=True)
        return healed

    def header_pointer_text(self) -> str:
        with self._lock:
            label, _source = self._refresh_header_action_locked()
            return label

    def recovery_counts(self) -> dict[str, int]:
        with self._lock:
            unrecovered = self._count_unrecovered_dead_locked()
            pending = len(self.recovery_queue)
            running = 1 if self.recovery_in_progress else 0
            return {
                "unrecovered_dead_count": unrecovered,
                "recovery_pending_count": pending,
                "recovery_running_count": running,
            }

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

    def commit_presence_state(
        self, package: str, state: str, *, writer: str = "checking_system"
    ) -> None:
        """THE single writer of a package's visible presence state.

        Only the focused checker relay calls this.  Committing clears the
        transient ``Checking`` marker and the raw-evidence-pending flags for
        that package (the evidence has now been consumed by the relay).
        """
        now = time.time()
        with self._lock:
            self.presence_state_writer = str(writer or "checking_system")[:80]
            row = self._pkg(package)
            row.committed_presence_state = state
            row.last_real_state = state
            row.display_state = state
            row.checking_committed_state_at = now
            row.raw_online_evidence_pending = False
            row.raw_dead_evidence_pending = False
            if state == "Online" and row.online_evidence_at is None:
                row.online_evidence_at = now
            self._persist(force=True)
        try:
            from .lime_detection_speed import get_active_lime_tracker

            lime = get_active_lime_tracker()
            if lime is not None:
                lime.note_checking_committed(package, at=now, state=state)
        except Exception:  # noqa: BLE001
            pass

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
            committed = str(row.committed_presence_state or "").strip()
            if committed == "Dead":
                return "Dead"
            if package == self.checking_active_package:
                if committed in ("Online", "No Heartbeat"):
                    return committed
                return "Checking"
            if committed in ("Online", "No Heartbeat"):
                return committed
            val = str(row.display_state or row.last_real_state or "").strip()
            if val == "Waiting Check":
                return "Waiting"
            if val in ("Monitoring", "Opening"):
                return "Waiting" if val == "Monitoring" else "Launching"
            return val

    def set_online_evidence(self, package: str, source: str, age_ms: float | None) -> None:
        now = time.time()
        with self._lock:
            row = self._pkg(package)
            row.last_online_evidence_source = source
            row.last_online_evidence_age_ms = age_ms
        try:
            from .lime_detection_speed import get_active_lime_tracker

            lime = get_active_lime_tracker()
            if lime is not None:
                lime.note_online_evidence(package, at=now, source=source)
        except Exception:  # noqa: BLE001
            pass

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
                    "state": row.display_state or row.last_real_state or None,
                    "last_real_state": row.last_real_state or None,
                    "last_committed_presence_state": row.committed_presence_state or None,
                    "raw_online_evidence_pending": row.raw_online_evidence_pending,
                    "raw_dead_evidence_pending": row.raw_dead_evidence_pending,
                    "launch_requested_at": row.launch_requested_at,
                    "launch_dispatched_at": row.launch_dispatched_at,
                    "waiting_entered_at": row.waiting_entered_at,
                    "waiting_reason": row.waiting_reason or None,
                    "last_state_transition_reason": row.last_state_transition_reason or None,
                    "dead_detected_at": row.dead_detected_at,
                    "process_dead_detected_at": row.process_dead_detected_at,
                    "logcat_dead_detected_at": row.logcat_dead_detected_at,
                    "ocr_dead_detected_at": row.ocr_dead_detected_at,
                    "online_evidence_at": row.online_evidence_at,
                    "checking_committed_state_at": row.checking_committed_state_at,
                    "detection_latency_ms": row.detection_latency_ms,
                    "recovery_requested_at": row.recovery_requested_at,
                    "recovery_started_at": row.recovery_started_at,
                    "recovery_finished_at": row.recovery_finished_at,
                    "recovery_attempt": row.recovery_attempt,
                    "recovery_last_error": row.recovery_last_error or None,
                }
                for pkg, row in self._packages.items()
            }
            snap = {
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
                "getting_ready_at": self.getting_ready_at,
                "checking_system_started_at": self.checking_system_started_at,
                "monitoring_started_at": self.monitoring_started_at,
                "lifecycle_blocker": self.lifecycle_blocker or None,
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
                "checking_active_package": self.checking_active_package or None,
                "checking_package_started_at": self.checking_package_started_at,
                "checking_elapsed_ms": self.checking_elapsed_ms,
                "checking_deadline_ms": self.checking_deadline_ms,
                "checking_over_deadline": self.checking_over_deadline,
                "checking_timeout_action": self.checking_timeout_action or None,
                "checking_last_decision_package": self.checking_last_decision_package or None,
                "checking_last_decision_state": self.checking_last_decision_state or None,
                "checking_last_decision_reason": self.checking_last_decision_reason or None,
                "presence_state_writer": self.presence_state_writer or None,
                "invalid_presence_write_attempts": self.invalid_presence_write_attempts,
                "recovery_queue": list(self.recovery_queue),
                "recovery_queue_size": len(self.recovery_queue),
                "recovery_pause_checking": self.recovery_pause_checking,
                "recovery_resume_package": self.recovery_resume_package or None,
                "recovery_last_result": self.recovery_last_result or None,
                "recovery_last_error": self.recovery_last_error or None,
                "unrecoverable_dead_packages": dict(self.unrecoverable_dead_packages),
                "dead_without_recovery_queue": list(self.dead_without_recovery_queue),
                "checker_watchdog_alive": self.checker_watchdog_alive,
                "checker_restart_count": self.checker_restart_count,
                "checker_last_restart_at": self.checker_last_restart_at,
                "checker_stale_detected_at": self.checker_stale_detected_at,
                "checker_stale_age_s": self.checker_stale_age_s,
                "checker_restart_reason": self.checker_restart_reason or None,
                "logcat_restart_count": self.logcat_restart_count,
                "logcat_last_restart_at": self.logcat_last_restart_at,
                "logcat_unavailable_fallback_active": self.logcat_unavailable_fallback_active,
                "checker_status": self.checker_status or None,
                "checker_idle_reason": self.checker_idle_reason or None,
                "checker_paused_reason": self.checker_paused_reason or None,
                "monitoring_paused_reason": self.checker_paused_reason or None,
                "header_action_source": self.header_action_source or None,
                "header_action_label": self.header_action_label or None,
                "last_state_transition_reason": self.last_state_transition_reason or None,
                "unrecovered_dead_count": self._count_unrecovered_dead_locked(),
                "recovery_pending_count": len(self.recovery_queue),
                "recovery_running_count": 1 if self.recovery_in_progress else 0,
                "per_package": per_package,
            }
        snap_out = dict(snap)
        try:
            from . import start_lifecycle as _start_lifecycle

            snap_out.update(_start_lifecycle.probe_snapshot())
        except Exception:  # noqa: BLE001
            pass
        return snap_out


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
