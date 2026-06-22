from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from agent.config import default_config, package_entry, validate_config
from agent.commands import _config_menu_package, _package_menu_add
from agent import package_username, root_access
from agent.package_username import detect_package_username_quick, resolve_package_display_username


def _root_ok() -> root_access.RootCheckReport:
    return root_access.RootCheckReport(
        ok=True, tool="su", uid="uid=0(root)", whoami="root",
        data_dir_readable=True, steps=(), detail="ok",
    )


class PackageUsernameSafeTests(unittest.TestCase):
    def test_root_scan_username_displays_in_package_menu(self) -> None:
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [package_entry("com.moons.litesc", "deng1629", True, "manual")]
        with mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch("agent.commands.safe_io.safe_prompt", return_value="0"), \
             mock.patch("agent.commands.safe_io.tty_session"), \
             mock.patch("agent.commands.package_username.scan_package_username_for_menu") as scan_fn, \
             redirect_stdout(io.StringIO()) as out:
            scan_fn.return_value = package_username.UsernameScanReport(
                package="com.moons.litesc",
                username="deng1629",
                source="root_shared_prefs",
                supported=True,
                reason="",
                root_used=True,
            )
            _config_menu_package(cfg)
        text = out.getvalue()
        self.assertIn("com.moons.litesc", text)
        self.assertIn("username: deng1629", text)
        self.assertNotIn("Refresh Account Mapping", text)

    def test_root_scan_displays_no_account_without_cache(self) -> None:
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [package_entry("com.moons.litesc", "", True, "not_set")]
        cfg["package_username_cache"] = {"com.moons.litesc": "cacheduser"}
        with mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch("agent.commands.safe_io.safe_prompt", return_value="0"), \
             mock.patch("agent.commands.safe_io.tty_session"), \
             mock.patch("agent.commands.package_username.scan_package_username_for_menu") as scan_fn, \
             mock.patch("agent.commands.get_package_display_username", return_value=package_username.NO_ACCOUNT_LABEL), \
             redirect_stdout(io.StringIO()) as out:
            scan_fn.return_value = package_username.UsernameScanReport(
                package="com.moons.litesc",
                username="",
                source="root_scan_no_account",
                supported=True,
                reason="no_logged_in_account_found_in_root_readable_data",
                root_used=True,
            )
            _config_menu_package(cfg)
        self.assertIn("no account", out.getvalue().lower())
        self.assertNotIn("cacheduser", out.getvalue())

    def test_known_prefs_username_detector_is_bounded_and_exact(self) -> None:
        xml = """<map><string name="username">JBDENG8</string><string name="displayName">DENG</string></map>"""
        with mock.patch("agent.package_username.root_access.root_required_preflight", return_value=_root_ok()), \
             mock.patch("agent.package_username.root_access.list_root_glob", return_value=["/data/data/com.moons.litesc/shared_prefs/prefs.xml"]), \
             mock.patch("agent.package_username.root_access.read_root_file", return_value=xml) as read_root, \
             mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("", True, "")), \
             mock.patch("agent.android.package_installed", return_value=True):
            result = detect_package_username_quick("com.moons.litesc", timeout_seconds=1.0)
        self.assertEqual(result.username, "JBDENG8")
        read_root.assert_called()

    def test_detector_failure_returns_scanner_error_not_unknown(self) -> None:
        cfg = validate_config(default_config())
        entry = package_entry("com.moons.litesc", "", True, "not_set")
        pre = root_access.RootCheckReport(
            ok=False, tool=None, uid="", whoami="", data_dir_readable=False,
            steps=(), detail="no root", error="no root",
        )
        with mock.patch("agent.package_username.root_access.root_required_preflight", return_value=pre):
            updated, result = resolve_package_display_username(entry, cfg, allow_detect=True, timeout_seconds=1.0)
        self.assertNotEqual(result.username, "Unknown")
        self.assertIn("Scanner Error", result.username)
        self.assertFalse(updated.get("account_username"))

    def test_manual_add_saves_detected_username_automatically(self) -> None:
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [package_entry("com.roblox.client", "", True, "not_set")]
        prompts = iter(["m", "y"])
        with mock.patch("agent.commands._gather_roblox_candidates_for_ui", return_value=[]), \
             mock.patch("agent.commands._prompt_manual_package", return_value="com.moons.litesc"), \
             mock.patch("agent.commands.android.package_installed", return_value=True), \
             mock.patch("agent.commands.safe_io.safe_prompt", side_effect=lambda *_a, **_k: next(prompts)), \
             mock.patch("agent.commands.save_config", side_effect=lambda data: data), \
             mock.patch("agent.commands._package_menu_refresh_mapping", side_effect=AssertionError("refresh mapping")), \
             mock.patch("agent.commands.account_detect.detect_account_username", side_effect=AssertionError("old username scan")), \
             mock.patch("agent.package_username.safe_detect_username_for_package", return_value="autouser"):
            result = _package_menu_add(cfg)
        added = result["roblox_packages"][-1]
        self.assertEqual(added["account_username"], "autouser")
        self.assertEqual(added["username_source"], "detected_safe_pref")

    def test_public_menu_does_not_call_cookie_or_webview_scans(self) -> None:
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [package_entry("com.moons.litesc", "", True, "not_set")]
        row = package_username.UsernameDisplayRow(
            "com.moons.litesc", package_username.NO_ACCOUNT_LABEL, "no_account", "root_scan_no_account"
        )
        with mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch("agent.commands.safe_io.safe_prompt", return_value="0"), \
             mock.patch("agent.commands.safe_io.tty_session"), \
             mock.patch("agent.commands._auto_detect_cookies_for_entries", side_effect=AssertionError("cookie scan")), \
             mock.patch("agent.commands._safe_refresh_account_mapping_entries", side_effect=AssertionError("refresh mapping")), \
             mock.patch("agent.commands.account_detect.detect_account_username", side_effect=AssertionError("old username scan")), \
             mock.patch("agent.package_username.username_display_for_package", return_value=row):
            _config_menu_package(cfg)


if __name__ == "__main__":
    unittest.main()
