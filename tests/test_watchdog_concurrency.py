"""Watchdog daemon concurrency: background thread vs main-thread launch."""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.supervisor import (
    STATUS_LAUNCHING,
    STATUS_NO_HEARTBEAT,
    STATUS_ONLINE,
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
    return {"supervisor": {"health_check_interval_seconds": 10}, "log_level": "INFO"}


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


class TestWatchdogDaemonThread(unittest.TestCase):
    def test_start_daemon_returns_immediately(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_LAUNCHING})
        rounds = {"n": 0}

        def _slow_loop(**_kwargs) -> None:
            while not sup.stop_event.is_set() and rounds["n"] < 3:
                rounds["n"] += 1
                time.sleep(0.05)

        with patch.object(sup, "_run_watchdog_loop", side_effect=_slow_loop):
            t0 = time.monotonic()
            sup.start_daemon()
            elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 0.2, "start_daemon must not block the caller")
        self.assertTrue(sup.watchdog_thread_alive())
        sup.stop()
        if sup._watchdog_thread is not None:
            sup._watchdog_thread.join(timeout=2.0)

    def test_watchdog_runs_while_main_thread_sleeps(self) -> None:
        sup = WatchdogSupervisor(
            [_entry(_PKG), _entry(_PKG2)],
            _cfg(),
            initial_status={_PKG: STATUS_LAUNCHING, _PKG2: STATUS_LAUNCHING},
        )
        transitions: list[str] = []
        presence = MagicMock()
        presence.is_in_game = True
        presence.is_offline = False
        presence.is_lobby = False
        presence.is_unknown = False

        original_set = sup._set_status

        def _track_set(pkg: str, status: str) -> None:
            transitions.append(f"{pkg}:{status}")
            original_set(pkg, status)

        with patch.object(sup, "_set_status", side_effect=_track_set), \
             patch.object(sup, "_process_alive_fast", return_value=True), \
             patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=presence), \
             patch("agent.supervisor.db.insert_event"), \
             patch("agent.supervisor.db.insert_heartbeat"):
            sup.start_daemon(display_interval=0.05)
            time.sleep(0.35)
            sup.stop()
            if sup._watchdog_thread is not None:
                sup._watchdog_thread.join(timeout=3.0)
        joined = [t for t in transitions if STATUS_ONLINE in t or "Checking" in t]
        self.assertTrue(joined, f"expected state transitions during parallel sleep, got {transitions}")

    def test_nhb_kill_switch_uses_monotonic_clock(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        sup._nhb_since[_PKG] = time.monotonic() - (sup.NHB_KILL_SWITCH_SECONDS + 5)
        with patch.object(sup, "_force_stop_target_package", return_value=True) as mock_stop, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"), \
             patch("time.sleep"):
            sup._handle_state(_PKG, _entry(), STATUS_NO_HEARTBEAT, STATUS_ONLINE, time.time())
        mock_stop.assert_called_once_with(_PKG)
        self.assertEqual(sup.status_map.get(_PKG), "Dead")

    def test_nhb_kill_switch_not_blocked_by_grace(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        sup._grace_until[_PKG] = time.time() + 300
        sup._nhb_since[_PKG] = time.monotonic() - (sup.NHB_KILL_SWITCH_SECONDS + 1)
        with patch.object(sup, "_force_stop_target_package", return_value=True) as mock_stop, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"), \
             patch("time.sleep"):
            sup._handle_state(_PKG, _entry(), STATUS_NO_HEARTBEAT, STATUS_NO_HEARTBEAT, time.time())
        mock_stop.assert_called_once()

    def test_bootstrap_before_stagger_in_commands_source(self) -> None:
        import inspect
        import agent.commands as cmd

        src = inspect.getsource(cmd.cmd_start)
        boot_idx = src.find("Bootstrap watchdog daemon BEFORE staggered launch")
        phase2_idx = src.find("PHASE 2: staggered launching")
        self.assertGreater(boot_idx, -1)
        self.assertGreater(phase2_idx, boot_idx)
        self.assertIn("start_daemon", src[boot_idx:phase2_idx])


if __name__ == "__main__":
    unittest.main()
