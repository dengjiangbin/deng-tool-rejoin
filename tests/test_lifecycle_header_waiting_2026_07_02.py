"""Header/recovery gate and Launching->Waiting lifecycle regressions (2026-07-02)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from agent.checker_pointer import (
    POINTER_RESUME_CHECKING,
    POINTER_START_RECOVERY,
    CheckerPointerState,
)
from agent.checking_system import CheckingSystem
from agent.supervisor import STATUS_LAUNCHING, STATUS_WAITING, WatchdogSupervisor


def test_header_not_resume_when_unrecovered_dead_exists():
    ptr = CheckerPointerState()
    ptr.commit_presence_state("com.moons.litesf", "Dead")
    ptr.sync_dead_packages_into_recovery_queue()
    ptr.recovery_pause_checking = True
    ptr.end_recovery(resume=True)
    label = ptr.header_pointer_text()
    assert label != POINTER_RESUME_CHECKING
    assert label in (POINTER_START_RECOVERY, "Dead Detected")
    snap = ptr.probe_snapshot()
    assert snap["unrecovered_dead_count"] >= 1
    assert snap["header_action_source"] in ("recovery_pending", "unrecovered_dead")


def test_dead_detected_sets_recovery_requested_at():
    ptr = CheckerPointerState()
    ptr.mark_dead_detected("com.moons.litesf", "timeout", "checking", "no_process")
    row = ptr.probe_snapshot()["per_package"]["com.moons.litesf"]
    assert row["recovery_requested_at"] is not None
    assert row["dead_detected_at"] is not None
    assert "com.moons.litesf" in ptr.recovery_queue


def test_self_heal_missing_recovery_request():
    ptr = CheckerPointerState()
    ptr.commit_presence_state("com.moons.litesg", "Dead")
    healed = ptr.self_heal_missing_recovery_requests()
    assert "com.moons.litesg" in healed
    row = ptr.probe_snapshot()["per_package"]["com.moons.litesg"]
    assert row["recovery_requested_at"] is not None


def test_launch_dispatched_enters_waiting_without_online():
    ptr = CheckerPointerState()
    ptr.mark_launch_dispatched("com.moons.litesc", reason="launch_dispatched_waiting_for_monitoring")
    row = ptr.probe_snapshot()["per_package"]["com.moons.litesc"]
    assert row["waiting_entered_at"] is not None
    assert row["waiting_reason"] == "launch_dispatched_waiting_for_monitoring"
    assert ptr.display_state("com.moons.litesc") == "Waiting"


def test_supervisor_waiting_status_persists():
    entries = [{"package": "com.moons.litesc", "enabled": True}]
    cfg = {"supervisor": {}, "roblox_packages": entries}
    sup = WatchdogSupervisor(entries, cfg, initial_status={"com.moons.litesc": STATUS_WAITING})
    assert sup.status_map["com.moons.litesc"] == STATUS_WAITING
    sup._set_lifecycle_status("com.moons.litesc", STATUS_WAITING)
    assert sup.status_map["com.moons.litesc"] == STATUS_WAITING


def test_stale_launching_forced_to_waiting_after_dispatch():
    entries = [{"package": "com.moons.litesc", "enabled": True}]
    cfg = {"supervisor": {}, "roblox_packages": entries}
    sup = WatchdogSupervisor(entries, cfg)
    sup.mark_package_launched("com.moons.litesc")
    sup.status_map["com.moons.litesc"] = STATUS_LAUNCHING
    from agent import checker_pointer

    checker_pointer._INSTANCE = CheckerPointerState()  # noqa: SLF001
    ptr = checker_pointer.get()
    ptr.mark_launch_dispatched(
        "com.moons.litesc", reason="launch_dispatched_waiting_for_checker"
    )
    ptr._packages["com.moons.litesc"].launch_dispatched_at = time.time() - 3.0
    sup.enforce_stale_lifecycle_to_waiting(max_age_s=2.0)
    assert sup.status_map["com.moons.litesc"] == STATUS_WAITING


def test_checker_recovery_called_on_dead():
    ptr = CheckerPointerState()
    from agent import checker_pointer

    checker_pointer._INSTANCE = ptr  # noqa: SLF001
    sup = MagicMock()
    sup._handle_state = MagicMock(
        side_effect=lambda pkg, *_a, **_k: ptr.begin_recovery(pkg)
    )
    cs = CheckingSystem(sup)
    ptr.mark_dead_detected("com.moons.litesf", "timeout", "checking", "")
    cs._process_next_recovery(None)
    assert sup._handle_state.called
    row = ptr.probe_snapshot()["per_package"]["com.moons.litesf"]
    assert row["recovery_started_at"] is not None


def test_only_checking_commits_presence():
    ptr = CheckerPointerState()
    ptr.commit_presence_state("p0", "Online", writer="checking_system")
    assert ptr.committed_presence_state("p0") == "Online"
    ptr.record_invalid_presence_write(
        source="supervisor", package="p0", attempted_state="Online"
    )
    assert ptr.probe_snapshot()["invalid_presence_write_attempts"] == 1
