"""Focused round-robin lifecycle checker (single scheduler / state machine).

This is the one and only scheduler that decides, at any moment, which
package is being watched and whether a recovery is running.  It is written
as a pure state machine with *injected* side-effect callables so the whole
control flow can be unit-tested deterministically with a fake clock and
fake evidence providers, and then wired into the live supervisor.

Design guarantees (see task spec):

* Only ONE package is focused at a time (``focus_window_s`` default 10s).
* Online confirmed before the window ends → advance immediately.
* Dead / force-close / crash confirmed → stop checking, run recovery now.
* ``no_heartbeat_limit`` (7) consecutive completed focus windows with no
  online evidence → run recovery.
* Recovery is exclusive: a second dead package can never interrupt an
  active recovery (``recovery_in_progress`` guard + lock).
* First launch is fixed-interval (default 60s); it never waits for a
  previous package to become Online before launching the next.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from . import checker_pointer

# Focus-window outcomes
OUTCOME_ONLINE_EARLY = "online_early"
OUTCOME_DEAD = "dead"
OUTCOME_NO_HEARTBEAT = "no_heartbeat"
OUTCOME_STOP = "stop"


@dataclass
class OnlineEvidence:
    source: str
    age_ms: float | None = None


@dataclass
class DeadEvidence:
    reason: str
    source: str
    evidence: str = ""


@dataclass
class CheckerDeps:
    """Injected side-effects.  Everything the checker touches goes here."""

    packages: list[str]
    clock: Callable[[], float]
    sleep: Callable[[float], None]
    should_stop: Callable[[], bool]
    launch: Callable[[str], bool]
    online_evidence: Callable[[str], OnlineEvidence | None]
    dead_evidence: Callable[[str], DeadEvidence | None]
    clear_cache: Callable[[str], None]
    pointer: checker_pointer.CheckerPointerState
    on_render: Callable[[], None] | None = None

    first_launch_interval_s: float = 30.0
    focus_window_s: float = 10.0
    focus_poll_s: float = 0.25
    no_heartbeat_limit: int = 7
    recovery_wait_online_s: float = 90.0


class FocusedRoundRobinChecker:
    """Single scheduler driving first-launch, focused checking, and recovery."""

    def __init__(self, deps: CheckerDeps) -> None:
        self.d = deps
        self._recovery_lock = threading.Lock()
        self._first_launch_started = False
        self._checking_started = False

    # ── helpers ───────────────────────────────────────────────────────
    def _render(self) -> None:
        if self.d.on_render is not None:
            try:
                self.d.on_render()
            except Exception:  # noqa: BLE001
                pass

    def _stop(self) -> bool:
        try:
            return bool(self.d.should_stop())
        except Exception:  # noqa: BLE001
            return False

    def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep in small slices so stop is honoured promptly."""
        remaining = float(seconds)
        step = min(0.5, self.d.focus_poll_s if self.d.focus_poll_s > 0 else 0.25)
        while remaining > 0 and not self._stop():
            self.d.sleep(min(step, remaining))
            remaining -= step

    # ── first launch (fixed interval) ─────────────────────────────────
    def run_first_launch(self) -> None:
        """Launch every configured package on a fixed interval.

        Never waits for a package to become Online before launching the
        next one.  Returns once checking is allowed to begin.
        """
        if self._first_launch_started:
            # Duplicate-start guard: a second caller must never spawn a
            # parallel first-launch sequence.
            self.d.pointer.set_loop_health(duplicate_loop_guard_status="first_launch_reentry_blocked")
            return
        self._first_launch_started = True

        pkgs = list(self.d.packages)
        self.d.pointer.begin_getting_ready(pkgs)
        self._render()

        all_launched_ok = True
        last_supposed_at = self.d.clock()
        interval = float(self.d.first_launch_interval_s)

        for i, pkg in enumerate(pkgs):
            if self._stop():
                return
            now = self.d.clock()
            last_supposed_at = now
            self.d.pointer.begin_opening(pkg, next_package_at=now + interval)
            self._render()
            try:
                ok = bool(self.d.launch(pkg))
            except Exception:  # noqa: BLE001
                ok = False
            # We *suppose* it launched the moment we issued the command,
            # even if it crashes right after — that is what drives the
            # "60s after last supposed launch" checking start.
            self.d.pointer.mark_supposedly_launched(pkg)
            self.d.pointer.reset_no_heartbeat(pkg)
            if not ok:
                all_launched_ok = False

            is_last = i == len(pkgs) - 1
            if not is_last:
                # Fixed interval before the next launch — no Online gate.
                self._interruptible_sleep(interval)

        # Decide when checking may begin:
        #   1. all launched normally  → start now, or
        #   2. 60s after the last supposed launch (covers last-package crash).
        if not all_launched_ok:
            deadline = last_supposed_at + interval
            while self.d.clock() < deadline and not self._stop():
                self._interruptible_sleep(min(0.5, max(0.05, deadline - self.d.clock())))

    # ── focused checking ──────────────────────────────────────────────
    def focus_once(self, package: str, index: int) -> str:
        """Focus a single package for up to ``focus_window_s`` seconds.

        Returns one of the ``OUTCOME_*`` constants.  Advances early on
        Online; returns immediately on confirmed Dead.
        """
        now = self.d.clock()
        self.d.pointer.begin_focus(package, index, now=now, window_s=self.d.focus_window_s)
        self._render()

        window = float(self.d.focus_window_s)
        poll = self.d.focus_poll_s if self.d.focus_poll_s > 0 else 0.25
        last_shown_second = -1

        while True:
            if self._stop():
                return OUTCOME_STOP
            elapsed = self.d.clock() - now

            dead = self._dead_evidence(package)
            if dead is not None:
                self.d.pointer.mark_dead_detected(
                    package, dead.reason, dead.source, dead.evidence
                )
                self._render()
                return OUTCOME_DEAD

            online = self._online_evidence(package)
            if online is not None:
                self.d.pointer.set_online_evidence(package, online.source, online.age_ms)
                self.d.pointer.set_pointer_text(checker_pointer.POINTER_ONLINE)
                self._render()
                return OUTCOME_ONLINE_EARLY

            shown = int(elapsed)
            if shown != last_shown_second and shown >= 1:
                last_shown_second = shown
                self.d.pointer.update_focus_timer(min(shown, int(window)))
                self._render()

            if elapsed >= window:
                return OUTCOME_NO_HEARTBEAT

            self.d.sleep(min(poll, max(0.0, window - elapsed)))

    def _online_evidence(self, package: str) -> OnlineEvidence | None:
        try:
            return self.d.online_evidence(package)
        except Exception:  # noqa: BLE001
            return None

    def _dead_evidence(self, package: str) -> DeadEvidence | None:
        try:
            return self.d.dead_evidence(package)
        except Exception:  # noqa: BLE001
            return None

    def run_checking_round(self) -> None:
        """One full round-robin pass over all packages."""
        for index, pkg in enumerate(self.d.packages, start=1):
            if self._stop():
                return
            outcome = self.focus_once(pkg, index)
            if outcome == OUTCOME_STOP:
                return
            if outcome == OUTCOME_ONLINE_EARLY:
                self.d.pointer.reset_no_heartbeat(pkg)
                continue
            if outcome == OUTCOME_DEAD:
                self.run_recovery(pkg)
                continue
            if outcome == OUTCOME_NO_HEARTBEAT:
                count = self.d.pointer.increment_no_heartbeat(pkg)
                if count >= self.d.no_heartbeat_limit:
                    self.run_recovery(pkg)

    def run_checking(self) -> None:
        """Infinite focused round-robin until stop."""
        self.d.pointer.set_loop_health(checker_loop_alive=True)
        try:
            while not self._stop():
                self.run_checking_round()
        finally:
            self.d.pointer.set_loop_health(checker_loop_alive=False)

    # ── recovery (exclusive) ──────────────────────────────────────────
    def run_recovery(self, package: str) -> bool:
        """Clear cache → relaunch → wait for Online, then resume checking.

        Exclusive: a second dead package can never interrupt an active
        recovery.  Returns True if Online was reconfirmed.
        """
        if not self._recovery_lock.acquire(blocking=False):
            # Another recovery is already running — never interrupt it.
            self.d.pointer.set_loop_health(duplicate_loop_guard_status="recovery_busy")
            return False
        try:
            if self.d.pointer.recovery_in_progress:
                return False
            self.d.pointer.begin_recovery(package)
            self.d.pointer.reset_no_heartbeat(package)
            self._render()

            # Clear cache for the target package only.
            self.d.pointer.set_recovery_stage("clear_cache")
            self._render()
            try:
                self.d.clear_cache(package)
            except Exception:  # noqa: BLE001
                pass

            self.d.pointer.set_recovery_stage("reopening")
            self._render()

            self.d.pointer.set_recovery_stage("relaunching")
            self._render()
            try:
                self.d.launch(package)
            except Exception:  # noqa: BLE001
                pass

            self.d.pointer.set_recovery_stage("wait_online")
            self._render()
            online = self._wait_recovery_online(package)
            if online:
                self.d.pointer.set_recovery_stage("online")
                self._render()
            return online
        finally:
            self.d.pointer.end_recovery()
            self.d.pointer.resume_checking()
            self._render()
            self._recovery_lock.release()

    def _wait_recovery_online(self, package: str) -> bool:
        deadline = self.d.clock() + float(self.d.recovery_wait_online_s)
        poll = self.d.focus_poll_s if self.d.focus_poll_s > 0 else 0.25
        while self.d.clock() < deadline and not self._stop():
            online = self._online_evidence(package)
            if online is not None:
                self.d.pointer.set_online_evidence(package, online.source, online.age_ms)
                return True
            self.d.sleep(poll)
        return False
