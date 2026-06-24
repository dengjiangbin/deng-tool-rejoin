"""Android-local watchdog state regression tests."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.supervisor import STATUS_DEAD, STATUS_ONLINE, STATUS_RELAUNCHING, WatchdogSupervisor


PKG = "com.moons.litesc"
ENTRY = {"package": PKG, "enabled": True, "roblox_user_id": 12345}


def _alive() -> dict[str, bool]:
    return {
        "running": True, "root_running": False, "window": False,
        "surface": False, "foreground": False, "strict_alive": True,
    }


class AndroidLocalSupervisorTests(unittest.TestCase):
    def _supervisor(self) -> WatchdogSupervisor:
        return WatchdogSupervisor([ENTRY], {"supervisor": {}})

    def test_alive_package_is_online_without_cookie_or_heartbeat(self) -> None:
        sup = self._supervisor()
        with patch("agent.android.package_installed", return_value=True), \
             patch("agent.android.get_package_alive_evidence", return_value=_alive()), \
             patch.object(sup, "_fetch_presence", side_effect=AssertionError("web presence used")):
            state, detail = sup._detect_package_state(PKG, ENTRY)
        self.assertEqual(state, STATUS_ONLINE)
        self.assertEqual(detail["reason"], "android_alive_evidence")

    def test_dead_package_clears_only_target_cache_then_relaunches(self) -> None:
        sup = self._supervisor()
        cache = {"success": True, "method": "root_rm_cache", "error": ""}
        with patch("agent.android.clear_package_cache_verified", return_value=cache) as clear, \
             patch.object(sup, "_do_launch", return_value=True), \
             patch("agent.supervisor.log_event"):
            gated = sup._handle_state(PKG, ENTRY, STATUS_DEAD, STATUS_ONLINE, time.time())
        self.assertTrue(gated)
        clear.assert_called_once_with(PKG)
        self.assertNotEqual(clear.call_args.args[0], "com.termux")
        self.assertEqual(sup.status_map[PKG], STATUS_RELAUNCHING)

    def test_relaunch_lock_prevents_a_second_targeted_relaunch(self) -> None:
        sup = self._supervisor()
        sup._relaunch_inflight.add(PKG)
        with patch("agent.android.clear_package_cache_verified") as clear, \
             patch.object(sup, "_do_launch") as launch:
            self.assertFalse(sup._handle_state(PKG, ENTRY, STATUS_DEAD, STATUS_RELAUNCHING, time.time()))
        clear.assert_not_called()
        launch.assert_not_called()

    def test_android_evidence_after_relaunch_marks_online(self) -> None:
        sup = self._supervisor()
        sup._relaunch_inflight.add(PKG)
        sup._relaunch_verify_until[PKG] = time.monotonic() + 30
        with patch("agent.android.package_installed", return_value=True), \
             patch("agent.android.get_package_alive_evidence", return_value=_alive()):
            state, _detail = sup._detect_package_state(PKG, ENTRY)
        self.assertEqual(state, STATUS_ONLINE)
        self.assertNotIn(PKG, sup._relaunch_inflight)

    def test_missing_android_evidence_is_dead(self) -> None:
        sup = self._supervisor()
        with patch("agent.android.package_installed", return_value=True), \
             patch("agent.android.get_package_alive_evidence", return_value={"strict_alive": False}):
            state, _detail = sup._detect_package_state(PKG, ENTRY)
        self.assertEqual(state, STATUS_DEAD)


if __name__ == "__main__":
    unittest.main()
