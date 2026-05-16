"""Tests for Start flow UX, launcher fallback, and config migration."""

import argparse
import io
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

from agent.commands import (
      _print_config_summary,
      _run_edit_config_menu,
      build_final_summary,
      build_start_table,
      build_start_verbose_details,
)
from agent.config import default_config, validate_config
from agent import android as amod


def _row(
    idx: int,
    pkg: str,
    user: str,
    state: str,
) -> tuple:
    return (idx, pkg, user, state)


class StartTableUxTests(unittest.TestCase):
    """Verify the single clean start table format."""

    def _make_rows(self):
        return [
            _row(1, "com.roblox.client", "deng1629", "Online"),
            _row(2, "com.example.robloxclone", "AltAccount1", "Failed"),
        ]

    def test_start_table_has_only_public_columns(self):
        rows = self._make_rows()
        table = build_start_table(rows)
        for col in ("#", "Package", "Username", "State"):
            self.assertIn(col, table)
        for banned in ("Cache", "Graphics", "Status", "Method", "Reason", "Private URL", "Label"):
            self.assertNotIn(banned, table)

    def test_start_table_shows_username_and_package_separately(self):
        rows = self._make_rows()
        table = build_start_table(rows)
        self.assertIn("deng1629", table)
        self.assertIn("com.roblox.client", table)
        self.assertIn("AltAccount1", table)
        self.assertIn("com.example.robloxclone", table)

    def test_start_table_not_label_column(self):
        rows = [_row(1, "com.roblox.client", "Main", "Launching")]
        table = build_start_table(rows)
        self.assertNotIn("Label", table)
        self.assertIn("Username", table)

    def test_start_table_shows_state(self):
        rows = self._make_rows()
        table = build_start_table(rows)
        self.assertIn("Online", table)
        self.assertIn("Failed", table)

    def test_start_table_has_box_borders(self):
        rows = [_row(1, "com.roblox.client", "Main", "Online")]
        table = build_start_table(rows)
        self.assertIn("┌", table)
        self.assertIn("┐", table)
        self.assertIn("└", table)
        self.assertIn("┘", table)
        self.assertIn("│", table)

    def test_start_table_is_one_table_not_multiple(self):
        rows = self._make_rows()
        table = build_start_table(rows)
        self.assertEqual(table.count("┌"), 1)

    def test_unknown_username_shows_unknown_not_waiting(self):
        rows = [_row(1, "com.example.robloxclone", "Unknown", "Launching")]
        table = build_start_table(rows)
        self.assertIn("Unknown", table)
        self.assertNotIn("Waiting", table)
        self.assertNotIn("Username not set", table)

    def test_verbose_details_include_cache_graphics_not_in_public_table(self):
        rows = [_row(1, "com.roblox.client", "Main", "Online")]
        table = build_start_table(rows)
        self.assertNotIn("Cleared", table)
        detail = build_start_verbose_details(
            [{"package": "com.roblox.client", "cache": "Cleared", "graphics": "Skipped", "launch_detail": "ok"}]
        )
        self.assertIn("cache=Cleared", detail)
        self.assertIn("graphics=Skipped", detail)
        combined = table + detail
        self.assertNotIn("Private URL", combined)

    def test_final_summary_is_multi_line_final_block(self):
        entries = [
            {"package": "com.roblox.client", "account_username": "deng1629", "enabled": True, "username_source": "manual"},
            {"package": "com.two.pkg", "account_username": "", "enabled": True, "username_source": "not_set"},
        ]
        text = build_final_summary(
            entries,
            {"com.roblox.client": "Online", "com.two.pkg": "Reconnecting"},
        )
        self.assertIn("Final:", text)
        self.assertIn("online", text.lower())
        self.assertIn("reconnecting", text.lower())
        self.assertGreaterEqual(text.count("\n"), 1)

    def test_final_summary_reports_zero_when_no_rows_emitted(self):
        entries = [{"package": "com.roblox.client", "account_username": "", "enabled": True, "username_source": "not_set"}]
        text = build_final_summary(entries, {"com.roblox.client": "Unknown"})
        self.assertIn("Final:", text)
        self.assertIn("unknown", text.lower())

    def test_start_table_does_not_contain_monkey(self):
        rows = [_row(1, "com.roblox.client", "Main", "Launching")]
        combined = build_start_table(rows) + "\n" + build_final_summary(
            [{"package": "com.roblox.client", "account_username": "", "enabled": True, "username_source": "not_set"}],
            {"com.roblox.client": "Online"},
        )
        self.assertNotIn("monkey", combined.lower())

    def test_settings_summary_is_not_raw_json(self):
        cfg = validate_config(default_config())
        output = io.StringIO()
        with redirect_stdout(output):
            _print_config_summary(cfg)

        text = output.getvalue()
        self.assertIn("DENG Tool: Rejoin Settings", text)
        self.assertIn("Roblox Packages:", text)
        self.assertIn("Username", text)
        self.assertNotIn("Label:", text)
        self.assertNotIn('{"', text)
        self.assertNotIn("Username not set", text)

    def test_public_config_menu_has_no_manual_auto_resize_step(self):
        args = argparse.Namespace(no_color=True)
        cfg = validate_config(default_config())
        output = io.StringIO()
        with redirect_stdout(output), unittest.mock.patch("agent.commands._is_interactive", return_value=False):
            _run_edit_config_menu(cfg, args)

        text = output.getvalue()
        self.assertNotIn("Auto Resize / Window Layout Setup", text)
        self.assertIn("Auto Resize:", text)
        self.assertIn("Automatic based on selected package count and device DPI", text)

    def test_no_script_execution_config_key(self):
        cfg = validate_config(default_config())
        lowered_keys = " ".join(cfg.keys()).lower()
        self.assertNotIn("script", lowered_keys)
        self.assertNotIn("executor", lowered_keys)
        self.assertNotIn("post_launch_action", cfg)

    def test_start_flow_does_not_use_pm_clear(self):
        source = (Path(__file__).resolve().parents[1] / "agent").rglob("*.py")
        combined = "\n".join(path.read_text(encoding="utf-8") for path in source)
        self.assertNotIn("pm clear", combined)

    def test_clear_terminal_helper_exists(self):
        from agent.commands import _clear_terminal
        self.assertTrue(callable(_clear_terminal))

    def test_new_state_lobby_in_start_table(self):
        """build_start_table must render 'Lobby' without error."""
        rows = [_row(1, "com.roblox.client", "Main", "Lobby")]
        table = build_start_table(rows)
        self.assertIn("Lobby", table)

    def test_new_state_joining_in_start_table(self):
        """build_start_table must render 'Joining' without error."""
        rows = [_row(1, "com.roblox.client", "Main", "Joining")]
        table = build_start_table(rows)
        self.assertIn("Joining", table)

    def test_new_state_in_server_in_start_table(self):
        """build_start_table must render 'In Server' without error."""
        rows = [_row(1, "com.roblox.client", "Main", "In Server")]
        table = build_start_table(rows)
        self.assertIn("In Server", table)

    def test_final_summary_lobby_maps_to_online(self):
        from agent.commands import build_final_summary
        entries = [{"package": "com.roblox.client", "account_username": "", "enabled": True, "username_source": "not_set"}]
        text = build_final_summary(entries, {"com.roblox.client": "Lobby"})
        self.assertIn("online", text.lower())

    def test_final_summary_in_server_maps_to_online(self):
        from agent.commands import build_final_summary
        entries = [{"package": "com.roblox.client", "account_username": "", "enabled": True, "username_source": "not_set"}]
        text = build_final_summary(entries, {"com.roblox.client": "In Server"})
        self.assertIn("online", text.lower())

    def test_final_summary_joining_maps_to_launching(self):
        from agent.commands import build_final_summary
        entries = [{"package": "com.roblox.client", "account_username": "", "enabled": True, "username_source": "not_set"}]
        text = build_final_summary(entries, {"com.roblox.client": "Joining"})
        self.assertIn("launching", text.lower())


