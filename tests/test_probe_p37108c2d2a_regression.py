"""Regression: probe p-37108c2d2a — launch-gap false dead + wrong-server blocks HB Online."""

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
    RjnLifecycleMonitor,
    STATE_DISCONNECTED,
    STATE_LAUNCHING,
    STATE_ONLINE_CONFIRMED,
)

PKG = "com.moons.litesc"


class LaunchGapFalseDeadTests(unittest.TestCase):
    def test_dead_lane_skips_launching_package_before_process_seen(self) -> None:
        mon = RjnLifecycleMonitor([PKG])
        mon.start_session()
        row = mon._states[PKG]
        row.internal_state = STATE_LAUNCHING
        row.watchdog_active = True
        row.process_seen_since_launch = False
        row.launch_started_at = time.time()

        with patch.object(mon, "_process_check", return_value=(False, [], True)), \
             patch.object(mon, "try_mark_force_close_dead") as mark:
            mon._poll_dead_hot_lane()
        mark.assert_not_called()

    def test_dead_lane_skips_never_launched_packages(self) -> None:
        mon = RjnLifecycleMonitor([PKG, "com.moons.litesd"])
        mon.start_session()
        with patch.object(mon, "_process_check", return_value=(False, [], False)), \
             patch.object(mon, "try_mark_force_close_dead") as mark:
            mon._poll_dead_hot_lane()
        mark.assert_not_called()


class RelaunchWrongServerTests(unittest.TestCase):
    def test_fresh_hb_after_relaunch_not_wrong_server_during_watchdog(self) -> None:
        mon = RjnLifecycleMonitor([PKG])
        mon.start_session()
        row = mon._states[PKG]
        row.process_exists = True
        row.watchdog_active = True
        row.internal_state = STATE_LAUNCHING
        row.launch_started_at = time.time() - 5
        row.anchor_job_id_hash = "old_job_hash"
        row.expected_private_code_hash = "abc123"

        with patch.object(mon, "_process_check", return_value=(True, ["9001"], False)):
            verdict = mon.ingest_push_heartbeat(
                PKG,
                alive=True,
                place_id=121864768012064,
                universe_id=6701277882,
                job_id="new-server-job-id-after-relaunch",
                at=time.time(),
            )
        self.assertEqual(verdict, "online")
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED)
        self.assertNotEqual(row.internal_state, STATE_DISCONNECTED)

    def test_clear_online_evidence_resets_job_anchor(self) -> None:
        mon = RjnLifecycleMonitor([PKG])
        mon.start_session()
        row = mon._states[PKG]
        row.anchor_job_id_hash = "stale"
        row.observed_job_id_hash = "stale"
        row.wrong_server_active = True
        mon._clear_online_evidence(PKG)
        self.assertEqual(row.anchor_job_id_hash, "")
        self.assertEqual(row.observed_job_id_hash, "")
        self.assertFalse(row.wrong_server_active)


if __name__ == "__main__":
    unittest.main()
