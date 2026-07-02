"""Checking System — 7s timeout, recovery queue, presence ownership, watchdog."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from agent.checker_pointer import CheckerPointerState
from agent.checking_system import CHECKING_DEADLINE_S, CheckingSystem


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._t = float(start)
        self._mono = 0.0

    def monotonic(self) -> float:
        return self._mono

    def time(self) -> float:
        return self._t


def test_checking_timeout_commits_within_seven_seconds(monkeypatch):
    from agent import android
    from agent import checker_pointer

    monkeypatch.setattr(android, "get_package_pid", lambda *_a, **_k: "")
    ptr = CheckerPointerState()
    checker_pointer._INSTANCE = ptr  # noqa: SLF001
    sup = MagicMock()
    sup.stop_event.is_set.return_value = False
    sup._root_info.available = True
    sup._root_info.tool = "su"
    sup.status_map = {}
    sup._focused_online_evidence = MagicMock(return_value=None)
    sup._focused_dead_evidence = MagicMock(return_value=None)
    sup._interruptible_sleep = lambda _s: None
    sup.FOCUS_POLL_SECONDS = 0.05
    sup.NO_HEARTBEAT_FOCUS_LIMIT = 7
    sup._handle_state = MagicMock(return_value=False)
    cs = CheckingSystem(sup)

    outcome = cs.focus_package("com.moons.litesc", {"package": "com.moons.litesc"}, 1, 1000.0, None)
    assert outcome == "dead"
    assert ptr.committed_presence_state("com.moons.litesc") == "Dead"
    assert ptr.checking_deadline_ms == CHECKING_DEADLINE_S * 1000.0


def test_dead_handoff_enqueues_recovery():
    from agent import checker_pointer

    ptr = CheckerPointerState()
    checker_pointer._INSTANCE = ptr  # noqa: SLF001
    ptr.commit_presence_state("com.moons.litesf", "Dead")
    ptr.sync_dead_packages_into_recovery_queue()
    assert "com.moons.litesf" in ptr.recovery_queue
    assert ptr.dead_without_recovery_queue == []


def test_multiple_dead_packages_queued():
    from agent import checker_pointer

    ptr = CheckerPointerState()
    checker_pointer._INSTANCE = ptr  # noqa: SLF001
    ptr.commit_presence_state("com.moons.litesf", "Dead")
    ptr.commit_presence_state("com.moons.litesg", "Dead")
    ptr.sync_dead_packages_into_recovery_queue()
    assert set(ptr.recovery_queue) == {"com.moons.litesf", "com.moons.litesg"}


def test_recovery_failure_marks_unrecoverable_and_continues():
    from agent import checker_pointer

    ptr = CheckerPointerState()
    checker_pointer._INSTANCE = ptr  # noqa: SLF001
    err = "package not installed for current user: com.moons.litesh"
    sup = MagicMock()
    sup._handle_state = MagicMock(
        side_effect=lambda pkg, *_a, **_k: (
            ptr.begin_recovery(pkg),
            ptr.end_recovery(failed=True, reason=err, resume=True),
        )[-1]
    )
    cs = CheckingSystem(sup)
    ptr.enqueue_recovery("com.moons.litesh")
    ptr.enqueue_recovery("com.moons.litesf")
    cs._process_next_recovery(None)
    assert ptr.is_unrecoverable("com.moons.litesh")
    assert not ptr.recovery_in_progress


def test_checking_timer_header_format():
    ptr = CheckerPointerState()
    ptr.begin_checking_package("p0", 1, now=1000.0, deadline_s=7.0)
    ptr.update_checking_timer(3.2, deadline_s=7.0)
    assert ptr.pointer_text() == "Checking 3/7s"


def test_invalid_presence_write_attempts_recorded():
    ptr = CheckerPointerState()
    ptr.record_invalid_presence_write(
        source="supervisor", package="p0", attempted_state="Online"
    )
    snap = ptr.probe_snapshot()
    assert snap["invalid_presence_write_attempts"] == 1


def test_end_checking_focus_clears_stuck_checking_display():
    ptr = CheckerPointerState()
    ptr.begin_checking_package("p0", 1, now=1.0, deadline_s=7.0)
    ptr.commit_presence_state("p0", "Dead")
    ptr.end_checking_focus("p0")
    assert ptr.active_focus_package == ""
    assert ptr.display_state("p0") == "Dead"


def test_logcat_fallback_flag():
    ptr = CheckerPointerState()
    sup = MagicMock()
    sup._rjn_monitor._logcat_stream_alive = False
    sup._rjn_monitor._ensure_logcat_stream = MagicMock()
    cs = CheckingSystem(sup)
    cs._ensure_logcat(ptr)
    assert ptr.logcat_unavailable_fallback_active is True
    assert ptr.logcat_restart_count >= 1
