"""Regression tests for probe p-0dac4920dc.

User force-closed com.moons.litesd at ~2:22. Account Dead webhook fired at 2:26
(4 min) with WRONG reason "Roblox left the live server (in-game detection stopped)"
instead of force-close. Account Recovered at 2:36 (10 min after dead).

Root cause: heartbeat_lost fired while the process was already gone (or before
the slow watchdog round reached the package), misclassifying a force-close as
heartbeat silence. PROCESS_MISSING_CONFIRM=2 plus multi-minute watchdog rounds
added further delay.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.constants import DATA_DIR
from agent import rjn_lifecycle_monitor as rlm
from agent.rjn_lifecycle_monitor import (
    RjnLifecycleMonitor,
    STATE_DEAD,
    STATE_DISCONNECTED,
    STATE_ONLINE_CONFIRMED,
    FORCE_CLOSE_HB_SILENCE_SECONDS,
)
from agent.lifecycle_reasons import format_user_friendly_dead_reason


def _monitor(pkg: str = "com.moons.litesd", uid: str = "10105") -> RjnLifecycleMonitor:
    mon = RjnLifecycleMonitor([pkg])
    mon._uid_map = {pkg: uid}
    mon._uid_to_package = {uid: pkg}
    mon._monitor_started_at = time.time() - 600
    row = mon._states[pkg]
    row.uid = uid
    row.pids = ["24614"]
    row.process_exists = True
    return mon


def _online_via_hb(mon: RjnLifecycleMonitor, pkg: str) -> None:
    mon.ingest_push_heartbeat(
        pkg, alive=True, place_id=121864768012064, universe_id=6701277882, at=time.time()
    )
    assert mon._states[pkg].internal_state == STATE_ONLINE_CONFIRMED


class ForceCloseNotHeartbeatLostTests(unittest.TestCase):
    def setUp(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def tearDown(self) -> None:
        (DATA_DIR / "status-monitor-runtime-state.json").unlink(missing_ok=True)

    def test_definitive_absent_marks_dead_on_first_check(self) -> None:
        pkg = "com.pkg.fc_lane"
        mon = _monitor(pkg)
        _online_via_hb(mon, pkg)
        with patch.object(mon, "_process_check", return_value=(False, [], True)), \
             patch.object(mon, "_dump_pkg_logcat", return_value=[]):
            ev = mon.evaluate_package(pkg)
        self.assertEqual(ev.internal_state, STATE_DEAD)
        self.assertIn("process_missing", ev.detail.get("reason_internal", ""))

    def test_heartbeat_loss_suppressed_when_process_gone(self) -> None:
        pkg = "com.pkg.fc_hb"
        mon = _monitor(pkg)
        _online_via_hb(mon, pkg)
        row = mon._states[pkg]
        row.last_ingame_hb_wall_at = time.time() - (FORCE_CLOSE_HB_SILENCE_SECONDS + 30)
        with patch.object(mon, "_process_check", return_value=(False, [], True)), \
             patch.object(mon, "_dump_pkg_logcat", return_value=[]):
            ev = mon.evaluate_package(pkg)
        self.assertEqual(ev.internal_state, STATE_DEAD)
        self.assertNotEqual(row.last_transition_reason, "heartbeat_lost")

    def test_try_mark_force_close_dead_upgrades_disconnect(self) -> None:
        pkg = "com.pkg.fc_up"
        mon = _monitor(pkg)
        _online_via_hb(mon, pkg)
        row = mon._states[pkg]
        row.internal_state = STATE_DISCONNECTED
        row.last_transition_reason = "heartbeat_lost"
        with patch.object(mon, "_process_check", return_value=(False, [], True)):
            self.assertTrue(mon.try_mark_force_close_dead(pkg))
        self.assertEqual(mon._states[pkg].internal_state, STATE_DEAD)
        self.assertEqual(mon._states[pkg].last_transition_reason, "process_missing")

    def test_fast_lane_poll_after_hb_silence(self) -> None:
        pkg = "com.pkg.fc_poll"
        mon = _monitor(pkg)
        _online_via_hb(mon, pkg)
        row = mon._states[pkg]
        row.last_ingame_hb_wall_at = time.time() - (FORCE_CLOSE_HB_SILENCE_SECONDS + 2)
        mon._last_force_close_lane_at = 0.0
        with patch.object(mon, "_process_check", return_value=(False, [], True)):
            mon._poll_force_close_fast_lane()
        self.assertEqual(mon._states[pkg].internal_state, STATE_DEAD)

    def test_force_close_user_friendly_reason(self) -> None:
        self.assertEqual(
            format_user_friendly_dead_reason("process_missing"),
            "Roblox was closed or force-stopped",
        )


if __name__ == "__main__":
    unittest.main()
