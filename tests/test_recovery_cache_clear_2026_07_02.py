"""Regression tests for probe p-2606bd7609.

Covers the bounded recovery cache-clear state machine, Getting Ready render
ordering, stale-session reset, cross-process liveness/heartbeat, and the new
probe fields — all so recovery can never freeze at Clearing Cache and the
probe never reports the checker idle while a Start session is running.
"""

from __future__ import annotations

import time

import pytest

from agent import checker_pointer
from agent.checker_pointer import CheckerPointerState


# ── Getting Ready render ordering ──────────────────────────────────────
def test_getting_ready_is_first_pointer_after_start():
    p = CheckerPointerState()
    p.reset_for_new_start(session_id="s1", pid=999)
    p.begin_getting_ready(["a", "b"], interval_s=30)
    assert p.pointer_text() == checker_pointer.POINTER_GETTING_READY
    # First recorded pointer history entry must be Getting Ready.
    snap = p.probe_snapshot()
    assert snap["last_ui_pointer_history"][0] == checker_pointer.POINTER_GETTING_READY


def test_opening_never_precedes_getting_ready():
    p = CheckerPointerState()
    p.reset_for_new_start(session_id="s1", pid=1)
    p.begin_getting_ready(["a"], interval_s=30)
    p.begin_opening("a")
    hist = p.probe_snapshot()["last_ui_pointer_history"]
    assert hist.index(checker_pointer.POINTER_GETTING_READY) < hist.index(
        checker_pointer.POINTER_OPENING
    )


# ── Stale-session reset ────────────────────────────────────────────────
def test_reset_for_new_start_clears_stale_recovery_and_cache_clear():
    p = CheckerPointerState()
    # Simulate a prior session that ended mid-recovery with cache-clear data.
    p.reset_for_new_start(session_id="old", pid=111)
    p.begin_recovery("a", reason="force_stop")
    p.begin_cache_clear(command_kind="su_find_delete")
    p.record_cache_clear_result(status="timeout_continue_relaunch", timed_out=True,
                                exit_code=124, error="deadline")
    # New Start must wipe all of it and stamp a fresh session.
    p.reset_for_new_start(session_id="new", pid=222)
    snap = p.probe_snapshot()
    assert snap["session_id"] == "new"
    assert snap["checker_pid"] == 222
    assert snap["recovery_in_progress"] is False
    assert snap["recovery_stage"] is None
    assert snap["cache_clear_status"] is None
    assert snap["cache_clear_timed_out"] is False
    assert snap["last_ui_pointer_history"] == []


# ── Bounded recovery stage machine ─────────────────────────────────────
def test_recovery_stage_has_deadline_and_elapsed():
    p = CheckerPointerState()
    p.reset_for_new_start(session_id="s", pid=1)
    p.begin_recovery("a")
    p.set_recovery_stage("clear_cache")
    snap = p.probe_snapshot()
    assert snap["recovery_stage"] == "clear_cache"
    assert snap["recovery_stage_deadline_s"] == 30.0
    assert snap["recovery_stage_elapsed_s"] is not None


def test_recovery_stage_expired_after_deadline():
    p = CheckerPointerState()
    p.reset_for_new_start(session_id="s", pid=1)
    p.begin_recovery("a")
    p.set_recovery_stage("reopening", deadline_s=0.05)
    time.sleep(0.08)
    assert p.recovery_stage_expired() is True


def test_end_recovery_releases_lock():
    p = CheckerPointerState()
    p.reset_for_new_start(session_id="s", pid=1)
    p.begin_recovery("a")
    p.set_recovery_stage("clear_cache")
    p.end_recovery()
    snap = p.probe_snapshot()
    assert snap["recovery_in_progress"] is False
    assert snap["active_recovery_package"] is None
    assert snap["recovery_stage_started_at"] is None


def test_end_recovery_failed_marks_stage():
    p = CheckerPointerState()
    p.reset_for_new_start(session_id="s", pid=1)
    p.begin_recovery("a")
    p.end_recovery(failed=True, reason="relaunch_failed")
    snap = p.probe_snapshot()
    assert snap["recovery_in_progress"] is False
    assert snap["recovery_stage"] == "recovery_failed"


# ── Bounded cache-clear wrapper ────────────────────────────────────────
def test_bounded_cache_clear_timeout_advances(monkeypatch):
    from agent import cache_clear_phases

    # Force the underlying clear to hang forever; the bounded wrapper must
    # still return (advancing recovery) via its hard deadline.
    def _hang(*_a, **_k):
        time.sleep(60)
        return {"success": True}

    monkeypatch.setattr(
        cache_clear_phases.android, "clear_package_cache_recovery", _hang
    )
    p = CheckerPointerState()
    p.reset_for_new_start(session_id="s", pid=1)
    started = time.monotonic()
    result = cache_clear_phases.run_recovery_cache_clear_bounded(
        "com.moons.litesc", checker_pointer=p, deadline_s=0.5,
    )
    elapsed = time.monotonic() - started
    assert elapsed < 10.0  # did NOT hang for 60s
    assert result["cache_clear_status"] == "timeout_continue_relaunch"
    assert result["cache_clear_timed_out"] is True
    assert result["cache_clear_exit_code"] == 124
    assert p.cache_clear_status == "timeout_continue_relaunch"


