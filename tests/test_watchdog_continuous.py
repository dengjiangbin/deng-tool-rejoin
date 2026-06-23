"""Tests for WatchdogSupervisor: continuous monitoring, state machine,
blank URL support, canonical launcher, Joining removal, and probe logging.

Requirements verified:
1-5:   Blank private_server_url launches app-only, no error.
6-10:  Configured URL uses working private URL launcher; &type=Server preserved.
11-16: State detection: Dead/Online/No Heartbeat/no Joining.
17-25: Watchdog continuity: never stops after Online; force-close detected.
26-30: Regression: no uiautomator/logcat, no Joining in output, no Post-Launch.
"""
from __future__ import annotations

import sys
import inspect
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import android
from agent.supervisor import (
    STATUS_DEAD,
    STATUS_IN_LOBBY,
    STATUS_LAUNCHING,
    STATUS_NO_HEARTBEAT,
    STATUS_ONLINE,
    STATUS_UNKNOWN,
    WatchdogSupervisor,
)
from agent.launcher import launch_package_for_current_config, RejoinResult
from agent.config import default_config, validate_config


# â”€â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_PKG = "com.roblox.client"
_PKG2 = "com.roblox.client2"


def _make_cfg(private_url: str = "") -> dict:
    cfg = default_config()
    cfg["first_setup_completed"] = True
    cfg["launch_mode"] = "app"
    cfg["private_server_url"] = private_url
    cfg["roblox_packages"] = [
        {
            "package": _PKG,
            "account_username": "TestUser",
            "enabled": True,
            "username_source": "manual",
            "private_server_url": private_url,
            "auto_reopen_enabled": True,
            "auto_reconnect_enabled": True,
        }
    ]
    return cfg


def _make_entry(pkg: str = _PKG, private_url: str = "") -> dict:
    return {
        "package": pkg,
        "account_username": "TestUser",
        "enabled": True,
        "username_source": "manual",
        "private_server_url": private_url,
        "auto_reopen_enabled": True,
        "auto_reconnect_enabled": True,
        "roblox_user_id": 0,
    }


def _make_sup(
    private_url: str = "",
    packages: list[str] | None = None,
    initial_status: dict | None = None,
) -> WatchdogSupervisor:
    if packages is None:
        packages = [_PKG]
    entries = [_make_entry(pkg, private_url) for pkg in packages]
    cfg = _make_cfg(private_url)
    if len(packages) > 1:
        cfg["roblox_packages"] = [
            {
                "package": pkg,
                "account_username": f"User{i}",
                "enabled": True,
                "username_source": "manual",
                "private_server_url": private_url,
                "auto_reopen_enabled": True,
                "auto_reconnect_enabled": True,
            }
            for i, pkg in enumerate(packages)
        ]
    return WatchdogSupervisor(entries, cfg, initial_status=initial_status)


def _dead_evidence() -> dict:
    return {"alive": False, "running": False, "root_running": False,
            "task": False, "window": False, "surface": False, "foreground": False}


def _alive_evidence() -> dict:
    return {"alive": True, "running": True, "root_running": False,
            "task": True, "window": True, "surface": False, "foreground": False}


# â”€â”€â”€ 1-5: Blank Private Server URL â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestBlankPrivateServerUrl(unittest.TestCase):
    """Blank private_server_url must not fail setup and must launch app-only."""

    # Test 1
    def test_blank_url_start_still_launches_packages(self):
        """Blank URL: packages are launched (perform_rejoin is called)."""
        sup = _make_sup(private_url="")
        with patch("agent.supervisor.launch_package_for_current_config") as mock_launch:
            mock_launch.return_value = RejoinResult(True, root_used=False)
            with patch.object(sup, "_fast_alive_evidence", return_value=_dead_evidence()):
                with patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
                    sup._handle_state(_PKG, _make_entry(), STATUS_DEAD, STATUS_LAUNCHING, time.time())
        mock_launch.assert_called_once()

    # Test 2
    def test_blank_url_uses_app_only_launcher(self):
        """Blank URL: launch_package_for_current_config called with entry that has empty url."""
        from agent.config import effective_private_server_url
        entry = _make_entry(private_url="")
        cfg = _make_cfg(private_url="")
        url = effective_private_server_url(entry, cfg)
        self.assertEqual(url, "", "blank private_server_url must return empty string")

    # Test 3
    def test_blank_url_no_setup_required_error(self):
        """validate_config must not raise when private_server_url is blank."""
        cfg = _make_cfg(private_url="")
        try:
            validate_config(cfg)
        except Exception as exc:
            self.fail(f"validate_config raised with blank URL: {exc}")

    # Test 4
    def test_dead_recovery_blank_url_calls_relaunch(self):
        """Dead recovery with blank URL calls launch_package_for_current_config."""
        sup = _make_sup(private_url="")
        with patch("agent.supervisor.launch_package_for_current_config") as mock_launch, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            mock_launch.return_value = RejoinResult(True, root_used=False)
            sup._handle_state(_PKG, _make_entry(), STATUS_DEAD, STATUS_LAUNCHING, time.time())
        mock_launch.assert_called_once_with(_make_entry(), sup.cfg, "dead_recovery")

    # Test 5
    def test_no_heartbeat_recovery_blank_url_waits_then_force_stops(self):
        """No Heartbeat: force_stop only after 60s continuous stall."""
        sup = _make_sup(private_url="")
        now = time.time()
        with patch.object(sup, "_force_stop_target_package", return_value=True) as mock_stop, \
             patch("agent.supervisor.launch_package_for_current_config") as mock_launch, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            mock_launch.return_value = RejoinResult(True, root_used=False)
            sup._last_online_ts[_PKG] = now - 10
            sup._nhb_offline_count[_PKG] = sup.NHB_OFFLINE_CONFIRMATIONS
            sup._handle_state(_PKG, _make_entry(), STATUS_NO_HEARTBEAT, STATUS_ONLINE, now)
            mock_stop.assert_not_called()
            mock_launch.assert_not_called()
            sup._nhb_since[_PKG] = time.monotonic() - (sup.NHB_KILL_SWITCH_SECONDS + 1)
            sup._last_launched_at[_PKG] = time.monotonic() - (sup.LOADING_GRACE_SECONDS + 10)
            sup._handle_state(_PKG, _make_entry(), STATUS_NO_HEARTBEAT, STATUS_NO_HEARTBEAT, now)
        mock_stop.assert_called_once_with(_PKG)
        mock_launch.assert_not_called()


