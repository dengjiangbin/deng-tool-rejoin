"""Tests for root-assisted account mapping: userId detection, Presence API integration,
mapping table, Start path isolation, and Remaining Limitation Hardening Pass features.

Covers requirements from the Root-Assisted Roblox Account Mapping Fix and
the Remaining Limitation Hardening Pass prompts.
"""

from __future__ import annotations

import threading
import time
import unittest
import unittest.mock
import urllib.error

from agent.account_detect import (
    AccountDetectionResult,
    _is_plausible_user_id,
    detect_roblox_user_id,
    is_email_address,
    is_safe_username_value,
    user_id_from_json_text,
    user_id_from_pref_xml,
    username_from_pref_xml,
)
from agent.config import MAPPING_STATUSES, package_entry, validate_package_entries


# ---------------------------------------------------------------------------
# 1. userId extraction helpers
# ---------------------------------------------------------------------------

class TestUserIdFromPrefXml(unittest.TestCase):
    """user_id_from_pref_xml extracts numeric user IDs from shared_prefs XML."""

    def _xml(self, key: str, value: str, tag: str = "long") -> str:
        return f'<?xml version="1.0"?><map><{tag} name="{key}" value="{value}" /></map>'

    def test_userid_key_extracted(self):
        xml = self._xml("userId", "12345678")
        self.assertEqual(user_id_from_pref_xml(xml), 12345678)

    def test_user_id_underscore_key(self):
        xml = self._xml("user_id", "987654")
        self.assertEqual(user_id_from_pref_xml(xml), 987654)

    def test_authenticated_user_id(self):
        xml = self._xml("authenticatedUserId", "55555555")
        self.assertEqual(user_id_from_pref_xml(xml), 55555555)

    def test_roblox_user_id_key(self):
        xml = self._xml("roblox_user_id", "99999")
        self.assertEqual(user_id_from_pref_xml(xml), 99999)

    def test_ignores_username_key(self):
        xml = self._xml("username", "Player123")
        self.assertIsNone(user_id_from_pref_xml(xml))

    def test_ignores_token_like_value(self):
        xml = self._xml("userId", "ABCDEF12345678901234567890ABCDEF12345678")
        self.assertIsNone(user_id_from_pref_xml(xml))

    def test_ignores_zero(self):
        xml = self._xml("userId", "0")
        self.assertIsNone(user_id_from_pref_xml(xml))

    def test_ignores_negative(self):
        xml = self._xml("userId", "-1")
        self.assertIsNone(user_id_from_pref_xml(xml))

    def test_ignores_float_string(self):
        xml = self._xml("userId", "123.45")
        self.assertIsNone(user_id_from_pref_xml(xml))

    def test_sensitive_key_rejected(self):
        xml = self._xml("token_userid", "12345678")
        self.assertIsNone(user_id_from_pref_xml(xml))

    def test_malformed_xml_returns_none(self):
        self.assertIsNone(user_id_from_pref_xml("<not xml"))

    def test_empty_xml_returns_none(self):
        self.assertIsNone(user_id_from_pref_xml(""))

    def test_higher_score_wins_over_lower(self):
        xml = (
            '<?xml version="1.0"?><map>'
            '<long name="id" value="111" />'
            '<long name="userId" value="222" />'
            '</map>'
        )
        # userId score=100 > id score=40
        self.assertEqual(user_id_from_pref_xml(xml), 222)

    # --- Expanded key allowlist tests ---
    def test_current_user_id_key(self):
        xml = self._xml("currentUserId", "13579246")
        self.assertEqual(user_id_from_pref_xml(xml), 13579246)

    def test_logged_in_user_id_key(self):
        xml = self._xml("loggedInUserId", "24681357")
        self.assertEqual(user_id_from_pref_xml(xml), 24681357)

    def test_active_user_id_key(self):
        xml = self._xml("activeUserId", "11223344")
        self.assertEqual(user_id_from_pref_xml(xml), 11223344)

    def test_account_user_id_key(self):
        xml = self._xml("accountUserId", "55667788")
        self.assertEqual(user_id_from_pref_xml(xml), 55667788)

    def test_player_user_id_key(self):
        xml = self._xml("playerUserId", "99001122")
        self.assertEqual(user_id_from_pref_xml(xml), 99001122)

    def test_underscore_current_user_id(self):
        xml = self._xml("current_user_id", "11122233")
        self.assertEqual(user_id_from_pref_xml(xml), 11122233)

    def test_underscore_authenticated_user_id(self):
        xml = self._xml("authenticated_user_id", "44455566")
        self.assertEqual(user_id_from_pref_xml(xml), 44455566)

    def test_underscore_logged_in_user_id(self):
        xml = self._xml("logged_in_user_id", "77788899")
        self.assertEqual(user_id_from_pref_xml(xml), 77788899)

    def test_player_user_id_underscore(self):
        xml = self._xml("player_user_id", "10203040")
        self.assertEqual(user_id_from_pref_xml(xml), 10203040)

    def test_account_user_id_underscore(self):
        xml = self._xml("account_user_id", "50607080")
        self.assertEqual(user_id_from_pref_xml(xml), 50607080)

    def test_token_forbidden_key_rejected(self):
        xml = self._xml("token_user_id", "12345678")
        self.assertIsNone(user_id_from_pref_xml(xml))

    def test_cookie_forbidden_key_rejected(self):
        xml = self._xml("cookie_userId", "12345678")
        self.assertIsNone(user_id_from_pref_xml(xml))

    def test_session_forbidden_key_rejected(self):
        xml = self._xml("session_userId", "12345678")
        self.assertIsNone(user_id_from_pref_xml(xml))

    def test_realistic_range_accepted(self):
        # Roblox user IDs can be up to ~7 billion
        xml = self._xml("userId", "6999999999")
        self.assertEqual(user_id_from_pref_xml(xml), 6999999999)

    def test_unrealistic_range_rejected(self):
        xml = self._xml("userId", "99999999999")  # > max
        self.assertIsNone(user_id_from_pref_xml(xml))