def test_bounded_cache_clear_success_advances(monkeypatch):
    from agent import cache_clear_phases

    monkeypatch.setattr(
        cache_clear_phases.android,
        "clear_package_cache_recovery",
        lambda *_a, **_k: {"success": True, "command_kind": "su_find_delete:su"},
    )
    p = CheckerPointerState()
    p.reset_for_new_start(session_id="s", pid=1)
    result = cache_clear_phases.run_recovery_cache_clear_bounded(
        "com.moons.litesc", checker_pointer=p, deadline_s=5,
    )
    assert result["cache_clear_status"] == "success"
    assert result["cache_clear_exit_code"] == 0


def test_bounded_cache_clear_failure_advances(monkeypatch):
    from agent import cache_clear_phases

    monkeypatch.setattr(
        cache_clear_phases.android,
        "clear_package_cache_recovery",
        lambda *_a, **_k: {"success": False, "error": "clear_failed",
                           "command_kind": "su_find_delete:su"},
    )
    p = CheckerPointerState()
    p.reset_for_new_start(session_id="s", pid=1)
    result = cache_clear_phases.run_recovery_cache_clear_bounded(
        "com.moons.litesc", checker_pointer=p, deadline_s=5,
    )
    assert result["cache_clear_status"] == "failed_continue_relaunch"
    assert result["cache_clear_exit_code"] == 1


def test_bounded_cache_clear_never_raises_on_worker_exception(monkeypatch):
    from agent import cache_clear_phases

    def _boom(*_a, **_k):
        raise RuntimeError("root shell exploded")

    monkeypatch.setattr(
        cache_clear_phases.android, "clear_package_cache_recovery", _boom
    )
    result = cache_clear_phases.run_recovery_cache_clear_bounded(
        "com.moons.litesc", deadline_s=5,
    )
    assert result["cache_clear_status"] == "failed_continue_relaunch"
    assert "root shell exploded" in str(result["cache_clear_error"])


# ── Liveness / heartbeat / probe fields ────────────────────────────────
def test_heartbeat_keeps_checker_alive_field():
    p = CheckerPointerState()
    p.reset_for_new_start(session_id="s", pid=1)
    before = p.probe_snapshot()["checker_last_heartbeat_at"]
    time.sleep(0.01)
    p.heartbeat()
    after = p.probe_snapshot()["checker_last_heartbeat_at"]
    assert after >= before
    assert p.probe_snapshot()["checker_loop_alive"] is True


def test_probe_snapshot_exposes_all_recovery_fields():
    p = CheckerPointerState()
    p.reset_for_new_start(session_id="s", pid=42)
    snap = p.probe_snapshot()
    for key in (
        "session_id", "checker_pid", "start_pressed_at",
        "checker_last_heartbeat_at", "checker_dead_reason",
        "recovery_stage_started_at", "recovery_stage_elapsed_s",
        "recovery_stage_deadline_s", "cache_clear_started_at",
        "cache_clear_finished_at", "cache_clear_duration_ms",
        "cache_clear_command_kind", "cache_clear_exit_code",
        "cache_clear_timed_out", "cache_clear_error", "cache_clear_status",
        "last_state_transition", "last_ui_pointer_history",
    ):
        assert key in snap, key


def test_stale_state_file_reports_dead_checker(tmp_path, monkeypatch):
    # A stale state file must surface a dead checker (not idle) so the probe
    # explains why the checker stopped heartbeating.
    from agent import checker_pointer as cp

    state_path = tmp_path / "focused-checker-state.json"
    monkeypatch.setattr(cp, "_state_file_path", lambda: state_path)
    # Force the live singleton to look idle.
    monkeypatch.setattr(cp, "get", lambda: CheckerPointerState())
    import json as _json

    state_path.write_text(_json.dumps({
        "written_at": time.time() - 120.0,  # 2 min old → stale
        "pid": 777,
        "snapshot": {
            "checker_mode": "checking", "checker_loop_alive": True,
            "session_id": "old-sess", "checker_pid": 777,
        },
    }), encoding="utf-8")
    snap = cp.probe_snapshot()
    assert snap["_source"] == "state_file_stale"
    assert snap["checker_loop_alive"] is False
    assert "state_file_stale" in str(snap["checker_dead_reason"])
    assert snap["checker_state_file_age_s"] >= 100


# ── Bounded first-launch (probe p-5dacb6657a) ──────────────────────────
def test_bounded_launch_returns_result_when_fast():
    from agent.commands import run_callable_with_deadline

    result, timed_out = run_callable_with_deadline(lambda: "launched", 5.0)
    assert result == "launched"
    assert timed_out is False


def test_bounded_launch_times_out_and_advances():
    from agent.commands import run_callable_with_deadline

    def _hang():
        time.sleep(30)
        return "should_not_reach"

    started = time.monotonic()
    result, timed_out = run_callable_with_deadline(_hang, 0.4)
    elapsed = time.monotonic() - started
    assert timed_out is True
    assert result is None
    assert elapsed < 5.0  # advanced quickly; did NOT wait 30s


def test_bounded_launch_propagates_exception():
    from agent.commands import run_callable_with_deadline

    def _boom():
        raise RuntimeError("launch blew up")

    with pytest.raises(RuntimeError, match="launch blew up"):
        run_callable_with_deadline(_boom, 5.0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