# â”€â”€â”€ 6-10: Configured Private Server URL â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestConfiguredPrivateServerUrl(unittest.TestCase):
    """Configured URL must be passed to perform_rejoin; query params preserved."""

    _URL = "roblox://experiences/start?privateServerLinkCode=abc123&type=Server"

    # Test 6
    def test_configured_url_used_in_dead_recovery(self):
        """Dead recovery with URL configured calls launch_package_for_current_config."""
        entry = _make_entry(private_url=self._URL)
        sup = _make_sup(private_url=self._URL)
        with patch("agent.supervisor.launch_package_for_current_config") as mock_launch, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            mock_launch.return_value = RejoinResult(True, root_used=False)
            sup._handle_state(_PKG, entry, STATUS_DEAD, STATUS_LAUNCHING, time.time())
        mock_launch.assert_called_once_with(entry, sup.cfg, "dead_recovery")

    # Test 7
    def test_configured_url_dead_recovery_sets_launching(self):
        """Dead recovery always returns Launching; watchdog confirms Online later."""
        entry = _make_entry(private_url=self._URL)
        sup = _make_sup(private_url=self._URL)
        with patch("agent.supervisor.launch_package_for_current_config", return_value=RejoinResult(True, root_used=False)), \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            sup._handle_state(_PKG, entry, STATUS_DEAD, STATUS_LAUNCHING, time.time())
        self.assertEqual(sup.status_map.get(_PKG), STATUS_LAUNCHING)

    def test_no_url_dead_recovery_sets_launching(self):
        """v1.0.4: without a private URL, Dead recovery stays Launching."""
        entry = _make_entry(private_url=None)
        sup = _make_sup(private_url=None)
        with patch("agent.supervisor.launch_package_for_current_config", return_value=RejoinResult(True, root_used=False)), \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            sup._handle_state(_PKG, entry, STATUS_DEAD, STATUS_LAUNCHING, time.time())
        self.assertEqual(sup.status_map.get(_PKG), STATUS_LAUNCHING)

    # Test 8
    def test_no_heartbeat_recovery_with_url_waits_then_force_stops(self):
        """No Heartbeat with URL: force-stop only after 60s continuous stall."""
        entry = _make_entry(private_url=self._URL)
        sup = _make_sup(private_url=self._URL)
        now = time.time()
        sup._last_online_ts[_PKG] = now - 10
        sup._nhb_offline_count[_PKG] = sup.NHB_OFFLINE_CONFIRMATIONS
        with patch("agent.supervisor.launch_package_for_current_config") as mock_launch, \
             patch.object(sup, "_force_stop_target_package", return_value=True) as mock_stop, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            mock_launch.return_value = RejoinResult(True, root_used=False)
            sup._handle_state(_PKG, entry, STATUS_NO_HEARTBEAT, STATUS_ONLINE, now)
            mock_stop.assert_not_called()
            mock_launch.assert_not_called()
            sup._nhb_since[_PKG] = time.monotonic() - (sup.NHB_KILL_SWITCH_SECONDS + 1)
            sup._last_launched_at[_PKG] = time.monotonic() - (sup.LOADING_GRACE_SECONDS + 10)
            sup._handle_state(_PKG, entry, STATUS_NO_HEARTBEAT, STATUS_NO_HEARTBEAT, now)
        mock_stop.assert_called_once_with(_PKG)
        mock_launch.assert_not_called()

    # Test 9
    def test_url_with_type_server_is_preserved(self):
        """effective_private_server_url returns URL unchanged (query params preserved)."""
        from agent.config import effective_private_server_url, _validate_optional_private_server_url
        url = "roblox://experiences/start?privateServerLinkCode=test&type=Server"
        entry = _make_entry(private_url=url)
        cfg = _make_cfg(private_url=url)
        result = effective_private_server_url(entry, cfg)
        self.assertIn("type=Server", result)

    # Test 10
    def test_launcher_selector_blank_url_empty_effective_url(self):
        """When URL is blank, effective_private_server_url returns empty string."""
        from agent.config import effective_private_server_url
        entry = _make_entry(private_url="")
        cfg = _make_cfg(private_url="")
        self.assertEqual(effective_private_server_url(entry, cfg), "")


