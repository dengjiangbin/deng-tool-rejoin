"""Kaeru-style Termux UX: license success, colors, prefixes, menu structure."""

from __future__ import annotations

import argparse
import inspect
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import commands, menu, safe_io, termux_ui
from agent.config import default_config, validate_config


def _args(**kw) -> argparse.Namespace:
    ns = argparse.Namespace(no_color=False, verbose=False, debug=False, lines=50)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class TestLicenseSuccessFlow(unittest.TestCase):
    def test_success_lines_defined(self) -> None:
        self.assertEqual(
            termux_ui.LICENSE_SUCCESS_VERIFIED,
            "[!] License Key Verified Successfully.",
        )
        self.assertEqual(
            termux_ui.LICENSE_SUCCESS_WELCOME,
            "[!] Welcome To DENG Tool: Rejoin.",
        )

    def test_print_license_success_uses_bold_green(self) -> None:
        out = io.StringIO()
        with patch("agent.termux_ui.time.sleep"), redirect_stdout(out):
            termux_ui.print_license_success(pause_seconds=0)
        text = out.getvalue()
        self.assertIn(termux_ui.GREEN, text)
        self.assertIn("[!] License Key Verified Successfully.", text)
        self.assertIn("[!] Welcome To DENG Tool: Rejoin.", text)

    def test_success_does_not_include_full_key(self) -> None:
        sample_key = "ABCD-EFGH-IJKL-MNOP"
        out = io.StringIO()
        with patch("agent.termux_ui.time.sleep"), redirect_stdout(out):
            termux_ui.print_license_success(pause_seconds=0)
        self.assertNotIn(sample_key, out.getvalue())

    def test_manual_remote_entry_sets_flag(self) -> None:
        cfg = {"license": {"key": "", "mode": "remote"}, "install_id": "abc"}
        saved = {"license": {"key": "DENG-TEST-KEY-1234", "mode": "remote"}, "install_id": "abc"}
        commands._license_manual_verification_success = False
        with patch.object(commands, "load_config", side_effect=[cfg, saved]), \
             patch.object(commands, "_ensure_install_id_saved", side_effect=lambda c: c), \
             patch.object(commands, "_is_interactive", return_value=True), \
             patch.object(commands, "validate_license_key", return_value="DENG-TEST-KEY-1234"), \
             patch.object(commands, "save_config", return_value=saved), \
             patch.object(commands, "_remote_license_run_bind", return_value=("active", "ok")), \
             patch.object(commands, "_persist_license_status", side_effect=lambda c, _s: c), \
             patch.object(safe_io, "safe_prompt", return_value="DENG-TEST-KEY-1234"):
            ok = commands._ensure_remote_license_menu_loop(cfg, _args(), True)
        self.assertTrue(ok)
        self.assertTrue(commands._license_manual_verification_success)

    def test_cmd_menu_clears_after_license_success(self) -> None:
        commands._license_manual_verification_success = True
        with patch.object(commands, "load_config", return_value=default_config()), \
             patch.object(commands, "_enforce_configured_screen_mode"), \
             patch.object(commands, "_enforce_termux_left_layout"), \
             patch.object(commands, "ensure_app_dirs"), \
             patch.object(safe_io, "check_and_report_crash_log", return_value=None), \
             patch.object(commands, "_ensure_install_id_saved", side_effect=lambda c: c), \
             patch.object(commands, "_ensure_remote_license_menu_loop", return_value=True), \
             patch.object(safe_io, "safe_clear_screen") as clear_mock, \
             patch.object(commands, "_run_top_menu_with_clean_exit", return_value=0), \
             patch("agent.termux_ui.time.sleep"):
            rc = commands.cmd_menu(_args())
        self.assertEqual(rc, 0)
        clear_mock.assert_called_once()
        self.assertFalse(commands._license_manual_verification_success)

    def test_failed_license_does_not_clear_or_show_success(self) -> None:
        cfg = {"license": {"key": "", "mode": "remote"}}
        with patch.object(commands, "load_config", return_value=cfg), \
             patch.object(commands, "_ensure_install_id_saved", side_effect=lambda c: c), \
             patch.object(commands, "validate_license_key", return_value="BAD-KEY"), \
             patch.object(commands, "save_config", return_value=cfg), \
             patch.object(commands, "_remote_license_run_bind", return_value=("not_found", "bad")), \
             patch.object(commands, "_persist_license_status", side_effect=lambda c, _s: c), \
             patch.object(commands, "_enforce_configured_screen_mode"), \
             patch.object(commands, "_enforce_termux_left_layout"), \
             patch.object(commands, "ensure_app_dirs"), \
             patch.object(safe_io, "check_and_report_crash_log", return_value=None), \
             patch.object(safe_io, "safe_clear_screen") as clear_mock, \
             patch.object(safe_io, "safe_prompt", side_effect=["BAD-KEY", "2"]):
            rc = commands.cmd_menu(_args())
        self.assertEqual(rc, 1)
        clear_mock.assert_not_called()


