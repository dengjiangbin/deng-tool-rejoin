"""Tests for first-run license gate before the main menu and remote check UX."""

from __future__ import annotations

import argparse
import io
import unittest
import unittest.mock
from contextlib import redirect_stdout

from agent import commands


def _args(*, no_color: bool = False) -> argparse.Namespace:
    return argparse.Namespace(no_color=no_color)


class LicenseGateMenuTests(unittest.TestCase):
    def test_invalid_remote_license_does_not_show_main_menu(self):
        cfg = commands.validate_config(commands.default_config())
        cfg["license"]["key"] = commands.validate_license_key("DENG-AAAA-BBBB-CCCC-DDDD")

        out = io.StringIO()
        with unittest.mock.patch("agent.commands.load_config", return_value=cfg), \
             unittest.mock.patch("agent.commands.save_config", side_effect=lambda c: c), \
             unittest.mock.patch("agent.commands._ensure_install_id_saved", side_effect=lambda c: c), \
             unittest.mock.patch("agent.commands.keystore.DEV_MODE", False), \
             unittest.mock.patch(
                 "agent.commands._remote_license_run_check",
                 return_value=("not_found", "Key not found."),
             ), \
             unittest.mock.patch("agent.commands._is_interactive", return_value=True), \
             unittest.mock.patch("builtins.input", side_effect=["2"]), \
             redirect_stdout(out):
            rc = commands.cmd_menu(_args(no_color=True))

        self.assertEqual(rc, 1)
        text = out.getvalue()
        self.assertNotIn("Choose option:", text)
        self.assertNotIn("First Time Setup Config", text)
        self.assertIn("ERROR:", text)

    def test_valid_remote_license_shows_menu(self):
        cfg = commands.validate_config(commands.default_config())
        cfg["license"]["key"] = commands.validate_license_key("DENG-1111-2222-3333-4444")

        out = io.StringIO()
        with unittest.mock.patch("agent.commands.load_config", return_value=cfg), \
             unittest.mock.patch("agent.commands.save_config", side_effect=lambda c: c), \
             unittest.mock.patch("agent.commands._ensure_install_id_saved", side_effect=lambda c: c), \
             unittest.mock.patch("agent.commands.keystore.DEV_MODE", False), \
             unittest.mock.patch(
                 "agent.commands._remote_license_run_check",
                 return_value=("active", "License active."),
             ), \
             unittest.mock.patch("agent.commands._is_interactive", return_value=True), \
             unittest.mock.patch("agent.menu._is_interactive", return_value=True), \
             unittest.mock.patch("builtins.input", side_effect=["0"]), \
             redirect_stdout(out):
            rc = commands.cmd_menu(_args(no_color=False))

        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("First Time Setup Config", text)
        self.assertIn("Goodbye.", text)

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


if __name__ == "__main__":
    unittest.main()
