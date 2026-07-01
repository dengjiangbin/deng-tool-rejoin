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

import threading
import time
from dataclasses import dataclass, field
from typing import Any

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

    _packages: dict[str, _PackagePointer] = field(default_factory=dict)

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

    def set_mode(self, mode: str, pointer_text: str | None = None) -> None:
        with self._lock:
            self.checker_mode = mode
            if pointer_text is not None:
                self.state_pointer_text = pointer_text

    def set_pointer_text(self, text: str) -> None:
        with self._lock:
            self.state_pointer_text = text

    def begin_getting_ready(self, packages: list[str]) -> None:
        with self._lock:
            self.checker_mode = MODE_GETTING_READY
            self.state_pointer_text = POINTER_GETTING_READY
            self.first_launch_phase = "getting_ready"
            self.first_launch_started_packages = []
            self.first_launch_supposedly_launched_packages = []
            for pkg in packages:
                self._pkg(pkg)

    def begin_opening(self, package: str, *, next_package_at: float | None = None) -> None:
        with self._lock:
            self.checker_mode = MODE_FIRST_LAUNCHING
            self.state_pointer_text = POINTER_OPENING
            self.first_launch_phase = "first_launching"
            self.first_launch_next_package_at = next_package_at
            if package and package not in self.first_launch_started_packages:
                self.first_launch_started_packages.append(package)

    def mark_supposedly_launched(self, package: str) -> None:
        with self._lock:
            if package and package not in self.first_launch_supposedly_launched_packages:
                self.first_launch_supposedly_launched_packages.append(package)

    def begin_focus(self, package: str, index: int, *, now: float, window_s: float | None = None) -> None:
        with self._lock:
            self.checker_mode = MODE_CHECKING
            self.active_focus_package = package
            self.active_focus_index = index
            self.focus_started_at = now
            if window_s is not None:
                self.focus_window_s = float(window_s)
            self.state_pointer_text = POINTER_CHECKING

    def update_focus_timer(self, elapsed_s: float) -> None:
        """Publish the ``1s``..``Ns`` countdown text during a focus window."""
        with self._lock:
            secs = max(0, int(elapsed_s))
            self.state_pointer_text = f"{secs}s"

    def mark_dead_detected(self, package: str, reason: str, source: str, evidence: str) -> None:
        with self._lock:
            self.checker_mode = MODE_DEAD_DETECTED
            self.state_pointer_text = POINTER_DEAD_DETECTED
            self.last_dead_reason = reason
            self.last_dead_source = source
            self.last_dead_evidence = evidence

    def begin_recovery(self, package: str) -> None:
        with self._lock:
            self.recovery_in_progress = True
            self.active_recovery_package = package
            self.recovery_stage = "start"
            self.checker_mode = MODE_DEAD_DETECTED
            self.state_pointer_text = POINTER_START_RECOVERY

    def set_recovery_stage(self, stage: str) -> None:
        _map = {
            "clear_cache": (MODE_RECOVERY_CLEAR_CACHE, POINTER_CLEARING_CACHE),
            "reopening": (MODE_RECOVERY_REOPENING, POINTER_REOPENING),
            "relaunching": (MODE_RECOVERY_RELAUNCHING, POINTER_RELAUNCHING),
            "wait_online": (MODE_RECOVERY_WAIT_ONLINE, POINTER_RELAUNCHING),
            "online": (MODE_RECOVERY_WAIT_ONLINE, POINTER_ONLINE),
        }
        with self._lock:
            self.recovery_stage = stage
            mode_text = _map.get(stage)
            if mode_text is not None:
                self.checker_mode, self.state_pointer_text = mode_text

    def end_recovery(self) -> None:
        with self._lock:
            self.recovery_in_progress = False
            self.active_recovery_package = ""
            self.recovery_stage = ""

    def resume_checking(self) -> None:
        with self._lock:
            self.checker_mode = MODE_RESUME_CHECKING
            self.state_pointer_text = POINTER_RESUME_CHECKING

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
                "last_dead_reason": self.last_dead_reason or None,
                "last_dead_source": self.last_dead_source or None,
                "last_dead_evidence": self.last_dead_evidence or None,
                "logcat_reader_alive": self.logcat_reader_alive,
                "checker_loop_alive": self.checker_loop_alive,
                "duplicate_loop_guard_status": self.duplicate_loop_guard_status,
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


def probe_snapshot() -> dict[str, Any]:
    """Probe entry point: serialise the global checker pointer state."""
    return get().probe_snapshot()