class TestMenuPrefixes(unittest.TestCase):
    def test_top_menu_select_prompt(self) -> None:
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [{"package": "com.roblox.client", "enabled": True}]
        out = io.StringIO()
        with patch("agent.menu.load_config", return_value=cfg), \
             patch("agent.menu.print_banner"), \
             redirect_stdout(out):
            menu.print_menu(_args(), [])
        # "Top Menu" header removed per user request (p-1bc476d931); the menu
        # still renders its numbered items.
        text = out.getvalue()
        self.assertNotIn("Top Menu", text)
        self.assertIn("First Time Setup Config", text)
        self.assertIn("Start", text)

    def test_setup_config_prompt_prefix(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            termux_ui.print_config_menu()
        text = out.getvalue()
        self.assertIn(termux_ui.CYAN, text)
        self.assertIn("Setup / Edit Config", text)

    def test_submenu_success_prefix(self) -> None:
        line = termux_ui.success_line("Config Saved")
        self.assertIn("[!]", line)
        self.assertIn(termux_ui.GREEN, line)

    def test_first_time_setup_uses_header(self) -> None:
        src = inspect.getsource(commands._run_first_time_setup_wizard)
        self.assertIn("termux_ui.header", src)


class TestColorReadability(unittest.TestCase):
    def test_prompt_bold_cyan(self) -> None:
        line = termux_ui.prompt_prefix("Select Option")
        self.assertIn(termux_ui.CYAN, line)
        self.assertIn("[?]", line)

    def test_warning_bold_yellow(self) -> None:
        line = termux_ui.warning_line("Invalid Option")
        self.assertIn(termux_ui.YELLOW, line)

    def test_error_bold_red(self) -> None:
        line = termux_ui.error_line("License Key Invalid")
        self.assertIn(termux_ui.RED, line)

    def test_menu_numbers_bold_yellow(self) -> None:
        line = termux_ui.menu_number("1", "Start")
        self.assertIn(termux_ui.YELLOW, line)

    def test_top_menu_contains_color_codes(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            termux_ui.print_top_menu()
        text = out.getvalue()
        self.assertIn("\033[", text)

    def test_config_menu_contains_color_codes(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            termux_ui.print_config_menu()
        self.assertIn("\033[", out.getvalue())

    def test_submenu_contains_color_codes(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            termux_ui.print_submenu("Packages", [("1", "Add Package"), ("0", "Back")])
        self.assertIn("\033[", out.getvalue())

    def test_headers_use_bright_separators(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            termux_ui.header("DENG Tool: Rejoin")
        text = out.getvalue()
        self.assertIn("=", text)
        self.assertIn(termux_ui.CYAN, text)

    def test_normal_menu_labels_not_red(self) -> None:
        line = termux_ui.menu_number("2", "Setup / Edit Config")
        self.assertNotIn(termux_ui.RED, line)


class TestMenuStructureProtection(unittest.TestCase):
    def test_top_menu_exact_items(self) -> None:
        labels = [item[1] for item in menu.MENU_ITEMS]
        self.assertEqual(
            labels,
            [
                "First Time Setup Config",
                "Setup / Edit Config",
                "Start",
                "Exit",
            ],
        )

    def test_top_menu_no_key_and_no_auto_execute_option_4(self) -> None:
        labels = [item[1] for item in menu.MENU_ITEMS]
        numbers = [item[0] for item in menu.MENU_ITEMS]
        self.assertNotIn("Key", labels)
        self.assertNotIn("Auto Execute", labels)
        self.assertNotIn("4", numbers)
        self.assertNotIn("5", numbers)

    def test_setup_config_contains_auto_execute(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            termux_ui.print_config_menu()
        text = out.getvalue()
        self.assertIn("Auto Execute", text)
        self.assertNotIn("4. Key", text)

    def test_setup_config_navigation_source(self) -> None:
        src = inspect.getsource(commands._run_edit_config_menu)
        self.assertIn("_config_menu_auto_execute", src)
        self.assertIn('choice == "4"', src)


class TestSafeClearHelper(unittest.TestCase):
    def test_safe_clear_does_not_call_home(self) -> None:
        src = inspect.getsource(safe_io.safe_clear_screen)
        body = src.split('"""', 2)[-1]
        self.assertNotIn('os.system("HOME")', body)
        self.assertNotIn("force-stop", body)
        self.assertNotIn("close-all", body)
        self.assertNotIn("am start", body)

    def test_license_success_does_not_call_package_cleanup(self) -> None:
        src = inspect.getsource(commands.cmd_menu)
        self.assertNotIn("package cleanup", src.lower())
        self.assertNotIn("force-stop", src)

    def test_menu_render_no_recursion(self) -> None:
        src = inspect.getsource(menu.run_menu)
        self.assertNotIn("cmd_menu", src)
        self.assertNotIn("_run_top_menu_with_clean_exit", src)

    def test_styling_no_orientation_commands(self) -> None:
        src = inspect.getsource(termux_ui)
        self.assertNotIn("wm size", src)
        self.assertNotIn("user_rotation", src)


if __name__ == "__main__":
    unittest.main()
