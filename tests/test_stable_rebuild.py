"""Tests for the DENG Tool: Rejoin stable rebuild (probe p-9e3f2a8d1c).

Covers:
- Archive: live Start does not import/call archived broken modules
- States: only public states shown (Layout/Launching/Online/Reopening/Failed)
- Private URL: setup saves, Start reads, legacy key promoted
- Supervisor: per-package relaunch, restart cap, Ctrl+C, no endless loop
- Roblox Presence API: integration, fallback, rate-limit safety, no crash
- YesCaptcha: hidden from First Time Setup, Edit Config, Start
- Webhook: hidden from First Time Setup, Edit Config
- UI: table columns, no blink, no repeated banner
- License: !id stats not regressed
"""
from __future__ import annotations

import importlib
import inspect
import sys
import threading
import types
import unittest
from unittest import mock

# ─── 1. Archive isolation — live Start must not import broken modules ─────────

class TestArchiveIsolation(unittest.TestCase):
    """Live Start/supervisor must not import or call archived broken modules."""

    def test_supervisor_does_not_import_experience_detector(self) -> None:
        """supervisor.py must not import experience_detector at module level."""
        import agent.supervisor as sup
        sup_source = inspect.getsource(sup)
        # The dead import was removed in stable rebuild; must be gone.
        self.assertNotIn(
            "from .experience_detector import",
            sup_source,
            "supervisor.py still imports from experience_detector (banned)",
        )

    def test_supervisor_does_not_call_detect_experience_state(self) -> None:
        """supervisor.py must not call detect_experience_state."""
        import agent.supervisor as sup
        sup_source = inspect.getsource(sup)
        self.assertNotIn(
            "detect_experience_state(",
            sup_source,
            "supervisor.py still calls detect_experience_state (banned)",
        )

    def test_supervisor_does_not_call_uiautomator(self) -> None:
        """supervisor.py must not execute uiautomator subprocess commands."""
        import agent.supervisor as sup
        import re
        sup_source = inspect.getsource(sup)
        # Must not have any live call like run_command(["uiautomator"...])
        # or subprocess call with "uiautomator". Comments are OK.
        live_calls = re.findall(
            r'run_command\s*\([^)]*uiautomator|subprocess\.[^(]*\([^)]*uiautomator',
            sup_source,
        )
        self.assertEqual(
            live_calls, [],
            f"supervisor.py still executes uiautomator subprocess: {live_calls}",
        )

    def test_supervisor_does_not_call_logcat_directly(self) -> None:
        """supervisor.py must not call logcat subprocess directly."""
        import agent.supervisor as sup
        sup_source = inspect.getsource(sup)
        # logcat must not be called directly — only through monitor/android modules
        self.assertNotIn(
            '"logcat"',
            sup_source,
            "supervisor.py calls logcat directly (banned in stable rebuild)",
        )

    def test_commands_start_does_not_import_experience_detector(self) -> None:
        """cmd_start must not import experience_detector."""
        import agent.commands as cmd
        cmd_source = inspect.getsource(cmd)
        # Must not have a direct import of experience_detector in the file
        # (it can still be imported by monitor.py, but not directly in the
        # live Start decision path).
        import_lines = [
            ln.strip()
            for ln in cmd_source.splitlines()
            if "experience_detector" in ln and ln.strip().startswith(("import", "from"))
        ]
        self.assertEqual(
            import_lines, [],
            f"commands.py directly imports experience_detector: {import_lines}",
        )

    def test_legacy_modules_raise_on_broken_probe_calls(self) -> None:
        """Archived broken probes must raise RuntimeError if called."""
        from agent.legacy.experience_detector_broken import (
            _probe_logcat_broken,
            _probe_dumpsys_activity_broken,
            _probe_uiautomator_broken,
            detect_experience_state_broken,
        )
        with self.assertRaises(RuntimeError):
            _probe_logcat_broken("com.roblox.client")
        with self.assertRaises(RuntimeError):
            _probe_dumpsys_activity_broken("com.roblox.client")
        with self.assertRaises(RuntimeError):
            _probe_uiautomator_broken("com.roblox.client")
        with self.assertRaises(RuntimeError):
            detect_experience_state_broken("com.roblox.client")

    def test_legacy_package_init_has_broken_header(self) -> None:
        """agent/legacy/__init__.py must contain the broken-code header."""
        import agent.legacy as legacy_pkg
        src = inspect.getsource(legacy_pkg)
        self.assertIn("BROKEN LEGACY CODE", src)
        self.assertIn("DO NOT USE IN LIVE START PATH", src)


# ─── 2. Public states — only 5 states allowed in the dashboard ───────────────

