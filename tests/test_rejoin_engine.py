"""Rejoin engine: dead recovery, presence profiles, probe payload guards."""

from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import probe
from agent.launcher import RejoinResult
from agent.roblox_presence import PresenceResult, PresenceType, map_presence_profile, poll_presence_gate_state
from agent.supervisor import (
    STATUS_CHECKING,
    STATUS_DEAD,
    STATUS_FAILED,
    STATUS_IN_GAME,
    STATUS_IN_LOBBY,
    STATUS_JOINING,
    STATUS_LAUNCHING,
    STATUS_NO_HEARTBEAT,
    STATUS_ONLINE,
    STATUS_REOPENING,
    STATUS_RELAUNCHING,
    WatchdogSupervisor,
)

_PKG = "com.moons.litesc"


def _entry(**overrides: object) -> dict:
    base = {
        "package": _PKG,
        "account_username": "TestUser",
        "enabled": True,
        "roblox_user_id": 10957545286,
        "roblox_cookie": "test-cookie-value",
    }
    base.update(overrides)
    return base


def _cfg() -> dict:
    return {
        "first_setup_completed": True,
        "supervisor": {"health_check_interval_seconds": 10},
        "ram_optimization_enabled": True,
        "ram_check_delay_after_online_sec": 0,
        "ram_trim_interval_sec": 0,
    }


def _dead_evidence() -> dict:
    return {
        "alive": False,
        "running": False,
        "root_running": False,
        "task": False,
        "window": False,
        "surface": False,
        "foreground": False,
        "foreground_package": "",
        "process_check_attempted": True,
        "process_missing": True,
    }


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


class DeadRecoveryTests(unittest.TestCase):
    def test_missing_process_transitions_to_dead(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        with patch.object(sup, "_fast_alive_evidence", return_value=_dead_evidence()):
            state, detail = sup._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_DEAD)
        self.assertEqual(detail["process_running"], "false")
        self.assertEqual(detail["reason"], "process_not_running")

    def test_dead_triggers_reopen(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_ONLINE})
        with patch("agent.supervisor.launch_package_for_current_config", return_value=RejoinResult(True, root_used=True)) as launch, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            sup._handle_state(_PKG, _entry(), STATUS_DEAD, STATUS_ONLINE, time.time())
        launch.assert_called_once()
        self.assertIn(sup.status_map[_PKG], {STATUS_LAUNCHING, STATUS_JOINING, STATUS_REOPENING, STATUS_RELAUNCHING})
        sup = WatchdogSupervisor([_entry()], _cfg(), initial_status={_PKG: STATUS_FAILED})
        with patch.object(sup, "_fast_alive_evidence", return_value=_dead_evidence()), \
             patch("agent.supervisor.launch_package_for_current_config", return_value=RejoinResult(True, root_used=True)) as launch, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            state, _ = sup._detect_package_state(_PKG, _entry())
            self.assertEqual(state, STATUS_DEAD)
            sup._handle_state(_PKG, _entry(), state, STATUS_FAILED, time.time())
        launch.assert_called_once()


class PresenceProfileTests(unittest.TestCase):
    def test_map_presence_profile_labels(self) -> None:
        self.assertEqual(
            map_presence_profile(PresenceResult(user_id=1, presence_type=PresenceType.IN_GAME)),
            "Online",
        )
        self.assertEqual(
            map_presence_profile(PresenceResult(user_id=1, presence_type=PresenceType.ONLINE)),
            "In Lobby",
        )

    def test_in_game_presence_maps_to_online_state(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        game = MagicMock()
        game.is_in_game = True
        game.is_offline = False
        game.is_unknown = False
        game.is_lobby = False
        with patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=game), \
             patch.object(sup, "_check_ram_optimization") as ram_check:
            state, _ = sup._detect_package_state(_PKG, _entry())
            sup._handle_state(_PKG, _entry(), state, STATUS_LAUNCHING, time.time())
        self.assertEqual(state, STATUS_ONLINE)
        self.assertEqual(STATUS_IN_GAME, STATUS_ONLINE)
        ram_check.assert_called_once()

    def test_transient_presence_api_failure_returns_no_heartbeat(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        sup._presence_last_detail[_PKG] = {"roblox_api_status": "rate_limited"}
        with patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=None):
            state, detail = sup._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_NO_HEARTBEAT)
        self.assertIn("presence_api_rate_limited", detail["reason"])

    def test_in_lobby_state_maps_to_no_heartbeat(self) -> None:
        sup = WatchdogSupervisor([_entry()], _cfg())
        lobby = MagicMock()
        lobby.is_in_game = False
        lobby.is_offline = False
        lobby.is_unknown = False
        lobby.is_lobby = True
        with patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=lobby):
            state, _ = sup._detect_package_state(_PKG, _entry())
        self.assertEqual(state, STATUS_NO_HEARTBEAT)
        self.assertNotEqual(state, STATUS_DEAD)

    def test_poll_presence_gate_state_online_on_type_2(self) -> None:
        ingame = PresenceResult(user_id=1, presence_type=PresenceType.IN_GAME)
        with patch("agent.roblox_presence.fetch_presence_one", return_value=ingame):
            self.assertEqual(
                poll_presence_gate_state(1, cookie="cookie", process_alive=True),
                "Online",
            )


class StaggeredLaunchTests(unittest.TestCase):
    def test_launch_stagger_constant_is_30_seconds(self) -> None:
        from agent.supervisor import WatchdogSupervisor
        self.assertEqual(WatchdogSupervisor.LAUNCH_STAGGER_SECONDS, 30)

    def test_presence_timeout_under_15_seconds(self) -> None:
        from agent.roblox_presence import HTTP_TIMEOUT
        from agent.supervisor import WatchdogSupervisor
        self.assertLess(HTTP_TIMEOUT, 15.0)
        self.assertEqual(WatchdogSupervisor.PRESENCE_POLL_TIMEOUT_SECONDS, 14)

    def test_nhb_kill_switch_is_60_seconds(self) -> None:
        from agent.supervisor import WatchdogSupervisor
        self.assertEqual(WatchdogSupervisor.NHB_KILL_SWITCH_SECONDS, 60)


class ProbePayloadTests(unittest.TestCase):
    def test_compact_probe_errors_dedupes_and_caps(self) -> None:
        errors = [{"step": "a", "error": "x"}] * 60
        errors.append({"step": "b", "error": "y"})
        compact = probe.compact_probe_errors(errors)
        self.assertLessEqual(len(compact), probe._PROBE_ERROR_MAX)
        self.assertEqual(compact[0], {"step": "a", "error": "x"})
        self.assertEqual(compact[-1], {"step": "b", "error": "y"})

    def test_clamp_probe_payload_size_under_budget(self) -> None:
        big = {"summary": {"probe_id": "x"}, "errors": [], "blob": "x" * 600_000}
        clamped = probe.clamp_probe_payload_size(big)
        raw = json.dumps(clamped, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(raw), probe._UPLOAD_RAW_MAX_BYTES)


if __name__ == "__main__":
    unittest.main()
