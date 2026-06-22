"""Regression tests for probe p-03b5e2269a dead-state priority."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import android
from agent.launcher import RejoinResult
from agent.supervisor import (
    STATUS_DEAD,
    STATUS_IN_GAME,
    STATUS_IN_LOBBY,
    STATUS_JOINING,
    STATUS_LAUNCHING,
    STATUS_ONLINE,
    WatchdogSupervisor,
)


_PKG = "com.moons.litesc"
_PKG2 = "com.moons.litesd"
_URL = "roblox://navigation/share_links?code=abc123&type=Server"


def _entry(pkg: str = _PKG, *, url: str = "", user_id: int = 10957545286) -> dict:
    return {
        "package": pkg,
        "account_username": "TestUser",
        "enabled": True,
        "private_server_url": url,
        "auto_reopen_enabled": True,
        "auto_reconnect_enabled": True,
        "roblox_user_id": user_id,
    }


def _cfg(*, url: str = "") -> dict:
    return {
        "first_setup_completed": True,
        "private_server_url": url,
        "foreground_grace_seconds": 30,
        "supervisor": {"health_check_interval_seconds": 10},
        "roblox_packages": [_entry(url=url)],
    }


def _dead_evidence(**overrides: object) -> dict:
    evidence = {
        "alive": False,
        "running": False,
        "root_running": False,
        "task": False,
        "window": False,
        "surface": False,
        "foreground": False,
        "foreground_package": "com.termux",
        "process_check_attempted": True,
        "process_missing": True,
    }
    evidence.update(overrides)
    return evidence


def _alive_evidence() -> dict:
    return {
        "alive": True,
        "running": True,
        "root_running": False,
        "task": True,
        "window": True,
        "surface": True,
        "foreground": True,
        "foreground_package": _PKG,
        "process_check_attempted": True,
        "process_missing": False,
    }


def _lobby_presence() -> MagicMock:
    presence = MagicMock()
    presence.is_in_game = False
    presence.is_offline = False
    presence.is_unknown = False
    presence.is_lobby = True
    return presence


def _game_presence() -> MagicMock:
    presence = MagicMock()
    presence.is_in_game = True
    presence.is_offline = False
    presence.is_unknown = False
    return presence


class DeadPriorityRegressionTests(unittest.TestCase):
    def _supervisor(self, *, url: str = "", initial_status: dict | None = None) -> WatchdogSupervisor:
        entries = [_entry(_PKG, url=url), _entry(_PKG2, url="")]
        cfg = _cfg(url=url)
        cfg["roblox_packages"] = entries
        return WatchdogSupervisor(entries, cfg, initial_status=initial_status)

    def test_process_missing_api_says_in_lobby_final_state_dead(self) -> None:
        sup = self._supervisor()
        with patch.object(sup, "_fast_alive_evidence", return_value=_dead_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=_lobby_presence()) as fetch_presence:
            state, detail = sup._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_DEAD)
        self.assertEqual(detail["process_running"], "false")
        fetch_presence.assert_not_called()

    def test_process_missing_cache_says_in_lobby_final_state_dead(self) -> None:
        sup = self._supervisor(initial_status={_PKG: STATUS_IN_LOBBY})
        with patch.object(sup, "_fast_alive_evidence", return_value=_dead_evidence()):
            state, _ = sup._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_DEAD)

    def test_process_missing_previous_online_final_state_dead(self) -> None:
        sup = self._supervisor(initial_status={_PKG: STATUS_ONLINE})
        sup._prev_state[_PKG] = STATUS_ONLINE
        with patch.object(sup, "_fast_alive_evidence", return_value=_dead_evidence()):
            state, _ = sup._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_DEAD)

    def test_process_missing_valid_username_user_id_final_state_dead(self) -> None:
        sup = self._supervisor()
        entry = _entry(user_id=10957545286)
        with patch.object(sup, "_fast_alive_evidence", return_value=_dead_evidence()):
            state, detail = sup._detect_package_state(_PKG, entry)
        self.assertEqual(state, STATUS_DEAD)
        self.assertEqual(detail["reason"], "process_not_running")

    def test_process_alive_api_says_lobby_is_in_lobby(self) -> None:
        sup = self._supervisor()
        with patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=_lobby_presence()):
            state, _ = sup._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_IN_LOBBY)

    def test_process_alive_api_says_in_game_can_be_online(self) -> None:
        sup = self._supervisor()
        with patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=_game_presence()):
            state, _ = sup._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_ONLINE)

    def test_dead_state_relaunches_only_that_package(self) -> None:
        sup = self._supervisor(initial_status={_PKG: STATUS_ONLINE, _PKG2: STATUS_ONLINE})
        with patch("agent.supervisor.launch_package_for_current_config", return_value=RejoinResult(True, root_used=True)) as launch, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            sup._handle_state(_PKG, _entry(_PKG), STATUS_DEAD, STATUS_ONLINE, time.time())
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[0]["package"], _PKG)
        self.assertEqual(sup.status_map[_PKG2], STATUS_ONLINE)

    def test_dead_relaunch_uses_private_url_when_configured(self) -> None:
        sup = self._supervisor(url=_URL, initial_status={_PKG: STATUS_ONLINE})
        entry = _entry(_PKG, url=_URL)
        with patch("agent.supervisor.launch_package_for_current_config", return_value=RejoinResult(True, root_used=True)) as launch, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            sup._handle_state(_PKG, entry, STATUS_DEAD, STATUS_ONLINE, time.time())
        self.assertEqual(launch.call_args.args, (entry, sup.cfg, "dead_recovery"))
        self.assertIn(sup.status_map[_PKG], {STATUS_LAUNCHING, STATUS_JOINING})

    def test_dead_relaunch_uses_app_only_when_url_blank(self) -> None:
        sup = self._supervisor(url="", initial_status={_PKG: STATUS_ONLINE})
        entry = _entry(_PKG, url="")
        with patch("agent.supervisor.launch_package_for_current_config", return_value=RejoinResult(True, root_used=True)) as launch, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            sup._handle_state(_PKG, entry, STATUS_DEAD, STATUS_ONLINE, time.time())
        self.assertEqual(launch.call_args.args, (entry, sup.cfg, "dead_recovery"))

    def test_dead_relaunch_respects_separate_package_url(self) -> None:
        sup = self._supervisor(url="", initial_status={_PKG: STATUS_ONLINE})
        sup.cfg["private_url_mode"] = "separate"
        sup.cfg["private_server_url"] = _URL
        entry = _entry(_PKG, url="roblox://navigation/share_links?code=package&type=Server")
        with patch("agent.supervisor.launch_package_for_current_config", return_value=RejoinResult(True, root_used=True)) as launch, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"), \
             patch("agent.supervisor.log_event") as log_event:
            sup._handle_state(_PKG, entry, STATUS_DEAD, STATUS_ONLINE, time.time())
        self.assertEqual(launch.call_args.args, (entry, sup.cfg, "dead_recovery"))
        decision = [call for call in log_event.call_args_list if call.args[2] == "[DENG_REJOIN_RECOVERY_DECISION]"][0]
        self.assertEqual(decision.kwargs["private_url_mode"], "separate")
        self.assertEqual(decision.kwargs["url_config_source"], "package_specific")


class ProcessScanRegressionTests(unittest.TestCase):
    def test_proc_scan_passes_package_as_argv_not_embedded_in_script(self) -> None:
        args = android.process_cmdline_scan_args(_PKG)
        self.assertEqual(args[-1], _PKG)
        self.assertNotIn(_PKG, args[2])
        self.assertIn("target=$1", args[2])


if __name__ == "__main__":
    unittest.main()
