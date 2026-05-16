import io
import json
import logging
import unittest
import unittest.mock

from agent import account_detect
from agent.account_detect import (
    AccountDetectionResult,
    detect_account_username,
    detect_account_usernames_for_packages,
    detect_account_username_for_package,
    get_cached_account_username,
    is_safe_username_value,
    is_sensitive_key_name,
    is_sensitive_value,
    sanitize_detected_username,
    set_cached_account_username,
    username_from_pref_xml,
)
from agent.config import default_config, package_entry, validate_config


class AccountDetectTests(unittest.TestCase):
    def setUp(self) -> None:
        account_detect.set_sqlite_username_hook(None)
        account_detect._USERNAME_CACHE.clear()

    def test_extracts_only_allowlisted_username_key(self):
        xml = """<?xml version="1.0" encoding="utf-8"?>
<map>
  <string name="display_name">deng1629</string>
  <string name="theme">dark</string>
</map>
"""
        self.assertEqual(username_from_pref_xml(xml), "deng1629")

    def test_user_name_beats_display_name_in_xml(self):
        xml = """<?xml version="1.0" encoding="utf-8"?>
<map>
  <string name="displayName">Shown Name</string>
  <string name="username">strict_user_1</string>
</map>
"""
        self.assertEqual(username_from_pref_xml(xml), "strict_user_1")

    def test_ignores_forbidden_secret_keys(self):
        xml = """<?xml version="1.0" encoding="utf-8"?>
<map>
  <string name="session_token">abcdef1234567890abcdef1234567890abcdef</string>
  <string name="cookie">.ROBLOSECURITY=secret</string>
  <string name="password">nope</string>
</map>
"""
        self.assertIsNone(username_from_pref_xml(xml))

    def test_ignores_token_like_values_even_on_safe_keys(self):
        xml = """<?xml version="1.0" encoding="utf-8"?>
<map>
  <string name="username">abcdefghijklmnopqrstuvwxyz1234567890</string>
</map>
"""
        self.assertIsNone(username_from_pref_xml(xml))

    def test_safe_username_value_rules(self):
        self.assertTrue(is_safe_username_value("AltAccount1"))
        self.assertFalse(is_safe_username_value("abc/def"))
        self.assertFalse(is_safe_username_value("token_abcdefghijklmnopqrstuvwxyz123456"))

    def test_sensitive_key_and_value(self):
        self.assertTrue(is_sensitive_key_name("csrfToken"))
        self.assertTrue(is_sensitive_value(".ROBLOSECURITY=xyz"))
        self.assertTrue(is_sensitive_value("https://example.com"))
        self.assertFalse(is_sensitive_value("deng1629"))

    def test_sanitize_username(self):
        self.assertEqual(sanitize_detected_username("  a  b  "), "a b")

    def test_config_manual_wins(self):
        cfg = validate_config(default_config())
        entry = package_entry("com.roblox.client", "SavedUser", True, "manual")
        r = detect_account_username(
            "com.roblox.client",
            entry=entry,
            config=cfg,
            respect_config_manual=True,
        )
        self.assertIsNotNone(r)
        self.assertEqual(r.username, "SavedUser")
        self.assertEqual(r.source, "config_manual")

    def test_json_username_detected(self):
        payload = {"userName": "jsonUser1", "extra": "x"}
        self.assertEqual(account_detect._username_from_json_text(json.dumps(payload)), "jsonUser1")

    def test_json_display_name_fallback(self):
        payload = {"displayName": "Nice Name"}
        self.assertEqual(account_detect._username_from_json_text(json.dumps(payload)), "Nice Name")

    def test_json_ignores_sensitive_keys(self):
        payload = {"authToken": "should_not_show", "username": "okuser"}
        # authToken key is sensitive — skipped; username still read
        self.assertEqual(account_detect._username_from_json_text(json.dumps(payload)), "okuser")

    def test_root_unavailable_returns_none_cleanly(self):
        cfg = validate_config(default_config())
        with unittest.mock.patch("agent.account_detect.detect_android_app_label", return_value=None):
            with unittest.mock.patch("agent.account_detect.detect_username_from_safe_prefs", return_value=None):
                with unittest.mock.patch("agent.android.detect_root", return_value=unittest.mock.MagicMock(available=False, tool=None)):
                    with unittest.mock.patch("agent.account_detect._root_scan_package_data", return_value=(None, None)):
                        r = detect_account_username(
                            "com.roblox.client",
                            entry=package_entry("com.roblox.client", "", True, "not_set"),
                            config=cfg,
                            use_root=True,
                            respect_config_manual=False,
                        )
        self.assertIsNone(r)

    def test_timeout_returns_unknown_cleanly(self):
        cfg = validate_config(default_config())
        with unittest.mock.patch("agent.account_detect.detect_android_app_label", return_value=None):
            with unittest.mock.patch("agent.account_detect.detect_username_from_safe_prefs", return_value=None):
                with unittest.mock.patch("agent.android.detect_root", return_value=unittest.mock.MagicMock(available=True, tool="su")):
                    with unittest.mock.patch(
                        "agent.android.run_root_command",
                        return_value=unittest.mock.MagicMock(ok=False, timed_out=True, stdout="", stderr="timeout"),
                    ):
                        r = detect_account_username(
                            "com.roblox.client",
                            entry=package_entry("com.roblox.client", "", True, "not_set"),
                            config=cfg,
                            respect_config_manual=False,
                        )
        self.assertIsNone(r)

    def test_sqlite_hook_username(self):
        account_detect.set_sqlite_username_hook(lambda _p: "SqliteNick")
        cfg = validate_config(default_config())
        with unittest.mock.patch("agent.account_detect.detect_android_app_label", return_value=None):
            with unittest.mock.patch("agent.account_detect.detect_username_from_safe_prefs", return_value=None):
                with unittest.mock.patch("agent.android.detect_root", return_value=unittest.mock.MagicMock(available=True, tool="su")):
                    with unittest.mock.patch(
                        "agent.account_detect._root_list_scan_files",
                        return_value=["/data/data/com.roblox.client/databases/app.db"],
                    ):
                        with unittest.mock.patch("agent.account_detect._root_read_file_capped", return_value=None):
                            r = detect_account_username(
                                "com.roblox.client",
                                entry=package_entry("com.roblox.client", "", True, "not_set"),
                                config=cfg,
                                respect_config_manual=False,
                            )
        self.assertIsNotNone(r)
        self.assertEqual(r.username, "SqliteNick")
        self.assertEqual(r.source, "root_sqlite")

    def test_no_raw_file_contents_logged(self):
        cfg = validate_config(default_config())
        stream = io.StringIO()
        h = logging.StreamHandler(stream)
        log = logging.getLogger("deng_tool_rejoin")
        old_handlers = list(log.handlers)
        log.handlers = [h]
        try:
            with unittest.mock.patch("agent.account_detect.detect_android_app_label", return_value=None):
                with unittest.mock.patch("agent.account_detect.detect_username_from_safe_prefs", return_value=None):
                    with unittest.mock.patch("agent.android.detect_root", return_value=unittest.mock.MagicMock(available=True, tool="su")):
                        with unittest.mock.patch(
                            "agent.account_detect._root_list_scan_files",
                            return_value=["/data/data/com.roblox.client/cache/x.json"],
                        ):
                            with unittest.mock.patch(
                                "agent.account_detect._root_read_file_capped",
                                return_value='{"secret":"Bearer superlongsecrettokenzzzz","username":"u1"}',
                            ):
                                detect_account_username(
                                    "com.roblox.client",
                                    entry=package_entry("com.roblox.client", "", True, "not_set"),
                                    config=cfg,
                                    respect_config_manual=False,
                                )
            out = stream.getvalue()
            self.assertNotIn("Bearer", out)
            self.assertNotIn("superlong", out)
        finally:
            log.handlers = old_handlers

    def test_cache_get_set(self):
        set_cached_account_username("com.roblox.client", "cacheuser")
        self.assertEqual(get_cached_account_username("com.roblox.client"), "cacheuser")

    def test_detect_usernames_for_packages_order(self):
        cfg = validate_config(default_config())
        pkgs = [
            package_entry("com.roblox.client", "Main", True, "manual"),
            package_entry("com.moons.alt1", "", True, "not_set"),
        ]
        with unittest.mock.patch("agent.account_detect.detect_account_username") as m:
            m.side_effect = [
                AccountDetectionResult("Main", "config_manual"),
                AccountDetectionResult("altfound", "root_pref"),
            ]
            pairs = detect_account_usernames_for_packages(pkgs, config=cfg, respect_config_manual=False)
        self.assertEqual(len(pairs), 2)

    def test_backward_compat_detect_account_username_for_package(self):
        with unittest.mock.patch.object(account_detect, "detect_account_username", return_value=AccountDetectionResult("x", "android_app_label")) as m:
            r = detect_account_username_for_package("com.roblox.client")
        self.assertEqual(r.username, "x")
        m.assert_called_once()