class SinglePackageLaunchTests(unittest.TestCase):
    """Verify that a single selected package still triggers the launcher."""

    def _make_cfg(self):
        cfg = default_config()
        cfg["first_setup_completed"] = True
        cfg["launch_mode"] = "app"
        return cfg

    def test_single_package_calls_launch_app(self):
        """One package must still call launch — no skipping."""
        from agent.launcher import perform_rejoin

        cfg = self._make_cfg()
        with unittest.mock.patch.object(amod, "launch_package_with_options") as mock_launch, \
             unittest.mock.patch.object(amod, "package_installed", return_value=True):
            mock_launch.return_value = (amod.CommandResult(("am", "start"), 0, "Success", ""), "am_or_resolve")
            result = perform_rejoin(cfg, reason="start")
            mock_launch.assert_called_once()
        self.assertTrue(result.success)

    def test_single_package_start_table_row_says_started(self):
        """Table rows may use Online or Launching after heartbeat."""
        rows = [_row(1, "com.roblox.client", "Main", "Online")]
        table = build_start_table(rows)
        self.assertIn("Online", table)
        self.assertNotIn("Launch skipped", table)

    def test_launch_skipped_phrase_not_in_table(self):
        """The phrase 'Launch skipped' must never appear in a start table."""
        rows = [_row(1, "com.roblox.client", "Main", "Online")]
        table = build_start_table(rows)
        self.assertNotIn("Launch skipped", table)


