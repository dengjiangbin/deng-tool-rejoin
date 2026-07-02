"""Tests for the focused round-robin checker + checker pointer state.

Covers task spec sections: first-launch interval, round robin, dead
detection, recovery lock, and UI pointer-text transitions.  Uses a
virtual clock so 60s intervals and 10s focus windows run instantly and
deterministically.
"""

from __future__ import annotations

import threading

import pytest

from agent import checker_pointer
from agent.checker_pointer import CheckerPointerState
from agent.focused_checker import (
    CheckerDeps,
    DeadEvidence,
    FocusedRoundRobinChecker,
    OnlineEvidence,
    OUTCOME_DEAD,
    OUTCOME_NO_HEARTBEAT,
    OUTCOME_ONLINE_EARLY,
)


class VirtualClock:
    """Deterministic clock: virtual time only advances when sleep is called."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, float(seconds))


def _make_deps(packages, clock, **overrides):
    launches: list[tuple[str, float]] = []
    cache_clears: list[str] = []

    def launch(pkg):
        launches.append((pkg, clock.now()))
        return overrides.get("launch_result", lambda p: True)(pkg)

    def clear_cache(pkg):
        cache_clears.append(pkg)
        cc = overrides.get("clear_cache_hook")
        if cc is not None:
            cc(pkg)

    deps = CheckerDeps(
        packages=list(packages),
        clock=clock.now,
        sleep=clock.sleep,
        should_stop=overrides.get("should_stop", lambda: False),
        launch=launch,
        online_evidence=overrides.get("online_evidence", lambda p: None),
        dead_evidence=overrides.get("dead_evidence", lambda p: None),
        clear_cache=clear_cache,
        pointer=overrides.get("pointer") or CheckerPointerState(),
        first_launch_interval_s=overrides.get("first_launch_interval_s", 30.0),
        focus_window_s=overrides.get("focus_window_s", 10.0),
        focus_poll_s=overrides.get("focus_poll_s", 0.5),
        no_heartbeat_limit=overrides.get("no_heartbeat_limit", 7),
        recovery_wait_online_s=overrides.get("recovery_wait_online_s", 90.0),
    )
    deps._launches = launches  # type: ignore[attr-defined]
    deps._cache_clears = cache_clears  # type: ignore[attr-defined]
    return deps


# ── First launch interval ─────────────────────────────────────────────
def test_checker_deps_default_interval_is_30s():
    # Requirement 7: first-time launch interval changed 60s → 30s.
    deps = CheckerDeps(
        packages=["p1"],
        clock=lambda: 0.0,
        sleep=lambda s: None,
        should_stop=lambda: False,
        launch=lambda p: True,
        online_evidence=lambda p: None,
        dead_evidence=lambda p: None,
        clear_cache=lambda p: None,
        pointer=CheckerPointerState(),
    )
    assert deps.first_launch_interval_s == 30.0


def test_first_launch_second_package_after_30s_without_online():
    clock = VirtualClock()
    deps = _make_deps(["p1", "p2"], clock)
    checker = FocusedRoundRobinChecker(deps)
    checker.run_first_launch()

    launches = deps._launches  # type: ignore[attr-defined]
    assert [p for p, _ in launches] == ["p1", "p2"]
    assert launches[0][1] == pytest.approx(0.0, abs=1.0)
    # p2 launches 30s after p1 even though p1 never became Online.
    assert launches[1][1] == pytest.approx(30.0, abs=1.0)


def test_first_launch_all_ok_starts_checking_immediately_after_last():
    clock = VirtualClock()
    deps = _make_deps(["p1", "p2"], clock)
    checker = FocusedRoundRobinChecker(deps)
    checker.run_first_launch()
    # p1 at 0, wait 30, p2 at 30; all ok → no extra 30s wait.
    assert clock.now() == pytest.approx(30.0, abs=1.0)


def test_first_launch_checking_starts_30s_after_last_supposed_launch_on_crash():
    clock = VirtualClock()

    def launch_result(pkg):
        return pkg != "p2"  # last package crashes on launch

    deps = _make_deps(["p1", "p2"], clock, launch_result=launch_result)
    checker = FocusedRoundRobinChecker(deps)
    checker.run_first_launch()
    # p1 at 0, wait 30, p2 (crash) at 30, then wait 30 more → ~60.
    assert clock.now() == pytest.approx(60.0, abs=1.0)


def test_first_launch_duplicate_start_guard():
    clock = VirtualClock()
    deps = _make_deps(["p1"], clock)
    checker = FocusedRoundRobinChecker(deps)
    checker.run_first_launch()
    n = len(deps._launches)  # type: ignore[attr-defined]
    checker.run_first_launch()  # second call must be a no-op
    assert len(deps._launches) == n  # type: ignore[attr-defined]
    assert deps.pointer.duplicate_loop_guard_status == "first_launch_reentry_blocked"


# ── Round robin ───────────────────────────────────────────────────────
def test_focus_moves_early_when_online_confirmed():
    clock = VirtualClock()
    deps = _make_deps(
        ["p1"], clock, online_evidence=lambda p: OnlineEvidence("push_heartbeat", 100.0)
    )
    checker = FocusedRoundRobinChecker(deps)
    outcome = checker.focus_once("p1", 1)
    assert outcome == OUTCOME_ONLINE_EARLY
    assert clock.now() < 10.0  # returned before the full window


def test_focus_waits_full_window_when_no_evidence():
    clock = VirtualClock()
    deps = _make_deps(["p1"], clock)
    checker = FocusedRoundRobinChecker(deps)
    outcome = checker.focus_once("p1", 1)
    assert outcome == OUTCOME_NO_HEARTBEAT
    assert clock.now() == pytest.approx(10.0, abs=0.75)


def test_no_heartbeat_increments_once_per_completed_window():
    clock = VirtualClock()
    deps = _make_deps(["p1", "p2"], clock)
    checker = FocusedRoundRobinChecker(deps)
    checker.run_checking_round()
    assert deps.pointer.get_no_heartbeat("p1") == 1
    assert deps.pointer.get_no_heartbeat("p2") == 1
    checker.run_checking_round()
    assert deps.pointer.get_no_heartbeat("p1") == 2


def test_online_resets_no_heartbeat_count():
    clock = VirtualClock()
    state = {"online": False}
    deps = _make_deps(
        ["p1"],
        clock,
        online_evidence=lambda p: OnlineEvidence("push_heartbeat", 10.0) if state["online"] else None,
    )
    checker = FocusedRoundRobinChecker(deps)
    checker.run_checking_round()
    assert deps.pointer.get_no_heartbeat("p1") == 1
    state["online"] = True
    checker.run_checking_round()
    assert deps.pointer.get_no_heartbeat("p1") == 0


def test_focus_timer_resets_per_package():
    clock = VirtualClock()
    deps = _make_deps(["p1", "p2"], clock)
    checker = FocusedRoundRobinChecker(deps)
    checker.focus_once("p1", 1)
    start_p1 = deps.pointer.focus_started_at
    checker.focus_once("p2", 2)
    start_p2 = deps.pointer.focus_started_at
    assert start_p2 is not None and start_p1 is not None
    assert start_p2 > start_p1  # each focus restarts the timer


# ── Dead detection ────────────────────────────────────────────────────
def test_logcat_fatal_triggers_immediate_recovery():
    clock = VirtualClock()
    calls = {"clear": 0}

    def dead_ev(pkg):
        return DeadEvidence("crash", "logcat", "FATAL EXCEPTION") if pkg == "p1" else None

    def clear_hook(pkg):
        calls["clear"] += 1

    deps = _make_deps(
        ["p1"], clock, dead_evidence=dead_ev, clear_cache_hook=clear_hook
    )
    checker = FocusedRoundRobinChecker(deps)
    checker.run_checking_round()
    assert deps.pointer.last_dead_source == "logcat"
    assert calls["clear"] == 1  # recovery ran


def test_force_stop_triggers_recovery():
    clock = VirtualClock()
    deps = _make_deps(
        ["p1"], clock, dead_evidence=lambda p: DeadEvidence("force_stop", "logcat", "Force stopping")
    )
    checker = FocusedRoundRobinChecker(deps)
    outcome = checker.focus_once("p1", 1)
    assert outcome == OUTCOME_DEAD
    assert deps.pointer.last_dead_reason == "force_stop"


def test_pid_vanished_source_propagates():
    clock = VirtualClock()
    deps = _make_deps(
        ["p1"], clock, dead_evidence=lambda p: DeadEvidence("process_missing", "process_poll", "pid_gone")
    )
    checker = FocusedRoundRobinChecker(deps)
    assert checker.focus_once("p1", 1) == OUTCOME_DEAD
    assert deps.pointer.last_dead_source == "process_poll"


def test_unrelated_package_crash_does_not_affect_focused_package():
    clock = VirtualClock()

    def dead_ev(pkg):
        return DeadEvidence("crash", "logcat") if pkg == "other" else None

    deps = _make_deps(["p1"], clock, dead_evidence=dead_ev)
    checker = FocusedRoundRobinChecker(deps)
    outcome = checker.focus_once("p1", 1)
    assert outcome == OUTCOME_NO_HEARTBEAT  # p1 unaffected by "other" crash


def test_no_heartbeat_limit_triggers_recovery():
    clock = VirtualClock()
    calls = {"clear": 0}
    deps = _make_deps(
        ["p1"], clock, no_heartbeat_limit=7, clear_cache_hook=lambda p: calls.__setitem__("clear", calls["clear"] + 1)
    )
    checker = FocusedRoundRobinChecker(deps)
    for _ in range(6):
        checker.run_checking_round()
    assert calls["clear"] == 0  # not yet
    checker.run_checking_round()  # 7th
    assert calls["clear"] == 1


# ── Recovery lock ─────────────────────────────────────────────────────
def test_recovery_sets_and_clears_in_progress():
    clock = VirtualClock()
    seen = {}

    def clear_hook(pkg):
        seen["in_progress_during"] = deps.pointer.recovery_in_progress
        seen["active_pkg"] = deps.pointer.active_recovery_package

    deps = _make_deps(["p1"], clock, clear_cache_hook=clear_hook)
    checker = FocusedRoundRobinChecker(deps)
    checker.run_recovery("p1")
    assert seen["in_progress_during"] is True
    assert seen["active_pkg"] == "p1"
    assert deps.pointer.recovery_in_progress is False


def test_recovery_only_touches_target_package():
    clock = VirtualClock()
    deps = _make_deps(["p1", "p2"], clock)
    checker = FocusedRoundRobinChecker(deps)
    checker.run_recovery("p2")
    assert deps._cache_clears == ["p2"]  # type: ignore[attr-defined]
    assert [p for p, _ in deps._launches] == ["p2"]  # type: ignore[attr-defined]


def test_second_dead_cannot_interrupt_active_recovery():
    clock = VirtualClock()
    started = threading.Event()
    release = threading.Event()

    def clear_hook(pkg):
        started.set()
        release.wait(2.0)

    deps = _make_deps(["p1", "p2"], clock, clear_cache_hook=clear_hook)
    checker = FocusedRoundRobinChecker(deps)

    t = threading.Thread(target=lambda: checker.run_recovery("p1"))
    t.start()
    assert started.wait(2.0)
    # While p1 recovery holds the lock, a second recovery must be refused.
    assert checker.run_recovery("p2") is False
    release.set()
    t.join(3.0)
    assert deps.pointer.recovery_in_progress is False


def test_recovery_resumes_checking_after_online():
    clock = VirtualClock()
    deps = _make_deps(
        ["p1"], clock, online_evidence=lambda p: OnlineEvidence("gamejoinloadtime", 5.0)
    )
    checker = FocusedRoundRobinChecker(deps)
    ok = checker.run_recovery("p1")
    assert ok is True
    assert deps.pointer.checker_mode == checker_pointer.MODE_RESUME_CHECKING
    assert deps.pointer.recovery_in_progress is False


# ── UI pointer-text transitions ───────────────────────────────────────
def test_pointer_text_transitions():
    p = CheckerPointerState()
    p.begin_getting_ready(["p1"])
    assert p.state_pointer_text == checker_pointer.POINTER_GETTING_READY
    p.begin_opening("p1")
    assert p.state_pointer_text == checker_pointer.POINTER_OPENING
    p.begin_focus("p1", 1, now=0.0)
    assert p.state_pointer_text == checker_pointer.POINTER_CHECKING
    p.update_focus_timer(3)
    assert p.state_pointer_text == "3s"
    p.mark_dead_detected("p1", "crash", "logcat", "FATAL")
    assert p.state_pointer_text == checker_pointer.POINTER_DEAD_DETECTED
    p.begin_recovery("p1")
    assert p.state_pointer_text == checker_pointer.POINTER_START_RECOVERY
    p.set_recovery_stage("clear_cache")
    assert p.state_pointer_text == checker_pointer.POINTER_CLEARING_CACHE
    p.set_recovery_stage("reopening")
    assert p.state_pointer_text == checker_pointer.POINTER_REOPENING
    p.set_recovery_stage("relaunching")
    assert p.state_pointer_text == checker_pointer.POINTER_RELAUNCHING
    p.set_recovery_stage("online")
    assert p.state_pointer_text == checker_pointer.POINTER_ONLINE
    p.resume_checking()
    assert p.state_pointer_text == checker_pointer.POINTER_RESUME_CHECKING


def test_table_two_row_state_header_only():
    from agent import commands

    rows = [(1, "com.moons.litesc", "user1", "Online")]
    with_ptr = commands.build_start_table(rows, use_color=False, pointer_text="Getting Ready..")
    no_ptr = commands.build_start_table(rows, use_color=False)
    lp = with_ptr.splitlines()
    ln = no_ptr.splitlines()
    # Pointer adds exactly one extra header row (the State-column second row).
    assert len(lp) == len(ln) + 1
    # The pointer text appears exactly once, in the header region.
    assert sum("Getting Ready" in line for line in lp) == 1
    # Legacy render (no pointer) must be unchanged — no second header row.
    assert not any("Getting Ready" in line for line in ln)


def test_table_pointer_timer_text():
    from agent import commands

    rows = [(1, "com.moons.litesc", "user1", "Online")]
    out = commands.build_start_table(rows, use_color=False, pointer_text="3s")
    assert any(line.strip().endswith("3s │") or " 3s " in line for line in out.splitlines())


def test_getting_ready_pointer_appears_before_opening():
    # Requirement 3: after 3.Start the FIRST visible pointer text must be
    # "Getting Ready..", and only then may it become "Opening..".
    p = CheckerPointerState()
    seen: list[str] = []
    p.begin_getting_ready(["p1", "p2"])
    seen.append(p.pointer_text())
    p.begin_opening("p1")
    seen.append(p.pointer_text())
    assert seen[0] == checker_pointer.POINTER_GETTING_READY
    assert seen[1] == checker_pointer.POINTER_OPENING
    assert seen.index(checker_pointer.POINTER_GETTING_READY) < seen.index(
        checker_pointer.POINTER_OPENING
    )


def test_header_row_is_centered_and_yellow_bold():
    from agent import commands

    rows = [(1, "com.moons.litesc", "user1", "Online")]
    out = commands.build_start_table(rows, use_color=True, pointer_text="Checking..")
    header_line = next(ln for ln in out.splitlines() if "State" in ln and "Package" in ln)
    # Yellow-bold ANSI wraps each header label.
    assert commands._ANSI_YELLOW in header_line
    # Centered: the "State" label has leading padding (not flush-left in its cell).
    plain = commands._ANSI_RE.sub("", header_line)
    state_cell = plain.split("│")[4]  # #, Package, Username, State, (trailing)
    assert state_cell != state_cell.lstrip(), "State header should be centered, not left-biased"


def test_pointer_second_row_is_bordered_box():
    from agent import commands

    rows = [(1, "com.moons.litesc", "user1", "Online")]
    out = commands.build_start_table(rows, use_color=False, pointer_text="Checking..")
    assert "[ Checking.. ]" in out


def test_pointer_checking_is_pink():
    from agent import commands

    rows = [(1, "com.moons.litesc", "user1", "Online")]
    out = commands.build_start_table(rows, use_color=True, pointer_text="Checking..")
    box_line = next(ln for ln in out.splitlines() if "Checking.." in ln)
    assert commands._ANSI_PINK in box_line


def test_checking_status_colorized_pink():
    from agent import commands

    colored = commands._colorize_status("Checking", use_color=True)
    assert commands._ANSI_PINK in colored


def test_only_active_focus_package_marked_checking():
    # Requirement 4: only the focused package's row shows "Checking"; the
    # previously focused package reverts to its last real state.
    p = CheckerPointerState()
    p.begin_getting_ready(["p1", "p2"])
    p.begin_focus("p1", 1, now=0.0)
    assert p.display_state("p1") == "Checking"
    p.set_real_state("p1", "Online")
    p.begin_focus("p2", 2, now=1.0)
    assert p.display_state("p2") == "Checking"
    # p1 is no longer "Checking" — shows its resolved real state.
    assert p.display_state("p1") == "Online"
    # Exactly one package is the active focus at a time.
    assert p.active_focus_package == "p2"


def test_active_row_changes_from_checking_to_result():
    p = CheckerPointerState()
    p.begin_focus("p1", 1, now=0.0)
    assert p.display_state("p1") == "Checking"
    p.set_real_state("p1", "No Heartbeat")
    assert p.display_state("p1") == "No Heartbeat"
    p.mark_dead_detected("p1", "crash", "logcat", "FATAL")
    assert p.display_state("p1") == "Dead"


def test_state_file_roundtrip_reflects_running_checker(tmp_path, monkeypatch):
    # Root-cause fix: the probe process reads the persisted state file so it
    # never reports "idle" while a Start session is live in another process.
    import agent.checker_pointer as cp

    state_file = tmp_path / "focused-checker-state.json"
    monkeypatch.setattr(cp, "_state_file_path", lambda: state_file)

    p = cp.CheckerPointerState()
    p.enable_persistence()
    p.set_loop_health(checker_loop_alive=True, logcat_reader_alive=True)
    p.begin_getting_ready(["p1"])
    p.begin_focus("p1", 1, now=100.0)

    disk = cp.read_state_file()
    assert disk is not None
    assert disk["checker_loop_alive"] is True
    assert disk["active_focus_package"] == "p1"
    assert disk["checker_mode"] == cp.MODE_CHECKING
    assert disk["_source"] == "state_file"


def test_read_state_file_rejects_stale(tmp_path, monkeypatch):
    import json as _json
    import agent.checker_pointer as cp

    state_file = tmp_path / "focused-checker-state.json"
    state_file.write_text(
        _json.dumps({"written_at": 0.0, "snapshot": {"checker_mode": "checking"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cp, "_state_file_path", lambda: state_file)
    # written_at=0 is ancient → treated as stale and ignored.
    assert cp.read_state_file(max_age_s=20.0) is None


def test_probe_snapshot_has_all_required_fields():
    p = CheckerPointerState()
    p.begin_getting_ready(["p1"])
    p.begin_focus("p1", 1, now=0.0)
    p.increment_no_heartbeat("p1")
    snap = p.probe_snapshot(now=5.0)
    for key in (
        "checker_mode",
        "active_focus_package",
        "active_focus_index",
        "focus_started_at",
        "focus_elapsed_s",
        "focus_window_s",
        "state_pointer_text",
        "first_launch_phase",
        "first_launch_next_package_at",
        "first_launch_started_packages",
        "first_launch_supposedly_launched_packages",
        "recovery_in_progress",
        "active_recovery_package",
        "recovery_stage",
        "last_dead_reason",
        "last_dead_source",
        "last_dead_evidence",
        "logcat_reader_alive",
        "checker_loop_alive",
        "duplicate_loop_guard_status",
        "per_package",
    ):
        assert key in snap, key
    pp = snap["per_package"]["p1"]
    assert pp["consecutive_no_heartbeat_focus_count"] == 1
    # New per-package probe fields (requirement: display_state / last_real_state).
    assert "display_state" in pp
    assert "last_real_state" in pp
    assert pp["display_state"] == "Checking"  # p1 is the active focus
    assert snap["focus_elapsed_s"] == pytest.approx(5.0, abs=0.01)


def test_force_close_race_state_file_reports_enabled(tmp_path, monkeypatch):
    # Root-cause fix for p-aeafb00ced: force_close_race.enabled must be true
    # during an active session even when read from a separate probe process.
    import json as _json
    import agent.force_close_race as fcr

    state_file = tmp_path / "force-close-detector-state.json"
    monkeypatch.setattr(fcr, "RACE_STATE_PATH", state_file)
    state_file.write_text(
        _json.dumps(
            {
                "written_at": __import__("time").time(),
                "pid": 4321,
                "snapshot": {"enabled": True, "packages": {"p1": {"status": "tracking"}}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fcr, "get_active_force_close_race_detector", lambda: None)
    snap = fcr.probe_force_close_race_snapshot()
    assert snap["enabled"] is True
    assert snap["source"] == "state_file"
    assert "p1" in snap["packages"]