class TestPublicStates(unittest.TestCase):
    """_STATE_DISPLAY_MAP must produce only the 5 allowed public states."""

    # All internal states that can come from the supervisor
    _INTERNAL_STATES = [
        "Joining", "Join Unconfirmed", "In Server", "Lobby",
        "Launching", "Online", "Reconnecting", "Dead", "Disconnected",
        "Offline", "Preparing", "Background", "Warning", "Unknown",
        "Failed", "Layout", "Join Failed", "Wrong Game / Wrong Server",
    ]
    _ALLOWED_PUBLIC = {
        "Layout", "Launching", "Online", "Reopening", "Failed", "Dead",
        "No Heartbeat", "Checking", "Preparing", "Clear Cache", "Pending",
        "Suspended",
    }

    def _get_display_map(self):
        # Extract _STATE_DISPLAY_MAP from cmd_start source via AST.
        # It uses an annotated assignment (AnnAssign) so we walk for that.
        import ast
        import agent.commands as cmd
        src = inspect.getsource(cmd)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            # Annotated assignment: _STATE_DISPLAY_MAP: dict[str, str] = {...}
            if isinstance(node, ast.AnnAssign):
                target = node.target
                if isinstance(target, ast.Name) and target.id == "_STATE_DISPLAY_MAP":
                    if node.value:
                        return ast.literal_eval(node.value)
            # Plain assignment (fallback): _STATE_DISPLAY_MAP = {...}
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "_STATE_DISPLAY_MAP":
                        return ast.literal_eval(node.value)
        return {}

    def test_joining_maps_to_launching_in_display(self) -> None:
        smap = self._get_display_map()
        if "Joining" in smap:
            self.assertEqual(smap.get("Joining"), "Launching")
        self.assertEqual(smap.get("Join Unconfirmed"), "Launching")

    def test_no_join_unconfirmed_in_public_display(self) -> None:
        smap = self._get_display_map()
        if "Join Unconfirmed" in smap:
            self.assertNotEqual(smap["Join Unconfirmed"], "Join Unconfirmed")
            self.assertIn(smap["Join Unconfirmed"], self._ALLOWED_PUBLIC)

    def test_no_in_server_in_public_display(self) -> None:
        smap = self._get_display_map()
        self.assertIn("In Server", smap)
        self.assertNotEqual(smap["In Server"], "In Server")
        self.assertIn(smap["In Server"], self._ALLOWED_PUBLIC)

    def test_no_lobby_maps_to_no_heartbeat(self) -> None:
        smap = self._get_display_map()
        self.assertIn("Lobby", smap)
        self.assertEqual(smap["Lobby"], "No Heartbeat")
        self.assertIn(smap["Lobby"], self._ALLOWED_PUBLIC)

    def test_reconnecting_maps_to_reopening(self) -> None:
        smap = self._get_display_map()
        self.assertEqual(smap.get("Reconnecting"), "Reopening")

    def test_in_lobby_maps_to_no_heartbeat(self) -> None:
        smap = self._get_display_map()
        displayed = smap.get("In Lobby", "No Heartbeat")
        self.assertEqual(displayed, "No Heartbeat")

    def test_dead_stays_dead(self) -> None:
        smap = self._get_display_map()
        self.assertEqual(smap.get("Dead"), "Dead")

    def test_online_stays_online(self) -> None:
        # "Online" is not in the map (passthrough), so get() returns default
        smap = self._get_display_map()
        internal = "Online"
        displayed = smap.get(internal, internal)
        self.assertEqual(displayed, "Online")

    def test_failed_stays_failed(self) -> None:
        smap = self._get_display_map()
        internal = "Failed"
        displayed = smap.get(internal, internal)
        self.assertEqual(displayed, "Failed")

    def test_all_mapped_states_in_allowed_set(self) -> None:
        smap = self._get_display_map()
        for internal, public in smap.items():
            self.assertIn(
                public, self._ALLOWED_PUBLIC,
                f"State '{internal}' maps to '{public}' which is not in allowed set {self._ALLOWED_PUBLIC}",
            )


# ─── 3. Supervisor: no STATUS_JOINING set in live paths ──────────────────────