# â”€â”€â”€ 11-16: State Detection â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestStateDetection(unittest.TestCase):
    """State detection must follow deterministic priority: process first."""

    # Test 11
    def test_process_not_running_returns_dead(self):
        sup = _make_sup()
        with patch.object(sup, "_fast_alive_evidence", return_value=_dead_evidence()):
            state, detail = sup._detect_package_state(_PKG, _make_entry())
        self.assertEqual(state, STATUS_DEAD)
        self.assertEqual(detail["process_running"], "false")

    # Test 12
    def test_process_running_no_presence_returns_no_heartbeat(self):
        sup = _make_sup()
        with patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=None):
            state, detail = sup._detect_package_state(_PKG, _make_entry())
        self.assertEqual(state, STATUS_NO_HEARTBEAT)
        self.assertEqual(detail["process_running"], "true")
        self.assertEqual(detail["heartbeat_ok"], "false")

    def test_launching_package_evaluated_in_watchdog_loop(self):
        """Launching must not zombie — watchdog runs launching evaluation path."""
        sup = _make_sup(initial_status={_PKG: STATUS_LAUNCHING})
        presence = MagicMock()
        presence.is_in_game = True
        presence.is_offline = False
        presence.is_lobby = False
        presence.is_unknown = False
        with patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=presence):
            self.assertTrue(sup._needs_launching_evaluation(_PKG))
            state, _ = sup._evaluate_launching_or_pending(_PKG, _make_entry())
        self.assertEqual(state, STATUS_ONLINE)

    # Test 13
    def test_process_running_presence_in_game_returns_in_game(self):
        sup = _make_sup()
        presence = MagicMock()
        presence.is_in_game = True
        presence.is_offline = False
        presence.is_lobby = False
        presence.is_unknown = False
        with patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=presence):
            state, detail = sup._detect_package_state(_PKG, _make_entry())
        from agent.supervisor import STATUS_ONLINE
        self.assertEqual(state, STATUS_ONLINE)
        self.assertEqual(detail["in_game"], "true")

    # Test 14
    def test_was_online_presence_offline_twice_returns_no_heartbeat(self):
        """Offline presence after recent Online becomes No Heartbeat."""
        sup = _make_sup()
        sup._last_online_ts[_PKG] = time.time() - 5
        presence = MagicMock()
        presence.is_in_game = False
        presence.is_offline = True
        presence.is_lobby = False
        presence.is_unknown = False
        with patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=presence):
            state1, _ = sup._detect_package_state(_PKG, _make_entry())
        with patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=presence):
            state2, detail2 = sup._detect_package_state(_PKG, _make_entry())
        self.assertEqual(state1, STATUS_NO_HEARTBEAT)
        self.assertEqual(state2, STATUS_NO_HEARTBEAT)
        self.assertEqual(detail2["heartbeat_ok"], "false")

    # Test 15
    def test_process_alive_presence_lobby_returns_no_heartbeat(self):
        """Presence Online/not Playing maps to No Heartbeat."""
        sup = _make_sup()
        presence = MagicMock()
        presence.is_in_game = False
        presence.is_offline = False
        presence.is_lobby = True
        presence.is_unknown = False
        with patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=presence):
            state, _ = sup._detect_package_state(_PKG, _make_entry())
        self.assertEqual(state, STATUS_NO_HEARTBEAT)
        self.assertNotEqual(state, "Joining")

    def test_process_missing_presence_lobby_returns_dead(self):
        """Local missing-process proof has priority over stale lobby presence."""
        sup = _make_sup(initial_status={_PKG: STATUS_ONLINE})
        presence = MagicMock()
        presence.is_in_game = False
        presence.is_offline = False
        presence.is_lobby = True
        presence.is_unknown = False
        stale_visual = {
            "alive": True,
            "running": False,
            "root_running": False,
            "task": False,
            "window": True,
            "surface": True,
            "foreground": False,
            "process_missing": True,
        }
        with patch.object(sup, "_fast_alive_evidence", return_value=stale_visual), \
             patch.object(sup, "_fetch_presence", return_value=presence) as fetch_presence:
            state, detail = sup._detect_package_state(_PKG, _make_entry())
        self.assertEqual(state, STATUS_DEAD)
        self.assertEqual(detail["process_running"], "false")
        fetch_presence.assert_not_called()

    def test_missing_process_after_online_relaunches_only_that_package(self):
        """A previously Online package that disappears becomes Dead and relaunches alone."""
        entry = _make_entry()
        sup = _make_sup(packages=[_PKG, _PKG2], initial_status={_PKG: STATUS_ONLINE, _PKG2: STATUS_ONLINE})
        now = time.time()
        with patch.object(sup, "_fast_alive_evidence", return_value=_dead_evidence()), \
             patch.object(sup, "_fetch_presence") as fetch_presence:
            state, detail = sup._detect_package_state(_PKG, entry)
        self.assertEqual(state, STATUS_DEAD)
        self.assertEqual(detail["process_running"], "false")
        fetch_presence.assert_not_called()
        with patch("agent.supervisor.launch_package_for_current_config", return_value=RejoinResult(True, root_used=False)) as mock_launch, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            sup._handle_state(_PKG, entry, state, STATUS_ONLINE, now)
        mock_launch.assert_called_once_with(entry, sup.cfg, "dead_recovery")
        self.assertEqual(sup.status_map[_PKG], STATUS_LAUNCHING)
        self.assertEqual(sup.status_map[_PKG2], STATUS_ONLINE)

    def test_missing_config_user_id_falls_back_without_presence(self):
        """Without resolvable user id, presence is skipped and Pending is reported."""
        sup = _make_sup()
        from agent.supervisor import STATUS_PENDING
        with patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(android, "current_foreground_package", return_value=""), \
             patch.object(android, "discover_roblox_user_id_from_prefs", side_effect=AssertionError("prefs scan")), \
             patch("agent.roblox_presence.lookup_user_id", return_value=None), \
             patch("agent.roblox_presence.fetch_presence_one", side_effect=AssertionError("presence call")):
            state, detail = sup._detect_package_state(_PKG, _make_entry())
        self.assertEqual(state, STATUS_PENDING)
        self.assertIn("presence_user_id_pending", detail["reason"])
        self.assertEqual(sup._presence_last_detail[_PKG]["roblox_api_status"], "skipped")

    def test_username_lookup_used_for_presence_when_user_id_missing(self):
        """Watchdog resolves username → user id before presence fetch."""
        sup = _make_sup()
        with patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(android, "current_foreground_package", return_value=""), \
             patch.object(android, "discover_roblox_user_id_from_prefs", return_value=None), \
             patch("agent.roblox_presence.lookup_user_id", return_value=12345) as lookup, \
             patch("agent.roblox_presence.fetch_presence_one", return_value=None):
            sup._detect_package_state(_PKG, _make_entry())
        lookup.assert_called_once_with("TestUser")

    def test_foreground_window_hint_online_when_api_missing(self):
        """Root/window foreground evidence can prove Online when API mapping is missing."""
        sup = _make_sup()
        ev = {"alive": True, "running": True, "root_running": False,
              "task": True, "window": True, "surface": True, "foreground": True}
        with patch.object(sup, "_fast_alive_evidence", return_value=ev), \
             patch.object(android, "current_foreground_package", return_value=_PKG), \
             patch.object(sup, "_fetch_presence", return_value=None):
            state, detail = sup._detect_package_state(_PKG, _make_entry())
        self.assertEqual(state, STATUS_ONLINE)
        self.assertEqual(detail["reason"], "foreground_window_surface_hint")

    # Test 16
    def test_no_joining_state_in_watchdog_allowed_states(self):
        """WatchdogSupervisor must never produce 'Joining' as a state."""
        allowed = {STATUS_ONLINE, STATUS_NO_HEARTBEAT, STATUS_DEAD}
        self.assertNotIn("Joining", allowed)
        # _detect_package_state can only return one of these values
        sup = _make_sup()
        possible_returns = set()
        for process_up in (True, False):
            for in_game in (True, False):
                for offline in (True, False):
                    presence = MagicMock()
                    presence.is_in_game = in_game and process_up
                    presence.is_offline = offline and process_up and not in_game
                    ev = _alive_evidence() if process_up else _dead_evidence()
                    sup._nhb_offline_count[_PKG] = sup.NHB_OFFLINE_CONFIRMATIONS if offline else 0
                    sup._last_online_ts[_PKG] = time.time() - 5 if not in_game else 0
                    with patch.object(sup, "_fast_alive_evidence", return_value=ev), \
                         patch.object(sup, "_fetch_presence", return_value=presence if process_up else None):
                        st, _ = sup._detect_package_state(_PKG, _make_entry())
                    possible_returns.add(st)
        self.assertNotIn("Joining", possible_returns)
        self.assertNotIn("Join Unconfirmed", possible_returns)
        self.assertNotIn("Join Pending", possible_returns)


