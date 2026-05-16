"""Tests for license gate before the main menu and related Start-time UX."""

from __future__ import annotations

import argparse
import io
import unittest
import unittest.mock
from contextlib import redirect_stdout

from agent import commands


def _args(*, no_color: bool = False) -> argparse.Namespace:
    return argparse.Namespace(no_color=no_color, verbose=False, debug=False)


def _cfg_with_key(key: str = "DENG-1111-2222-3333-4444") -> dict:
    cfg = commands.validate_config(commands.default_config())
    cfg["license"]["key"] = commands.validate_license_key(key)
    return cfg


class LicenseGateMenuTests(unittest.TestCase):
    """cmd_menu must check license BEFORE showing the menu."""

    def test_valid_license_opens_menu(self):
        """With an active (mocked) license, cmd_menu opens the menu."""
        cfg = _cfg_with_key()

        out = io.StringIO()
        with unittest.mock.patch("agent.commands.load_config", return_value=cfg), \
             unittest.mock.patch("agent.commands.save_config", return_value=cfg), \
             unittest.mock.patch("agent.menu.load_config", return_value=cfg), \
             unittest.mock.patch("agent.commands.keystore.DEV_MODE", False), \
             unittest.mock.patch("agent.menu.keystore.DEV_MODE", False), \
             unittest.mock.patch("agent.commands._is_interactive", return_value=True), \
             unittest.mock.patch("agent.menu._is_interactive", return_value=True), \
             unittest.mock.patch("agent.commands._remote_license_run_check", return_value=("active", "OK")), \
             unittest.mock.patch("agent.commands.ensure_app_dirs"), \
             unittest.mock.patch("builtins.input", side_effect=["0"]), \
             redirect_stdout(out):
            rc = commands.cmd_menu(_args(no_color=True))

        self.assertEqual(rc, 0)
        text = out.getvalue()
        # Menu must open
        self.assertIn("Menu:", text)
        self.assertIn("First Time Setup Config", text)
        self.assertIn("Goodbye.", text)

    def test_valid_license_menu_has_no_license_item(self):
        """After license gate passes, 'Enter / Update License Key' must NOT appear in menu."""
        cfg = _cfg_with_key()

        out = io.StringIO()
        with unittest.mock.patch("agent.commands.load_config", return_value=cfg), \
             unittest.mock.patch("agent.commands.save_config", return_value=cfg), \
             unittest.mock.patch("agent.menu.load_config", return_value=cfg), \
             unittest.mock.patch("agent.commands.keystore.DEV_MODE", False), \
             unittest.mock.patch("agent.menu.keystore.DEV_MODE", False), \
             unittest.mock.patch("agent.commands._is_interactive", return_value=True), \
             unittest.mock.patch("agent.menu._is_interactive", return_value=True), \
             unittest.mock.patch("agent.commands._remote_license_run_check", return_value=("active", "OK")), \
             unittest.mock.patch("agent.commands.ensure_app_dirs"), \
             unittest.mock.patch("builtins.input", side_effect=["0"]), \
             redirect_stdout(out):
            rc = commands.cmd_menu(_args(no_color=True))

        text = out.getvalue()
        # License entry is now a pre-menu gate, not a menu item
        self.assertNotIn("Enter / Update License Key", text)
        # New User Help is removed from the menu
        self.assertNotIn("New User Help", text)
        # Setup status block is NOT shown on the normal menu
        self.assertNotIn("Setup Status", text)
        self.assertNotIn("License: Missing", text)

    def test_invalid_license_does_not_open_menu(self):
        """With an invalid/unverified license and user choosing Exit, menu must NOT appear."""
        cfg = _cfg_with_key("DENG-AAAA-BBBB-CCCC-DDDD")
        cfg["license"]["last_status"] = "not_found"

        out = io.StringIO()
        with unittest.mock.patch("agent.commands.load_config", return_value=cfg), \
             unittest.mock.patch("agent.commands.save_config", return_value=cfg), \
             unittest.mock.patch("agent.menu.load_config", return_value=cfg), \
             unittest.mock.patch("agent.commands.keystore.DEV_MODE", False), \
             unittest.mock.patch("agent.commands._is_interactive", return_value=True), \
             unittest.mock.patch("agent.commands._remote_license_run_check",
                                 return_value=("not_found", "Key not found")), \
             unittest.mock.patch("agent.commands.ensure_app_dirs"), \
             unittest.mock.patch("builtins.input", side_effect=["2"]), \
             redirect_stdout(out):
            rc = commands.cmd_menu(_args(no_color=True))

        self.assertNotEqual(rc, 0)
        text = out.getvalue()
        # Menu must NOT open when license fails
        self.assertNotIn("Menu:", text)
        self.assertNotIn("Goodbye.", text)

    def test_dev_mode_skips_license_and_opens_menu(self):
        """DEV_MODE bypasses the license gate entirely."""
        out = io.StringIO()
        with unittest.mock.patch("agent.commands.keystore.DEV_MODE", True), \
             unittest.mock.patch("agent.menu.keystore.DEV_MODE", True), \
             unittest.mock.patch("agent.commands._is_interactive", return_value=True), \
             unittest.mock.patch("agent.menu._is_interactive", return_value=True), \
             unittest.mock.patch("agent.commands.ensure_app_dirs"), \
             unittest.mock.patch("builtins.input", side_effect=["0"]), \
             redirect_stdout(out):
            rc = commands.cmd_menu(_args(no_color=True))

        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("Menu:", text)
        self.assertIn("Goodbye.", text)

    def test_no_args_defaults_to_menu_and_requires_license(self):
        """parse_args([]) defaults to menu; cmd_menu then requires license."""
        ns = commands.parse_args([])
        self.assertEqual(ns.resolved_command, "menu")