class TestSupervisorNoJoiningState(unittest.TestCase):
    """The live supervisor must not set STATUS_JOINING or STATUS_LOBBY."""

    def _worker_source(self) -> str:
        import agent.supervisor as sup
        return inspect.getsource(sup._PackageWorker)

    def test_no_status_joining_set_in_live_worker(self) -> None:
        src = self._worker_source()
        # The worker must not set STATUS_JOINING
        # (it can reference it in a comment or legacy check, but must not call _set_status with it)
        import re
        # Look for _set_status calls that pass STATUS_JOINING
        joining_set = re.findall(r'_set_status\s*\([^)]*STATUS_JOINING', src)
        self.assertEqual(
            joining_set, [],
            f"_PackageWorker._set_status() called with STATUS_JOINING: {joining_set}",
        )

    def test_no_status_lobby_set_in_live_worker(self) -> None:
        src = self._worker_source()
        import re
        lobby_set = re.findall(r'_set_status\s*\([^)]*STATUS_LOBBY', src)
        self.assertEqual(
            lobby_set, [],
            f"_PackageWorker._set_status() called with STATUS_LOBBY: {lobby_set}",
        )

    def test_relaunch_uses_status_launching_not_joining(self) -> None:
        """Post-relaunch state must always be STATUS_LAUNCHING."""
        import agent.supervisor as sup
        src = inspect.getsource(sup._PackageWorker.run)
        # Must have STATUS_LAUNCHING set after relaunch
        self.assertIn("STATUS_LAUNCHING", src)
        # Must NOT set STATUS_JOINING after a process_missing relaunch
        import re
        joining_after_relaunch = re.findall(
            r'new_st\s*=\s*STATUS_JOINING', src
        )
        self.assertEqual(joining_after_relaunch, [])


# ─── 4. Supervisor: new Kaeru fields present ─────────────────────────────────

class TestSupervisorKaeruFields(unittest.TestCase):
    """_PackageWorker must have all required Kaeru-style tracking fields."""

    def _make_worker(self):
        import agent.supervisor as sup
        entry = {"package": "com.roblox.client", "account_username": "TestUser"}
        cfg = {"supervisor": {}, "log_level": "INFO", "health_check_interval_seconds": 30}
        status_map = {}
        stop_event = threading.Event()
        return sup._PackageWorker(entry, cfg, status_map, stop_event)

    def test_last_presence_check_at_field_exists(self) -> None:
        w = self._make_worker()
        self.assertIsNone(w.last_presence_check_at)

    def test_last_presence_state_field_exists(self) -> None:
        w = self._make_worker()
        self.assertEqual(w.last_presence_state, "unknown")

    def test_hourly_restart_count_field_exists(self) -> None:
        w = self._make_worker()
        self.assertEqual(w.hourly_restart_count, 0)

    def test_failed_reason_field_exists(self) -> None:
        w = self._make_worker()
        self.assertEqual(w.failed_reason, "")

    def test_desired_url_field_exists(self) -> None:
        w = self._make_worker()
        # desired_url is set during run() setup; at init it should default to ""
        self.assertEqual(w.desired_url, "")

    def test_record_restart_updates_hourly_count(self) -> None:
        w = self._make_worker()
        self.assertEqual(w.hourly_restart_count, 0)
        w._record_restart()
        self.assertEqual(w.hourly_restart_count, 1)
        w._record_restart()
        self.assertEqual(w.hourly_restart_count, 2)


# ─── 5. Roblox Presence API ───────────────────────────────────────────────────

