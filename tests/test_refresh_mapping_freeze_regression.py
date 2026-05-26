from __future__ import annotations

import io
import re
import unittest
from contextlib import redirect_stdout
from unittest import mock

from agent.config import default_config, package_entry, validate_config
from agent.commands import (
    _choose_packages_menu,
    _config_menu_package,
    _package_menu_add,
    _package_menu_auto_detect,
)


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _plain(text: str) -> str:
    return ANSI_RE.sub("", text)


def _cfg(packages: list[dict] | None = None) -> dict:
    cfg = validate_config(default_config())
    if packages is not None:
        cfg["roblox_packages"] = packages
    return cfg


class TestRefreshMappingRemoval(unittest.TestCase):
    """Refresh Account Mapping is removed from public runtime paths."""

    def test_package_submenu_does_not_show_refresh_mapping(self):
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        with mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch("agent.commands.safe_io.safe_prompt", return_value="0"), \
             redirect_stdout(io.StringIO()) as buf:
            result = _config_menu_package(cfg)
        self.assertIs(result, cfg)
        out = _plain(buf.getvalue())
        self.assertIn("Auto Detect Package", out)
        self.assertIn("Add Package", out)
        self.assertIn("Remove Package", out)
        self.assertNotIn("Refresh Account Mapping", out)
        self.assertNotIn("Account Mapping", out)

    def test_old_refresh_mapping_option_is_invalid_unreachable(self):
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        prompts = iter(["5", "0"])
        with mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch("agent.commands.safe_io.safe_prompt", side_effect=lambda *_a, **_k: next(prompts)), \
             mock.patch("agent.commands._package_menu_refresh_mapping", side_effect=AssertionError("refresh mapping")), \
             redirect_stdout(io.StringIO()) as buf:
            _config_menu_package(cfg)
        out = _plain(buf.getvalue())
        self.assertIn("Invalid", out)
        self.assertNotIn("Refresh Account Mapping", out)

    def test_first_time_auto_detect_saves_package_names_only(self):
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        candidate = mock.Mock(package="com.moons.litesc", app_name="Lite C", launchable=True)
        prompts = iter(["1", "a"])
        with mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch("agent.commands._gather_roblox_candidates_for_ui", return_value=[candidate]), \
             mock.patch("agent.commands.safe_io.safe_prompt", side_effect=lambda *_a, **_k: next(prompts)), \
             mock.patch("agent.commands._run_account_mapping_table", side_effect=AssertionError("old mapping")), \
             mock.patch("agent.commands._auto_detect_cookies_for_entries", side_effect=AssertionError("cookie scan")), \
             mock.patch("agent.commands._safe_refresh_account_mapping_entries", side_effect=AssertionError("refresh mapping")), \
             mock.patch("agent.commands.account_detect.detect_account_username", side_effect=AssertionError("username scan")):
            selected, _hints = _choose_packages_menu(cfg["roblox_packages"], cfg["package_detection_hints"], cfg)
        self.assertEqual(selected[0]["package"], "com.moons.litesc")
        self.assertFalse(selected[0].get("account_username"))
        self.assertFalse(selected[0].get("roblox_cookie"))
        self.assertFalse(selected[0].get("roblox_user_id"))

    def test_setup_config_auto_detect_saves_package_names_only(self):
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        candidate = mock.Mock(package="com.moons.litesc", app_name="Lite C", launchable=True)
        with mock.patch("agent.commands._gather_roblox_candidates_for_ui", return_value=[candidate]), \
             mock.patch("agent.commands.safe_io.safe_prompt", return_value="a"), \
             mock.patch("agent.commands.save_config", side_effect=lambda data: data), \
             mock.patch("agent.commands._run_account_mapping_table", side_effect=AssertionError("old mapping")), \
             mock.patch("agent.commands._auto_detect_cookies_for_entries", side_effect=AssertionError("cookie scan")), \
             mock.patch("agent.commands._safe_refresh_account_mapping_entries", side_effect=AssertionError("refresh mapping")), \
             mock.patch("agent.commands.account_detect.detect_account_username", side_effect=AssertionError("username scan")):
            result = _package_menu_auto_detect(cfg)
        added = result["roblox_packages"][-1]
        self.assertEqual(added["package"], "com.moons.litesc")
        self.assertFalse(added.get("account_username"))
        self.assertFalse(added.get("roblox_cookie"))
        self.assertFalse(added.get("roblox_user_id"))

    def test_manual_add_saves_package_name_only(self):
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        prompts = iter(["m", "y"])
        with mock.patch("agent.commands._gather_roblox_candidates_for_ui", return_value=[]), \
             mock.patch("agent.commands._prompt_manual_package", return_value="com.moons.litesc"), \
             mock.patch("agent.commands.android.package_installed", return_value=True) as installed, \
             mock.patch("agent.commands.safe_io.safe_prompt", side_effect=lambda *_a, **_k: next(prompts)), \
             mock.patch("agent.commands.save_config", side_effect=lambda data: data), \
             mock.patch("agent.commands._detect_or_prompt_account_username", side_effect=AssertionError("old username detect")), \
             mock.patch("agent.commands._run_account_mapping_table", side_effect=AssertionError("old mapping")), \
             mock.patch("agent.commands._auto_detect_cookies_for_entries", side_effect=AssertionError("cookie scan")), \
             mock.patch("agent.commands._safe_refresh_account_mapping_entries", side_effect=AssertionError("refresh mapping")):
            result = _package_menu_add(cfg)
        installed.assert_called_once_with("com.moons.litesc")
        added = result["roblox_packages"][-1]
        self.assertEqual(added["package"], "com.moons.litesc")
        self.assertFalse(added.get("account_username"))
        self.assertFalse(added.get("roblox_cookie"))
        self.assertFalse(added.get("roblox_user_id"))


if __name__ == "__main__":
    unittest.main()
