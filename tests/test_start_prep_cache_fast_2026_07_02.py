"""Fast Start prep / Clear Cache / first-launch scheduler regressions (2026-07-02)."""

from __future__ import annotations

import threading
import time

import pytest

from agent import cache_clear_phases, start_lifecycle
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


def test_prepare_optional_diagnostics_do_not_block_clear_cache(monkeypatch):
    """Cloud memory sweep is async; prep finish is immediate after force-stop."""
    start_lifecycle.reset_for_start(["p0"])
    start_lifecycle.mark_prepare_started()
    blocked = threading.Event()

    def _slow_cloud(*_a, **_k):
        blocked.wait(timeout=2.0)
        return {"cooldown_skipped": False, "stopped": []}

    monkeypatch.setattr("agent.android.optimize_cloud_phone_memory", _slow_cloud)
    # Simulate cmd_start: prep marks finished before cloud thread completes.
    start_lifecycle.mark_prepare_finished()
    snap = start_lifecycle.probe_snapshot()
    assert snap["prepare_duration_ms"] is not None
    assert snap["prepare_duration_ms"] <= 5000.0
    assert not blocked.is_set()


def test_clear_cache_success_continues_immediately(monkeypatch):
    monkeypatch.setattr(
        cache_clear_phases,
        "run_start_mass_cache_clear",
        lambda packages, **_k: {p: "Cleared" for p in packages},
    )
    started = time.monotonic()
    out = cache_clear_phases.run_start_mass_cache_clear_bounded(
        ["com.moons.litesc"],
        deadline_s=5.0,
    )
    elapsed_ms = (time.monotonic() - started) * 1000.0
    assert out["cache_clear_status"] == "success"
    assert out["cache_clear_duration_ms"] <= 5000.0
    assert elapsed_ms <= 5500.0


def test_clear_cache_timeout_continues_immediately(monkeypatch):
    def _hang(*_a, **_k):
        time.sleep(60)
        return {}

    monkeypatch.setattr(cache_clear_phases, "run_start_mass_cache_clear", _hang)
    started = time.monotonic()
    out = cache_clear_phases.run_start_mass_cache_clear_bounded(
        ["com.moons.litesc"],
        deadline_s=0.35,
    )
    elapsed_ms = (time.monotonic() - started) * 1000.0
    assert out["cache_clear_timeout"] is True
    assert elapsed_ms <= 5000.0


def test_stale_clear_cache_ui_write_ignored():
    start_lifecycle.reset_for_start(["p0"])
    v_new = start_lifecycle.bump_ui_phase_version(phase="Opening", source="first_launch")
    assert start_lifecycle.try_write_table_phase("Clear Cache", v_new - 1) is False
    snap = start_lifecycle.probe_snapshot()
    assert snap["table_state_phase"] == "Opening"
    assert snap["stale_ui_write_ignored_count"] >= 1


def test_first_package_launches_immediately_after_getting_ready_bridge():
    clock = FakeClock(100.0)
    sched = LaunchScheduler(
        session_id="s-fast",
        packages=["p0"],
        first_launch_delay_seconds=0.0,
        interval_seconds=30.0,
    )
    sched.mark_clear_cache_started(monotonic_now=clock.monotonic())
    clock.advance(3.5)
    sched.record_clear_cache_finished(
        finished_at=clock.monotonic(),
        duration_ms=3500.0,
        reanchor_launches=True,
    )
    clock.advance(0.8)
    sched.reanchor_launches_from_getting_ready_finished(finished_at=clock.monotonic())

    def launch_one(_index: int, _package: str) -> str:
        return "success"

    sched.run_schedule(
        launch_one,
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
    )
    snap = sched.probe_snapshot()
    assert snap["first_launch_delay_from_clear_cache_finish_ms"] == 800.0
    assert snap["launch_anchor_mode"] == "getting_ready_finish"


def test_five_packages_launch_every_thirty_seconds():
    clock = FakeClock(0.0)
    packages = ["c", "d", "f", "g", "h"]
    sched = LaunchScheduler(
        session_id="s-five",
        packages=packages,
        first_launch_delay_seconds=1.0,
        interval_seconds=30.0,
    )
    sched.mark_clear_cache_started(monotonic_now=0.0)
    clock.advance(3.5)
    sched.record_clear_cache_finished(finished_at=clock.monotonic(), reanchor_launches=True)
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
    assert [t for _, t in fired] == [4.5, 34.5, 64.5, 94.5, 124.5]
    snap = sched.probe_snapshot()
    assert snap["launch_interval_observed_ms"] == [30000.0] * 4
    assert snap["all_packages_launched_at"] is not None
    assert snap["checking_system_started_at"] is not None


def test_checker_idle_until_all_packages_launched():
    p = CheckerPointerState()
    p.reset_for_new_start(session_id="s-idle", pid=1)
    p.set_checker_idle_during_first_launch(reason="first_launch_scheduler_active")
    snap = p.probe_snapshot()
    assert snap["checker_status"] == "idle_until_all_packages_launched"
    assert snap["checker_idle_reason"] == "first_launch_scheduler_active"
    assert snap["checker_loop_alive"] is True


def test_probe_launch_calls_from_scheduler_state():
    clock = FakeClock(0.0)
    sched = LaunchScheduler(session_id="s-probe", packages=["p0", "p1"])
    sched.mark_clear_cache_started(monotonic_now=0.0)
    sched.record_clear_cache_finished(finished_at=3.5, reanchor_launches=True)

    def launch_one(_index: int, package: str) -> str:
        return "success"

    sched.run_schedule(
        launch_one,
        monotonic_fn=clock.monotonic,
        sleep_fn=clock.sleep,
    )
    calls = sched.probe_snapshot()["launch_calls"]
    assert len(calls) == 2
    assert calls[0]["package"] == "p0"
    assert calls[0]["result"] == "success"
    assert calls[1]["package"] == "p1"