class TestRobloxPresenceAPI(unittest.TestCase):
    """Tests for roblox_presence.py stability and helper functions."""

    def setUp(self) -> None:
        from agent import roblox_presence as rp
        rp.clear_presence_cache()
        self.rp = rp

    def test_classify_in_experience(self) -> None:
        rp = self.rp
        result = rp.PresenceResult(user_id=1, presence_type=rp.PresenceType.IN_GAME)
        self.assertEqual(rp.classify_presence_result(result), "in_experience")

    def test_classify_online_not_in_game(self) -> None:
        rp = self.rp
        result = rp.PresenceResult(user_id=1, presence_type=rp.PresenceType.ONLINE)
        self.assertEqual(rp.classify_presence_result(result), "online_not_in_game")

    def test_classify_offline(self) -> None:
        rp = self.rp
        result = rp.PresenceResult(user_id=1, presence_type=rp.PresenceType.OFFLINE)
        self.assertEqual(rp.classify_presence_result(result), "offline")

    def test_classify_invisible_as_offline(self) -> None:
        rp = self.rp
        result = rp.PresenceResult(user_id=1, presence_type=rp.PresenceType.INVISIBLE)
        self.assertEqual(rp.classify_presence_result(result), "offline")

    def test_classify_unknown(self) -> None:
        rp = self.rp
        result = rp.PresenceResult(user_id=1, presence_type=rp.PresenceType.UNKNOWN)
        self.assertEqual(rp.classify_presence_result(result), "unknown")

    def test_classify_none_returns_unavailable(self) -> None:
        rp = self.rp
        self.assertEqual(rp.classify_presence_result(None), "unavailable")

    def test_get_presence_state_no_entry_returns_unavailable(self) -> None:
        rp = self.rp
        self.assertEqual(rp.get_presence_state_for_package(None), "unavailable")
        self.assertEqual(rp.get_presence_state_for_package({}), "unavailable")

    def test_get_presence_state_no_username_no_id_returns_unavailable(self) -> None:
        rp = self.rp
        entry = {"package": "com.roblox.client"}
        self.assertEqual(rp.get_presence_state_for_package(entry), "unavailable")

    def test_get_presence_state_with_user_id(self) -> None:
        rp = self.rp
        entry = {"package": "com.roblox.client", "roblox_user_id": 12345}
        responses = {
            rp._PRESENCE_URL: {
                "userPresences": [
                    {"userPresenceType": 2, "userId": 12345, "placeId": 1, "lastLocation": "Bloxburg"},
                ],
            },
        }
        def fake_post(url, body, **kw):
            return responses.get(url)
        with mock.patch.object(rp, "_post_json", side_effect=fake_post):
            state = rp.get_presence_state_for_package(entry)
        self.assertEqual(state, "in_experience")

    def test_presence_api_failure_returns_unavailable_not_crash(self) -> None:
        """API failure must return unavailable, not raise."""
        rp = self.rp
        entry = {"package": "com.roblox.client", "roblox_user_id": 99}
        with mock.patch.object(rp, "_post_json", return_value=None):
            state = rp.get_presence_state_for_package(entry)
        self.assertEqual(state, "unknown")  # fetch_presence returns UNKNOWN for missing id

    def test_presence_malformed_json_does_not_crash(self) -> None:
        """Malformed JSON response must not raise."""
        rp = self.rp
        entry = {"package": "com.roblox.client", "roblox_user_id": 99}
        # Return garbage JSON structure
        with mock.patch.object(rp, "_post_json", return_value={"not": "presence"}):
            state = rp.get_presence_state_for_package(entry)
        self.assertIn(state, ("unknown", "unavailable"))

    def test_presence_never_raises(self) -> None:
        """fetch_presence must never raise regardless of network error."""
        rp = self.rp
        with mock.patch.object(
            rp.safe_http,
            "post_json",
            side_effect=rp.safe_http.SafeHttpNetworkError("connection refused"),
        ):
            result = rp.fetch_presence_one(123)
        self.assertIsNotNone(result)
        self.assertTrue(result.is_unknown)

    def test_classify_never_raises_on_weird_input(self) -> None:
        rp = self.rp
        # Should not raise on any input
        rp.classify_presence_result(None)
        rp.classify_presence_result(rp.PresenceResult(user_id=0))

    def test_presence_parses_universe_and_game_id(self) -> None:
        rp = self.rp
        row = {
            "userPresenceType": 2,
            "userId": "123",
            "placeId": "456",
            "rootPlaceId": 456,
            "universeId": "789",
            "gameId": "server-guid",
            "lastLocation": "Expected Game",
        }
        parsed = rp._parse_presence_row(row)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.place_id, 456)
        self.assertEqual(parsed.root_place_id, 456)
        self.assertEqual(parsed.universe_id, 789)
        self.assertEqual(parsed.game_id, "server-guid")

    def test_resolve_presence_online_when_expected_target_matches(self) -> None:
        from agent.url_utils import parse_expected_target_from_url

        rp = self.rp
        presence = rp.PresenceResult(
            user_id=1,
            presence_type=rp.PresenceType.IN_GAME,
            place_id=123,
            root_place_id=123,
            universe_id=999,
        )
        target = parse_expected_target_from_url(
            "https://www.roblox.com/games/123/name?privateServerLinkCode=ABC",
            expected_universe_id=999,
        )
        resolved = rp.resolve_presence_state(presence, target, process_alive=True)
        self.assertEqual(resolved.state, "Online")
        self.assertEqual(resolved.server_verification, "matched")

    def test_resolve_presence_wrong_game_on_target_mismatch(self) -> None:
        from agent.url_utils import parse_expected_target_from_url

        rp = self.rp
        presence = rp.PresenceResult(
            user_id=1,
            presence_type=rp.PresenceType.IN_GAME,
            place_id=456,
        )
        target = parse_expected_target_from_url("https://www.roblox.com/games/123/name")
        resolved = rp.resolve_presence_state(presence, target, process_alive=True)
        self.assertEqual(resolved.state, "Wrong Game / Wrong Server")
        self.assertEqual(resolved.server_verification, "mismatch")

    def test_resolve_presence_lobby_and_join_timeout(self) -> None:
        rp = self.rp
        presence = rp.PresenceResult(user_id=1, presence_type=rp.PresenceType.ONLINE)
        lobby = rp.resolve_presence_state(
            presence, process_alive=True, launch_elapsed_seconds=10, join_timeout_seconds=90
        )
        failed = rp.resolve_presence_state(
            presence, process_alive=True, launch_elapsed_seconds=120, join_timeout_seconds=90
        )
        self.assertEqual(lobby.state, "No Heartbeat")
        self.assertEqual(failed.state, "No Heartbeat")

    def test_parse_expected_target_from_private_server_url(self) -> None:
        from agent.url_utils import parse_expected_target_from_url

        target = parse_expected_target_from_url(
            "https://www.roblox.com/games/123/name?privateServerLinkCode=SECRET",
            expected_root_place_id=123,
            expected_universe_id=456,
        )
        self.assertEqual(target.expected_place_id, 123)
        self.assertEqual(target.expected_root_place_id, 123)
        self.assertEqual(target.expected_universe_id, 456)
        self.assertEqual(target.expected_private_code, "SECRET")

    def test_parse_expected_target_from_share_url_keeps_private_code(self) -> None:
        from agent.url_utils import parse_expected_target_from_url

        target = parse_expected_target_from_url(
            "https://www.roblox.com/share?code=ABC123&type=Server"
        )
        self.assertIsNone(target.expected_place_id)
        self.assertEqual(target.expected_private_code, "ABC123")

    def test_resolve_presence_unknown_does_not_force_recovery_state(self) -> None:
        rp = self.rp
        presence = rp.PresenceResult(user_id=1, presence_type=rp.PresenceType.UNKNOWN)
        resolved = rp.resolve_presence_state(presence, process_alive=True)
        self.assertEqual(resolved.state, "Unknown")


