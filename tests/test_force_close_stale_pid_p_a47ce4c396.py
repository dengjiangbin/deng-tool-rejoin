"""Regression: stale PID + stale logcat HB kept force-closed clone Online (p-a47ce4c396).

Probe: com.moons.litese stayed Online with process_running=true after force-close;
cached /proc PID from another clone + logcat dump re-ingesting old DENGRJN_HB lines.
"""

from __future__ import annotations

import sys
import time
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.rjn_lifecycle_monitor import (
    HB_DUMP_MAX_AGE_SECONDS,
    PackageRjnState,
    RjnLifecycleMonitor,
    STATE_ONLINE_CONFIRMED,
)

PKG = "com.moons.litese"
STALE_HB = (
    "06-30 15:30:01.000  10001  10001 I Roblox: DENGRJN_HB|111|111|222|jobA|1"
)


class AuthoritativePidTests(unittest.TestCase):
    def test_stale_proc_pid_ignored_when_cmdline_empty(self) -> None:
        mon = RjnLifecycleMonitor([PKG])
        mon.start_session()
        row = mon._states.setdefault(PKG, PackageRjnState(package=PKG))
        row.pids = ["32312"]  # another clone's PID still in /proc
        row.internal_state = STATE_ONLINE_CONFIRMED
        row.ingame_hb_ever = True
        row.online_since = time.time() - 60

        empty = type("R", (), {"ok": True, "stdout": ""})()
        with patch.object(mon, "_authoritative_package_pids", return_value=[]):
            exists, pids, definitive = mon._process_check(PKG)
        self.assertFalse(exists)
        self.assertTrue(definitive)

    def test_fast_lane_marks_dead_without_hb_silence(self) -> None:
        mon = RjnLifecycleMonitor([PKG])
        mon.start_session()
        row = mon._states[PKG]
        row.internal_state = STATE_ONLINE_CONFIRMED
        row.ingame_hb_ever = True
        row.last_ingame_hb_wall_at = time.time()  # "fresh" HB must not block
        row.online_since = time.time() - 120

        with patch.object(mon, "_process_check", return_value=(False, [], True)), \
             patch.object(mon, "try_mark_force_close_dead", return_value=True) as mark:
            mon._poll_force_close_fast_lane()
        mark.assert_called_once_with(PKG, at=unittest.mock.ANY)


class StaleDumpHeartbeatTests(unittest.TestCase):
    def test_dump_skips_stale_hb_lines(self) -> None:
        mon = RjnLifecycleMonitor([PKG])
        mon.start_session()
        row = mon._states[PKG]
        row.pids = ["10001"]
        row.internal_state = STATE_ONLINE_CONFIRMED
        row.last_ingame_hb_at = time.time() - 300
        row.last_ingame_hb_wall_at = time.time() - 300

        old_epoch = time.time() - 300
        with patch.object(mon, "_dump_pkg_logcat", return_value=[STALE_HB]), \
             patch.object(mon, "_logcat_line_epoch", return_value=old_epoch), \
             patch.object(mon, "_ingest_logcat_heartbeat") as ingest:
            mon._scan_logcat_dump(PKG, time.time(), force=True)
        ingest.assert_not_called()
        self.assertGreater(HB_DUMP_MAX_AGE_SECONDS, 0)

    def test_ingest_does_not_refresh_wall_on_duplicate_beat(self) -> None:
        mon = RjnLifecycleMonitor([PKG])
        mon.start_session()
        seen = time.time() - 5
        mon.ingest_push_heartbeat(
            PKG, alive=True, place_id=111, universe_id=222, job_id="a", at=seen
        )
        row = mon._states[PKG]
        wall_before = row.last_ingame_hb_wall_at
        time.sleep(0.01)
        mon.ingest_push_heartbeat(
            PKG, alive=True, place_id=111, universe_id=222, job_id="a", at=seen
        )
        self.assertEqual(row.last_ingame_hb_wall_at, wall_before)


if __name__ == "__main__":
    unittest.main()
