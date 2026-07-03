"""test/latest2-only runtime patches (v1.3.0 base + lime overlay).

Applied when ``lime_detection_enabled()`` — never on stable/main-dev channels.
"""

from __future__ import annotations

import time
from typing import Any

_PATCHED = False


def apply_test_latest2_runtime_patches() -> None:
    """Monkey-patch v1.3.0 behaviors that block lime speed tests or hard stagger."""
    global _PATCHED
    if _PATCHED:
        return
    try:
        from .lime_channel import lime_detection_enabled
    except Exception:  # noqa: BLE001
        return
    if not lime_detection_enabled():
        return

    _patch_supervisor_recovery_gate()
    _patch_supervisor_stagger_safety()
    _patch_monitoring_relay()
    _PATCHED = True


def _patch_supervisor_recovery_gate() -> None:
    """Do not halt the watchdog round-robin on one package's recovery gate.

    v1.3.0 ``_run_blocking_recovery_gate`` spins until Online/Dead for the
    current package only.  While blocked, the main round-robin never reaches
    other clones — force-close on them looks "stuck on the last package".
    Lime's parallel dead hot lane + force_close_race still run, but the UX
    and supervisor relaunch cadence suffer.  On test/latest2 we cap the gate
    at 15s then continue the round so every package keeps getting checked.
    """
    try:
        from . import supervisor as sup
        from .logger import log_event
    except Exception:  # noqa: BLE001
        return

    cls = sup.WatchdogSupervisor
    if getattr(cls, "_test_latest2_recovery_gate_patched", False):
        return
    orig = cls._run_blocking_recovery_gate

    def _bounded_gate(
        self: Any,
        pkg: str,
        entry: dict[str, Any],
        *,
        package_index: int = 0,
        package_total: int = 0,
        render_callback: Any = None,
    ) -> None:
        started = time.time()
        deadline = started + 15.0
        while time.time() < deadline and not self.stop_event.is_set():
            try:
                state = self._evaluate_package_presence_isolated(pkg, entry)
            except Exception:  # noqa: BLE001
                break
            self._set_status(pkg, state)
            self._prev_state[pkg] = state
            if state in {sup.STATUS_ONLINE, sup.STATUS_DEAD}:
                log_event(
                    self._logger,
                    "info",
                    "[DENG_REJOIN_RECOVERY_GATE_EXIT]",
                    package=pkg,
                    result=state.lower(),
                    mode="test_latest2_bounded",
                )
                return
            try:
                cb = render_callback or self._render_callback
                if callable(cb):
                    cb()
            except Exception:  # noqa: BLE001
                pass
            self._interruptible_sleep(min(2.0, max(0.5, self.RECOVERY_GATE_POLL_SECONDS)))

        log_event(
            self._logger,
            "info",
            "[DENG_REJOIN_RECOVERY_GATE_SKIPPED]",
            package=pkg,
            mode="test_latest2_continue_round_robin",
            waited_sec=round(time.time() - started, 1),
        )

    cls._run_blocking_recovery_gate = _bounded_gate  # type: ignore[method-assign]
    cls._test_latest2_recovery_gate_patched = True


def _patch_supervisor_stagger_safety() -> None:
    """Backport stagger-safe recovery/display guards missing from v1.3.0 supervisor.

    v1.3.0 starts the watchdog as soon as clone 1 opens.  A brief process
    absence during load can map to Dead → cache clear → Relaunching while the
    Start thread is still staggering the rest (probe p-5e86495d7b).
    """
    try:
        from . import supervisor as sup
        from .logger import log_event
    except Exception:  # noqa: BLE001
        return

    cls = sup.WatchdogSupervisor
    if getattr(cls, "_test_latest2_stagger_safety_patched", False):
        return

    _recovery_states = {
        sup.STATUS_DEAD,
        sup.STATUS_DISCONNECTED,
        sup.STATUS_JOIN_FAILED,
    }

    if not hasattr(cls, "_test_latest2_orig_init"):
        cls._test_latest2_orig_init = cls.__init__

        def _init_with_initial_launch(self: Any, *args: Any, **kwargs: Any) -> None:
            cls._test_latest2_orig_init(self, *args, **kwargs)
            self._initial_launch_inflight = set()

        cls.__init__ = _init_with_initial_launch  # type: ignore[method-assign]

    if not hasattr(cls, "_test_latest2_orig_mark_launched"):
        cls._test_latest2_orig_mark_launched = cls.mark_package_launched

        def _mark_with_initial_launch(self: Any, pkg: str) -> None:
            with self._state_lock:
                first_open = pkg not in self._package_opened
            cls._test_latest2_orig_mark_launched(self, pkg)
            if first_open:
                self._initial_launch_inflight.add(pkg)

        cls.mark_package_launched = _mark_with_initial_launch  # type: ignore[method-assign]

    if not hasattr(cls, "_test_latest2_orig_detect"):
        cls._test_latest2_orig_detect = cls._detect_package_state

        def _detect_with_stagger_grace(
            self: Any, pkg: str, entry: dict[str, Any]
        ) -> tuple[str, dict[str, Any]]:
            state, detail = cls._test_latest2_orig_detect(self, pkg, entry)
            if state == sup.STATUS_ONLINE:
                self._initial_launch_inflight.discard(pkg)
                return state, detail
            if state in _recovery_states and (
                self._in_loading_grace(pkg)
                or (not self._all_launches_completed and pkg in self._initial_launch_inflight)
            ):
                detail = dict(detail)
                detail["reason"] = "stagger_launch_grace_suppress_dead"
                detail["reason_internal"] = "stagger_launch_grace_suppress_dead"
                return sup.STATUS_LAUNCHING, detail
            if state in {sup.STATUS_RELAUNCHING, sup.STATUS_REOPENING} and (
                pkg in self._initial_launch_inflight
            ):
                detail = dict(detail)
                detail["reason"] = "initial_launch_no_dead_event"
                return sup.STATUS_LAUNCHING, detail
            return state, detail

        cls._detect_package_state = _detect_with_stagger_grace  # type: ignore[method-assign]

    if not hasattr(cls, "_test_latest2_orig_handle_state"):
        cls._test_latest2_orig_handle_state = cls._handle_state

        def _handle_with_stagger_defer(
            self: Any,
            pkg: str,
            entry: dict[str, Any],
            state: str,
            prev: str,
            now: float,
            render_callback: Any = None,
            immediate_recovery: bool = False,
            detail: dict[str, Any] | None = None,
        ) -> bool:
            if state in _recovery_states and not self._all_launches_completed:
                log_event(
                    self._logger,
                    "debug",
                    "[DENG_REJOIN_STAGGER_RECOVERY_DEFERRED]",
                    package=pkg,
                    state=state,
                    action="wait_for_all_launches_completed",
                    mode="test_latest2",
                )
                return False
            if (
                state in _recovery_states
                and self._in_loading_grace(pkg)
                and pkg in self._initial_launch_inflight
            ):
                log_event(
                    self._logger,
                    "debug",
                    "[DENG_REJOIN_STAGGER_RECOVERY_DEFERRED]",
                    package=pkg,
                    state=state,
                    action="loading_grace_initial_launch",
                    mode="test_latest2",
                )
                return False
            return cls._test_latest2_orig_handle_state(
                self,
                pkg,
                entry,
                state,
                prev,
                now,
                render_callback=render_callback,
                immediate_recovery=immediate_recovery,
                detail=detail,
            )

        cls._handle_state = _handle_with_stagger_defer  # type: ignore[method-assign]

    cls._test_latest2_stagger_safety_patched = True