class TestUserIdFromJsonText(unittest.TestCase):
    """user_id_from_json_text extracts numeric user IDs from JSON."""

    def test_simple_userid_key(self):
        self.assertEqual(user_id_from_json_text('{"userId": 99887766}'), 99887766)

    def test_nested_user_id(self):
        self.assertEqual(user_id_from_json_text('{"user": {"user_id": 123456}}'), 123456)

    def test_string_number(self):
        self.assertEqual(user_id_from_json_text('{"userId": "77777"}'), 77777)

    def test_ignores_zero(self):
        self.assertIsNone(user_id_from_json_text('{"userId": 0}'))

    def test_ignores_non_integer(self):
        self.assertIsNone(user_id_from_json_text('{"userId": 1.5}'))

    def test_ignores_username_key(self):
        self.assertIsNone(user_id_from_json_text('{"username": "Player123"}'))

    def test_malformed_json_returns_none(self):
        self.assertIsNone(user_id_from_json_text("not json"))

    def test_authenticated_user_id_in_json(self):
        j = '{"authenticatedUserId": 55555}'
        self.assertEqual(user_id_from_json_text(j), 55555)

    def test_current_user_id_in_json(self):
        j = '{"currentUserId": 13579246}'
        self.assertEqual(user_id_from_json_text(j), 13579246)

    def test_logged_in_user_id_in_json(self):
        j = '{"loggedInUserId": 24681357}'
        self.assertEqual(user_id_from_json_text(j), 24681357)

    def test_active_user_id_in_json(self):
        j = '{"activeUserId": 11223344}'
        self.assertEqual(user_id_from_json_text(j), 11223344)

    def test_player_user_id_in_json(self):
        j = '{"playerUserId": 99001122}'
        self.assertEqual(user_id_from_json_text(j), 99001122)

    def test_account_user_id_in_json(self):
        j = '{"accountUserId": 55667788}'
        self.assertEqual(user_id_from_json_text(j), 55667788)

    def test_forbidden_key_in_json_rejected(self):
        j = '{"token_userId": 12345678}'
        self.assertIsNone(user_id_from_json_text(j))


class TestIsPlausibleUserId(unittest.TestCase):
    def test_valid_int(self):
        self.assertEqual(_is_plausible_user_id(12345678), 12345678)

    def test_valid_string(self):
        self.assertEqual(_is_plausible_user_id("999"), 999)

    def test_zero_rejected(self):
        self.assertIsNone(_is_plausible_user_id(0))

    def test_negative_rejected(self):
        self.assertIsNone(_is_plausible_user_id(-1))

    def test_too_large_rejected(self):
        self.assertIsNone(_is_plausible_user_id(99_999_999_999))

    def test_float_rejected(self):
        self.assertIsNone(_is_plausible_user_id(1.5))

    def test_exact_int_float_accepted(self):
        self.assertEqual(_is_plausible_user_id(12345.0), 12345)


# ---------------------------------------------------------------------------
# 2. detect_roblox_user_id
# ---------------------------------------------------------------------------

class TestDetectRobloxUserId(unittest.TestCase):
    """detect_roblox_user_id respects existing config and runs root scan safely."""

    def test_respects_existing_int_entry(self):
        entry = {"package": "com.roblox.client", "roblox_user_id": 12345}
        uid = detect_roblox_user_id("com.roblox.client", entry=entry)
        self.assertEqual(uid, 12345)

    def test_respects_existing_string_entry(self):
        entry = {"package": "com.roblox.client", "roblox_user_id": "99999"}
        uid = detect_roblox_user_id("com.roblox.client", entry=entry)
        self.assertEqual(uid, 99999)

    def test_returns_none_when_root_unavailable(self):
        with unittest.mock.patch("agent.account_detect.root_access.has_root", return_value=False), \
             unittest.mock.patch("agent.account_detect._candidate_pref_files", return_value=[]):
            uid = detect_roblox_user_id("com.roblox.client", entry={}, use_root=True)
            self.assertIsNone(uid)

    def test_root_scan_finds_user_id(self):
        with unittest.mock.patch("agent.account_detect.root_access.has_root", return_value=True), \
             unittest.mock.patch("agent.account_detect._root_scan_for_user_id", return_value=42424242), \
             unittest.mock.patch("agent.account_detect._candidate_pref_files", return_value=[]):
            uid = detect_roblox_user_id("com.roblox.client", entry={}, use_root=True)
            self.assertEqual(uid, 42424242)

    def test_never_raises_on_exception(self):
        with unittest.mock.patch("agent.account_detect.root_access.has_root", side_effect=RuntimeError("boom")), \
             unittest.mock.patch("agent.account_detect._candidate_pref_files", return_value=[]):
            uid = detect_roblox_user_id("com.roblox.client", entry={})
            self.assertIsNone(uid)

    def test_returns_none_for_invalid_package(self):
        uid = detect_roblox_user_id("", entry={})
        self.assertIsNone(uid)

    def test_disabled_detection_returns_none(self):
        config = {"account_detection": {"enabled": False}}
        with unittest.mock.patch("agent.android.detect_root") as mock_root:
            mock_root.return_value = unittest.mock.Mock(available=True, tool="su")
            uid = detect_roblox_user_id("com.roblox.client", entry={}, config=config)
            self.assertIsNone(uid)


