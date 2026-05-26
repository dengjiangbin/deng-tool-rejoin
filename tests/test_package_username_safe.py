from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from agent.config import default_config, package_entry, validate_config
from agent.commands import _config_menu_package, _package_menu_add
from agent.package_username import detect_package_username_quick, resolve_package_display_username


class PackageUsernameSafeTests(unittest.TestCase):
    def test_config_username_displays_in_package_menu(self) -> None:
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [package_entry("com.moons.litesc", "deng1629", True, "manual")]
        with mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch("agent.commands.safe_io.safe_prompt", return_value="0"), \
             mock.patch("agent.package_username.detect_package_username_quick") as detector, \
             redirect_stdout(io.StringIO()) as out:
            _config_menu_package(cfg)
        detector.assert_not_called()
        text = out.getvalue()
        self.assertIn("com.moons.litesc", text)
        self.assertIn("username: deng1629", text)
        self.assertNotIn("Refresh Account Mapping", text)

    def test_cached_username_displays_without_detection(self) -> None:
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [package_entry("com.moons.litesc", "", True, "not_set")]
        cfg["package_username_cache"] = {"com.moons.litesc": "cacheduser"}
        with mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch("agent.commands.safe_io.safe_prompt", return_value="0"), \
             mock.patch("agent.package_username.detect_package_username_quick") as detector, \
             redirect_stdout(io.StringIO()) as out:
            _config_menu_package(cfg)
        detector.assert_not_called()
        self.assertIn("cacheduser", out.getvalue())

    def test_known_prefs_username_detector_is_bounded_and_exact(self) -> None:
        xml = """<map><string name="username">JBDENG8</string><string name="displayName">DENG</string></map>"""
        with mock.patch("agent.root_access.read_root_file", return_value=xml) as read_root:
            result = detect_package_username_quick("com.moons.litesc", timeout_seconds=1.0)
        self.assertEqual(result.username, "JBDENG8")
        read_root.assert_called_once()
        args, kwargs = read_root.call_args
        self.assertEqual(args[0], "/data/data/com.moons.litesc/shared_prefs/prefs.xml")
        self.assertLessEqual(kwargs["timeout"], 1)
        self.assertLessEqual(kwargs["detect_timeout"], 1)

    def test_detector_failure_falls_back_to_unknown(self) -> None:
        cfg = validate_config(default_config())
        entry = package_entry("com.moons.litesc", "", True, "not_set")
        with mock.patch("agent.root_access.read_root_file", return_value=None):
            updated, result = resolve_package_display_username(entry, cfg, allow_detect=True, timeout_seconds=1.0)
        self.assertEqual(result.username, "Unknown")
        self.assertFalse(updated.get("account_username"))

    def test_manual_add_can_save_optional_label(self) -> None:
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [package_entry("com.roblox.client", "", True, "not_set")]
        prompts = iter(["m", "manualuser", "y"])
        with mock.patch("agent.commands._gather_roblox_candidates_for_ui", return_value=[]), \
             mock.patch("agent.commands._prompt_manual_package", return_value="com.moons.litesc"), \
             mock.patch("agent.commands.android.package_installed", return_value=True), \
             mock.patch("agent.commands.safe_io.safe_prompt", side_effect=lambda *_a, **_k: next(prompts)), \
             mock.patch("agent.commands.save_config", side_effect=lambda data: data), \
             mock.patch("agent.commands._package_menu_refresh_mapping", side_effect=AssertionError("refresh mapping")), \
             mock.patch("agent.commands.account_detect.detect_account_username", side_effect=AssertionError("old username scan")):
            result = _package_menu_add(cfg)
        added = result["roblox_packages"][-1]
        self.assertEqual(added["account_username"], "manualuser")
        self.assertEqual(added["username_source"], "manual")

    def test_public_menu_does_not_call_cookie_or_webview_scans(self) -> None:
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [package_entry("com.moons.litesc", "", True, "not_set")]
        with mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch("agent.commands.safe_io.safe_prompt", return_value="0"), \
             mock.patch("agent.commands._auto_detect_cookies_for_entries", side_effect=AssertionError("cookie scan")), \
             mock.patch("agent.commands._safe_refresh_account_mapping_entries", side_effect=AssertionError("refresh mapping")), \
             mock.patch("agent.commands.account_detect.detect_account_username", side_effect=AssertionError("old username scan")), \
             mock.patch("agent.root_access.read_root_file", return_value=None):
            _config_menu_package(cfg)


if __name__ == "__main__":
    unittest.main()
