"""Regression coverage for the frozen runtime-session display behavior."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.commands import format_runtime_compact
from agent.supervisor import STATUS_DEAD, STATUS_ONLINE, STATUS_RELAUNCHING, WatchdogSupervisor


PKG = "com.moons.litesc"
ENTRY = {"package": PKG, "enabled": True, "roblox_user_id": 12345}


class RuntimeTimerFreezeTests(unittest.TestCase):
    def test_compact_runtime_uses_at_most_two_units(self) -> None:
        expected = {
            2: "2s",
            59: "59s",
            60: "1m 0s",
            122: "2m 2s",
            3720: "1H 2m",
            86400: "1D 0H",
            172800: "2D 0H",
            172860: "2D 0H",
        }
        for seconds, rendered in expected.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(format_runtime_compact(seconds), rendered)

    def test_relaunch_online_creates_a_new_ticking_session(self) -> None:
        supervisor = WatchdogSupervisor([ENTRY], {"supervisor": {}})
        supervisor._record_runtime_session_state(PKG, "Launching", STATUS_ONLINE, 100.0)
        self.assertEqual(supervisor._online_start_ts[PKG], 100.0)

        supervisor._record_runtime_session_state(PKG, STATUS_ONLINE, STATUS_DEAD, 700.0)
        self.assertNotIn(PKG, supervisor._online_start_ts)

        supervisor._record_runtime_session_state(PKG, STATUS_RELAUNCHING, STATUS_ONLINE, 800.0)
        self.assertEqual(supervisor._online_start_ts[PKG], 800.0)
        self.assertEqual(format_runtime_compact(803.0 - supervisor._online_start_ts[PKG]), "3s")

    def test_ongoing_online_state_preserves_its_session_start(self) -> None:
        supervisor = WatchdogSupervisor([ENTRY], {"supervisor": {}})
        supervisor._record_runtime_session_state(PKG, "Launching", STATUS_ONLINE, 100.0)
        supervisor._record_runtime_session_state(PKG, STATUS_ONLINE, STATUS_ONLINE, 103.0)
        self.assertEqual(supervisor._online_start_ts[PKG], 100.0)
        self.assertEqual(supervisor._last_online_ts[PKG], 103.0)


if __name__ == "__main__":
    unittest.main()
