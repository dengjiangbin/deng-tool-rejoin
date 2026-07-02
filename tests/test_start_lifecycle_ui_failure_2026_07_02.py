"""Start lifecycle — UI failure tolerance, cache-clear guards, scheduler cadence."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

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


def test_render_oserror_does_not_abort_scheduler(monkeypatch):
    clock = FakeClock(100.0)
    start_lifecycle.reset_for_start(["p0", "p1"])
    start_lifecycle.mark_cache_clear_closed()

    sched = LaunchScheduler(
        session_id="s-ui",
        packages=["p0", "p1"],
        first_launch_delay_seconds=5.0,
        interval_seconds=30.0,
    )
    sched.mark_clear_cache_started(monotonic_now=clock.monotonic())
    launched: list[str] = []

    def launch_one(_index: int, package: str) -> str:
        launched.append(package)
        return "success"

    with patch(
        "agent.start_lifecycle.record_ui_render_error",
        wraps=start_lifecycle.record_ui_render_error,
    ) as record_err:
        sched.run_schedule(
            launch_one,
            monotonic_fn=clock.monotonic,
            sleep_fn=clock.sleep,
        )
        start_lifecycle.record_ui_render_error(OSError(5, "I/O error"))
        start_lifecycle.mark_scheduler_survived_ui_failure()

    assert launched == ["p0", "p1"]
    snap = sched.probe_snapshot()
    assert snap["first_launch_delay_from_clear_cache_start_ms"] == 5000.0
    assert snap["launch_interval_observed_ms"] == [30000.0]
    assert snap["checking_system_started_at"] is not None
    assert record_err.called
    assert start_lifecycle.probe_snapshot()["scheduler_survived_ui_failure"] is True


def test_command_started_before_slow_username_lookup():
    clock = FakeClock(0.0)
    sched = LaunchScheduler(
        session_id="s-user",
        packages=["p0", "p1"],
        first_launch_delay_seconds=5.0,
        interval_seconds=30.0,
    )
    sched.mark_clear_cache_started(monotonic_now=0.0)
    gate = threading.Event()

    def slow_username(_index: int, _package: str) -> str:
        gate.wait(timeout=2.0)
        return "user"

    def launch_one(_index: int, _package: str) -> str:
        return "success"

    worker = threading.Thread(
        target=lambda: sched.run_schedule(
            launch_one,
            username_for_index=slow_username,
            monotonic_fn=clock.monotonic,
            sleep_fn=clock.sleep,
        ),
        daemon=True,
    )
    worker.start()
    clock.advance(5.0)
    time.sleep(0.05)
    row0 = sched.probe_snapshot()["launch_calls"][0]
    assert row0["command_started_at"] == 5.0
    gate.set()
    worker.join(timeout=3.0)
    assert sched.probe_snapshot()["launch_calls"][1]["command_started_at"] == 35.0


def test_cache_clear_timeout_aborts_leftover_batch(monkeypatch):
    from agent import android, cache_clear_phases

    calls: list[str] = []

    def _slow_batch(packages, **_k):
        for pkg in packages:
            if start_lifecycle.start_cache_clear_abort_requested():
                calls.append(f"abort:{pkg}")
                continue
            calls.append(f"clear:{pkg}")
            time.sleep(0.2)
        return {p: "Cleared" for p in packages}

    monkeypatch.setattr(cache_clear_phases, "run_start_mass_cache_clear", _slow_batch)
    start_lifecycle.reset_for_start(["a", "b", "c", "d"])
    out = cache_clear_phases.run_start_mass_cache_clear_bounded(
        ["a", "b", "c", "d"],
        deadline_s=0.35,
    )
    assert out["cache_clear_timeout"] is True
    assert start_lifecycle.start_cache_clear_abort_requested() is True
    assert any(c.startswith("clear:") for c in calls)


def test_force_stop_blocked_after_cache_clear_closed():
    start_lifecycle.reset_for_start(["com.moons.litesc"])
    start_lifecycle.mark_cache_clear_closed()
    assert start_lifecycle.should_block_force_stop("com.moons.litesc") is True
    snap = start_lifecycle.probe_snapshot()
    assert snap["cache_clear_closed"] is True
    assert snap["blocked_force_stop_count"] == 1


def test_clear_cache_phase_exit_fields():
    start_lifecycle.reset_for_start(["p0"])
    start_lifecycle.exit_clear_cache_phase("success")
    snap = start_lifecycle.probe_snapshot()
    assert snap["clear_cache_phase_exited_at"] is not None
    assert snap["clear_cache_phase_exit_reason"] == "success"


def test_multi_package_thirty_second_cadence_all_dispatched():
    clock = FakeClock(0.0)
    packages = ["c", "d", "f", "g", "h"]
    sched = LaunchScheduler(
        session_id="s-multi",
        packages=packages,
        first_launch_delay_seconds=5.0,
        interval_seconds=30.0,
    )
    sched.mark_clear_cache_started(monotonic_now=0.0)
    fired: list[tuple[str, float]] = []

    def launch_one(_index: int, package: str) -> str:
        fired.append((package, clock.monotonic()))
        return "success"

    sched.run_schedule(
        launch_one,
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
    )
    assert [p for p, _ in fired] == packages
    assert [t for _, t in fired] == [5.0, 35.0, 65.0, 95.0, 125.0]
    snap = sched.probe_snapshot()
    assert snap["blocked_by_online_wait"] is False
    assert snap["checking_system_started_at"] is not None


def test_cache_clear_timeout_starts_scheduler_path(monkeypatch):
    from agent import cache_clear_phases

    monkeypatch.setattr(
        cache_clear_phases,
        "run_start_mass_cache_clear",
        lambda *_a, **_k: time.sleep(60) or {},
    )
    p = CheckerPointerState()
    out = cache_clear_phases.run_start_mass_cache_clear_bounded(
        ["com.moons.litesc"],
        deadline_s=0.3,
        checker_pointer=p,
    )
    assert out["cache_clear_timeout"] is True
    start_lifecycle.exit_clear_cache_phase(str(out.get("cache_clear_status")))
    assert start_lifecycle.probe_snapshot()["clear_cache_phase_exited_at"] is not None