class LauncherFallbackTests(unittest.TestCase):
    """Verify multi-method Android launch fallback in android.py."""

    def test_missing_monkey_falls_back_to_am(self):
        """If monkey is unavailable, launch_app must succeed using am."""

        def fake_find(*names):
            for name in names:
                if name in ("am", "/system/bin/am"):
                    return "/system/bin/am"
            return None  # monkey and cmd unavailable

        def fake_run(cmd, **kwargs):
            if cmd and "am" in str(cmd[0]):
                return amod.CommandResult(tuple(cmd), 0, "Success", "")
            return amod.CommandResult(tuple(cmd), 127, "", "not found")

        with unittest.mock.patch("agent.android._find_command", side_effect=fake_find), \
             unittest.mock.patch("agent.android.run_command", side_effect=fake_run):
            result = amod.launch_app("com.roblox.client")

        self.assertTrue(result.ok)

    def test_missing_monkey_no_file_not_found_error(self):
        """launch_app must never raise FileNotFoundError when monkey is missing."""

        def fake_find(*names):
            return None  # nothing available

        with unittest.mock.patch("agent.android._find_command", side_effect=fake_find):
            try:
                result = amod.launch_app("com.roblox.client")
            except FileNotFoundError:
                self.fail("launch_app raised FileNotFoundError when commands were missing")

        self.assertFalse(result.ok)

    def test_all_launch_commands_missing_clean_failure(self):
        """When no Android launch commands exist, get a clean CommandResult, not an exception."""
        with unittest.mock.patch("agent.android._find_command", return_value=None):
            result = amod.launch_app("com.roblox.client")

        self.assertFalse(result.ok)
        self.assertNotEqual(result.returncode, 0)

    def test_all_commands_missing_failure_includes_reason(self):
        """Clean failure message when all commands are missing."""
        with unittest.mock.patch("agent.android._find_command", return_value=None):
            result = amod.launch_app("com.roblox.client")

        self.assertIn("am/cmd/monkey", result.stderr)

    def test_am_method_tried_before_monkey(self):
        """Method 1 (am MAIN LAUNCHER) is tried before monkey."""
        call_log = []

        def fake_find(*names):
            for name in names:
                if name in ("am", "/system/bin/am"):
                    return "/system/bin/am"
                if name in ("monkey", "/system/bin/monkey"):
                    return "/system/bin/monkey"
            return None

        def fake_run(cmd, **kwargs):
            call_log.append(list(cmd))
            if cmd and "am" in str(cmd[0]) and "start" in cmd:
                return amod.CommandResult(tuple(cmd), 0, "Success", "")
            return amod.CommandResult(tuple(cmd), 1, "", "failed")

        with unittest.mock.patch("agent.android._find_command", side_effect=fake_find), \
             unittest.mock.patch("agent.android.run_command", side_effect=fake_run):
            result = amod.launch_app("com.roblox.client")

        self.assertTrue(result.ok)
        first_cmd = call_log[0] if call_log else []
        self.assertFalse(any("monkey" in str(x) for x in first_cmd), "monkey was called before am")

    def test_monkey_used_as_fallback_when_am_fails(self):
        """When am fails, monkey is tried as the final fallback."""

        def fake_find(*names):
            for name in names:
                if name in ("am", "/system/bin/am"):
                    return "/system/bin/am"
                if name in ("monkey", "/system/bin/monkey"):
                    return "/system/bin/monkey"
            return None

        def fake_run(cmd, **kwargs):
            if cmd and "monkey" in str(cmd[0]):
                return amod.CommandResult(tuple(cmd), 0, "Events injected: 1", "")
            return amod.CommandResult(tuple(cmd), 1, "", "failed")

        with unittest.mock.patch("agent.android._find_command", side_effect=fake_find), \
             unittest.mock.patch("agent.android.run_command", side_effect=fake_run):
            result = amod.launch_app("com.roblox.client")

        self.assertTrue(result.ok)


