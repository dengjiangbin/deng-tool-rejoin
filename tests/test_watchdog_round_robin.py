"""Round-robin watchdog pacing and Termux-safe force-stop regression tests."""

from __future__ import annotations

import inspect
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import android
from agent.supervisor import (
    STATUS_CHECKING,
    STATUS_LAUNCHING,
    STATUS_ONLINE,
    STATUS_PENDING,
    WatchdogSupervisor,
)

_PKG = "com.roblox.client"
_PKG2 = "com.roblox.client2"


def _entry(pkg: str = _PKG) -> dict:
    return {
        "package": pkg,
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


class TestRoundRobinWatchdog(unittest.TestCase):
    def test_package_round_robin_constant_is_10_seconds(self) -> None:
        self.assertEqual(WatchdogSupervisor.PACKAGE_ROUND_ROBIN_SECONDS, 10)

    def test_watchdog_loop_source_uses_round_robin_pause(self) -> None:
        src = inspect.getsource(WatchdogSupervisor._run_watchdog_loop)
        self.assertIn("PACKAGE_ROUND_ROBIN_SECONDS", src)
        self.assertIn("_interruptible_sleep", src)
        self.assertIn("WATCHDOG_ROUND_ROBIN_PAUSE", src)

    def test_sequential_packages_sleep_between_evaluations(self) -> None:
        sup = WatchdogSupervisor(
            [_entry(_PKG), _entry(_PKG2)],
            _cfg(),
            initial_status={_PKG: STATUS_LAUNCHING, _PKG2: STATUS_PENDING},
        )
        sleep_calls: list[float] = []
        presence = MagicMock()
        presence.is_in_game = True
        presence.is_offline = False
        presence.is_lobby = False
        presence.is_unknown = False

        def _record_sleep(seconds: float) -> None:
            sleep_calls.append(float(seconds))

        with patch.object(sup, "_interruptible_sleep", side_effect=_record_sleep), \
             patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=presence), \
             patch("agent.supervisor.db.insert_event"), \
             patch("agent.supervisor.db.insert_heartbeat"), \
             patch("agent.supervisor.log_event"):
            sup.start_daemon(display_interval=0.05)

            def _stop_soon() -> None:
                time.sleep(0.15)
                sup.stop("test")

            threading.Thread(target=_stop_soon, daemon=True).start()
            if sup._watchdog_thread is not None:
                sup._watchdog_thread.join(timeout=5.0)

        self.assertTrue(
            any(s == 10.0 for s in sleep_calls),
            f"expected 10.0s round-robin pause between packages, got {sleep_calls}",
        )

    def test_launching_package_sets_checking_before_eval(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_LAUNCHING})
        seen: list[str] = []
        original_set = sup._set_status

        def _track(pkg: str, status: str) -> None:
            seen.append(status)
            original_set(pkg, status)

        with patch.object(sup, "_set_status", side_effect=_track), \
             patch.object(
                 sup,
                 "_detect_package_state",
                 return_value=(STATUS_ONLINE, {"reason": "roblox_presence_in_game"}),
             ):
            sup._evaluate_launching_or_pending(_PKG, _entry())
        self.assertIn(STATUS_CHECKING, seen)


class TestJoiningStateRemoved(unittest.TestCase):
    def test_supervisor_has_no_joining_constant(self) -> None:
        import agent.supervisor as sup_mod

        self.assertFalse(hasattr(sup_mod, "STATUS_JOINING"))

    def test_set_status_maps_joining_to_launching(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        sup._set_status(_PKG, "Joining")
        self.assertEqual(sup.status_map[_PKG], STATUS_LAUNCHING)

    def test_commands_start_has_no_joining_phase(self) -> None:
        import agent.commands as cmd

        src = inspect.getsource(cmd.cmd_start)
        self.assertNotIn('phase[package] = "Joining"', src)
        self.assertNotIn("_STATUS_JOINING", src)


class TestTermuxSafeForceStop(unittest.TestCase):
    def test_termux_is_force_stop_protected(self) -> None:
        self.assertTrue(android._is_force_stop_protected("com.termux"))

    def test_force_stop_package_skips_termux(self) -> None:
        with patch("agent.android.run_root_command") as mock_run:
            result = android.force_stop_package("com.termux")
        mock_run.assert_not_called()
        self.assertFalse(result.ok)

    def test_watchdog_kill_switch_uses_targeted_force_stop(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        sup._nhb_since[_PKG] = time.monotonic() - (sup.NHB_KILL_SWITCH_SECONDS + 5)
        with patch.object(sup, "_force_stop_target_package", return_value=True) as mock_kill, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"), \
             patch("time.sleep"):
            sup._handle_state(_PKG, _entry(), "No Heartbeat", "Online", time.time())
        mock_kill.assert_called_once_with(_PKG)
        self.assertEqual(sup.status_map.get(_PKG), "Dead")

    def test_force_stop_target_blocks_termux(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        sup.packages = ["com.termux"]
        with patch("agent.android.force_stop_package") as mock_stop:
            ok = sup._force_stop_target_package("com.termux")
        self.assertFalse(ok)
        mock_stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