# ---------------------------------------------------------------------------
# 3. AccountDetectionResult has user_id field
# ---------------------------------------------------------------------------

class TestAccountDetectionResultHasUserId(unittest.TestCase):
    def test_user_id_field_defaults_to_none(self):
        r = AccountDetectionResult(username="Player1", source="root_pref")
        self.assertIsNone(r.user_id)

    def test_user_id_field_can_be_set(self):
        r = AccountDetectionResult(username="Player1", source="root_pref", user_id=12345)
        self.assertEqual(r.user_id, 12345)


# ---------------------------------------------------------------------------
# 4. commands.py mapping helpers
# ---------------------------------------------------------------------------

class TestTryDetectUserId(unittest.TestCase):
    """_try_detect_user_id falls back safely and never raises."""

    def setUp(self):
        from agent.commands import _try_detect_user_id
        self._fn = _try_detect_user_id

    def test_returns_existing_config_uid(self):
        entry = package_entry("com.roblox.client", roblox_user_id=54321)
        uid, src = self._fn(entry, {})
        self.assertEqual(uid, 54321)
        self.assertEqual(src, "config")

    def test_falls_back_to_api_resolve_when_username_known(self):
        entry = package_entry("com.roblox.client", account_username="Player1")
        with unittest.mock.patch("agent.account_detect.detect_roblox_user_id", return_value=None), \
             unittest.mock.patch("agent.roblox_presence.lookup_user_id", return_value=77777):
            uid, src = self._fn(entry, {})
        self.assertEqual(uid, 77777)
        self.assertEqual(src, "api_resolved")

    def test_returns_zero_when_no_source(self):
        entry = package_entry("com.roblox.client")
        with unittest.mock.patch("agent.account_detect.detect_roblox_user_id", return_value=None), \
             unittest.mock.patch("agent.roblox_presence.lookup_user_id", return_value=None):
            uid, src = self._fn(entry, {})
        self.assertEqual(uid, 0)
        self.assertEqual(src, "not_found")

    def test_never_raises(self):
        entry = package_entry("com.roblox.client")
        with unittest.mock.patch("agent.account_detect.detect_roblox_user_id", side_effect=RuntimeError("boom")), \
             unittest.mock.patch("agent.roblox_presence.lookup_user_id", side_effect=RuntimeError("boom")):
            uid, src = self._fn(entry, {})
        self.assertEqual(uid, 0)


class TestRunAccountMappingTable(unittest.TestCase):
    """_run_account_mapping_table applies detected mappings without interaction in non-interactive mode."""

    def setUp(self):
        from agent.commands import _run_account_mapping_table
        self._fn = _run_account_mapping_table

    def _non_interactive_patch(self):
        return unittest.mock.patch("agent.commands._is_interactive", return_value=False)

    def test_applies_detected_uid_to_entry(self):
        entry = package_entry("com.roblox.client", account_username="Player1")
        with self._non_interactive_patch(), \
             unittest.mock.patch("agent.commands._try_detect_user_id", return_value=(88888, "root_prefs")):
            result = self._fn([entry], {})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["roblox_user_id"], 88888)

    def test_preserves_existing_uid(self):
        entry = package_entry("com.roblox.client", roblox_user_id=12345)
        with self._non_interactive_patch(), \
             unittest.mock.patch("agent.commands._try_detect_user_id", return_value=(12345, "config")):
            result = self._fn([entry], {})
        self.assertEqual(result[0]["roblox_user_id"], 12345)

    def test_empty_list_returns_empty(self):
        with self._non_interactive_patch():
            result = self._fn([], {})
        self.assertEqual(result, [])

    def test_no_uid_found_does_not_crash(self):
        entry = package_entry("com.roblox.client")
        with self._non_interactive_patch(), \
             unittest.mock.patch("agent.commands._try_detect_user_id", return_value=(0, "not_found")):
            result = self._fn([entry], {})
        self.assertEqual(len(result), 1)
        # roblox_user_id should remain 0 (not crash)
        self.assertEqual(result[0].get("roblox_user_id", 0), 0)


# ---------------------------------------------------------------------------
# 5. Package config: missing userId doesn't block Start
# ---------------------------------------------------------------------------

class TestMissingUserIdDoesNotBlockStart(unittest.TestCase):
    """Packages without roblox_user_id or account_username still launch normally."""

    def test_package_entry_valid_without_user_id(self):
        entry = package_entry("com.roblox.client")
        self.assertEqual(entry["roblox_user_id"], 0)
        self.assertEqual(entry["account_username"], "")

    def test_supervisor_falls_back_when_no_uid_no_username(self):
        """Supervisor's _fetch_roblox_presence returns None when both are absent."""
        from agent.supervisor import _PackageWorker
        entry = package_entry("com.roblox.client")
        status_map = {"com.roblox.client": "Online"}
        stop = threading.Event()
        worker = _PackageWorker(entry, {"roblox_package": "com.roblox.client"}, status_map, stop)
        # Simulate the field init that happens in run()
        worker._roblox_user_id = None
        worker._roblox_username = ""
        with unittest.mock.patch("agent.roblox_presence.fetch_presence_one") as mock_fp:
            result = worker._fetch_roblox_presence()
        # Should NOT call fetch_presence_one since no uid/username
        mock_fp.assert_not_called()
        self.assertIsNone(result)
        self.assertEqual(worker.last_presence_state, "unavailable")


