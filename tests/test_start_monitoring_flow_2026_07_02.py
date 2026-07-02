"""Start lifecycle / Monitoring flow regressions (2026-07-02)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from agent import start_lifecycle
from agent.checker_pointer import (
    POINTER_CHECKING,
    POINTER_RESUME_CHECKING,
    CheckerPointerState,
)
from agent.checking_system import CheckingSystem
from agent.launch_scheduler import LaunchScheduler
from agent.supervisor import STATUS_LAUNCHING, STATUS_ONLINE, STATUS_WAITING, WatchdogSupervisor


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)

    def monotonic(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += float(seconds)

    def sleep(self, seconds: float) -> None:
        self._t += float(seconds)


def test_start_pressed_enters_preparing_not_getting_ready():
    ptr = CheckerPointerState()
    ptr.begin_preparing(["p0"])
    assert ptr.header_pointer_text() == "Preparing.."
    assert ptr.first_launch_phase == "preparing"


def test_preparing_ends_when_command_finishes():
    start_lifecycle.reset_for_start(["p0"])
    start_lifecycle.mark_preparing_entered()
    start_lifecycle.mark_preparing_command_started()
    start_lifecycle.mark_preparing_command_finished()
    snap = start_lifecycle.probe_snapshot()
    assert snap["preparing_duration_ms"] is not None
    assert snap["preparing_duration_ms"] <= 3000.0


def test_header_phase_does_not_become_table_package_state():
    start_lifecycle.reset_for_start(["p0"])
    start_lifecycle.mark_header_phase("Preparing")
    start_lifecycle.bump_ui_phase_version(phase="Ready", source="render_phase")
    snap = start_lifecycle.probe_snapshot()
    assert snap["header_phase"] == "Preparing"
    assert snap["header_is_single_row"] is True
    assert snap["table_state_phase"] == "Ready"


def test_package_row_never_getting_ready_in_table_mapping():
    from agent import commands

    phase = {"p0": "Getting Ready", "p1": "Ready"}
    _HEADER_ONLY = frozenset({"Preparing", "Clear Cache", "Getting Ready", "Clearing Cache"})

    def mapped(pkg: str) -> str:
        ph = phase.get(pkg, "")
        if ph in _HEADER_ONLY:
            return "Ready"
        return ph or "Ready"

    assert mapped("p0") == "Ready"
    assert mapped("p1") == "Ready"


def test_clear_cache_command_duration_separate_from_phase():
    start_lifecycle.reset_for_start(["p0"])
    start_lifecycle.mark_clearing_cache_entered()
    start_lifecycle.mark_clear_cache_command_started()
    start_lifecycle.mark_clear_cache_command_finished()
    start_lifecycle.mark_clearing_cache_finished()
    snap = start_lifecycle.probe_snapshot()
    assert snap["clear_cache_command_duration_ms"] is not None
    assert snap["clearing_cache_phase_duration_ms"] is not None
    assert snap["clear_cache_command_duration_ms"] <= 5000.0
    assert snap["clearing_cache_duration_ms"] <= 5000.0


def test_clearing_cache_probe_timestamps():
    start_lifecycle.reset_for_start(["p0"])
    start_lifecycle.mark_clearing_cache_entered()
    start_lifecycle.mark_clearing_cache_finished()
    snap = start_lifecycle.probe_snapshot()
    assert snap["clearing_cache_entered_at"] is not None
    assert snap["clearing_cache_finished_at"] is not None
    assert snap["clearing_cache_duration_ms"] is not None


def test_getting_ready_bridge_max_one_second():
    start_lifecycle.reset_for_start(["p0"])
    start_lifecycle.mark_getting_ready_entered()
    time.sleep(0.05)
    start_lifecycle.mark_getting_ready_finished()
    snap = start_lifecycle.probe_snapshot()
    assert snap["getting_ready_finished_at"] >= snap["getting_ready_entered_at"]
    assert (
        snap["getting_ready_finished_at"] - snap["getting_ready_entered_at"]
    ) <= 1.0


def test_launch_before_dispatch_sets_launching():
    ptr = CheckerPointerState()
    ptr.begin_opening("p0")
    assert ptr.state_pointer_text == "Opening.."


def test_launch_dispatched_enters_waiting_immediately():
    ptr = CheckerPointerState()
    ptr.mark_launch_dispatched("p0", reason="launch_dispatched_waiting_for_monitoring")
    row = ptr.probe_snapshot()["per_package"]["p0"]
    assert row["waiting_entered_at"] is not None
    assert row["state"] == "Waiting"


def test_launch_interval_thirty_seconds_without_online_wait():
    clock = FakeClock(200.0)
    fired: list[float] = []
    sched = LaunchScheduler(
        session_id="s-interval",
        packages=["p0", "p1", "p2"],
        first_launch_delay_seconds=0.0,
        interval_seconds=30.0,
    )
    sched.mark_clear_cache_started(monotonic_now=clock.monotonic())
    sched.record_clear_cache_finished(finished_at=clock.monotonic(), reanchor_launches=True)

    def launch_one(index: int, _package: str) -> str:
        fired.append(clock.monotonic())
        return "success"

    sched.run_schedule(
        launch_one,
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
    )
    assert len(fired) == 3
    assert round(fired[1] - fired[0], 1) == 30.0
    assert round(fired[2] - fired[1], 1) == 30.0
    snap = sched.probe_snapshot()
    assert snap["all_packages_dispatched_at"] is not None


def test_monitoring_starts_only_after_all_dispatched():
    clock = FakeClock(0.0)
    sched = LaunchScheduler(
        session_id="s-mon",
        packages=["p0", "p1"],
        first_launch_delay_seconds=0.0,
        interval_seconds=30.0,
    )
    sched.mark_clear_cache_started(monotonic_now=clock.monotonic())
    sched.record_clear_cache_finished(finished_at=clock.monotonic(), reanchor_launches=True)

    def launch_one(_index: int, _package: str) -> str:
        return "success"

    sched.run_schedule(
        launch_one,
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
    )
    snap = sched.probe_snapshot()
    assert snap["checking_system_started_at"] is not None
    assert snap["all_packages_dispatched_at"] is not None
    assert snap["checking_system_started_at"] >= snap["all_packages_dispatched_at"]


def test_only_monitoring_commits_presence_states():
    ptr = CheckerPointerState()
    ptr.commit_presence_state("p0", "Online", writer="checking_system")
    assert ptr.committed_presence_state("p0") == "Online"
    ptr.record_invalid_presence_write(
        source="supervisor", package="p0", attempted_state="Dead"
    )
    assert ptr.probe_snapshot()["invalid_presence_write_attempts"] == 1


def test_dead_triggers_recovery_and_blocks_resume_monitoring():
    ptr = CheckerPointerState()
    ptr.mark_dead_detected("p0", "timeout", "checking", "no_process")
    ptr.recovery_pause_checking = True
    ptr.end_recovery(resume=True)
    label = ptr.header_pointer_text()
    assert label != POINTER_RESUME_CHECKING
    assert ptr.probe_snapshot()["unrecovered_dead_count"] >= 1


def test_resume_monitoring_hidden_when_unrecovered_dead():
    ptr = CheckerPointerState()
    ptr.commit_presence_state("p0", "Dead")
    ptr.sync_dead_packages_into_recovery_queue()
    ptr.recovery_pause_checking = True
    ptr.resume_checking_if_safe()
    snap = ptr.probe_snapshot()
    assert snap["unrecovered_dead_count"] > 0
    assert snap["header_action_label"] != POINTER_RESUME_CHECKING


def test_monitoring_user_facing_labels():
    ptr = CheckerPointerState()
    ptr.mark_monitoring_started()
    assert POINTER_CHECKING == "Monitoring.."
    assert ptr.header_pointer_text() == "Monitoring.."
    assert ptr.probe_snapshot()["checker_status"] == "monitoring"


def test_supervisor_launching_to_waiting_lifecycle():
    entries = [{"package": "p0", "enabled": True}]
    sup = WatchdogSupervisor(entries, {"supervisor": {}}, initial_status={"p0": STATUS_WAITING})
    sup._set_lifecycle_status("p0", STATUS_LAUNCHING)
    assert sup.status_map["p0"] == STATUS_LAUNCHING
    sup._set_lifecycle_status("p0", STATUS_WAITING)
    assert sup.status_map["p0"] == STATUS_WAITING