class CandidatePrefFilesPermissionTests(unittest.TestCase):
    """_candidate_pref_files must never crash on PermissionError."""

    def test_permission_error_on_exists_returns_empty(self):
        """PermissionError when checking if base path exists returns [] without crashing."""
        from agent.account_detect import _candidate_pref_files
        from pathlib import Path

        with unittest.mock.patch.object(Path, "exists", side_effect=PermissionError("denied")):
            result = _candidate_pref_files("com.roblox.client")
        self.assertEqual(result, [])

    def test_permission_error_on_is_dir_returns_empty(self):
        """PermissionError when checking is_dir returns [] without crashing."""
        from agent.account_detect import _candidate_pref_files
        from pathlib import Path

        with unittest.mock.patch.object(Path, "exists", return_value=True), \
             unittest.mock.patch.object(Path, "is_dir", side_effect=PermissionError("denied")):
            result = _candidate_pref_files("com.roblox.client")
        self.assertEqual(result, [])

    def test_permission_error_on_glob_returns_partial(self):
        """PermissionError during glob returns whatever was collected without crashing."""
        from agent.account_detect import _candidate_pref_files
        from pathlib import Path

        with unittest.mock.patch.object(Path, "exists", return_value=True), \
             unittest.mock.patch.object(Path, "is_dir", return_value=True), \
             unittest.mock.patch.object(Path, "glob", side_effect=PermissionError("denied")):
            result = _candidate_pref_files("com.roblox.client")
        # No crash; result is [] (empty because glob failed before producing anything)
        self.assertIsInstance(result, list)

    def test_detect_username_from_safe_prefs_no_crash_on_permission(self):
        """detect_username_from_safe_prefs returns None without crashing on PermissionError."""
        from agent.account_detect import detect_username_from_safe_prefs
        from pathlib import Path

        with unittest.mock.patch.object(Path, "exists", side_effect=PermissionError("denied")):
            result = detect_username_from_safe_prefs("com.roblox.client")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
