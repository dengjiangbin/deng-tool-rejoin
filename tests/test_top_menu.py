"""Top menu cleanup: public options and safe exit."""

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
    ns = argparse.Namespace(no_color=True, verbose=False, debug=False, lines=50)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class TestTopMenuOutput(unittest.TestCase):
    def test_top_menu_exact_items(self):
        labels = [item[1] for item in menu.MENU_ITEMS]
        numbers = [item[0] for item in menu.MENU_ITEMS]
        self.assertEqual(
            list(zip(numbers, labels)),
            [
                ("1", "First Time Setup Config"),
                ("2", "Setup / Edit Config"),
                ("3", "Start"),
                ("0", "Exit"),
            ],
        )

    def test_top_menu_does_not_contain_key(self):
        labels = [item[1] for item in menu.MENU_ITEMS]
        numbers = [item[0] for item in menu.MENU_ITEMS]
        commands_map = {item[0]: item[2] for item in menu.MENU_ITEMS}
        self.assertNotIn("Key", labels)
        self.assertNotIn("Package Key", labels)
        self.assertNotIn("5", numbers)
        self.assertNotIn("package-key", commands_map.values())

    def test_print_menu_output(self):
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [{"package": "com.roblox.client", "enabled": True}]
        out = io.StringIO()
        with patch("agent.menu.load_config", return_value=cfg), \
             patch("agent.menu.print_banner"), \
             redirect_stdout(out):
            menu.print_menu(_args(), [])
        text = out.getvalue()
        self.assertIn("[?]", text)
        self.assertIn("Top Menu", text)
        self.assertIn("First Time Setup Config", text)
        self.assertIn("Setup / Edit Config", text)
        self.assertIn("Start", text)
        self.assertIn("Exit", text)
        self.assertNotIn("4. Key", text)
        self.assertNotIn("Package Key", text)


