from __future__ import annotations

import io
import re
import subprocess
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest import mock

from agent.config import default_config, package_entry, validate_config
from agent.commands import (
    _choose_packages_menu,
    _config_menu_package,
    _package_menu_add,
    _package_menu_auto_detect,
    _package_menu_refresh_mapping,
)


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _plain(text: str) -> str:
    return ANSI_RE.sub("", text)


def _cfg(packages: list[dict] | None = None) -> dict:
    cfg = validate_config(default_config())
    if packages is not None:
        cfg["roblox_packages"] = packages
    return cfg


class TestRefreshMappingFreezeRegression(unittest.TestCase):
    """Probe p-d35129b645: Refresh Mapping must never lock Termux."""

    def _run_refresh(self, cfg: dict, **patches):
        buf = io.StringIO()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch("agent.commands.safe_io.press_enter"))
        stack.enter_context(mock.patch("agent.commands.save_config", side_effect=lambda data: data))
        for target, value in patches.items():
            stack.enter_context(mock.patch(target, value))
        with redirect_stdout(buf):
            result = _package_menu_refresh_mapping(cfg)
        return result, _plain(buf.getvalue())

    def test_refresh_mapping_does_not_call_rich_table(self):
        cfg = _cfg([package_entry("com.roblox.client", "Main", True, "manual")])
        result, out = self._run_refresh(
            cfg,
            **{
                "agent.commands.build_account_mapping_table": mock.Mock(side_effect=AssertionError("table")),
                "agent.commands._run_account_mapping_table": mock.Mock(side_effect=AssertionError("table")),
            },
        )
        self.assertIs(result, cfg)
        self.assertIn("Refresh Mapping Finished", out)

    def test_refresh_mapping_handles_subprocess_timeout(self):
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        result, out = self._run_refresh(
            cfg,
            **{
                "agent.commands.account_detect.detect_account_username": mock.Mock(
                    side_effect=subprocess.TimeoutExpired(cmd="su -c", timeout=2)
                )
            },
        )
        self.assertIs(result, cfg)
        self.assertIn("Skipped", out)
        self.assertIn("Timeout", out)

    def test_refresh_mapping_handles_root_permission_denied(self):
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        result, out = self._run_refresh(
            cfg,
            **{
                "agent.commands.account_detect.detect_account_username": mock.Mock(side_effect=PermissionError("denied"))
            },
        )
        self.assertIs(result, cfg)
        self.assertIn("Permission Denied", out)

    def test_refresh_mapping_handles_broken_package_data(self):
        long_pkg = "com." + ("verylong" * 10)
        cfg = _cfg([
            {"package": None, "account_username": "", "enabled": True, "username_source": "not_set"},
            {"package": "com.roblox.client", "account_username": "   ", "enabled": True, "username_source": "manual"},
            {"package": long_pkg, "account_username": "", "enabled": True, "username_source": "not_set"},
        ])
        result, out = self._run_refresh(
            cfg,
            **{
                "agent.commands.account_detect.detect_account_username": mock.Mock(side_effect=ValueError("invalid XML"))
            },
        )
        self.assertIs(result, cfg)
        self.assertIn("Invalid Package", out)
        self.assertIn("Skipped", out)
        self.assertIn("...", out)

    def test_refresh_mapping_handles_no_configured_packages(self):
        cfg = _cfg([{"package": "com.roblox.client", "account_username": "", "enabled": False}])
        result, out = self._run_refresh(cfg)
        self.assertIs(result, cfg)
        self.assertIn("No Packages Configured", out)

    def test_refresh_mapping_catches_keyboard_interrupt(self):
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        result, out = self._run_refresh(
            cfg,
            **{
                "agent.commands.validate_package_entries": mock.Mock(side_effect=KeyboardInterrupt())
            },
        )
        self.assertIs(result, cfg)
        self.assertIn("Refresh Mapping Cancelled", out)

    def test_refresh_mapping_catches_eof_error(self):
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        result, out = self._run_refresh(
            cfg,
            **{
                "agent.commands.validate_package_entries": mock.Mock(side_effect=EOFError())
            },
        )
        self.assertIs(result, cfg)
        self.assertIn("Refresh Mapping Stopped", out)

    def test_refresh_mapping_always_restores_terminal_state(self):
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        stdout = io.StringIO()
        with mock.patch("agent.commands.sys.stdout", stdout), \
             mock.patch("agent.commands.safe_io.press_enter"), \
             mock.patch("agent.commands.account_detect.detect_account_username", side_effect=RuntimeError("boom")):
            _package_menu_refresh_mapping(cfg)
        self.assertIn("\033[0m", stdout.getvalue())
        self.assertIn("\033[?25h", stdout.getvalue())

    def test_refresh_mapping_returns_to_package_menu_after_success(self):
        cfg = _cfg([package_entry("com.roblox.client", "Main", True, "manual")])
        inputs = iter(["3", "0"])

        def prompt(_msg="", **_kwargs):
            return next(inputs)

        with mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch("agent.commands.safe_io.safe_prompt", side_effect=prompt), \
             mock.patch("agent.commands.safe_io.press_enter"), \
             mock.patch("agent.commands.save_config", side_effect=lambda data: data), \
             redirect_stdout(io.StringIO()) as buf:
            result = _config_menu_package(cfg)
        self.assertIs(result, cfg)
        out = _plain(buf.getvalue())
        self.assertIn("Refresh Mapping Finished", out)
        self.assertIn("Packages", out)

    def test_refresh_mapping_returns_to_package_menu_after_failure(self):
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        inputs = iter(["3", "0"])

        def prompt(_msg="", **_kwargs):
            return next(inputs)

        with mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch("agent.commands.safe_io.safe_prompt", side_effect=prompt), \
             mock.patch("agent.commands.safe_io.press_enter"), \
             mock.patch("agent.commands.account_detect.detect_account_username", side_effect=RuntimeError("boom")), \
             mock.patch("agent.commands.save_config", side_effect=lambda data: data), \
             redirect_stdout(io.StringIO()) as buf:
            result = _config_menu_package(cfg)
        self.assertIs(result, cfg)
        out = _plain(buf.getvalue())
        self.assertIn("Refresh Mapping Finished With", out)
        self.assertIn("Packages", out)

    def test_refresh_mapping_total_time_budget_aborts_long_scans(self):
        cfg = _cfg([
            package_entry("com.roblox.client", "", True, "not_set"),
            package_entry("com.moons.litesc", "", True, "not_set"),
        ])
        ticks = iter([0.0, 31.0, 31.0, 31.0, 31.0])
        result, out = self._run_refresh(
            cfg,
            **{
                "agent.commands.time.monotonic": mock.Mock(side_effect=lambda: next(ticks, 31.0))
            },
        )
        self.assertIs(result, cfg)
        self.assertIn("Timed Out", out)

    def test_refresh_mapping_output_uses_simple_lines_not_table_rows(self):
        cfg = _cfg([package_entry("com.roblox.client", "Main", True, "manual")])
        result, out = self._run_refresh(cfg)
        self.assertIs(result, cfg)
        self.assertIn("1. ..client", out)
        self.assertNotIn("│", out)
        self.assertNotIn("┌", out)
        self.assertNotIn("Package | Username | User ID", out)

    def test_first_time_auto_detect_uses_shared_safe_mapping(self):
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        candidate = mock.Mock(package="com.moons.litesc", app_name="Lite C", launchable=True)
        prompts = iter(["1", "a"])
        with mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch("agent.commands._gather_roblox_candidates_for_ui", return_value=[candidate]), \
             mock.patch("agent.commands.safe_io.safe_prompt", side_effect=lambda *_a, **_k: next(prompts)), \
             mock.patch("agent.commands._run_account_mapping_table", side_effect=AssertionError("old mapping")), \
             mock.patch("agent.commands._auto_detect_cookies_for_entries", side_effect=AssertionError("cookie scan")), \
             mock.patch("agent.commands._safe_refresh_account_mapping_entries",
                        return_value=[package_entry("com.moons.litesc", "User", True, "detected")]) as shared:
            selected, _hints = _choose_packages_menu(cfg["roblox_packages"], cfg["package_detection_hints"], cfg)
        shared.assert_called_once()
        self.assertEqual(selected[0]["package"], "com.moons.litesc")

    def test_setup_config_auto_detect_uses_shared_safe_mapping(self):
        cfg = _cfg([package_entry("com.roblox.client", "", True, "not_set")])
        candidate = mock.Mock(package="com.moons.litesc", app_name="Lite C", launchable=True)
        with mock.patch("agent.commands._gather_roblox_candidates_for_ui", return_value=[candidate]), \
             mock.patch("agent.commands.safe_io.safe_prompt", return_value="a"), \
             mock.patch("agent.commands.save_config", side_effect=lambda data: data), \
             mock.patch("agent.commands._run_account_mapping_table", side_effect=AssertionError("old mapping")), \
             mock.patch("agent.commands._auto_detect_cookies_for_entries", side_effect=AssertionError("cookie scan")), \
             mock.patch("agent.commands._safe_refresh_account_mapping_entries",
                        return_value=[package_entry("com.moons.litesc", "User", True, "detected")]) as shared:
            result = _package_menu_auto_detect(cfg)
        shared.assert_called_once()
        self.assertEqual(result["roblox_packages"][-1]["package"], "com.moons.litesc")

    def test_manual_add_uses_shared_safe_mapping_after_validation(self):
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
             mock.patch("agent.commands._safe_refresh_account_mapping_entries",
                        return_value=[package_entry("com.moons.litesc", "User", True, "detected")]) as shared:
            result = _package_menu_add(cfg)
        installed.assert_called_once_with("com.moons.litesc")
        shared.assert_called_once()
        self.assertEqual(result["roblox_packages"][-1]["package"], "com.moons.litesc")


if __name__ == "__main__":
    unittest.main()
