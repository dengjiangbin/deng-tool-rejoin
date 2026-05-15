"""Tests for main-menu onboarding, New User Help, and beginner Start messages."""

from __future__ import annotations

import argparse
import io
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

from agent import commands
from agent.config import (
    default_config,
    package_entry,
    validate_config,
    validate_license_key,
)
from agent.onboarding import NEW_USER_HELP_TEXT, build_onboarding_lines


def _args(*, no_color: bool = True) -> argparse.Namespace:
    return argparse.Namespace(no_color=no_color, verbose=False, debug=False)


class OnboardingStatusTests(unittest.TestCase):
    def test_setup_status_when_license_and_packages_missing(self):
        cfg = validate_config(default_config())
        cfg["license"]["key"] = ""
        cfg["license_key"] = ""
        cfg["first_setup_completed"] = False
        cfg["roblox_packages"] = [package_entry("com.example.off", "", False, "not_set")]

        lines = build_onboarding_lines(cfg, dev_mode=False)
        text = "\n".join(lines)
        self.assertIn("Setup Status", text)
        self.assertIn("License: Missing", text)
        self.assertIn("Config: Not created", text)
        self.assertIn("Packages: None selected", text)
        self.assertIn("Private URL: Optional", text)
        self.assertIn("Next Step:", text)

    def test_ready_to_start_when_license_config_packages_ok(self):
        cfg = validate_config(default_config())
        cfg["license"]["key"] = validate_license_key("DENG-1111-2222-3333-4444")
        cfg["license"]["last_status"] = "active"
        cfg["first_setup_completed"] = True
        cfg["roblox_packages"] = [
            package_entry("com.roblox.client", "Main", True, "manual"),
        ]
        lines = build_onboarding_lines(cfg, dev_mode=False)
        text = "\n".join(lines)
        self.assertIn("Ready To Start", text)
        self.assertIn("# | Package | Username | State", text)

    def test_dev_mode_license_line(self):
        cfg = validate_config(default_config())
        lines = build_onboarding_lines(cfg, dev_mode=True)
        self.assertTrue(any("development mode" in line.lower() for line in lines))


class NewUserHelpTests(unittest.TestCase):
    def test_help_text_covers_states_and_table(self):
        self.assertIn("Preparing", NEW_USER_HELP_TEXT)
        self.assertIn("Online", NEW_USER_HELP_TEXT)
        self.assertIn("Reconnecting", NEW_USER_HELP_TEXT)
        self.assertIn("Warning", NEW_USER_HELP_TEXT)
        self.assertIn("Failed", NEW_USER_HELP_TEXT)
        self.assertIn("Unknown", NEW_USER_HELP_TEXT)
        self.assertIn("# | Package | Username | State", NEW_USER_HELP_TEXT)

    def test_cmd_new_user_help_prints_tutorial(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = commands.cmd_new_user_help(_args())
        self.assertEqual(rc, 0)
        body = out.getvalue()
        self.assertIn("New User Help", body)
        self.assertIn("Enter / Update License Key", body)


class FirstTimeSetupCopyTests(unittest.TestCase):
    def test_first_setup_intro_mentions_detection_username_private_url(self):
        cfg = validate_config(default_config())
        args = _args()
        out = io.StringIO()
        with redirect_stdout(out), unittest.mock.patch("agent.commands._is_interactive", return_value=False):
            commands._run_first_time_setup_wizard(cfg, args)

        text = out.getvalue()
        self.assertIn("Package detection", text)
        self.assertIn("manual entry is fallback", text.lower())
        self.assertIn("Unknown is OK", text)
        self.assertIn("private url", text.lower())


class StartBeginnerMessageTests(unittest.TestCase):
    def test_no_license_remote_start_shows_friendly_block(self):
        cfg = validate_config(default_config())
        cfg["license"]["key"] = ""
        cfg["first_setup_completed"] = True
        cfg["roblox_packages"] = [package_entry("com.roblox.client", "x", True, "manual")]

        out = io.StringIO()
        with unittest.mock.patch("agent.commands.load_config", return_value=cfg), \
             unittest.mock.patch("agent.commands._ensure_install_id_saved", side_effect=lambda c: c), \
             unittest.mock.patch("agent.commands.keystore.DEV_MODE", False), \
             unittest.mock.patch("agent.commands._persist_license_status", side_effect=lambda c, s: c), \
             unittest.mock.patch(
                 "agent.commands._remote_license_run_check",
                 return_value=("missing_key", "No license key configured."),
             ), \
             redirect_stdout(out):
            rc = commands.cmd_start(_args())

        self.assertEqual(rc, 1)
        text = out.getvalue()
        self.assertIn("No License Key Found", text)
        self.assertIn("Key Panel", text)

    def test_no_package_selected_message(self):
        cfg = validate_config(default_config())
        cfg["first_setup_completed"] = True

        out = io.StringIO()
        with unittest.mock.patch("agent.commands.load_config", return_value=cfg), \
             unittest.mock.patch("agent.commands.keystore.DEV_MODE", True), \
             unittest.mock.patch("agent.commands.enabled_package_entries", return_value=[]), \
             unittest.mock.patch("agent.commands._ensure_install_id_saved", side_effect=lambda c: c), \
             redirect_stdout(out):
            rc = commands.cmd_start(_args())

        self.assertEqual(rc, 2)
        text = out.getvalue()
        self.assertIn("No Roblox Package Selected", text)
        self.assertIn("Setup / Edit Config", text)


class PackageDetectCopyTests(unittest.TestCase):
    def test_no_candidates_lists_install_steps(self):
        cfg = validate_config(default_config())
        args = _args()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True), \
             unittest.mock.patch("builtins.input", side_effect=["1"]), \
             unittest.mock.patch(
                 "agent.commands._interactive_discover_package_entries",
                 return_value=([], "no_candidates"),
             ):
            out = io.StringIO()
            with redirect_stdout(out):
                commands._choose_packages_menu(
                    [package_entry(cfg["roblox_package"], "", True, "not_set")],
                    list(cfg.get("package_detection_hints") or []),
                    cfg,
                )
        text = out.getvalue()
        self.assertIn("No Roblox Package Detected", text)
        self.assertIn("manual package entry", text.lower())


class DocsMentionTests(unittest.TestCase):
    def test_public_docs_mention_new_user_help(self):
        root = Path(__file__).resolve().parents[1]
        for name in (
            "README.md",
            "docs/NEW_USER_TERMUX_GUIDE.md",
            "docs/PUBLIC_USER_GUIDE.md",
            "docs/PUBLIC_INSTALL.md",
        ):
            text = (root / name).read_text(encoding="utf-8")
            self.assertIn("New User Help", text, msg=name)


if __name__ == "__main__":
    unittest.main()