class ConfigMigrationTests(unittest.TestCase):
    """Verify migration of legacy config fields."""

    def test_intent_type_disabled_migrates_to_app_launch_mode(self):
        """Old launcher.intent_type = disabled must migrate to launch_mode = app."""
        cfg = default_config()
        cfg["launcher"] = {"intent_type": "disabled"}
        validated = validate_config(cfg)
        self.assertEqual(validated["launch_mode"], "app")

    def test_intent_type_none_migrates_to_app(self):
        """Old launcher.intent_type = none must migrate to launch_mode = app."""
        cfg = default_config()
        cfg["launcher"] = {"intent_type": "none"}
        validated = validate_config(cfg)
        self.assertEqual(validated["launch_mode"], "app")

    def test_launch_mode_disabled_migrates_to_app(self):
        """launch_mode = disabled must be migrated to app (not raise ConfigError)."""
        cfg = default_config()
        cfg["launch_mode"] = "disabled"
        validated = validate_config(cfg)
        self.assertEqual(validated["launch_mode"], "app")

    def test_launch_mode_auto_migrates_to_app(self):
        """launch_mode = auto must be migrated to app."""
        cfg = default_config()
        cfg["launch_mode"] = "auto"
        validated = validate_config(cfg)
        self.assertEqual(validated["launch_mode"], "app")

    def test_label_migrates_to_account_username(self):
        """Old package entries with label must migrate to account_username."""
        cfg = default_config()
        cfg["roblox_packages"] = [{"package": "com.roblox.client", "label": "MyAccount", "enabled": True}]
        validated = validate_config(cfg)
        self.assertEqual(validated["roblox_packages"][0]["account_username"], "MyAccount")
        self.assertEqual(validated["roblox_packages"][0]["username_source"], "manual")

    def test_string_package_list_still_migrates(self):
        """Existing configs with roblox_packages as strings still migrate safely."""
        cfg = default_config()
        cfg["roblox_packages"] = ["com.roblox.client", "com.roblox.client.clone1"]
        validated = validate_config(cfg)
        self.assertEqual(len(validated["roblox_packages"]), 2)
        self.assertEqual(validated["roblox_packages"][0]["package"], "com.roblox.client")

    def test_unknown_username_does_not_block_launch(self):
        """launch is not skipped when account_username is empty."""
        from agent.launcher import perform_rejoin

        cfg = default_config()
        cfg["first_setup_completed"] = True
        cfg["roblox_packages"] = [
            {"package": "com.roblox.client", "account_username": "", "enabled": True, "username_source": "not_set"}
        ]
        with unittest.mock.patch.object(amod, "launch_package_with_options") as mock_launch, \
             unittest.mock.patch.object(amod, "package_installed", return_value=True):
            mock_launch.return_value = (amod.CommandResult(("am", "start"), 0, "Success", ""), "am_or_resolve")
            result = perform_rejoin(cfg, reason="start")
            mock_launch.assert_called_once()
        self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()