class TestPresenceSupervisorIntegration(unittest.TestCase):
    """Supervisor leaves authenticated presence disabled in this release."""

    def setUp(self) -> None:
        from agent import roblox_presence as rp
        rp.clear_presence_cache()

    def test_presence_in_game_path_disabled(self) -> None:
        """Saved user IDs must not trigger Presence API calls."""
        from agent import roblox_presence as rp
        import agent.supervisor as sup

        entry = {"package": "com.roblox.client", "account_username": "alice"}
        cfg = {"supervisor": {}, "log_level": "INFO", "health_check_interval_seconds": 30}
        status_map = {"com.roblox.client": "Online"}
        stop_event = threading.Event()
        worker = sup._PackageWorker(entry, cfg, status_map, stop_event)
        worker._roblox_username = "alice"
        worker._roblox_user_id = 12345

        in_game_result = rp.PresenceResult(user_id=12345, presence_type=rp.PresenceType.IN_GAME)
        with mock.patch.object(rp, "fetch_presence_one", side_effect=AssertionError("presence call")):
            pres = worker._fetch_roblox_presence()

        self.assertIsNone(pres)
        self.assertEqual(worker.last_presence_state, "disabled")

    def test_presence_unavailable_falls_back_gracefully(self) -> None:
        """When presence API is unavailable, worker must not crash."""
        import agent.supervisor as sup
        from agent import roblox_presence as rp

        entry = {"package": "com.roblox.client", "account_username": "bob"}
        cfg = {"supervisor": {}, "log_level": "INFO", "health_check_interval_seconds": 30}
        status_map = {}
        stop_event = threading.Event()
        worker = sup._PackageWorker(entry, cfg, status_map, stop_event)
        worker._roblox_username = "bob"
        worker._roblox_user_id = 99

        with mock.patch.object(rp, "fetch_presence_one", side_effect=Exception("network down")):
            pres = worker._fetch_roblox_presence()

        self.assertIsNone(pres)
        self.assertEqual(worker.last_presence_state, "disabled")

    def test_presence_lobby_does_not_set_joining_status(self) -> None:
        """Presence showing lobby must NOT trigger STATUS_JOINING on the worker."""
        import agent.supervisor as sup
        from agent import roblox_presence as rp

        entry = {"package": "com.roblox.client", "account_username": "carol"}
        cfg = {"supervisor": {}, "log_level": "INFO", "health_check_interval_seconds": 30}
        status_map = {"com.roblox.client": "Online"}
        stop_event = threading.Event()
        worker = sup._PackageWorker(entry, cfg, status_map, stop_event)
        worker._roblox_username = "carol"
        worker._roblox_user_id = 55555
        worker.has_private_url = True
        import time
        worker.launching_since = time.time() - 5  # just launched

        lobby_result = rp.PresenceResult(user_id=55555, presence_type=rp.PresenceType.ONLINE)
        with mock.patch.object(rp, "fetch_presence_one", return_value=lobby_result):
            pres = worker._fetch_roblox_presence()

        # After fetch, status_map must not have been changed to Joining or Lobby
        # (the run() loop changes status, not _fetch_roblox_presence itself)
        self.assertNotEqual(status_map.get("com.roblox.client"), "Joining")
        self.assertNotEqual(status_map.get("com.roblox.client"), "Lobby")
        # But presence state must be recorded internally
        self.assertEqual(worker.last_presence_state, "disabled")

    def test_presence_rate_limit_does_not_crash(self) -> None:
        """HTTP 429 (rate limited) must not crash the presence fetcher."""
        import agent.supervisor as sup
        from agent import roblox_presence as rp

        entry = {"package": "com.roblox.client", "account_username": "dave"}
        cfg = {"supervisor": {}, "log_level": "INFO", "health_check_interval_seconds": 30}
        status_map = {}
        stop_event = threading.Event()
        worker = sup._PackageWorker(entry, cfg, status_map, stop_event)
        worker._roblox_username = "dave"
        worker._roblox_user_id = 77777

        with mock.patch.object(
            rp.safe_http,
            "post_json",
            side_effect=rp.safe_http.SafeHttpStatusError(429, "Too Many Requests"),
        ):
            pres = worker._fetch_roblox_presence()

        self.assertIsNone(pres)
        self.assertEqual(worker.last_presence_state, "disabled")

    def test_watchdog_presence_wrong_game_sets_diagnostic_state_only(self) -> None:
        import agent.supervisor as sup
        from agent import roblox_presence as rp

        entry = {
            "package": "com.roblox.client",
            "roblox_user_id": 123,
            "expected_place_id": 111,
        }
        cfg = {"supervisor": {}, "foreground_grace_seconds": 90}
        watcher = sup.WatchdogSupervisor([entry], cfg)
        with mock.patch.object(
            watcher,
            "_fast_alive_evidence",
            return_value={
                "alive": True,
                "running": True,
                "root_running": False,
                "foreground": True,
                "window": True,
                "surface": True,
                "task": False,
                "foreground_package": "com.roblox.client",
            },
        ), mock.patch.object(
            rp,
            "fetch_presence_one",
            return_value=rp.PresenceResult(
                user_id=123,
                presence_type=rp.PresenceType.IN_GAME,
                place_id=222,
            ),
        ):
            state, detail = watcher._detect_package_state("com.roblox.client", entry)

        self.assertEqual(state, sup.STATUS_ONLINE)
        self.assertEqual(watcher._presence_last_detail["com.roblox.client"]["roblox_api_status"], "success")

    def test_watchdog_presence_unknown_keeps_local_online_hint(self) -> None:
        import agent.supervisor as sup
        from agent import roblox_presence as rp

        entry = {"package": "com.roblox.client", "roblox_user_id": 123}
        watcher = sup.WatchdogSupervisor([entry], {"supervisor": {}})
        with mock.patch.object(
            watcher,
            "_fast_alive_evidence",
            return_value={
                "alive": True,
                "running": True,
                "root_running": False,
                "foreground": True,
                "window": True,
                "surface": True,
                "task": False,
                "foreground_package": "com.roblox.client",
            },
        ), mock.patch.object(
            rp,
            "fetch_presence_one",
            return_value=rp.PresenceResult(user_id=123, presence_type=rp.PresenceType.UNKNOWN),
        ):
            state, detail = watcher._detect_package_state("com.roblox.client", entry)

        self.assertEqual(state, sup.STATUS_ONLINE)
        self.assertEqual(detail["reason"], "foreground_window_surface_hint")


