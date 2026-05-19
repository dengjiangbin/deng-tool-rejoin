"""Tests for root-assisted account mapping: userId detection, Presence API integration,
mapping table, and Start path isolation.

Covers requirements from the Root-Assisted Roblox Account Mapping Fix prompt.
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
    user_id_from_json_text,
    user_id_from_pref_xml,
)
from agent.config import package_entry


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
        with unittest.mock.patch("agent.android.detect_root") as mock_root, \
             unittest.mock.patch("agent.account_detect._candidate_pref_files", return_value=[]):
            mock_root.return_value = unittest.mock.Mock(available=False, tool=None)
            uid = detect_roblox_user_id("com.roblox.client", entry={}, use_root=True)
            self.assertIsNone(uid)

    def test_root_scan_finds_user_id(self):
        xml_content = '<?xml version="1.0"?><map><long name="userId" value="42424242" /></map>'
        with unittest.mock.patch("agent.android.detect_root") as mock_root, \
             unittest.mock.patch("agent.account_detect._root_scan_for_user_id", return_value=42424242):
            mock_root.return_value = unittest.mock.Mock(available=True, tool="su")
            uid = detect_roblox_user_id("com.roblox.client", entry={}, use_root=True)
            self.assertEqual(uid, 42424242)

    def test_never_raises_on_exception(self):
        with unittest.mock.patch("agent.account_detect.android.detect_root", side_effect=RuntimeError("boom")), \
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


if __name__ == "__main__":
    unittest.main()
