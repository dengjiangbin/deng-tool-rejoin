"""Strict Checking System state machine — single relay for presence + recovery."""

from __future__ import annotations

import os
import time
from typing import Any, Callable

from . import android
from . import checker_pointer as cp
from . import focused_checker as fc

CHECKING_DEADLINE_S = float(
    os.environ.get("DENG_REJOIN_CHECKING_DEADLINE_SEC", "7") or "7"
)
_STATE_FILE_STALE_S = float(
    os.environ.get("DENG_REJOIN_CHECKER_STALE_SEC", "10") or "10"
)


class CheckingSystem:
    """Orchestrates bounded checking, recovery queue, and liveness probes."""

    def __init__(self, supervisor: Any) -> None:
        self._sup = supervisor

    def _ptr(self) -> cp.CheckerPointerState | None:
        try:
            return cp.get()
        except Exception:  # noqa: BLE001
            return None

    def tick_round_start(self, render_cb: Callable[[], None] | None = None) -> bool:
        ptr = self._ptr()
        if ptr is None:
            return False
        ptr.heartbeat(reason="round_start")
        ptr.checker_watchdog_alive = True
        self._ensure_logcat(ptr)
        self._detect_stale_and_record(ptr)
        self.sync_dead_packages_into_recovery_queue()
        if ptr.recovery_pause_checking or ptr.recovery_in_progress:
            return True
        if ptr.recovery_queue:
            self._process_next_recovery(render_cb)
        return bool(ptr.recovery_pause_checking or ptr.recovery_in_progress)

    def sync_dead_packages_into_recovery_queue(self) -> None:
        ptr = self._ptr()
        if ptr is not None:
            ptr.sync_dead_packages_into_recovery_queue()

    def focus_package(
        self,
        pkg: str,
        entry: dict[str, Any],
        index: int,
        now: float,
        render_cb: Callable[[], None] | None,
    ) -> str:
        ptr = self._ptr()
        sup = self._sup
        if ptr is None:
            return fc.OUTCOME_NO_HEARTBEAT
        if ptr.recovery_pause_checking or ptr.recovery_in_progress:
            return fc.OUTCOME_STOP
        if ptr.is_unrecoverable(pkg):
            ptr.end_checking_focus(pkg)
            return fc.OUTCOME_NO_HEARTBEAT

        deadline_s = CHECKING_DEADLINE_S
        ptr.begin_checking_package(pkg, index, now=now, deadline_s=deadline_s)
        self._render(render_cb)

        start = time.monotonic()
        poll = max(0.1, float(getattr(sup, "FOCUS_POLL_SECONDS", 0.5)))
        last_tick = -1

        while not sup.stop_event.is_set():
            elapsed = time.monotonic() - start
            state_now = sup.status_map.get(pkg, "")
            dead = sup._focused_dead_evidence(pkg, state_now, {})
            if dead is not None:
                ptr.mark_dead_detected(pkg, dead[0], dead[1], dead[2])
                ptr.recovery_pause_checking = True
                self._render(render_cb)
                self._process_next_recovery(render_cb)
                return fc.OUTCOME_DEAD

            online = sup._focused_online_evidence(pkg)
            if online is not None:
                ptr.set_online_evidence(pkg, online[0], online[1])
                ptr.reset_no_heartbeat(pkg)
                ptr.finish_checking_decision(
                    pkg, "Online", "online_evidence", timeout_action=""
                )
                ptr.set_pointer_text(cp.POINTER_ONLINE)
                ptr.end_checking_focus(pkg)
                self._render(render_cb)
                return fc.OUTCOME_ONLINE_EARLY

            shown = int(elapsed)
            if shown != last_tick:
                last_tick = shown
                ptr.update_checking_timer(elapsed, deadline_s=deadline_s)
                self._render(render_cb)

            if elapsed >= deadline_s:
                break
            sup._interruptible_sleep(min(poll, max(0.0, deadline_s - elapsed)))

        action, state, reason = self._timeout_decision(pkg)
        ptr.finish_checking_decision(
            pkg, state, reason, timeout_action=action
        )
        ptr.end_checking_focus(pkg)
        self._render(render_cb)

        if state == "Dead":
            ptr.enqueue_recovery(pkg, reason=reason)
            ptr.recovery_pause_checking = True
            self._process_next_recovery(render_cb)
            return fc.OUTCOME_DEAD

        if state == "No Heartbeat":
            count = ptr.increment_no_heartbeat(pkg)
            limit = int(getattr(sup, "NO_HEARTBEAT_FOCUS_LIMIT", 7))
            if count >= limit:
                ptr.commit_presence_state(pkg, "Dead")
                ptr.enqueue_recovery(pkg, reason="no_heartbeat_limit")
                ptr.recovery_pause_checking = True
                self._process_next_recovery(render_cb)
                return fc.OUTCOME_DEAD
            return fc.OUTCOME_NO_HEARTBEAT

        return fc.OUTCOME_NO_HEARTBEAT

    def handle_focus_dead(
        self,
        pkg: str,
        entry: dict[str, Any],
        now: float,
        render_cb: Callable[[], None] | None,
    ) -> None:
        ptr = self._ptr()
        if ptr is None:
            return
        if ptr.committed_presence_state(pkg) != "Dead":
            ptr.mark_dead_detected(
                pkg,
                ptr.last_dead_reason or "focus_dead",
                ptr.last_dead_source or "focus",
                ptr.last_dead_evidence or "",
            )
        if not ptr.recovery_in_progress and pkg not in ptr.recovery_queue:
            ptr.enqueue_recovery(pkg, reason="focus_dead")
        ptr.recovery_pause_checking = True
        self._process_next_recovery(render_cb)

    def _timeout_decision(self, pkg: str) -> tuple[str, str, str]:
        sup = self._sup
        online = sup._focused_online_evidence(pkg)
        if online is not None:
            return "commit_online", "Online", "timeout_online_evidence"
        process_alive = False
        try:
            if sup._root_info.available:
                process_alive = bool(android.get_package_pid(pkg, sup._root_info))
        except Exception:  # noqa: BLE001
            process_alive = False
        if process_alive:
            return "commit_no_heartbeat", "No Heartbeat", "timeout_process_no_online"
        return "commit_dead", "Dead", "timeout_no_process"

    def _process_next_recovery(
        self, render_cb: Callable[[], None] | None
    ) -> None:
        ptr = self._ptr()
        sup = self._sup
        if ptr is None or ptr.recovery_in_progress:
            return
        pkg = ptr.dequeue_recovery()
        if not pkg:
            ptr.recovery_pause_checking = False
            return
        if ptr.is_unrecoverable(pkg):
            self.sync_dead_packages_into_recovery_queue()
            self._process_next_recovery(render_cb)
            return

        entry = sup.entry_by_pkg.get(pkg, {"package": pkg})
        ptr.recovery_pause_checking = True
        ptr.recovery_resume_package = pkg
        ptr.heartbeat(reason="recovery_start")

        from .supervisor import STATUS_DEAD

        try:
            sup._handle_state(
                pkg,
                entry,
                STATUS_DEAD,
                sup._prev_state.get(pkg, ""),
                time.time(),
                render_callback=render_cb,
                detail={"reason": "checking_system_recovery"},
            )
        except Exception as exc:  # noqa: BLE001
            ptr.mark_unrecoverable(pkg, str(exc)[:200])
            ptr.end_recovery(failed=True, reason=str(exc)[:200], resume=True)
            ptr.recovery_pause_checking = False
            self.sync_dead_packages_into_recovery_queue()
            self._process_next_recovery(render_cb)
            return

        launch_err = ""
        try:
            from .launch_relaunch_trace import probe_snapshot as _lr_probe

            snap = _lr_probe() or {}
            launch_err = str(snap.get("last_launch_error") or "")
        except Exception:  # noqa: BLE001
            pass

        not_installed = "not installed" in launch_err.lower()
        failed = ptr.recovery_stage == "recovery_failed" or not_installed
        if failed:
            reason = launch_err or ptr.recovery_last_error or "relaunch_failed"
            if not_installed:
                reason = launch_err or "package_not_installed_for_current_user"
            ptr.mark_unrecoverable(pkg, reason)
            ptr.end_recovery(failed=True, reason=reason[:200], resume=True)
            ptr.recovery_pause_checking = False
            self.sync_dead_packages_into_recovery_queue()
            self._process_next_recovery(render_cb)
            return

        ptr.recovery_pause_checking = False
        ptr.recovery_resume_package = pkg
        ptr.set_mode(cp.MODE_CHECKING, pointer_text=cp.POINTER_RESUME_CHECKING)
        self._render(render_cb)

    def _ensure_logcat(self, ptr: cp.CheckerPointerState) -> None:
        sup = self._sup
        alive = False
        try:
            alive = bool(getattr(sup._rjn_monitor, "_logcat_stream_alive", False))
        except Exception:  # noqa: BLE001
            alive = False
        if alive:
            ptr.logcat_unavailable_fallback_active = False
            ptr.set_loop_health(logcat_reader_alive=True)
            return
        ptr.logcat_unavailable_fallback_active = True
        ptr.set_loop_health(logcat_reader_alive=False)
        try:
            sup._rjn_monitor._ensure_logcat_stream()
            ptr.record_logcat_restart()
            alive = bool(getattr(sup._rjn_monitor, "_logcat_stream_alive", False))
            ptr.set_loop_health(logcat_reader_alive=alive)
            ptr.logcat_unavailable_fallback_active = not alive
        except Exception:  # noqa: BLE001
            pass

    def _detect_stale_and_record(self, ptr: cp.CheckerPointerState) -> None:
        if ptr.checker_last_heartbeat_at is None:
            return
        age = max(0.0, time.time() - float(ptr.checker_last_heartbeat_at))
        ptr.checker_stale_age_s = round(age, 2)
        if age > _STATE_FILE_STALE_S:
            if ptr.checker_stale_detected_at is None:
                ptr.checker_stale_detected_at = time.time()
            ptr.record_checker_restart("stale_heartbeat")
            ptr.write_state_file(force=True)

    @staticmethod
    def _render(render_cb: Callable[[], None] | None) -> None:
        if render_cb is None:
            return
        try:
            render_cb()
        except Exception:  # noqa: BLE001
            pass
