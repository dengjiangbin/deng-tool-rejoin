"""Regression: clear-cache must arm launch immediately (probe p-f372c13452)."""

from __future__ import annotations

import threading
import time

from agent import start_lifecycle
from agent.checker_pointer import CheckerPointerState
from agent.launch_scheduler import LaunchScheduler


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)

    def monotonic(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += float(seconds)

    def sleep(self, seconds: float) -> None:
        self._t += float(seconds)


def test_bootstrap_sets_first_launch_requested_immediately():
    start_lifecycle.reset_for_start(["com.moons.litesc"])
    start_lifecycle.mark_clearing_cache_entered()
    start_lifecycle.mark_clear_cache_command_started()
    start_lifecycle.mark_clear_cache_command_finished()
    start_lifecycle.mark_clearing_cache_finished()
    start_lifecycle.exit_clear_cache_phase("success")
    before = time.time()
    start_lifecycle.bootstrap_first_launch_after_cache(
        "com.moons.litesc",
        launch_scheduler=LaunchScheduler(session_id="s", packages=["com.moons.litesc"]),
        interval_s=30.0,
    )
    snap = start_lifecycle.probe_snapshot()
    assert snap["first_launch_requested_at"] is not None
    assert snap["launching_started_at"] is not None
    assert snap["first_launch_delay_after_clear_cache_ms"] is not None
    assert snap["first_launch_delay_after_clear_cache_ms"] <= 500.0
    assert snap["first_launch_requested_at"] >= before - 0.05


def test_scheduler_records_checker_launch_on_command_start(monkeypatch):
    from agent import checker_pointer

    p = CheckerPointerState()
    p.reset_for_new_start(session_id="s", pid=99)
    monkeypatch.setattr(checker_pointer, "_INSTANCE", p)
    start_lifecycle.reset_for_start(["com.moons.litesc"])
    sched = LaunchScheduler(session_id="s", packages=["com.moons.litesc"])
    sched.mark_clear_cache_started(monotonic_now=0.0)
    sched.record_clear_cache_finished(finished_at=1.0, reanchor_launches=True)

    def launch_one(_index: int, _package: str) -> str:
        row = p.probe_snapshot()["per_package"]["com.moons.litesc"]
        assert row["launch_requested_at"] is not None
        assert row["launch_dispatched_at"] is not None
        return "success"

    sched.run_schedule(launch_one, monotonic_fn=time.monotonic, sleep_fn=time.sleep)
    life = start_lifecycle.probe_snapshot()
    assert life["first_launch_requested_at"] is not None


def test_cache_clear_batch_budget_capped_at_five_seconds():
    from agent.cache_clear_phases import start_cache_clear_batch_budget_s

    assert start_cache_clear_batch_budget_s(1) == 2.0
    assert start_cache_clear_batch_budget_s(3) == 5.0
    assert start_cache_clear_batch_budget_s(10) == 5.0


def test_scheduler_runs_without_checker_heartbeat():
    clock = FakeClock(100.0)
    sched = LaunchScheduler(
        session_id="s-stale",
        packages=["p0", "p1"],
        first_launch_delay_seconds=0.0,
        interval_seconds=30.0,
    )
    sched.mark_clear_cache_started(monotonic_now=clock.monotonic())
    sched.record_clear_cache_finished(finished_at=clock.monotonic(), reanchor_launches=True)
    launched: list[str] = []

    def launch_one(_index: int, package: str) -> str:
        launched.append(package)
        return "success"

    sched.run_schedule(
        launch_one,
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
    )
    assert launched == ["p0", "p1"]
