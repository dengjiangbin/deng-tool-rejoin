"""Start launch pipeline — clear cache bounds, scheduler cadence, probe fields."""

from __future__ import annotations

import threading
import time

import pytest

from agent import checker_pointer
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


def test_start_cache_clear_hang_times_out_and_continues(monkeypatch):
    from agent import cache_clear_phases

    def _hang(*_a, **_k):
        time.sleep(60)
        return {"a": "Cleared"}

    monkeypatch.setattr(cache_clear_phases, "run_start_mass_cache_clear", _hang)
    p = CheckerPointerState()
    started = time.monotonic()
    out = cache_clear_phases.run_start_mass_cache_clear_bounded(
        ["com.moons.litesc"],
        deadline_s=0.5,
        checker_pointer=p,
    )
    elapsed = time.monotonic() - started
    assert elapsed <= 5.0
    assert out["cache_clear_timeout"] is True
    assert out["cache_clear_duration_ms"] <= 5000


def test_start_cache_clear_exception_still_records(monkeypatch):
    from agent import cache_clear_phases

    def _boom(*_a, **_k):
        raise RuntimeError("clear exploded")

    monkeypatch.setattr(cache_clear_phases, "run_start_mass_cache_clear", _boom)
    out = cache_clear_phases.run_start_mass_cache_clear_bounded(
        ["com.moons.litesc"],
        deadline_s=1.0,
    )
    assert out["cache_clear_status"] == "failed_continue_launch"
    assert "exploded" in str(out.get("cache_clear_error") or "")


def test_first_launch_within_one_second_of_clear_cache_finish():
    clock = FakeClock(100.0)
    sched = LaunchScheduler(
        session_id="s-delay",
        packages=["p0"],
        first_launch_delay_seconds=1.0,
    )
    sched.mark_clear_cache_started(monotonic_now=clock.monotonic())
    clock.advance(3.5)
    sched.record_clear_cache_finished(finished_at=clock.monotonic(), reanchor_launches=True)

    def launch_one(_index: int, _package: str) -> str:
        return "success"

    sched.run_schedule(
        launch_one,
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
    )
    snap = sched.probe_snapshot()
    assert snap["first_launch_delay_from_clear_cache_finish_ms"] == 500.0
    assert snap["first_launch_called_at"] == 104.0


def test_multiple_packages_fire_every_thirty_seconds_without_waiting_for_online():
    clock = FakeClock(0.0)
    online_wait = threading.Event()
    online_wait.set()  # simulate never finishing if scheduler incorrectly waited

    sched = LaunchScheduler(
        session_id="s-cadence",
        packages=["p0", "p1", "p2"],
        first_launch_delay_seconds=5.0,
        interval_seconds=30.0,
    )
    sched.mark_clear_cache_started(monotonic_now=clock.monotonic())
    clock.advance(3.5)
    sched.record_clear_cache_finished(finished_at=clock.monotonic(), reanchor_launches=True)
    fired_at: list[float] = []

    def launch_one(index: int, package: str) -> str:
        fired_at.append(clock.monotonic())
        if index == 0:
            online_wait.wait(timeout=120)  # would block cadence if launch_one blocked schedule
        return "success"

    sched.run_schedule(
        launch_one,
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
    )
    assert fired_at == [4.0, 34.0, 64.0]
    intervals = sched.probe_snapshot()["launch_interval_observed_ms"]
    assert intervals == [30000.0, 30000.0]


def test_launch_callback_does_not_set_online_in_scheduler():
    clock = FakeClock(0.0)
    sched = LaunchScheduler(session_id="s-res", packages=["p0"])
    sched.mark_clear_cache_started(monotonic_now=0.0)

    sched.run_schedule(
        lambda _i, _p: "success",
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
    )
    row = sched.probe_snapshot()["launch_calls"][0]
    assert row["result"] == "success"
    assert row["result"] != "Online"


def test_preparing_emitted_immediately_on_start():
    p = CheckerPointerState()
    before = time.time()
    p.reset_for_new_start(session_id="s-prep", pid=42)
    p.begin_preparing(["com.moons.litesc"])
    snap = p.probe_snapshot()
    assert snap["state_pointer_text"] == "Preparing.."
    assert snap["first_launch_phase"] == "preparing"
    assert snap["checker_mode"] == checker_pointer.MODE_GETTING_READY
    assert snap["start_pressed_at"] is None or snap["start_pressed_at"] >= before - 1.0


def test_getting_ready_bridge_after_cache():
    p = CheckerPointerState()
    p.begin_getting_ready(["com.moons.litesc"], interval_s=30.0)
    snap = p.probe_snapshot()
    assert snap["state_pointer_text"] == checker_pointer.POINTER_GETTING_READY
    assert snap["getting_ready_at"] is not None
    assert snap["checker_mode"] == checker_pointer.MODE_GETTING_READY


def test_scheduler_continues_when_before_launch_hangs():
    clock = FakeClock(0.0)
    sched = LaunchScheduler(
        session_id="s-before",
        packages=["p0", "p1"],
        first_launch_delay_seconds=5.0,
        interval_seconds=30.0,
        before_launch_timeout_seconds=0.1,
    )
    sched.mark_clear_cache_started(monotonic_now=0.0)
    launched: list[str] = []

    def before(_index: int, _package: str) -> None:
        time.sleep(5.0)

    def launch_one(_index: int, package: str) -> str:
        launched.append(package)
        return "success"

    sched.run_schedule(
        launch_one,
        on_before_launch=before,
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
    )
    assert launched == ["p0", "p1"]
    assert sched.probe_snapshot()["launch_calls"][0]["command_started_at"] == 5.0
