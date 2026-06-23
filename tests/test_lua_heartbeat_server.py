"""Regression tests for the local in-game Lua heartbeat server."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError
from urllib.request import urlopen

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.lua_heartbeat_server import LuaHeartbeatServer
from agent.supervisor import STATUS_LAUNCHING, STATUS_NO_HEARTBEAT, STATUS_ONLINE, WatchdogSupervisor

_PKG = "com.roblox.client"


def _entry(pkg: str = _PKG) -> dict:
    return {
        "package": pkg,
        "account_username": "TestUser",
        "roblox_user_id": 12345,
        "enabled": True,
    }


def _cfg() -> dict:
    return {"supervisor": {}, "log_level": "INFO"}


class TestLuaHeartbeatServer(unittest.TestCase):
    def setUp(self) -> None:
        self.server = LuaHeartbeatServer(port=0, allowed_packages={_PKG})
        self.server.start()
        self.addCleanup(self.server.stop)

    def test_get_heartbeat_records_package(self) -> None:
        url = f"http://127.0.0.1:{self.server.port}/heartbeat?package={_PKG}"
        with urlopen(url, timeout=2) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"ok")
        self.assertTrue(self.server.is_fresh(_PKG))
        self.assertTrue(self.server.ever_seen(_PKG))

    def test_invalid_package_rejected(self) -> None:
        url = f"http://127.0.0.1:{self.server.port}/heartbeat?package=not%20valid!"
        with self.assertRaises(URLError):
            urlopen(url, timeout=2)

    def test_stale_heartbeat_not_fresh(self) -> None:
        self.server.record_heartbeat(_PKG)
        with self.server._lock:
            self.server._heartbeats[_PKG] = time.monotonic() - 45.0
        self.assertFalse(self.server.is_fresh(_PKG))

    def test_ping_count_increments_and_resets_window(self) -> None:
        self.server.record_heartbeat(_PKG)
        self.server.record_heartbeat(_PKG)
        self.assertEqual(self.server.ping_count(_PKG), 2)
        self.assertEqual(self.server.ping_count(_PKG, window=False), 2)
        self.server.reset_window_ping_count(_PKG)
        self.assertEqual(self.server.ping_count(_PKG), 0)
        self.assertEqual(self.server.ping_count(_PKG, window=False), 2)
        self.server.record_heartbeat(_PKG)
        self.assertEqual(self.server.ping_count(_PKG), 1)


class TestWatchdogLuaPrimaryDetection(unittest.TestCase):
    def test_local_heartbeat_marks_online_without_api(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_ONLINE})
        sup._last_launched_at[_PKG] = time.monotonic() - (sup.LOADING_GRACE_SECONDS + 5)
        sup._lua_heartbeat_server.record_heartbeat(_PKG)
        offline = MagicMock()
        offline.is_in_game = False
        offline.is_offline = True
        offline.is_lobby = False
        offline.is_unknown = False
        with patch.object(sup, "_fetch_presence", side_effect=AssertionError("api must not run")):
            state, detail = sup._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_ONLINE)
        self.assertEqual(detail["reason"], "local_lua_heartbeat_fresh")

    def test_local_heartbeat_overrides_offline_api(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_LAUNCHING})
        sup._last_launched_at[_PKG] = time.monotonic() - (sup.LOADING_GRACE_SECONDS + 10)
        sup._lua_heartbeat_server.record_heartbeat(_PKG)
        offline = MagicMock()
        offline.is_in_game = False
        offline.is_offline = True
        offline.is_lobby = False
        offline.is_unknown = False
        with patch.object(sup, "_fetch_presence", return_value=offline) as fetch_presence:
            state, detail = sup._detect_package_state(_PKG, _entry())
        fetch_presence.assert_not_called()
        self.assertEqual(state, STATUS_ONLINE)
        self.assertEqual(detail["presence_source"], "local_lua_heartbeat")

    def test_stale_lua_after_grace_is_no_heartbeat_without_api(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_ONLINE})
        sup._last_launched_at[_PKG] = time.monotonic() - (sup.LOADING_GRACE_SECONDS + 5)
        sup._lua_heartbeat_server.record_heartbeat(_PKG)
        sup._lua_heartbeat_server._heartbeats[_PKG] = time.monotonic() - 45.0
        with patch.object(sup, "_fetch_presence", side_effect=AssertionError("api must not run")):
            state, detail = sup._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_NO_HEARTBEAT)
        self.assertEqual(detail["reason"], "local_lua_heartbeat_stale")

    def test_api_fallback_only_when_never_seen_lua_and_past_grace(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_LAUNCHING})
        sup._last_launched_at[_PKG] = time.monotonic() - (sup.LOADING_GRACE_SECONDS + 5)
        offline = MagicMock()
        offline.is_in_game = False
        offline.is_offline = True
        offline.is_lobby = False
        offline.is_unknown = False
        with patch.object(sup, "_fetch_presence", return_value=offline) as fetch_presence:
            state, detail = sup._detect_package_state(_PKG, _entry())
        fetch_presence.assert_called_once()
        self.assertEqual(state, STATUS_NO_HEARTBEAT)
        self.assertEqual(detail["reason"], "presence_offline")

    def test_loading_grace_without_lua_stays_launching(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_LAUNCHING})
        sup.mark_package_launched(_PKG)
        with patch.object(sup, "_fetch_presence", side_effect=AssertionError("api must not run")):
            state, detail = sup._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_LAUNCHING)
        self.assertEqual(detail["reason"], "local_lua_pending_loading_grace")

    def test_mark_package_launched_resets_window_ping_count(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        sup._lua_heartbeat_server.record_heartbeat(_PKG)
        sup._lua_heartbeat_server.record_heartbeat(_PKG)
        self.assertEqual(sup._lua_heartbeat_server.ping_count(_PKG), 2)
        sup.mark_package_launched(_PKG)
        self.assertEqual(sup._lua_heartbeat_server.ping_count(_PKG), 0)


class TestRecoveryMemoryFlush(unittest.TestCase):
    def test_recovery_gate_cycle_runs_gc_collect(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        with patch.object(sup, "_process_alive_fast", return_value=False), \
             patch.object(sup, "_force_stop_target_package", return_value=True), \
             patch.object(sup, "_do_launch", return_value=True), \
             patch("agent.supervisor.time.sleep"), \
             patch("agent.supervisor.gc.collect") as mock_gc, \
             patch("agent.supervisor.log_event"):
            sup._deploy_gate_recovery_cycle(_PKG, _entry(), time.time())
        mock_gc.assert_called_once()

    def test_recovery_force_stop_breath_constant(self) -> None:
        self.assertEqual(WatchdogSupervisor.RECOVERY_FORCE_STOP_BREATH_SECONDS, 1.5)


if __name__ == "__main__":
    unittest.main()