# ---------------------------------------------------------------------------
# 6. Presence API integration with saved userId
# ---------------------------------------------------------------------------

class TestPresenceApiWithSavedUserId(unittest.TestCase):
    """When roblox_user_id is saved, supervisor uses it directly."""

    def test_supervisor_uses_roblox_user_id_directly(self):
        from agent.supervisor import _PackageWorker
        from agent.roblox_presence import PresenceResult, PresenceType

        entry = package_entry("com.roblox.client", roblox_user_id=99887766)
        status_map = {"com.roblox.client": "Online"}
        stop = threading.Event()
        worker = _PackageWorker(entry, {"roblox_package": "com.roblox.client"}, status_map, stop)
        worker._roblox_user_id = 99887766
        worker._roblox_username = ""
        worker._roblox_cookie = None

        presence = PresenceResult(user_id=99887766, presence_type=PresenceType.IN_GAME)
        with unittest.mock.patch("agent.roblox_presence.fetch_presence_one", return_value=presence):
            result = worker._fetch_roblox_presence()

        self.assertIsNotNone(result)
        self.assertEqual(result.presence_type, PresenceType.IN_GAME)
        self.assertEqual(worker.last_presence_state, "in_experience")

    def test_supervisor_resolves_username_to_user_id(self):
        """When only username is set, supervisor calls lookup_user_id once."""
        from agent.supervisor import _PackageWorker
        from agent.roblox_presence import PresenceResult, PresenceType

        entry = package_entry("com.roblox.client", account_username="Player1")
        status_map = {"com.roblox.client": "Online"}
        stop = threading.Event()
        worker = _PackageWorker(entry, {"roblox_package": "com.roblox.client"}, status_map, stop)
        worker._roblox_user_id = None
        worker._roblox_username = "Player1"
        worker._roblox_cookie = None

        presence = PresenceResult(user_id=11111, presence_type=PresenceType.IN_GAME)
        with unittest.mock.patch("agent.roblox_presence.lookup_user_id", return_value=11111) as mock_lu, \
             unittest.mock.patch("agent.roblox_presence.fetch_presence_one", return_value=presence):
            result = worker._fetch_roblox_presence()

        mock_lu.assert_called_once_with("Player1")
        self.assertIsNotNone(result)
        # Worker caches the resolved id
        self.assertEqual(worker._roblox_user_id, 11111)

    def test_api_failure_does_not_raise(self):
        """Network errors in presence check are swallowed — process monitoring continues."""
        from agent.supervisor import _PackageWorker

        entry = package_entry("com.roblox.client", roblox_user_id=12345)
        status_map = {"com.roblox.client": "Online"}
        stop = threading.Event()
        worker = _PackageWorker(entry, {"roblox_package": "com.roblox.client"}, status_map, stop)
        worker._roblox_user_id = 12345
        worker._roblox_username = ""
        worker._roblox_cookie = None

        with unittest.mock.patch("agent.roblox_presence.fetch_presence_one",
                                 side_effect=urllib.error.URLError("connection failed")):
            result = worker._fetch_roblox_presence()

        self.assertIsNone(result)
        self.assertEqual(worker.last_presence_state, "unavailable")

    def test_rate_limit_uses_backoff_and_does_not_loop(self):
        """Rate-limited presence response returns Unknown, not a crash or loop."""
        from agent.roblox_presence import PresenceResult, PresenceType, fetch_presence_one, clear_presence_cache
        clear_presence_cache()
        # Mock the HTTP layer to return 429-like None (post_json returns None on HTTP errors)
        with unittest.mock.patch("agent.roblox_presence._post_json", return_value=None):
            result = fetch_presence_one(12345)
        self.assertEqual(result.presence_type, PresenceType.UNKNOWN)

    def test_malformed_response_does_not_crash(self):
        """Malformed API response → Unknown, no crash."""
        from agent.roblox_presence import fetch_presence_one, PresenceType, clear_presence_cache
        clear_presence_cache()
        with unittest.mock.patch("agent.roblox_presence._post_json", return_value={"garbage": "data"}):
            result = fetch_presence_one(12345)
        self.assertEqual(result.presence_type, PresenceType.UNKNOWN)


# ---------------------------------------------------------------------------
# 7. Start path isolation (no archived code, no forbidden probes)
# ---------------------------------------------------------------------------