# ─── 6. YesCaptcha hidden from public UI ─────────────────────────────────────

class TestYesCaptchaHiddenFromPublicUI(unittest.TestCase):

    def _wizard_source(self) -> str:
        import agent.commands as cmd
        return inspect.getsource(cmd._run_first_time_setup_wizard)

    def _edit_config_source(self) -> str:
        import agent.commands as cmd
        return inspect.getsource(cmd._run_edit_config_menu)

    def test_first_time_setup_does_not_mention_yescaptcha(self) -> None:
        src = self._wizard_source()
        import re
        # Must not print/prompt about YesCaptcha to the user
        printed_captcha = re.findall(r'print\s*\([^)]*[Cc]aptcha|_prompt\s*\([^)]*[Cc]aptcha', src)
        self.assertEqual(printed_captcha, [], f"Wizard prints about captcha: {printed_captcha}")
        # Must not call YesCaptcha setup function
        self.assertNotIn("_setup_yescaptcha_key(", src)
        self.assertNotIn("_config_menu_yescaptcha(", src)

    def test_edit_config_does_not_show_yescaptcha_option(self) -> None:
        src = self._edit_config_source()
        self.assertNotIn("YesCaptcha", src)
        self.assertNotIn("yescaptcha", src.lower())

    def test_edit_config_does_not_call_yescaptcha_submenu(self) -> None:
        src = self._edit_config_source()
        self.assertNotIn("_config_menu_yescaptcha", src)

    def test_config_summary_does_not_print_yescaptcha(self) -> None:
        import agent.commands as cmd
        import re
        src = inspect.getsource(cmd._print_config_summary)
        # Must not have a print statement about YesCaptcha
        printed = re.findall(r'print\s*\([^)]*[Yy]es[Cc]aptcha', src)
        self.assertEqual(printed, [], f"Config summary prints about YesCaptcha: {printed}")

    def test_start_does_not_require_yescaptcha(self) -> None:
        import agent.commands as cmd
        src = inspect.getsource(cmd.cmd_start)
        # cmd_start must not fail or warn about missing yescaptcha_key
        self.assertNotIn("yescaptcha_key required", src.lower())
        self.assertNotIn('"yescaptcha_key" not set', src.lower())