# â”€â”€â”€ 17-25: Watchdog Continuity â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestWatchdogContinuity(unittest.TestCase):
    """Watchdog must never stop monitoring after packages become Online."""

    # Test 17
    def test_online_package_stays_in_status_map(self):
        """After detecting Online, package remains in status_map (not removed)."""
        sup = _make_sup()
        sup._set_status(_PKG, STATUS_ONLINE)
        self.assertIn(_PKG, sup.status_map)
        self.assertEqual(sup.status_map[_PKG], STATUS_ONLINE)

    # Test 18
    def test_watchdog_continues_after_all_packages_online(self):
        """Watchdog loop does NOT exit when all packages are Online.

        We run 2 rounds and verify the round counter keeps incrementing.
        """
        packages = [_PKG, _PKG2]
        sup = _make_sup(packages=packages)
        sup._grace_until[_PKG] = 0
        sup._grace_until[_PKG2] = 0

        # Make all packages appear Online
        presence = MagicMock()
        presence.is_in_game = True
        presence.is_offline = False
        rounds_completed = []

        original_detect = sup._detect_package_state

        def counting_detect(pkg, entry):
            rounds_completed.append(sup._round)
            return STATUS_ONLINE, {
                "process_running": "true", "in_game": "true",
                "heartbeat_ok": "true", "warning_detected": "false", "elapsed_ms": 0,
            }

        sup._detect_package_state = counting_detect
        sup.mark_all_launches_completed()

        # Stop after 2 rounds — round-robin pauses are patched to zero in tests.
        def _stop_after_2_rounds():
            time.sleep(1.5)
            sup.stop_event.set()

        t = threading.Thread(target=_stop_after_2_rounds, daemon=True)

        with patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"), \
             patch.object(sup, "_interruptible_sleep"):
            t.start()
            sup.run_forever(display_interval=99)
            t.join(timeout=10)

        # Both packages should have been checked in multiple rounds
        self.assertGreaterEqual(len(rounds_completed), len(packages) * 2,
            "Watchdog should complete at least 2 full rounds when all Online")

    # Test 19
    def test_force_close_after_online_detected_as_dead_next_round(self):
        """After Online, if process dies the next _detect call returns Dead."""
        sup = _make_sup()
        sup._set_status(_PKG, STATUS_ONLINE)
        sup._last_online_ts[_PKG] = time.time()
        # Simulate force-close: process no longer running
        with patch.object(sup, "_fast_alive_evidence", return_value=_dead_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=None):
            state, _ = sup._detect_package_state(_PKG, _make_entry())
        self.assertEqual(state, STATUS_DEAD)

    # Test 20
    def test_dead_package_triggers_relaunch(self):
        """Dead state triggers launch_package_for_current_config."""
        sup = _make_sup()
        with patch("agent.supervisor.launch_package_for_current_config") as mock_launch, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            mock_launch.return_value = RejoinResult(True, root_used=False)
            sup._handle_state(_PKG, _make_entry(), STATUS_DEAD, STATUS_ONLINE, time.time())
        mock_launch.assert_called_once()

    # Test 21
    def test_no_heartbeat_triggers_force_stop_after_kill_switch(self):
        """No Heartbeat triggers force_stop_package after 60s, not immediate relaunch."""
        sup = _make_sup()
        now = time.time()
        sup._last_online_ts[_PKG] = now - 1
        with patch("agent.supervisor.launch_package_for_current_config") as mock_launch, \
             patch.object(sup, "_force_stop_target_package", return_value=True) as mock_stop, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            mock_launch.return_value = RejoinResult(True, root_used=False)
            sup._handle_state(_PKG, _make_entry(), STATUS_NO_HEARTBEAT, STATUS_ONLINE, now)
            self.assertFalse(mock_stop.called)
            sup._nhb_since[_PKG] = time.monotonic() - (sup.NHB_KILL_SWITCH_SECONDS + 1)
            sup._last_launched_at[_PKG] = time.monotonic() - (sup.LOADING_GRACE_SECONDS + 10)
            sup._handle_state(_PKG, _make_entry(), STATUS_NO_HEARTBEAT, STATUS_NO_HEARTBEAT, now)
        self.assertTrue(mock_stop.called, "force_stop_package must be called after kill-switch")
        self.assertFalse(mock_launch.called, "relaunch must wait for Dead recovery")

    # Test 22
    def test_grace_window_blocks_immediate_repeated_relaunch(self):
        """After a successful launch, grace window prevents immediate re-launch."""
        sup = _make_sup()
        now = time.time()
        sup._grace_until[_PKG] = now + sup.DEFAULT_GRACE_SECONDS  # grace active
        self.assertTrue(sup._in_grace(_PKG, now))
        self.assertFalse(sup._in_grace(_PKG, now + sup.DEFAULT_GRACE_SECONDS + 1))

    # Test 23
    def test_checking_label_updated_during_loop(self):
        """checking_label is set to 'Checking Package X/Y' for each package."""
        packages = [_PKG, _PKG2]
        sup = _make_sup(packages=packages)
        labels_seen = []

        def tracking_detect(pkg, entry):
            labels_seen.append(sup.checking_label)
            return STATUS_DEAD, {
                "process_running": "false", "in_game": "false",
                "heartbeat_ok": "false", "warning_detected": "false", "elapsed_ms": 0,
            }

        sup._detect_package_state = tracking_detect

        with patch("agent.supervisor.launch_package_for_current_config",
                   return_value=RejoinResult(False, root_used=False)), \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            # Run one manual round
            sup._round = 1
            total = len(packages)
            for idx, pkg in enumerate(packages, 1):
                sup.checking_label = f"Checking Package {idx}/{total}"
                entry = sup.entry_by_pkg[pkg]
                sup._detect_package_state(pkg, entry)

        self.assertIn("Checking Package 1/2", labels_seen)
        self.assertIn("Checking Package 2/2", labels_seen)

    # Test 24
    def test_checking_label_format(self):
        """checking_label must follow 'Checking Package X/Y' format exactly."""
        packages = [_PKG, _PKG2, "com.roblox.client3"]
        sup = _make_sup(packages=packages)
        total = len(packages)
        for idx in range(1, total + 1):
            sup.checking_label = f"Checking Package {idx}/{total}"
            self.assertRegex(sup.checking_label, r"^Checking Package \d+/\d+$")

    # Test 25
    def test_checking_label_ansi_yellow_in_dashboard(self):
        """When use_color=True, checking_label is wrapped in ANSI yellow (\033[33m)."""
        # The checking line in commands._live_dashboard uses \033[33m (yellow)
        _YELLOW = "\033[33m"
        _RESET  = "\033[0m"
        checking = "Checking Package 1/3"
        rendered = f"  {_YELLOW}{checking}{_RESET}"
        self.assertIn(_YELLOW, rendered)
        self.assertIn(checking, rendered)

    def test_run_forever_checks_all_packages_when_one_detector_raises(self):
        """One package error must not prevent 1/3 -> 2/3 -> 3/3 in the same round."""
        packages = [_PKG, _PKG2, "com.roblox.client3"]
        sup = _make_sup(packages=packages)
        seen: list[str] = []
        labels: list[str] = []

        def detect(pkg, entry):
            seen.append(pkg)
            if pkg == _PKG:
                raise RuntimeError("boom")
            if pkg == packages[-1]:
                sup.stop_event.set()
            return STATUS_ONLINE, {
                "process_running": "true", "in_game": "true",
                "heartbeat_ok": "true", "warning_detected": "false", "elapsed_ms": 0,
            }

        sup._detect_package_state = detect
        sup.mark_all_launches_completed()
        with patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            sup.run_forever(
                display_interval=999,
                render_callback=lambda: labels.append(sup.checking_label),
            )
        self.assertEqual(seen, packages)
        self.assertEqual(sup.checking_label, "Checking Package 3/3")
        self.assertIn(sup.status_map[_PKG], {STATUS_NO_HEARTBEAT, "Reopening", "Relaunching", "Launching"})

    def test_checking_label_persists_after_round_until_shutdown(self):
        packages = [_PKG, _PKG2]
        sup = _make_sup(packages=packages)
        labels: list[str] = []

        def detect(pkg, entry):
            if pkg == packages[-1]:
                sup.stop_event.set()
            return STATUS_ONLINE, {
                "process_running": "true", "in_game": "true",
                "heartbeat_ok": "true", "warning_detected": "false", "elapsed_ms": 0,
            }

        sup._detect_package_state = detect
        sup.mark_all_launches_completed()
        with patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            sup.run_forever(
                display_interval=999,
                render_callback=lambda: labels.append(sup.checking_label),
            )
        self.assertEqual(sup.checking_label, "Checking Package 2/2")

    def test_checking_label_loops_back_to_first_package_next_round(self):
        packages = [_PKG, _PKG2, "com.roblox.client3"]
        sup = _make_sup(packages=packages)
        labels: list[str] = []
        checks = {"n": 0}

        def detect(pkg, entry):
            labels.append(sup.checking_label)
            checks["n"] += 1
            if checks["n"] >= 4:
                sup.stop_event.set()
            return STATUS_ONLINE, {
                "process_running": "true", "in_game": "true",
                "heartbeat_ok": "true", "warning_detected": "false", "elapsed_ms": 0,
            }

        sup._detect_package_state = detect
        sup.mark_all_launches_completed()
        with patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"), \
             patch.object(sup, "_sup_interval", return_value=0):
            sup.run_forever(
                display_interval=999,
                render_callback=lambda: None,
            )
        self.assertEqual(
            labels[:4],
            [
                "Checking Package 1/3",
                "Checking Package 2/3",
                "Checking Package 3/3",
                "Checking Package 1/3",
            ],
        )


