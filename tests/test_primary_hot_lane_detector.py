"""Primary hot-lane detector: Online = fresh DENGRJN_HB, Dead = process missing."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.rjn_lifecycle_monitor import (
    ONLINE_HB_FRESH_SECONDS,
    PRIMARY_HOT_LANE_ONLY,
    PackageRjnState,
    RjnLifecycleMonitor,
    STATE_DEAD,
    STATE_ONLINE_CONFIRMED,
)

PKG_A = "com.moons.lite"
PKG_B = "com.moons.litese"
PKGS_SIX = [f"com.moons.clone{i}" for i in range(1, 7)]
HB_LINE_A = "06-30 12:00:01.000  10001  5555 I Roblox: DENGRJN_HB|111|111|222|jobA|1"
HB_LINE_B = "06-30 12:00:01.000  10002  6666 I Roblox: DENGRJN_HB|111|111|222|jobB|1"


class FreshHeartbeatOnlineTests(unittest.TestCase):
    def test_fresh_hb_marks_online_immediately(self) -> None:
        mon = RjnLifecycleMonitor([PKG_A])
        mon.start_session()
        mon._uid_map[PKG_A] = "10001"
        mon._uid_to_package["10001"] = PKG_A
        mon._states[PKG_A].process_exists = True
        with patch.object(mon, "_process_check", return_value=(True, ["5555"], False)):
            verdict = mon.ingest_push_heartbeat(
                PKG_A,
                alive=True,
                place_id=111,
                universe_id=222,
                job_id="jobA",
                pid="5555",
                uid="10001",
            )
        self.assertEqual(verdict, "online")
        row = mon._states[PKG_A]
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED)
        self.assertEqual(row.online_evidence_source, "push_heartbeat")

    def test_hb_for_package_a_cannot_mark_package_b_online(self) -> None:
        mon = RjnLifecycleMonitor([PKG_A, PKG_B])
        mon.start_session()
        mon._uid_map[PKG_A] = "10001"
        mon._uid_map[PKG_B] = "10002"
        mon._uid_to_package["10001"] = PKG_A
        mon._uid_to_package["10002"] = PKG_B
        mon._pid_to_package["5555"] = PKG_A
        mon._pid_to_package["6666"] = PKG_B
        mon._states[PKG_A].process_exists = True
        with patch.object(mon, "_process_check", return_value=(True, ["5555"], False)):
            mon.ingest_push_heartbeat(
                PKG_A,
                alive=True,
                place_id=111,
                pid="5555",
                uid="10001",
            )
        row_b = mon._states[PKG_B]
        self.assertNotEqual(row_b.internal_state, STATE_ONLINE_CONFIRMED)

    def test_gamejoinloadtime_does_not_confirm_online_in_hot_lane(self) -> None:
        self.assertTrue(PRIMARY_HOT_LANE_ONLY)
        mon = RjnLifecycleMonitor([PKG_A])
        mon.start_session()
        row = mon._states[PKG_A]
        with mon._lock:
            mon._confirm_online_evidence(PKG_A, time.time(), source="gamejoinloadtime")
        self.assertNotEqual(row.internal_state, STATE_ONLINE_CONFIRMED)


class ForceCloseDeadTests(unittest.TestCase):
    def test_force_close_marks_dead_without_round_robin(self) -> None:
        mon = RjnLifecycleMonitor([PKG_A])
        mon.start_session()
        row = mon._states[PKG_A]
        row.internal_state = STATE_ONLINE_CONFIRMED
        row.ingame_hb_ever = True
        row.last_ingame_hb_wall_at = time.time()
        row.online_since = time.time() - 60

        with patch.object(mon, "_process_check", return_value=(False, [], True)):
            mon._poll_dead_hot_lane()
        self.assertEqual(row.internal_state, STATE_DEAD)
        self.assertEqual(row.last_transition_reason, "process_missing")

    def test_stale_hb_after_force_close_cannot_restore_online(self) -> None:
        mon = RjnLifecycleMonitor([PKG_A])
        mon.start_session()
        row = mon._states[PKG_A]
        row.internal_state = STATE_DEAD
        row.last_process_gone_at = time.time() - 1
        row.last_ingame_hb_at = 0.0
        row.last_ingame_hb_wall_at = 0.0
        row.ingame_hb_ever = False

        stale_at = time.time() - 2
        with patch.object(mon, "_process_check", return_value=(False, [], True)):
            verdict = mon.ingest_push_heartbeat(
                PKG_A,
                alive=True,
                place_id=111,
                at=stale_at,
            )
        self.assertEqual(verdict, "")
        self.assertEqual(row.internal_state, STATE_DEAD)

    def test_process_missing_overrides_stale_heartbeat(self) -> None:
        mon = RjnLifecycleMonitor([PKG_A])
        mon.start_session()
        row = mon._states[PKG_A]
        row.internal_state = STATE_ONLINE_CONFIRMED
        row.ingame_hb_ever = True
        row.online_evidence_source = "push_heartbeat"
        row.last_ingame_hb_wall_at = time.time() - 30
        row.last_positive_online_evidence_at = time.time() - 30
        row.process_exists = True

        with patch.object(mon, "_process_check", return_value=(False, [], True)):
            ev = mon.evaluate_package(PKG_A, hot_lane_only=True)
        self.assertFalse(ev.is_online_confirmed)
        self.assertEqual(row.internal_state, STATE_DEAD)
        self.assertEqual(row.last_transition_reason, "process_missing")


class SixCloneParallelTests(unittest.TestCase):
    def test_sixth_package_force_close_detected_immediately(self) -> None:
        mon = RjnLifecycleMonitor(PKGS_SIX)
        mon.start_session()
        target = PKGS_SIX[5]
        row = mon._states[target]
        row.internal_state = STATE_ONLINE_CONFIRMED
        row.ingame_hb_ever = True
        row.online_since = time.time() - 120

        def _proc_check(pkg: str) -> tuple[bool, list[str], bool]:
            if pkg == target:
                return False, [], True
            return True, ["9999"], False

        with patch.object(mon, "_process_check", side_effect=_proc_check):
            mon._poll_dead_hot_lane()
        self.assertEqual(row.internal_state, STATE_DEAD)
        self.assertEqual(row.last_transition_reason, "process_missing")
        for other in PKGS_SIX[:5]:
            self.assertNotEqual(mon._states[other].internal_state, STATE_DEAD)


class WebhookReasonTests(unittest.TestCase):
    def test_force_close_reason_is_process_missing_not_heartbeat_lost(self) -> None:
        mon = RjnLifecycleMonitor([PKG_A])
        mon.start_session()
        row = mon._states[PKG_A]
        row.internal_state = STATE_ONLINE_CONFIRMED
        row.ingame_hb_ever = True
        row.last_ingame_hb_wall_at = time.time()

        with patch.object(mon, "_process_check", return_value=(False, [], True)):
            mon.try_mark_force_close_dead(PKG_A)
        self.assertEqual(row.last_transition_reason, "process_missing")
        self.assertNotEqual(row.last_transition_reason, "heartbeat_lost")


class DumpDemotedTests(unittest.TestCase):
    def test_dump_hb_ignored_when_hot_lane_only(self) -> None:
        self.assertTrue(PRIMARY_HOT_LANE_ONLY)
        mon = RjnLifecycleMonitor([PKG_A])
        mon.start_session()
        row = mon._states[PKG_A]
        row.pids = ["5555"]
        old_epoch = time.time() - 5
        with patch.object(mon, "_dump_pkg_logcat", return_value=[HB_LINE_A]), \
             patch.object(mon, "_logcat_line_epoch", return_value=old_epoch), \
             patch.object(mon, "_ingest_logcat_heartbeat") as ingest:
            mon._scan_logcat_dump(PKG_A, time.time(), force=True)
        ingest.assert_not_called()

    def test_evaluate_skips_heartbeat_loss_in_hot_lane(self) -> None:
        mon = RjnLifecycleMonitor([PKG_A])
        mon.start_session()
        row = mon._states[PKG_A]
        row.internal_state = STATE_ONLINE_CONFIRMED
        row.ingame_hb_ever = True
        row.last_ingame_hb_at = time.time() - 999
        row.last_ingame_hb_wall_at = time.time() - 999
        row.online_evidence_source = "push_heartbeat"
        row.last_positive_online_evidence_at = time.time() - 999

        with patch.object(mon, "_process_check", return_value=(True, ["5555"], False)):
            ev = mon.evaluate_package(PKG_A, hot_lane_only=True)
        self.assertNotEqual(row.last_transition_reason, "heartbeat_lost")
        self.assertFalse(ev.is_online_confirmed)


class DeadLaneIsolationTests(unittest.TestCase):
    def test_dead_lane_marks_only_missing_process_package(self) -> None:
        mon = RjnLifecycleMonitor([PKG_A, PKG_B])
        mon.start_session()
        for pkg in (PKG_A, PKG_B):
            row = mon._states[pkg]
            row.internal_state = STATE_ONLINE_CONFIRMED
            row.ingame_hb_ever = True
            row.online_since = time.time() - 60

        def _proc(pkg: str) -> tuple[bool, list[str], bool]:
            if pkg == PKG_A:
                return False, [], True
            return True, ["9001"], False

        with patch.object(mon, "_process_check", side_effect=_proc):
            mon._poll_dead_hot_lane()
        self.assertEqual(mon._states[PKG_A].internal_state, STATE_DEAD)
        self.assertEqual(mon._states[PKG_B].internal_state, STATE_ONLINE_CONFIRMED)


if __name__ == "__main__":
    unittest.main()
