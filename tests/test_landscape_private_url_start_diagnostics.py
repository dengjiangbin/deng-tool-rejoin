from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from agent import commands, termux_ui
from agent.config import default_config, package_entry, validate_config


class PublicConfigMenuTests(unittest.TestCase):
    def test_setup_edit_config_menu_has_only_packages_private_url_back(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            termux_ui.print_config_menu()
        text = out.getvalue()
        self.assertIn("1.", text)
        self.assertIn("Packages", text)
        self.assertIn("2.", text)
        self.assertIn("Private URL", text)
        self.assertIn("0.", text)
        self.assertIn("Back", text)
        self.assertNotIn("Screen Mode", text)
        self.assertNotIn("3. Screen Mode", text)

    def test_first_setup_noninteractive_does_not_show_screen_mode(self) -> None:
        cfg = validate_config(default_config())
        args = mock.Mock(no_color=True)
        out = io.StringIO()
        with mock.patch("agent.commands._is_interactive", return_value=False), redirect_stdout(out):
            commands._run_first_time_setup_wizard(cfg, args)
        text = out.getvalue()
        self.assertIn("First Time Setup Config", text)
        self.assertIn("Private URL", text)
        self.assertNotIn("Screen Mode", text)
        self.assertNotIn("Choose screen mode", text)

    def test_old_screen_mode_option_is_invalid_in_edit_config(self) -> None:
        cfg = validate_config(default_config())
        args = mock.Mock(no_color=True)
        with mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch("agent.commands.print_banner"), \
             mock.patch("agent.commands._config_menu_screen_mode") as screen_menu, \
             mock.patch("agent.commands.safe_io.safe_prompt", side_effect=["3", "0"]), \
             mock.patch("agent.commands.safe_io.press_enter"):
            commands._run_edit_config_menu(cfg, args)
        screen_menu.assert_not_called()

    def test_public_config_summary_does_not_show_screen_mode(self) -> None:
        cfg = validate_config(default_config())
        out = io.StringIO()
        with redirect_stdout(out):
            commands._print_config_summary(cfg)
        text = out.getvalue()
        self.assertIn("Private URL mode", text)
        self.assertNotIn("Screen Mode", text)


class PrivateUrlMenuTests(unittest.TestCase):
    def test_switching_to_separate_copies_global_only_with_confirmation(self) -> None:
        cfg = validate_config(default_config())
        cfg["private_server_url"] = "https://www.roblox.com/share?code=GLOBAL&type=Server"
        cfg["roblox_packages"] = [
            package_entry("com.test.one"),
            package_entry("com.test.two"),
        ]
        with mock.patch("agent.commands.safe_io.safe_prompt", side_effect=["2"]), \
             mock.patch("agent.commands._prompt_yes_no", return_value=True):
            updated = commands._private_url_change_mode_menu(cfg)
        self.assertEqual(updated["private_url_mode"], "separate")
        self.assertTrue(all("GLOBAL" in entry["private_server_url"] for entry in updated["roblox_packages"]))

    def test_switching_to_global_preserves_package_urls(self) -> None:
        cfg = validate_config(default_config())
        cfg["private_url_mode"] = "separate"
        cfg["roblox_packages"][0]["private_server_url"] = "https://www.roblox.com/share?code=PKG&type=Server"
        with mock.patch("agent.commands.safe_io.safe_prompt", side_effect=["1", ""]):
            updated = commands._private_url_change_mode_menu(cfg)
        self.assertEqual(updated["private_url_mode"], "global")
        self.assertIn("PKG", updated["roblox_packages"][0]["private_server_url"])


class StartSessionDiagnosticsTests(unittest.TestCase):
    def test_start_session_markers_and_previous_crash_detection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_dir = Path(td)
            state_path = log_dir / "last-start-session.json"
            with mock.patch("agent.commands.LOG_DIR", log_dir), \
                 mock.patch("agent.commands.START_CRASH_STATE_PATH", state_path), \
                 mock.patch("agent.commands.CRASH_LOG_PATH", log_dir / "crash.log"):
                logger = commands.StartSessionLogger("session-1")
                logger.mark("package_launch_begin")
                self.assertIn("[START_STEP] package_launch_begin", logger.path.read_text(encoding="utf-8"))
                self.assertIn("package_launch_begin", commands._previous_start_crash_notice() or "")
                logger.finish("completed")
                self.assertIsNone(commands._previous_start_crash_notice())
                data = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(data["status"], "completed")


if __name__ == "__main__":
    unittest.main()