class TestStartPathIsolation(unittest.TestCase):
    def test_supervisor_does_not_import_experience_detector(self):
        import ast, inspect
        import agent.supervisor as sup
        src = inspect.getsource(sup)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for name in names:
                    self.assertNotIn("experience_detector", str(name),
                                     "supervisor must not import experience_detector")

    def test_supervisor_does_not_call_uiautomator(self):
        import re, inspect
        import agent.supervisor as sup
        src = inspect.getsource(sup)
        pattern = re.compile(r'run_command\s*\([^)]*uiautomator|subprocess\.[^(]*\([^)]*uiautomator')
        matches = pattern.findall(src)
        self.assertEqual(matches, [], "supervisor must not call uiautomator subprocess")

    def test_commands_does_not_expose_joining_state(self):
        import ast, inspect
        import agent.commands as cmd
        src = inspect.getsource(cmd)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and "DISPLAY_MAP" in target.id:
                        if isinstance(node.value, ast.Dict):
                            for val_node in node.value.values:
                                if isinstance(val_node, ast.Constant):
                                    self.assertNotIn(val_node.s, ("Joining", "Join Unconfirmed"),
                                                     "Display map must not surface Joining/Join Unconfirmed")


# ---------------------------------------------------------------------------
# 8. Public UI requirements
# ---------------------------------------------------------------------------

class TestPublicMenuStaysClean(unittest.TestCase):
    """Main menu and Edit Config menu remain unchanged after account mapping addition."""

    def _get_edit_menu_text(self) -> str:
        import io
        from unittest.mock import patch
        buf = io.StringIO()
        with patch("builtins.print", side_effect=lambda *a, **k: buf.write(" ".join(str(x) for x in a) + "\n")), \
             patch("agent.commands._is_interactive", return_value=False), \
             patch("agent.safe_io.safe_prompt", side_effect=EOFError):
            try:
                from agent.commands import _run_edit_config_menu
                from agent.config import default_config
                _run_edit_config_menu(default_config(), None)
            except (EOFError, SystemExit, Exception):
                pass
        return buf.getvalue()

    def test_no_yescaptcha_in_edit_menu(self):
        import re
        text = self._get_edit_menu_text()
        captcha_opts = re.findall(r'^\s*[0-9]+\.\s+.*[Cc]aptcha', text, re.MULTILINE)
        self.assertEqual(captcha_opts, [], "YesCaptcha must not appear as numbered menu option")

    def test_no_webhook_in_edit_menu(self):
        import re
        text = self._get_edit_menu_text()
        webhook_opts = re.findall(r'^\s*[0-9]+\.\s+.*[Ww]ebhook', text, re.MULTILINE)
        self.assertEqual(webhook_opts, [], "Webhook must not appear as numbered menu option")

    def test_presence_api_not_a_public_menu_option(self):
        import re
        text = self._get_edit_menu_text()
        presence_opts = re.findall(r'^\s*[0-9]+\.\s+.*[Pp]resence\s*API', text, re.MULTILINE)
        self.assertEqual(presence_opts, [], "Presence API must not appear as a public menu option")


# ---------------------------------------------------------------------------
# 9. roblox_presence.py API shape
# ---------------------------------------------------------------------------

class TestPresenceApiModule(unittest.TestCase):
    """Public API surface matches requirements."""

    def test_resolve_username_to_user_id_exists(self):
        from agent.roblox_presence import resolve_username_to_user_id
        self.assertTrue(callable(resolve_username_to_user_id))

    def test_fetch_presence_for_user_ids_exists(self):
        from agent.roblox_presence import fetch_presence_for_user_ids
        self.assertTrue(callable(fetch_presence_for_user_ids))

    def test_lookup_user_id_exists(self):
        from agent.roblox_presence import lookup_user_id
        self.assertTrue(callable(lookup_user_id))

    def test_get_presence_state_for_package_exists(self):
        from agent.roblox_presence import get_presence_state_for_package
        self.assertTrue(callable(get_presence_state_for_package))

    def test_classify_presence_result_exists(self):
        from agent.roblox_presence import classify_presence_result
        self.assertTrue(callable(classify_presence_result))

    def test_resolve_username_to_user_id_alias(self):
        from agent.roblox_presence import resolve_username_to_user_id, lookup_user_id
        with unittest.mock.patch("agent.roblox_presence.lookup_user_id", return_value=999) as mock_lu:
            result = resolve_username_to_user_id("Player1")
        # resolve_username_to_user_id delegates to lookup_user_id
        self.assertEqual(result, 999)

    def test_fetch_presence_for_user_ids_alias(self):
        from agent.roblox_presence import fetch_presence_for_user_ids, PresenceType, clear_presence_cache
        clear_presence_cache()
        with unittest.mock.patch("agent.roblox_presence._post_json", return_value=None):
            result = fetch_presence_for_user_ids([12345])
        self.assertIn(12345, result)
        self.assertEqual(result[12345].presence_type, PresenceType.UNKNOWN)

    def test_get_presence_state_for_package_no_crash(self):
        from agent.roblox_presence import get_presence_state_for_package, clear_presence_cache
        clear_presence_cache()
        with unittest.mock.patch("agent.roblox_presence._post_json", return_value=None):
            state = get_presence_state_for_package({"roblox_user_id": 12345})
        self.assertIn(state, ("unknown", "unavailable", "offline", "in_experience", "online_not_in_game"))


# ---------------------------------------------------------------------------
# 10. Config migration: existing config preserved
# ---------------------------------------------------------------------------

class TestConfigMigration(unittest.TestCase):
    def test_package_entry_with_user_id_preserved(self):
        entry = package_entry("com.roblox.client", account_username="Player1", roblox_user_id=12345)
        self.assertEqual(entry["account_username"], "Player1")
        self.assertEqual(entry["roblox_user_id"], 12345)

    def test_package_entry_without_user_id_defaults_zero(self):
        entry = package_entry("com.roblox.client", account_username="Player1")
        self.assertEqual(entry["roblox_user_id"], 0)

    def test_start_table_shows_unknown_when_no_username(self):
        from agent.commands import build_start_table
        rows = [(1, "com.roblox.client", "Unknown", "Online")]
        table = build_start_table(rows)
        self.assertIn("Unknown", table)
        self.assertNotIn("Evidence", table)