class LicensePrintHelpersTests(unittest.TestCase):
    def test_license_ok_plain_no_color(self):
        out = io.StringIO()
        with redirect_stdout(out):
            commands._print_license_ok(use_color=False)
        self.assertEqual(out.getvalue().strip(), "OK: License Verified")

    def test_license_err_plain_prefix(self):
        out = io.StringIO()
        with redirect_stdout(out):
            commands._print_license_err("bad", use_color=False)
        self.assertTrue(out.getvalue().startswith("ERROR:"))


class MenuItemsTests(unittest.TestCase):
    """Validate that the menu is clean and minimal after the license gate."""

    def test_license_item_not_in_menu_items(self):
        """'Enter / Update License Key' must NOT appear in MENU_ITEMS."""
        from agent.menu import MENU_ITEMS

        labels = [item[1] for item in MENU_ITEMS]
        self.assertNotIn("Enter / Update License Key", labels)

    def test_new_user_help_not_in_menu_items(self):
        """'New User Help' must NOT appear in MENU_ITEMS."""
        from agent.menu import MENU_ITEMS

        labels = [item[1] for item in MENU_ITEMS]
        self.assertNotIn("New User Help", labels)

    def test_essential_menu_items_present(self):
        """First Time Setup, Config, Start, Exit must be in MENU_ITEMS."""
        from agent.menu import MENU_ITEMS

        labels = [item[1] for item in MENU_ITEMS]
        self.assertIn("First Time Setup Config", labels)
        self.assertIn("Setup / Edit Config", labels)
        self.assertIn("Start", labels)
        self.assertIn("Exit", labels)

    def test_menu_prelude_empty_when_packages_configured(self):
        """When packages are configured, the menu prelude must be empty."""
        from agent.menu import _menu_prelude_lines
        from agent.config import default_config, validate_config

        cfg = validate_config(default_config())
        # Add a fake enabled package entry so packages are detected as configured
        cfg["roblox_packages"] = [{"package": "com.roblox.client", "enabled": True}]
        with unittest.mock.patch("agent.menu.load_config", return_value=cfg):
            lines = _menu_prelude_lines()
        self.assertEqual(lines, [])

    def test_menu_prelude_hints_setup_when_no_packages(self):
        """When no packages configured, prelude shows a minimal one-line setup hint."""
        from agent.menu import _menu_prelude_lines
        from agent.config import ConfigError

        with unittest.mock.patch("agent.menu.load_config", side_effect=ConfigError("not found")):
            lines = _menu_prelude_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("Setup required", lines[0])

    def test_setup_status_block_not_shown_on_clean_menu(self):
        """After license gate passes, Setup Status block must NOT appear in menu output."""
        cfg = _cfg_with_key()
        # Simulate a package configured so prelude is empty
        cfg["roblox_packages"] = [{"package": "com.roblox.client", "enabled": True}]

        out = io.StringIO()
        with unittest.mock.patch("agent.commands.load_config", return_value=cfg), \
             unittest.mock.patch("agent.commands.save_config", return_value=cfg), \
             unittest.mock.patch("agent.menu.load_config", return_value=cfg), \
             unittest.mock.patch("agent.commands.keystore.DEV_MODE", False), \
             unittest.mock.patch("agent.menu.keystore.DEV_MODE", False), \
             unittest.mock.patch("agent.commands._is_interactive", return_value=True), \
             unittest.mock.patch("agent.menu._is_interactive", return_value=True), \
             unittest.mock.patch("agent.commands._remote_license_run_check", return_value=("active", "OK")), \
             unittest.mock.patch("agent.commands.ensure_app_dirs"), \
             unittest.mock.patch("builtins.input", side_effect=["0"]), \
             redirect_stdout(out):
            commands.cmd_menu(_args(no_color=True))

        text = out.getvalue()
        self.assertNotIn("Setup Status", text)
        self.assertNotIn("License: Missing", text)
        self.assertNotIn("Config: Not created", text)
        self.assertNotIn("Packages:", text)


if __name__ == "__main__":
    unittest.main()