class TestTopMenuDispatch(unittest.TestCase):
    def _run(self, inputs: list[str]) -> tuple[int, str]:
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [{"package": "com.roblox.client", "enabled": True}]
        prompts = iter(inputs)

        def fake_prompt(prompt="", **_kwargs):
            try:
                return next(prompts)
            except StopIteration:
                return None

        out = io.StringIO()
        handlers = {
            "first-setup": lambda _a: 0,
            "config": lambda _a: 0,
            "start": lambda _a: 0,
            "package-key": lambda _a: (_ for _ in ()).throw(AssertionError("package-key must not run")),
            "auto-execute": lambda _a: (_ for _ in ()).throw(AssertionError("auto-execute must not run from top menu")),
        }
        with patch("agent.menu.load_config", return_value=cfg), \
             patch("agent.menu.print_banner"), \
             patch("agent.menu._is_interactive", return_value=True), \
             patch("agent.menu.safe_io.safe_prompt", side_effect=fake_prompt), \
             patch("agent.menu.safe_io.press_enter"), \
             redirect_stdout(out):
            rc = menu.run_menu(_args(), handlers)
        return rc, out.getvalue()

    def test_option_4_invalid_does_not_open_auto_execute(self):
        rc, text = self._run(["4", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("Invalid Option", text)
        self.assertIn("Goodbye.", text)

    def test_option_44_invalid(self):
        rc, text = self._run(["44", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("Invalid Option", text)

    def test_option_5_invalid(self):
        rc, text = self._run(["5", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("Invalid Option", text)

    def test_option_key_alias_invalid(self):
        rc, text = self._run(["key", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("Invalid Option", text)

    def test_option_auto_alias_invalid(self):
        rc, text = self._run(["auto", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("Invalid Option", text)

    def test_blank_input_does_not_crash(self):
        rc, text = self._run(["", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("Invalid Option", text)


class TestAutoExecutePlacement(unittest.TestCase):
    def test_top_menu_does_not_contain_auto_execute(self):
        labels = [item[1] for item in menu.MENU_ITEMS]
        commands_map = {item[2] for item in menu.MENU_ITEMS}
        self.assertNotIn("Auto Execute", labels)
        self.assertNotIn("auto-execute", commands_map)

    def test_setup_config_contains_auto_execute(self):
        out = io.StringIO()
        with redirect_stdout(out):
            termux_ui.print_config_menu()
        text = out.getvalue()
        self.assertIn("Auto Execute", text)
        self.assertNotIn("4. Key", text)

    def test_first_time_setup_contains_auto_execute(self):
        src = inspect.getsource(commands._run_first_time_setup_wizard)
        self.assertIn("4. Auto Execute", src)
        self.assertIn("_config_menu_auto_execute(draft)", src)

    def test_setup_config_option_4_opens_auto_execute(self):
        src = inspect.getsource(commands._run_edit_config_menu)
        self.assertIn("_config_menu_auto_execute", src)
        self.assertIn('choice == "4"', src)


class TestPackageKeyNotInTopMenu(unittest.TestCase):
    def test_package_key_not_in_menu_items(self):
        commands_map = {item[2] for item in menu.MENU_ITEMS}
        self.assertNotIn("package-key", commands_map)
        self.assertNotIn("auto-execute", commands_map)

    def test_package_key_handler_not_reachable_from_top_menu(self):
        src = inspect.getsource(menu.run_menu)
        self.assertNotIn("package-key", src)

    def test_package_key_still_available_via_handler_dict(self):
        handlers = commands._handlers()
        self.assertTrue(callable(handlers.get("package-key")))


class TestTopMenuExit(unittest.TestCase):
    def test_option_zero_exits_cleanly(self):
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [{"package": "com.roblox.client", "enabled": True}]
        with patch("agent.menu.load_config", return_value=cfg), \
             patch("agent.menu.print_banner"), \
             patch("agent.menu._is_interactive", return_value=True), \
             patch("agent.menu.safe_io.safe_prompt", side_effect=["0"]), \
             patch("agent.commands._termux_exit_clean") as hard_exit, \
             redirect_stdout(io.StringIO()):
            rc = commands._run_top_menu_with_clean_exit(_args())
        self.assertEqual(rc, 0)
        hard_exit.assert_called_once()

    def test_option_zero_does_not_call_home_or_force_stop(self):
        src = inspect.getsource(menu.run_menu)
        self.assertNotIn("HOME", src)
        self.assertNotIn("force-stop", src)
        self.assertNotIn("close-all", src)
        self.assertNotIn("am force-stop", src)

    def test_run_menu_does_not_call_termux_exit_clean_directly(self):
        src = inspect.getsource(menu.run_menu)
        self.assertNotIn("termux_exit_clean", src)

    def test_eof_exits_cleanly(self):
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [{"package": "com.roblox.client", "enabled": True}]
        out = io.StringIO()
        with patch("agent.menu.load_config", return_value=cfg), \
             patch("agent.menu.print_banner"), \
             patch("agent.menu._is_interactive", return_value=True), \
             patch("agent.menu.safe_io.safe_prompt", return_value=None), \
             redirect_stdout(out):
            rc = menu.run_menu(_args(), commands._handlers())
        self.assertEqual(rc, 0)
        self.assertIn("Goodbye.", out.getvalue())

    def test_keyboard_interrupt_exits_cleanly(self):
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [{"package": "com.roblox.client", "enabled": True}]

        def boom(*_a, **_k):
            raise KeyboardInterrupt

        out = io.StringIO()
        with patch("agent.menu.load_config", return_value=cfg), \
             patch("agent.menu.print_banner"), \
             patch("agent.menu._is_interactive", return_value=True), \
             patch("agent.menu.safe_io.safe_prompt", side_effect=boom), \
             redirect_stdout(out):
            rc = menu.run_menu(_args(), commands._handlers())
        self.assertEqual(rc, 0)
        self.assertIn("Goodbye.", out.getvalue())

    def test_config_submenu_returns_to_menu_loop(self):
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [{"package": "com.roblox.client", "enabled": True}]
        prompts = iter(["2", "", "0"])
        calls = {"config": 0}

        def fake_config(_args):
            calls["config"] += 1
            return 0

        handlers = {
            "first-setup": lambda _a: 0,
            "config": fake_config,
            "start": lambda _a: 0,
        }
        with patch("agent.menu.load_config", return_value=cfg), \
             patch("agent.menu.print_banner"), \
             patch("agent.menu._is_interactive", return_value=True), \
             patch("agent.menu.safe_io.safe_prompt", side_effect=lambda *a, **k: next(prompts)), \
             patch("agent.menu.safe_io.press_enter"), \
             redirect_stdout(io.StringIO()):
            rc = menu.run_menu(_args(), handlers)
        self.assertEqual(rc, 0)
        self.assertEqual(calls["config"], 1)

    def test_termux_exit_clean_noops_off_termux(self):
        with patch.dict("os.environ", {}, clear=True):
            safe_io.termux_exit_clean()


if __name__ == "__main__":
    unittest.main()
