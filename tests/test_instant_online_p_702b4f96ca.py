"""Regression for probe p-702b4f96ca: instant Online + no stuck Launching/Relaunching."""

from __future__ import annotations

import inspect
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.rjn_lifecycle_monitor import (
    ONLINE_HB_FRESH_SECONDS,
    RjnLifecycleMonitor,
    STATE_LAUNCHING,
    STATE_ONLINE_CONFIRMED,
)
from agent.supervisor import (
    STATUS_LAUNCHING,
    STATUS_ONLINE,
    STATUS_RELAUNCHING,
    WatchdogSupervisor,
)

_PKG = "com.moons.litesc"


class InstantOnlineRegressionTests(unittest.TestCase):
    def test_primary_path_uses_push_fresh_not_hot_lane_online_fresh(self) -> None:
        from agent import supervisor as sup_mod

        src = inspect.getsource(sup_mod.WatchdogSupervisor._detect_android_package_state)
        hot_idx = src.find("if PRIMARY_HOT_LANE_ONLY:")
        block = src[hot_idx:hot_idx + 650]
        self.assertIn("_push_fresh(pkg)", block)
        self.assertIn("retry_confirm_pending_heartbeat", block)
        self.assertIn("_sync_logcat_hb_push_channel", block)
        self.assertNotIn("_hot_lane_online_fresh(pkg)", block)

    def test_retry_confirm_after_process_appears(self) -> None:
        mon = RjnLifecycleMonitor([_PKG])
        mon.start_session()
        mon.note_launch_watchdog(_PKG, relaunch=False)
        row = mon._states[_PKG]
        beat_at = time.time()
        with mon._lock:
            row.last_ingame_hb_at = beat_at
            row.last_ingame_hb_wall_at = time.time()
            row.ingame_hb_ever = True
            row.process_exists = False
            row.internal_state = STATE_LAUNCHING

        with patch.object(mon, "_process_check", return_value=(False, [], False)):
            self.assertFalse(mon.retry_confirm_pending_heartbeat(_PKG))

        with patch.object(mon, "_process_check", return_value=(True, ["1234"], False)):
            self.assertTrue(mon.retry_confirm_pending_heartbeat(_PKG))

        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED)
        self.assertEqual(row.online_evidence_source, "push_heartbeat")

    def test_ingest_retries_confirm_on_duplicate_beat(self) -> None:
        mon = RjnLifecycleMonitor([_PKG])
        mon.start_session()
        mon.note_launch_watchdog(_PKG, relaunch=False)
        beat_at = time.time()
        with patch.object(mon, "_process_check", return_value=(False, [], False)):
            mon.ingest_push_heartbeat(
                _PKG, alive=True, place_id=111, universe_id=222, job_id="j1", at=beat_at
            )
        row = mon._states[_PKG]
        self.assertNotEqual(row.internal_state, STATE_ONLINE_CONFIRMED)

        with patch.object(mon, "_process_check", return_value=(True, ["1234"], False)):
            verdict = mon.ingest_push_heartbeat(
                _PKG, alive=True, place_id=111, universe_id=222, job_id="j1", at=beat_at
            )
        self.assertEqual(verdict, "online")
        self.assertEqual(row.internal_state, STATE_ONLINE_CONFIRMED)

    def test_detect_promotes_launching_to_online_when_confirmed(self) -> None:
        sup = WatchdogSupervisor([{"package": _PKG, "enabled": True}], {})
        sup._all_launches_completed = True
        sup._package_opened.add(_PKG)
        sup._last_launched_at[_PKG] = time.monotonic()
        sup.status_map[_PKG] = STATUS_LAUNCHING

        ev = MagicMock()
        ev.is_online_confirmed = True
        ev.process_exists = True
        ev.internal_state = "ONLINE_CONFIRMED"
        ev.reason = "online because UID-matched push_heartbeat and process exists"
        ev.detail = {
            "online_evidence_source": "push_heartbeat",
            "launch_failed_reason": "",
        }
        ev.failed_checks = []

        with patch.object(sup, "_ingest_push_heartbeat"), patch.object(
            sup, "_sync_logcat_hb_push_channel"
        ), patch.object(sup, "_rjn_monitor") as mon, patch.object(
            sup, "_push_fresh", return_value=True
        ):
            mon.retry_confirm_pending_heartbeat.return_value = True
            mon.evaluate_package.return_value = ev
            mon.try_mark_force_close_dead.return_value = False
            state, _ = sup._detect_android_package_state(_PKG)

        self.assertEqual(state, STATUS_ONLINE)


if __name__ == "__main__":
    unittest.main()