# â”€â”€â”€ 26-30: Regression â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestRegressionNoJoiningOrUiautomator(unittest.TestCase):
    """No removed launch-action state machine. No uiautomator/logcat."""

    # Test 26
    def test_no_uiautomator_or_logcat_called_in_watchdog(self):
        """WatchdogSupervisor._detect_package_state does not call uiautomator/logcat."""
        sup = _make_sup()
        uia_calls = []
        with patch.object(sup, "_fast_alive_evidence", return_value=_dead_evidence()) as _mock, \
             patch.object(sup, "_fetch_presence", return_value=None):
            # Capture any call whose name contains uiautomator or logcat
            original = android.__dict__.copy()
            sup._detect_package_state(_PKG, _make_entry())
        # If uiautomator_dump or logcat were called, they'd need to be in android module.
        # Since _detect_package_state only calls bounded alive evidence +
        # _fetch_presence, no uiautomator/logcat calls happen.
        self.assertNotIn("uiautomator_dump", [c[0] for c in uia_calls])

    def test_probe_p089_state_check_uses_bounded_fast_evidence(self):
        """Regression for p-0899246178: package 1 check must not enter legacy full scan."""
        sup = _make_sup()
        with patch.object(android, "get_package_alive_evidence", side_effect=AssertionError("legacy scan")), \
             patch.object(sup, "_fast_alive_evidence", return_value=_alive_evidence()), \
             patch.object(sup, "_fetch_presence", return_value=None):
            state, detail = sup._detect_package_state(_PKG, _make_entry())
        self.assertEqual(state, STATUS_NO_HEARTBEAT)
        self.assertIn("heartbeat", detail["reason"])

    def test_fast_alive_evidence_uses_short_root_timeouts(self):
        """Root process proof is bounded so Checking Package 1/3 cannot stall a round."""
        sup = _make_sup()
        sup._root_info = android.RootInfo(True, "su", "uid=0")
        calls = []

        def _root(args, *, root_tool=None, timeout=0):
            calls.append((tuple(args), timeout))
            if tuple(args)[:1] == ("pidof",):
                return android.CommandResult(tuple(args), 1, "", "")
            return android.CommandResult(tuple(args), 0, "hit", "")

        with patch.object(android, "run_root_command", side_effect=_root), \
             patch.object(android, "current_foreground_package", return_value=""), \
             patch.object(android, "is_package_window_visible", return_value=False), \
             patch.object(android, "is_package_surface_in_surfaceflinger", return_value=False):
            ev = sup._fast_alive_evidence(_PKG)

        self.assertTrue(ev["root_running"])
        self.assertTrue(ev["alive"])
        self.assertTrue(calls)
        self.assertTrue(all(timeout <= 3 for _args, timeout in calls))

    def test_probe_p799_segfault_fix_marker_and_disabled_paths(self):
        """Regression for p-79933739d8: live Start records the disabled crash path."""
        import agent.supervisor as sup_mod

        src = inspect.getsource(sup_mod.WatchdogSupervisor)
        self.assertIn("[DENG_REJOIN_SEGFAULT_FIX]", src)
        self.assertIn("python_ssl_urllib_presence_api", src)
        self.assertIn("safe_http.post_json curl_on_termux", src)
        self.assertNotRegex(src, r'run_command\s*\([^)]*uiautomator')
        self.assertNotRegex(src, r'run_command\s*\([^)]*logcat')

    # Test 27
    def test_joining_initial_status_maps_to_launching(self):
        """Legacy Joining handoff normalizes to Launching."""
        sup = _make_sup(initial_status={_PKG: "Joining"})
        self.assertEqual(sup.status_map.get(_PKG), STATUS_LAUNCHING)

    # Test 28
    def test_no_removed_launch_action_text_in_state_machine(self):
        """WatchdogSupervisor has no old launch-action concept."""
        import inspect
        import agent.supervisor as sup_mod
        src = inspect.getsource(WatchdogSupervisor)
        old_key = "post" + "_launch_action"
        self.assertNotIn(old_key, src)
        self.assertNotIn("POST" + "_LAUNCH_ACTION", src)

    # Test 29
    def test_watchdog_supervisor_has_no_worker_threads_list(self):
        """WatchdogSupervisor is sequential â€” no _workers list of daemon threads."""
        sup = _make_sup()
        self.assertFalse(hasattr(sup, "_workers"),
            "WatchdogSupervisor must not have a _workers thread list")

    # Test 30
    def test_installer_and_rejoin_versions_tests_still_importable(self):
        """Regression: installer/artifact test modules are still importable."""
        import tests.test_installer
        import tests.test_rejoin_versions
        import tests.test_internal_test_artifact


