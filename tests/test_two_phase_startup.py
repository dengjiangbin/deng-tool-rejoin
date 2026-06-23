"""Two-phase startup and Joining watchdog evaluation regression tests."""

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

from agent.supervisor import (
    STATUS_CHECKING,
    STATUS_JOINING,
    STATUS_NO_HEARTBEAT,
    STATUS_ONLINE,
    STATUS_PENDING,
    WatchdogSupervisor,
)

_PKG = "com.roblox.client"


def _entry() -> dict:
    return {
        "package": _PKG,
        "account_username": "TestUser",
        "roblox_user_id": 12345,
        "enabled": True,
    }


def _cfg() -> dict:
    return {"supervisor": {}, "log_level": "INFO"}


def _alive_evidence() -> dict:
    return {
        "alive": True,
        "running": True,
        "root_running": False,
        "task": True,
        "window": True,
        "surface": False,
        "foreground": True,
        "process_missing": False,
    }


class TestTwoPhaseStartupSource(unittest.TestCase):
    def test_batch_clear_cache_before_staggered_launch(self) -> None:
        import agent.commands as cmd

        src = inspect.getsource(cmd.cmd_start)
        batch_idx = src.find("batch_clear_cache_begin")
        launch_idx = src.find("PHASE 2: staggered launching")
        self.assertGreater(batch_idx, -1, "batch clear cache phase missing")
        self.assertGreater(launch_idx, batch_idx, "launch must follow batch prep")
        loop_start = src.find("for index, entry in enumerate(entries, start=1):", launch_idx)
        loop_body = src[loop_start:loop_start + 2500]
        self.assertNotIn('phase[package] = "Preparing"', loop_body)
        self.assertNotIn("clear_package_cache_verified", loop_body)

    def test_batch_preparing_is_global(self) -> None:
        import agent.commands as cmd

        src = inspect.getsource(cmd.cmd_start)
        self.assertIn('_set_all_phase("Preparing"', src)
        self.assertIn('_set_all_phase("Clear Cache"', src)


class TestJoiningWatchdogEvaluation(unittest.TestCase):
    def test_joining_transitions_to_checking_then_online(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_JOINING})
        presence = MagicMock()
        presence.is_in_game = True
        presence.is_offline = False
        presence.is_lobby = False
        presence.is_unknown = False
        with patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=presence) as fetch:
            state, _ = sup._evaluate_joining_or_pending(_PKG, _entry())
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.kwargs.get("force_cookie_rescan"), True)
        self.assertEqual(state, STATUS_ONLINE)

    def test_joining_cookie_failure_maps_to_no_heartbeat(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_JOINING})
        sup._presence_last_detail[_PKG] = {
            "roblox_api_status": "skipped",
            "presence_error": "missing_cookie",
        }
        with patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=None):
            state, detail = sup._evaluate_joining_or_pending(_PKG, _entry())
        self.assertEqual(state, STATUS_NO_HEARTBEAT)
        self.assertIn("cookie", detail.get("reason", ""))

    def test_pending_package_is_evaluated(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_PENDING})
        self.assertTrue(sup._needs_joining_evaluation(_PKG))

    def test_checking_set_during_joining_eval(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_JOINING})
        seen: list[str] = []
        original_set = sup._set_status

        def _capture_status(pkg: str, status: str) -> None:
            seen.append(status)
            original_set(pkg, status)

        with patch.object(sup, "_set_status", side_effect=_capture_status), \
             patch.object(
                 sup,
                 "_detect_package_state",
                 return_value=(STATUS_ONLINE, {"reason": "roblox_presence_in_game"}),
             ):
            sup._evaluate_joining_or_pending(_PKG, _entry())
        self.assertIn(STATUS_CHECKING, seen)


class TestCookieScanPaths(unittest.TestCase):
    def test_root_shared_prefs_uses_multiple_globs(self) -> None:
        import agent.roblox_presence as rp

        src = inspect.getsource(rp._root_scan_shared_prefs)
        self.assertIn("root_shared_prefs", src)
        self.assertIn("/data/user/0/", src)
        self.assertIn("list_root_glob", src)


if __name__ == "__main__":
    unittest.main()
