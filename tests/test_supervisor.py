"""Root-plus-cookie liveness regression tests."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.supervisor import (
    STATUS_NO_HEARTBEAT,
    STATUS_ONLINE,
    STATUS_WAITING,
    WatchdogSupervisor,
)
from agent.commands import _ANSI_RESET, _ANSI_WHITE, _ANSI_YELLOW, _colorize_status


_PKG = "com.moons.litesc"


def _entry() -> dict:
    return {"package": _PKG, "enabled": True, "roblox_user_id": 12345}


class TestRootCookieLiveness(unittest.TestCase):
    def _supervisor(self) -> WatchdogSupervisor:
        supervisor = WatchdogSupervisor([_entry()], {"supervisor": {}})
        supervisor._root_info = MagicMock(available=True, tool="su")
        supervisor._last_launched_at[_PKG] = time.monotonic() - (
            supervisor.LOADING_GRACE_SECONDS + 1
        )
        return supervisor

    def test_missing_root_pidof_is_immediate_no_heartbeat_without_presence(self) -> None:
        supervisor = self._supervisor()
        result = MagicMock(ok=False, stdout="")
        with patch("agent.android.run_root_command", return_value=result) as pidof, \
             patch.object(supervisor, "_fetch_presence") as fetch:
            state, detail = supervisor._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_NO_HEARTBEAT)
        self.assertEqual(detail["reason"], "root_pidof_missing")
        pidof.assert_called_once_with(["pidof", _PKG], root_tool="su", timeout=2)
        fetch.assert_not_called()

    def test_live_root_process_then_cookie_ingame_is_online(self) -> None:
        supervisor = self._supervisor()
        presence = MagicMock(is_in_game=True, is_lobby=False, is_offline=False, is_unknown=False)
        result = MagicMock(ok=True, stdout="1234\n")
        with patch("agent.android.run_root_command", return_value=result), \
             patch.object(supervisor, "_fetch_presence", return_value=presence) as fetch:
            state, detail = supervisor._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_ONLINE)
        self.assertEqual(detail["reason"], "roblox_presence_in_game")
        fetch.assert_called_once_with(_PKG, force_cookie_rescan=False)

    def test_waiting_bypasses_launch_rescan_and_is_directly_evaluated(self) -> None:
        supervisor = self._supervisor()
        supervisor.mark_package_launched(_PKG)
        supervisor._set_status(_PKG, STATUS_WAITING)
        self.assertFalse(supervisor._needs_launching_evaluation(_PKG))
        presence = MagicMock(is_in_game=True, is_lobby=False, is_offline=False, is_unknown=False)
        result = MagicMock(ok=True, stdout="1234\n")
        with patch("agent.android.run_root_command", return_value=result), \
             patch.object(supervisor, "_fetch_presence", return_value=presence):
            state, detail = supervisor._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_ONLINE)
        self.assertEqual(detail["reason"], "roblox_presence_in_game")

    def test_status_colors_follow_launch_and_checking_contract(self) -> None:
        self.assertEqual(
            _colorize_status("Checking"),
            f"{_ANSI_YELLOW}Checking{_ANSI_RESET}",
        )
        self.assertEqual(
            _colorize_status("Launching"),
            f"{_ANSI_WHITE}Launching{_ANSI_RESET}",
        )
        self.assertEqual(
            _colorize_status("Relaunching"),
            f"{_ANSI_WHITE}Relaunching{_ANSI_RESET}",
        )


if __name__ == "__main__":
    unittest.main()