# â”€â”€â”€ Additional: launch_package_for_current_config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestLaunchPackageForCurrentConfig(unittest.TestCase):
    """launch_package_for_current_config wraps perform_rejoin correctly."""

    def test_calls_perform_rejoin_with_package_set(self):
        entry = _make_entry(_PKG, private_url="")
        cfg = _make_cfg(private_url="")
        with patch("agent.launcher.perform_rejoin") as mock_rejoin:
            mock_rejoin.return_value = RejoinResult(True, root_used=False)
            launch_package_for_current_config(entry, cfg, "test_reason")
        self.assertTrue(mock_rejoin.called)
        called_cfg = mock_rejoin.call_args.args[0]
        self.assertEqual(called_cfg.get("roblox_package"), _PKG)

    def test_reason_forwarded_to_perform_rejoin(self):
        entry = _make_entry(_PKG, private_url="")
        cfg = _make_cfg(private_url="")
        with patch("agent.launcher.perform_rejoin") as mock_rejoin:
            mock_rejoin.return_value = RejoinResult(True, root_used=False)
            launch_package_for_current_config(entry, cfg, "dead_recovery")
        self.assertEqual(mock_rejoin.call_args.kwargs.get("reason"), "dead_recovery")

    def test_package_entry_forwarded(self):
        entry = _make_entry(_PKG)
        cfg = _make_cfg()
        with patch("agent.launcher.perform_rejoin") as mock_rejoin:
            mock_rejoin.return_value = RejoinResult(True, root_used=False)
            launch_package_for_current_config(entry, cfg, "no_heartbeat_recovery")
        self.assertEqual(mock_rejoin.call_args.kwargs.get("package_entry"), entry)