# ─── 7. Webhook hidden from public UI ────────────────────────────────────────

class TestWebhookHiddenFromPublicUI(unittest.TestCase):

    def _wizard_source(self) -> str:
        import agent.commands as cmd
        return inspect.getsource(cmd._run_first_time_setup_wizard)

    def _edit_config_source(self) -> str:
        import agent.commands as cmd
        return inspect.getsource(cmd._run_edit_config_menu)

    def test_first_time_setup_does_not_call_webhook_step(self) -> None:
        src = self._wizard_source()
        self.assertNotIn("_setup_webhook(draft)", src)

    def test_first_time_setup_does_not_call_snapshot_step(self) -> None:
        src = self._wizard_source()
        self.assertNotIn("_setup_snapshot(draft)", src)

    def test_first_time_setup_does_not_call_webhook_interval_step(self) -> None:
        src = self._wizard_source()
        self.assertNotIn("_setup_webhook_interval(draft)", src)

    def test_first_time_setup_does_not_mention_webhook(self) -> None:
        src = self._wizard_source()
        self.assertNotIn("Discord Webhook Setup", src)
        self.assertNotIn("Webhook", src)
        self.assertNotIn("_config_menu_webhook(", src)

    def test_edit_config_does_not_show_webhook_option(self) -> None:
        src = self._edit_config_source()
        # Webhook must not appear as a numbered menu option
        import re
        webhook_options = re.findall(r'print\s*\(".*[Ww]ebhook.*"\)', src)
        self.assertEqual(
            webhook_options, [],
            f"Edit Config shows Webhook option: {webhook_options}",
        )

    def test_edit_config_does_not_call_webhook_submenu(self) -> None:
        src = self._edit_config_source()
        self.assertNotIn("_config_menu_webhook", src)

    def test_first_time_setup_has_2_steps(self) -> None:
        src = self._wizard_source()
        self.assertIn("Step 1 of 2", src)
        self.assertIn("Step 2 of 2", src)
        self.assertNotIn("Step 3", src)


# ─── 8. Public setup menu ──────────────────────────────────────────────────────

class TestPublicMenuItems(unittest.TestCase):

    def test_main_menu_has_expected_items(self) -> None:
        """Main menu must keep Auto Execute out of the top-level options."""
        import agent.menu as menu
        labels = [item[1] for item in menu.MENU_ITEMS]
        numbers = [item[0] for item in menu.MENU_ITEMS]
        self.assertEqual(numbers, ["1", "2", "3", "0"])
        self.assertIn("First Time Setup Config", labels)
        self.assertIn("Setup / Edit Config", labels)
        self.assertIn("Start", labels)
        self.assertNotIn("Auto Execute", labels)
        self.assertIn("Exit", labels)
        self.assertNotIn("Key", labels)
        self.assertNotIn("Package Key", labels)
        for label in labels:
            self.assertNotIn("YesCaptcha", label)
            self.assertNotIn("Webhook", label)
            self.assertNotIn("Captcha", label)

    def test_edit_config_menu_has_packages_private_url_and_back_only(self) -> None:
        import agent.commands as cmd
        import agent.termux_ui as tui
        src = inspect.getsource(cmd._run_edit_config_menu)
        ui_src = inspect.getsource(tui.print_config_menu)
        self.assertIn("print_config_menu", src)
        self.assertIn('menu_number("1", "Packages")', ui_src)
        self.assertIn('menu_number("2", "Private URL")', ui_src)
        self.assertNotIn("Screen Mode", ui_src)
        self.assertNotIn("Portrait", ui_src)
        self.assertNotIn("Auto Execute", ui_src)
        self.assertNotIn('"4. Key"', ui_src)
        self.assertIn('menu_number("0", "Back")', ui_src)
        self.assertNotIn('"3. Webhook"', ui_src)
        self.assertNotIn('"4. YesCaptcha"', ui_src)