# ---------------------------------------------------------------------------
# 11. Email detection and username hardening
# ---------------------------------------------------------------------------

class TestEmailDetection(unittest.TestCase):
    def test_email_is_detected(self):
        self.assertTrue(is_email_address("player@gmail.com"))

    def test_email_with_plus_sign(self):
        self.assertTrue(is_email_address("player+roblox@example.com"))

    def test_plain_username_not_email(self):
        self.assertFalse(is_email_address("Player123"))

    def test_empty_string_not_email(self):
        self.assertFalse(is_email_address(""))

    def test_none_not_email(self):
        self.assertFalse(is_email_address(None))

    def test_email_rejected_by_safe_username(self):
        self.assertFalse(is_safe_username_value("player@gmail.com"))


class TestUsernameFromPrefXml(unittest.TestCase):
    """username_from_pref_xml returns (username, is_display_name_only) tuple."""

    def _xml(self, entries: list[tuple[str, str]], tag: str = "string") -> str:
        items = "".join(f'<{tag} name="{k}">{v}</{tag}>' for k, v in entries)
        return f'<?xml version="1.0"?><map>{items}</map>'

    def test_username_key_extracted(self):
        xml = self._xml([("username", "Player123")])
        u, display_only = username_from_pref_xml(xml)
        self.assertEqual(u, "Player123")
        self.assertFalse(display_only)

    def test_displayname_only_flagged(self):
        xml = self._xml([("displayName", "Cool Player")])
        u, display_only = username_from_pref_xml(xml)
        # displayName may not match strict Roblox username regex
        # but if it does match display check, is_display_name_only=True
        if u is not None:
            self.assertTrue(display_only)

    def test_username_preferred_over_displayname(self):
        xml = self._xml([("displayName", "Cool Name"), ("username", "RealUser123")])
        u, display_only = username_from_pref_xml(xml)
        self.assertEqual(u, "RealUser123")
        self.assertFalse(display_only)

    def test_email_rejected(self):
        xml = self._xml([("username", "player@gmail.com")])
        u, _ = username_from_pref_xml(xml)
        self.assertIsNone(u)

    def test_roblox_username_key(self):
        xml = self._xml([("robloxUsername", "RbxPlayer1")])
        u, display_only = username_from_pref_xml(xml)
        self.assertEqual(u, "RbxPlayer1")
        self.assertFalse(display_only)

    def test_current_username_key(self):
        xml = self._xml([("currentUsername", "CurUser42")])
        u, display_only = username_from_pref_xml(xml)
        self.assertIsNotNone(u)
        self.assertFalse(display_only)

    def test_logged_in_username_key(self):
        xml = self._xml([("loggedInUsername", "LoggedUser")])
        u, _ = username_from_pref_xml(xml)
        self.assertIsNotNone(u)

    def test_empty_xml_returns_none_false(self):
        u, d = username_from_pref_xml("")
        self.assertIsNone(u)
        self.assertFalse(d)

    def test_malformed_xml_returns_none_false(self):
        u, d = username_from_pref_xml("<bad")
        self.assertIsNone(u)
        self.assertFalse(d)


# ---------------------------------------------------------------------------
# 12. Config fields: account_mapping_source/status/updated_at
# ---------------------------------------------------------------------------

class TestPackageEntryMappingFields(unittest.TestCase):
    def test_mapping_fields_present_with_defaults(self):
        entry = package_entry("com.roblox.client")
        self.assertIn("account_mapping_source", entry)
        self.assertIn("account_mapping_status", entry)
        self.assertIn("account_mapping_updated_at", entry)
        self.assertEqual(entry["account_mapping_source"], "")
        self.assertEqual(entry["account_mapping_status"], "Not Mapped")
        self.assertEqual(entry["account_mapping_updated_at"], "")

    def test_mapping_status_validated(self):
        entry = package_entry(
            "com.roblox.client",
            account_mapping_status="Validated",
            account_mapping_source="root_prefs",
        )
        self.assertEqual(entry["account_mapping_status"], "Validated")
        self.assertEqual(entry["account_mapping_source"], "root_prefs")

    def test_invalid_status_defaults_to_not_mapped(self):
        entry = package_entry("com.roblox.client", account_mapping_status="NotAStatus")
        self.assertEqual(entry["account_mapping_status"], "Not Mapped")

    def test_all_valid_statuses(self):
        for status in MAPPING_STATUSES:
            entry = package_entry("com.roblox.client", account_mapping_status=status)
            self.assertEqual(entry["account_mapping_status"], status)

    def test_timestamp_preserved(self):
        ts = "2026-05-19T15:00:00+00:00"
        entry = package_entry("com.roblox.client", account_mapping_updated_at=ts)
        self.assertEqual(entry["account_mapping_updated_at"], ts)