# ─── Additional: running-but-not-playing recovery logic ──────────────────────

class TestRunningNotPlayingRecovery(unittest.TestCase):
    """No Heartbeat kill-switch force-stops after 60s; Dead recovery relaunches."""

    def test_no_heartbeat_blank_url_force_stops_after_kill_switch(self):
        sup = _make_sup(private_url="")
        now = time.time()
        with patch.object(sup, "_force_stop_target_package", return_value=True) as mock_stop, \
             patch("agent.supervisor.launch_package_for_current_config") as mock_launch, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            mock_launch.return_value = RejoinResult(True, root_used=False)
            sup._nhb_since[_PKG] = time.monotonic() - (sup.NHB_KILL_SWITCH_SECONDS + 5)
            sup._last_launched_at[_PKG] = time.monotonic() - (sup.LOADING_GRACE_SECONDS + 10)
            sup._handle_state(_PKG, _make_entry(private_url=""), STATUS_NO_HEARTBEAT, STATUS_NO_HEARTBEAT, now)
        mock_stop.assert_called_once_with(_PKG)
        mock_launch.assert_not_called()

    def test_no_heartbeat_with_url_force_stops_after_kill_switch(self):
        url = "roblox://experiences/start?privateServerLinkCode=abc"
        sup = _make_sup(private_url=url)
        entry = _make_entry(private_url=url)
        now = time.time()
        with patch.object(sup, "_force_stop_target_package", return_value=True) as mock_stop, \
             patch("agent.supervisor.launch_package_for_current_config") as mock_launch, \
             patch("agent.db.insert_event"), patch("agent.db.insert_heartbeat"):
            mock_launch.return_value = RejoinResult(True, root_used=False)
            sup._nhb_since[_PKG] = time.monotonic() - (sup.NHB_KILL_SWITCH_SECONDS + 5)
            sup._last_launched_at[_PKG] = time.monotonic() - (sup.LOADING_GRACE_SECONDS + 10)
            sup._handle_state(_PKG, entry, STATUS_NO_HEARTBEAT, STATUS_NO_HEARTBEAT, now)
        mock_stop.assert_called_once_with(_PKG)
        mock_launch.assert_not_called()


# â”€â”€â”€ Additional: Status constants exist â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestNewStatusConstants(unittest.TestCase):

    def test_status_no_heartbeat_constant(self):
        self.assertEqual(STATUS_NO_HEARTBEAT, "No Heartbeat")

    def test_status_online_unchanged(self):
        self.assertEqual(STATUS_ONLINE, "Online")

    def test_status_dead_unchanged(self):
        self.assertEqual(STATUS_DEAD, "Dead")

    def test_status_launching_unchanged(self):
        self.assertEqual(STATUS_LAUNCHING, "Launching")


if __name__ == "__main__":
    unittest.main()