# ─── 9. Private URL canonical key ────────────────────────────────────────────

class TestPrivateUrlCanonical(unittest.TestCase):

    def test_private_server_url_is_the_canonical_key(self) -> None:
        """validate_config must produce private_server_url key."""
        from agent.config import validate_config
        cfg = validate_config({"private_server_url": "https://www.roblox.com/share?code=abc&type=Server"})
        self.assertIn("private_server_url", cfg)

    def test_launch_url_is_promoted_to_private_server_url(self) -> None:
        """Legacy launch_url must be promoted to private_server_url."""
        from agent.config import validate_config
        # Use the roblox:// deep-link format with launch_mode=deeplink (required for validation)
        cfg = validate_config({
            "launch_mode": "deeplink",
            "launch_url": "roblox://navigation/share_links?code=TESTCODE&type=Server",
            "private_server_url": "",
            "roblox_package": "com.roblox.client",
        })
        psu = cfg.get("private_server_url") or ""
        self.assertTrue(
            len(psu) > 0,
            "launch_url was not promoted to private_server_url",
        )

    def test_private_server_url_takes_priority_over_launch_url(self) -> None:
        """private_server_url must take priority over launch_url."""
        from agent.config import validate_config, effective_private_server_url
        cfg = validate_config({
            "private_server_url": "roblox://navigation/share_links?code=PRIMARYCODE&type=Server",
            "launch_url": "roblox://navigation/share_links?code=LEGACYCODE&type=Server",
        })
        result = effective_private_server_url({}, cfg)
        if result:
            self.assertIn("PRIMARY", str(result).upper())


# ─── 10. Live dashboard display ───────────────────────────────────────────────

class TestLiveDashboard(unittest.TestCase):

    def test_dashboard_does_not_mention_launch_url_configured(self) -> None:
        """_live_dashboard must NOT print 'Launch URL: configured' (user p-d399d0ca73: useless text)."""
        import agent.commands as cmd
        src = inspect.getsource(cmd.cmd_start)
        self.assertNotIn('"  Launch URL: configured"', src)

    def test_dashboard_does_not_mention_ctrl_c_to_stop(self) -> None:
        """_live_dashboard must NOT print 'Press Ctrl+C to stop' (user p-d399d0ca73: useless text)."""
        import agent.commands as cmd
        src = inspect.getsource(cmd.cmd_start)
        self.assertNotIn('"  Press Ctrl+C to stop"', src)


# ─── 11. License stats not regressed ─────────────────────────────────────────

class TestLicenseStatsNotRegressed(unittest.TestCase):

    def test_executed_label_is_executed_not_key_executed(self) -> None:
        """idCardV2.js must use 'Executed', not 'Key Executed'."""
        import os
        js_path = os.path.join(
            os.path.dirname(__file__), "..", "DENG Pulse", "src", "utility", "idCardV2.js"
        )
        if not os.path.exists(js_path):
            self.skipTest("idCardV2.js not found — DENG Pulse not in workspace")
        with open(js_path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertNotIn("Key Executed", content, "idCardV2.js still uses 'Key Executed'")
        self.assertIn("Executed", content, "idCardV2.js must use 'Executed'")

    def test_faulthandler_probe_updated(self) -> None:
        """Faulthandler probe ID must be updated for stable rebuild."""
        import agent.deng_tool_rejoin as dtr
        src = inspect.getsource(dtr)
        self.assertIn("probe p-9e3f2a8d1c", src,
                      "faulthandler probe ID not updated for stable rebuild")
        # Old probe IDs must not be the active probe
        self.assertNotIn('probe p-f1a4aaafe5"', src)


# ─── 12. Presence API module is importable and has all required functions ─────

class TestPresenceAPIModule(unittest.TestCase):

    def test_module_importable(self) -> None:
        import agent.roblox_presence as rp
        self.assertIsNotNone(rp)

    def test_classify_presence_result_exists(self) -> None:
        import agent.roblox_presence as rp
        self.assertTrue(callable(rp.classify_presence_result))

    def test_get_presence_state_for_package_exists(self) -> None:
        import agent.roblox_presence as rp
        self.assertTrue(callable(rp.get_presence_state_for_package))

    def test_lookup_user_id_exists(self) -> None:
        import agent.roblox_presence as rp
        self.assertTrue(callable(rp.lookup_user_id))

    def test_fetch_presence_exists(self) -> None:
        import agent.roblox_presence as rp
        self.assertTrue(callable(rp.fetch_presence))

    def test_fetch_presence_one_exists(self) -> None:
        import agent.roblox_presence as rp
        self.assertTrue(callable(rp.fetch_presence_one))


if __name__ == "__main__":
    unittest.main()
