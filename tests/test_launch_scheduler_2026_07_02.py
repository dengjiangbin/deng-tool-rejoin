"""Tests for deterministic launch schedule (probe p-bf0b2feb55)."""

from __future__ import annotations

import threading
import time

import pytest

from agent.launch_scheduler import LaunchScheduler


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._t = float(start)

    def monotonic(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += float(seconds)

    def sleep(self, seconds: float) -> None:
        self._t += float(seconds)


def test_first_package_due_at_clear_cache_plus_five():
    sched = LaunchScheduler(
        session_id="s1",
        packages=["a", "b", "c"],
        first_launch_delay_seconds=5.0,
        interval_seconds=30.0,
    )
    clock = FakeClock(500.0)
    sched.mark_clear_cache_started(monotonic_now=clock.monotonic())
    assert sched.due_at_for_index(0) == 505.0
    assert sched.due_at_for_index(1) == 535.0
    assert sched.due_at_for_index(2) == 565.0


def test_scheduler_fires_at_due_times_with_fake_clock():
    clock = FakeClock(0.0)
    fired: list[tuple[int, str, float]] = []

    sched = LaunchScheduler(
        session_id="s2",
        packages=["p0", "p1"],
        first_launch_delay_seconds=5.0,
        interval_seconds=30.0,
        command_timeout_seconds=1.0,
    )
    sched.mark_clear_cache_started(monotonic_now=clock.monotonic())

    def launch_one(index: int, package: str) -> str:
        fired.append((index, package, clock.monotonic()))
        return "success"

    sched.run_schedule(
        launch_one,
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
    )

    assert fired == [(0, "p0", 5.0), (1, "p1", 35.0)]
    snap = sched.probe_snapshot()
    assert snap["blocked_by_online_wait"] is False
    assert snap["blocked_by_clear_cache"] is False
    assert snap["launch_attempts"][0]["result"] == "success"
    assert snap["launch_attempts"][0]["delta_from_clear_cache_ms"] == 5000.0
    assert snap["launch_attempts"][1]["delta_from_clear_cache_ms"] == 35000.0


def test_scheduler_continues_when_launch_times_out():
    clock = FakeClock(0.0)
    sched = LaunchScheduler(
        session_id="s3",
        packages=["p0", "p1"],
        first_launch_delay_seconds=5.0,
        interval_seconds=30.0,
    )
    sched.mark_clear_cache_started(monotonic_now=clock.monotonic())

    def launch_one(index: int, package: str) -> str:
        if index == 0:
            clock.advance(60.0)  # simulate hang inside launch
            return "timeout_dispatched"
        return "success"

    sched.run_schedule(
        launch_one,
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
    )
    assert sched.probe_snapshot()["launch_attempts"][0]["result"] == "timeout_dispatched"
    assert sched.probe_snapshot()["launch_attempts"][1]["result"] == "success"


def test_scheduler_never_waits_for_online():
    sched = LaunchScheduler(session_id="s4", packages=["a"])
    sched.mark_clear_cache_started(monotonic_now=0.0)
    snap = sched.probe_snapshot()
    assert snap["blocked_by_online_wait"] is False
    assert snap["blocked_by_clear_cache"] is False


def test_probe_snapshot_fields_present():
    sched = LaunchScheduler(session_id="s5", packages=["a", "b"])
    sched.mark_clear_cache_started(monotonic_now=10.0)
    snap = sched.probe_snapshot()
    for key in (
        "session_id",
        "clear_cache_started_at",
        "clear_cache_finished_at",
        "clear_cache_duration_ms",
        "clear_cache_timeout",
        "first_launch_due_at",
        "launch_scheduler_started_at",
        "first_launch_called_at",
        "first_launch_delay_from_clear_cache_start_ms",
        "checking_system_started_at",
        "lifecycle_blocker",
        "interval_seconds",
        "first_launch_delay_seconds",
        "scheduler_alive",
        "blocked_by_clear_cache",
        "blocked_by_online_wait",
        "launch_interval_observed_ms",
        "launch_calls",
        "launch_attempts",
    ):
        assert key in snap
    assert snap["first_launch_delay_seconds"] == 5.0
    assert snap["interval_seconds"] == 30.0
    assert len(snap["launch_attempts"]) == 2


def test_async_launch_does_not_delay_next_due_time():
    clock = FakeClock(0.0)
    sched = LaunchScheduler(
        session_id="s6",
        packages=["p0", "p1"],
        first_launch_delay_seconds=5.0,
        interval_seconds=30.0,
    )
    sched.mark_clear_cache_started(monotonic_now=0.0)
    p0_started = threading.Event()
    p0_release = threading.Event()

    def launch_one(index: int, _package: str) -> str:
        if index == 0:
            p0_started.set()
            p0_release.wait(2.0)
        return "success"

    runner = threading.Thread(
        target=sched.run_schedule,
        kwargs={
            "launch_one": launch_one,
            "monotonic_fn": clock.monotonic,
            "sleep_fn": clock.sleep,
        },
        daemon=True,
    )
    runner.start()
    assert p0_started.wait(1.0)
    snap = sched.probe_snapshot()
    assert snap["launch_attempts"][1]["fired_at"] == 35.0
    p0_release.set()
    runner.join(5.0)
