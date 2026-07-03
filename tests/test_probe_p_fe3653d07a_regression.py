"""Regression tests for dev-probe p-fe3653d07a.

Probe showed disconnect_code_285 (lobby/wrong server) leaving packages stuck
after recovery: checker mode ``resume_checking`` with a stale
``Resume Monitoring..`` header while DISCONNECTED packages never re-entered
recovery under the focused checker relay.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agent import checker_pointer
from agent.checker_pointer import CheckerPointerState
from agent.checking_system import CheckingSystem
from agent.supervisor import STATUS_DISCONNECTED, WatchdogSupervisor


def test_resume_checking_if_safe_clears_stale_resume_label_after_recovery():
    ptr = CheckerPointerState()
    ptr.reset_for_new_start(session_id="s1", pid=1049)
    ptr.mark_checking_system_started()
    ptr.recovery_pause_checking = True
    ptr.state_pointer_text = checker_pointer.POINTER_RESUME_CHECKING
    ptr.header_action_label = checker_pointer.POINTER_RESUME_CHECKING
    ptr.checker_mode = checker_pointer.MODE_RESUME_CHECKING

    assert ptr.resume_checking_if_safe() is True
    snap = ptr.probe_snapshot()
    assert snap["checker_mode"] == checker_pointer.MODE_CHECKING
    assert snap["header_action_label"] != checker_pointer.POINTER_RESUME_CHECKING
    assert snap["recovery_pause_checking"] is False


def test_focused_dead_evidence_treats_disconnected_as_dead():
    sup = WatchdogSupervisor([], {"supervisor": {}})
    sup.status_map["com.moons.litesd"] = STATUS_DISCONNECTED
    row = MagicMock()
    row.last_transition_reason = "disconnect_code_285"
    sup._rjn_monitor._states = {"com.moons.litesd": row}
    sup._rjn_monitor._lock = MagicMock()
    sup._rjn_monitor._lock.__enter__ = MagicMock(return_value=None)
    sup._rjn_monitor._lock.__exit__ = MagicMock(return_value=False)

    dead = sup._focused_dead_evidence("com.moons.litesd", STATUS_DISCONNECTED, {})
    assert dead is not None
    assert dead[0] == "disconnect_code_285"
    assert dead[1] == "disconnect_detector"


def test_checking_system_pre_focus_disconnected_enqueues_recovery(monkeypatch):
    from agent import checker_pointer as cp

    ptr = CheckerPointerState()
    cp._INSTANCE = ptr  # noqa: SLF001
    sup = MagicMock()
    sup.stop_event.is_set.return_value = False
    sup.status_map = {"com.moons.litesd": STATUS_DISCONNECTED}
    sup._focused_dead_evidence = MagicMock(
        return_value=("disconnect_code_285", "disconnect_detector", "Disconnected:285")
    )
    sup._focused_online_evidence = MagicMock(return_value=None)
    sup._handle_state = MagicMock(return_value=False)
    sup._interruptible_sleep = lambda _s: None
    cs = CheckingSystem(sup)

    outcome = cs.focus_package(
        "com.moons.litesd",
        {"package": "com.moons.litesd"},
        1,
        1000.0,
        None,
    )

    from agent import focused_checker as fc

    assert outcome == fc.OUTCOME_DEAD
    assert ptr.committed_presence_state("com.moons.litesd") == "Dead"
    sup._handle_state.assert_called_once()


def test_end_recovery_success_then_resume_does_not_stick_on_resume_monitoring():
    ptr = CheckerPointerState()
    ptr.reset_for_new_start(session_id="s1", pid=1049)
    ptr.begin_recovery("com.moons.litesd", reason="disconnect_code_285")
    ptr.recovery_pause_checking = True
    ptr.end_recovery(failed=False, resume=True)

    snap = ptr.probe_snapshot()
    assert snap["checker_mode"] == checker_pointer.MODE_CHECKING
    assert snap["header_action_label"] != checker_pointer.POINTER_RESUME_CHECKING