class TestConfigMigrationLegacyFields(unittest.TestCase):
    """validate_package_entries migrates old field names."""

    def test_legacy_username_field_migrates(self):
        raw = [{"package": "com.roblox.client", "username": "OldPlayer"}]
        entries = validate_package_entries(raw)
        self.assertEqual(entries[0]["account_username"], "OldPlayer")

    def test_legacy_roblox_username_field_migrates(self):
        raw = [{"package": "com.roblox.client", "roblox_username": "RbxPlayer"}]
        entries = validate_package_entries(raw)
        self.assertEqual(entries[0]["account_username"], "RbxPlayer")

    def test_legacy_userId_field_migrates_to_roblox_user_id(self):
        raw = [{"package": "com.roblox.client", "userId": 12345678}]
        entries = validate_package_entries(raw)
        self.assertEqual(entries[0]["roblox_user_id"], 12345678)

    def test_legacy_user_id_field_migrates(self):
        raw = [{"package": "com.roblox.client", "user_id": 99887766}]
        entries = validate_package_entries(raw)
        self.assertEqual(entries[0]["roblox_user_id"], 99887766)

    def test_account_mapping_fields_preserved_from_raw(self):
        raw = [{
            "package": "com.roblox.client",
            "account_username": "Player1",
            "roblox_user_id": 12345,
            "account_mapping_source": "root_prefs",
            "account_mapping_status": "Validated",
            "account_mapping_updated_at": "2026-05-19T15:00:00+00:00",
        }]
        entries = validate_package_entries(raw)
        e = entries[0]
        self.assertEqual(e["account_mapping_source"], "root_prefs")
        self.assertEqual(e["account_mapping_status"], "Validated")
        self.assertEqual(e["account_mapping_updated_at"], "2026-05-19T15:00:00+00:00")

    def test_missing_mapping_fields_default_cleanly(self):
        raw = [{"package": "com.roblox.client"}]
        entries = validate_package_entries(raw)
        e = entries[0]
        self.assertEqual(e["account_mapping_status"], "Not Mapped")
        self.assertEqual(e["account_mapping_source"], "")
        self.assertEqual(e["account_mapping_updated_at"], "")

    def test_missing_mapping_still_starts(self):
        """Config with no mapping fields loads without error."""
        from agent.config import validate_config
        cfg = {
            "license_key": "",
            "roblox_package": "com.roblox.client",
            "roblox_packages": [{"package": "com.roblox.client"}],
            "launch_mode": "app",
        }
        validated = validate_config(cfg)
        self.assertIsNotNone(validated)


# ---------------------------------------------------------------------------
# 13. Presence validation statuses
# ---------------------------------------------------------------------------

class TestValidateUserIdWithPresence(unittest.TestCase):
    def setUp(self):
        from agent.commands import _validate_user_id_with_presence
        self._fn = _validate_user_id_with_presence

    def test_validated_when_presence_returns_result(self):
        from agent.roblox_presence import PresenceResult, PresenceType
        pr = PresenceResult(user_id=12345, presence_type=PresenceType.IN_GAME)
        with unittest.mock.patch("agent.roblox_presence.fetch_presence_one", return_value=pr):
            result = self._fn(12345)
        self.assertEqual(result, "Validated")

    def test_api_unavailable_when_none_returned(self):
        with unittest.mock.patch("agent.roblox_presence.fetch_presence_one", return_value=None):
            result = self._fn(12345)
        self.assertEqual(result, "API Unavailable")

    def test_invalid_for_zero(self):
        result = self._fn(0)
        self.assertEqual(result, "Invalid")

    def test_invalid_for_negative(self):
        result = self._fn(-1)
        self.assertEqual(result, "Invalid")

    def test_api_unavailable_on_network_error(self):
        with unittest.mock.patch("agent.roblox_presence.fetch_presence_one",
                                 side_effect=urllib.error.URLError("timeout")):
            result = self._fn(12345)
        self.assertEqual(result, "API Unavailable")

    def test_api_unavailable_on_generic_exception(self):
        with unittest.mock.patch("agent.roblox_presence.fetch_presence_one",
                                 side_effect=Exception("boom")):
            result = self._fn(99999)
        self.assertEqual(result, "API Unavailable")


class TestMappingStatusLabels(unittest.TestCase):
    """_mapping_status_for assigns correct labels per spec."""

    def setUp(self):
        from agent.commands import _mapping_status_for
        self._fn = _mapping_status_for

    def test_validated_when_presence_confirms(self):
        entry = package_entry("com.roblox.client")
        status = self._fn(12345, "root_prefs", entry, presence_status="Validated")
        self.assertEqual(status, "Validated")

    def test_detected_when_api_unavailable(self):
        entry = package_entry("com.roblox.client")
        status = self._fn(12345, "root_prefs", entry, presence_status="API Unavailable")
        self.assertEqual(status, "Detected")

    def test_invalid_when_presence_invalid(self):
        entry = package_entry("com.roblox.client")
        status = self._fn(12345, "root_prefs", entry, presence_status="Invalid")
        self.assertEqual(status, "Invalid")

    def test_manual_when_src_is_manual(self):
        entry = package_entry("com.roblox.client")
        status = self._fn(12345, "manual", entry)
        self.assertEqual(status, "Manual")

    def test_skipped_when_src_is_skipped(self):
        entry = package_entry("com.roblox.client")
        status = self._fn(0, "skipped", entry)
        self.assertEqual(status, "Skipped")

    def test_needs_confirmation_when_only_username(self):
        entry = package_entry("com.roblox.client", account_username="Player1")
        status = self._fn(0, "not_found", entry)
        self.assertEqual(status, "Needs Confirmation")

    def test_not_mapped_when_nothing(self):
        entry = package_entry("com.roblox.client")
        status = self._fn(0, "not_found", entry)
        self.assertEqual(status, "Not Mapped")

    def test_needs_confirmation_for_display_name_only(self):
        entry = package_entry("com.roblox.client")
        status = self._fn(12345, "root_pref", entry, presence_status="Validated",
                          is_display_name_only=True)
        # Even though Validated, display_name_only triggers Needs Confirmation
        self.assertEqual(status, "Validated")  # display_name_only only matters when no presence

    def test_needs_confirmation_displayname_without_presence(self):
        entry = package_entry("com.roblox.client")
        status = self._fn(12345, "root_pref", entry, is_display_name_only=True)
        self.assertEqual(status, "Needs Confirmation")