def _patch_monitoring_relay() -> None:
    """Route real presence commits through test/latest2 Monitoring relay only."""
    try:
        from . import supervisor as sup
        from .logger import log_event
        from .test_latest2_monitoring_relay import start_monitoring_relay
    except Exception:  # noqa: BLE001
        return

    cls = sup.WatchdogSupervisor
    if getattr(cls, "_test_latest2_monitoring_relay_patched", False):
        return

    _presence_states = frozenset(
        {
            sup.STATUS_ONLINE,
            sup.STATUS_DEAD,
            getattr(sup, "STATUS_NO_HEARTBEAT", "No Heartbeat"),
            sup.STATUS_DISCONNECTED,
            "No Heartbeat",
        }
    )

    if not hasattr(cls, "_test_latest2_orig_set_status"):
        cls._test_latest2_orig_set_status = cls._set_status

        def _set_status_relay_gated(self: Any, pkg: str, status: str) -> None:
            if (
                str(status or "").strip() in _presence_states
                and not getattr(self, "_monitoring_relay_commit", False)
            ):
                try:
                    from .test_latest2_monitoring_relay import submit_raw_evidence

                    hint = "online" if status == sup.STATUS_ONLINE else "dead"
                    if status == sup.STATUS_DISCONNECTED:
                        hint = "kicked"
                    submit_raw_evidence(
                        pkg,
                        hint=hint,
                        source="supervisor_blocked",
                        evidence=f"blocked_direct_set:{status}",
                    )
                except Exception:  # noqa: BLE001
                    pass
                log_event(
                    self._logger,
                    "debug",
                    "[DENG_REJOIN_MONITORING_RELAY_BLOCKED]",
                    package=pkg,
                    attempted_state=str(status),
                )
                return
            cls._test_latest2_orig_set_status(self, pkg, status)

        cls._set_status = _set_status_relay_gated  # type: ignore[method-assign]

    if not hasattr(cls, "_test_latest2_orig_mark_for_relay"):
        prior = cls.mark_package_launched

        def _mark_start_monitoring(self: Any, pkg: str) -> None:
            with self._state_lock:
                first_open = pkg not in self._package_opened
            prior(self, pkg)
            if first_open:
                try:
                    entries = {
                        p: self.entry_by_pkg.get(p) or {}
                        for p in self.packages
                    }
                    relay = start_monitoring_relay(
                        self,
                        list(self.packages),
                        entries=entries,
                        direct_set_status=cls._test_latest2_orig_set_status,
                    )
                    if relay is not None:
                        relay.note_launch(pkg)
                        log_event(
                            self._logger,
                            "info",
                            "[DENG_REJOIN_MONITORING_RELAY_STARTED]",
                            package_count=len(self.packages),
                        )
                except Exception as exc:  # noqa: BLE001
                    log_event(
                        self._logger,
                        "warning",
                        "[DENG_REJOIN_MONITORING_RELAY_START_FAILED]",
                        error=str(exc)[:120],
                    )
            else:
                try:
                    from .test_latest2_monitoring_relay import get_active_relay

                    relay = get_active_relay()
                    if relay is not None:
                        relay.note_launch(pkg)
                except Exception:  # noqa: BLE001
                    pass

        cls.mark_package_launched = _mark_start_monitoring  # type: ignore[method-assign]
        cls._test_latest2_orig_mark_for_relay = prior

    cls._test_latest2_monitoring_relay_patched = True