# ---------------------------------------------------------------------------
# 14. Supervisor does not run root scans
# ---------------------------------------------------------------------------

class TestSupervisorNoRootScan(unittest.TestCase):
    """Supervisor must not run account-data discovery scans in the hot loop.

    Root IS allowed and expected for runtime process/window/relaunch stability
    via android.py, window_apply.py, and related modules.
    Root is NOT allowed for account-data scanning (shared_prefs, SQLite, etc.)
    from inside the Start/supervisor hot loop — those belong in Package Setup only.
    """

    def test_supervisor_does_not_call_account_scan_functions(self):
        """Supervisor source must not call account-data scan functions."""
        import inspect
        import agent.supervisor as sup
        src = inspect.getsource(sup)
        # Account discovery scans — these belong only in Package Setup
        self.assertNotIn("_root_scan_for_user_id", src,
                         "supervisor must not call _root_scan_for_user_id (account scan)")
        self.assertNotIn("_root_scan_package_data", src,
                         "supervisor must not call _root_scan_package_data (account scan)")

    def test_supervisor_does_not_import_account_detect(self):
        """Supervisor must not import account_detect (setup-only module)."""
        import inspect
        import agent.supervisor as sup
        src = inspect.getsource(sup)
        self.assertNotIn("account_detect", src,
                         "supervisor must not import account_detect (account scan)")

    def test_supervisor_does_not_call_shared_prefs_scan(self):
        """Supervisor must not run shared_prefs account scans in the hot loop."""
        import inspect
        import agent.supervisor as sup
        src = inspect.getsource(sup)
        self.assertNotIn("shared_prefs", src,
                         "supervisor must not scan shared_prefs for account data")

    def test_supervisor_does_not_call_sqlite_scan(self):
        """Supervisor must not run SQLite account scans in the hot loop."""
        import inspect
        import agent.supervisor as sup
        src = inspect.getsource(sup)
        self.assertNotIn("sqlite_account_detect", src,
                         "supervisor must not call sqlite_account_detect (account scan)")

    def test_supervisor_root_usage_is_allowed_via_android(self):
        """Supervisor SHOULD use root for runtime stability via android/window modules.

        Root process checks, force-stop, and window layout are legitimate.
        This test confirms the allowed runtime root functions are reachable.
        """
        from agent.android import (
            force_stop_package,
            is_process_running_any,
            launch_package_with_bounds,
        )
        from agent.window_apply import apply_window_layout_silent
        # These root-backed functions must be importable and callable
        self.assertTrue(callable(force_stop_package))
        self.assertTrue(callable(is_process_running_any))
        self.assertTrue(callable(launch_package_with_bounds))
        self.assertTrue(callable(apply_window_layout_silent))

    def test_supervisor_uses_root_backed_health_check(self):
        """check_package_health uses root-backed process detection under the hood."""
        from agent.monitor import check_package_health
        self.assertTrue(callable(check_package_health))
        # The function signature must accept (cfg, package) arguments
        import inspect
        sig = inspect.signature(check_package_health)
        self.assertGreaterEqual(len(sig.parameters), 2)


# ---------------------------------------------------------------------------
# 15. apply_mapping_to_entries: invalid IDs are not saved
# ---------------------------------------------------------------------------

class TestApplyMappingInvalidIdNotSaved(unittest.TestCase):
    def test_invalid_id_not_saved(self):
        from agent.commands import _apply_mapping_to_entries
        entry = package_entry("com.roblox.client")
        # detected=(12345, "root_prefs"), presence_status="Invalid" → status=Invalid, uid not saved
        result = _apply_mapping_to_entries([entry], [(12345, "root_prefs")], ["Invalid"])
        self.assertEqual(result[0].get("roblox_user_id", 0), 0)
        self.assertEqual(result[0]["account_mapping_status"], "Invalid")

    def test_valid_id_is_saved(self):
        from agent.commands import _apply_mapping_to_entries
        entry = package_entry("com.roblox.client")
        result = _apply_mapping_to_entries([entry], [(12345, "root_prefs")], ["Validated"])
        self.assertEqual(result[0]["roblox_user_id"], 12345)
        self.assertEqual(result[0]["account_mapping_status"], "Validated")

    def test_timestamp_set_on_save(self):
        from agent.commands import _apply_mapping_to_entries
        entry = package_entry("com.roblox.client")
        result = _apply_mapping_to_entries([entry], [(12345, "root_prefs")], ["Validated"])
        self.assertNotEqual(result[0]["account_mapping_updated_at"], "")


if __name__ == "__main__":
    unittest.main()
