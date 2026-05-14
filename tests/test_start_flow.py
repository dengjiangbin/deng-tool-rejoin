"""Tests for Start flow UX, launcher fallback, and config migration."""

import argparse
import io
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

from agent.commands import _print_config_summary, _run_edit_config_menu, build_final_summary, build_start_table
from agent.config import default_config, validate_config
from agent import android as amod


class StartTableUxTests(unittest.TestCase):
    """Verify the single clean start table format."""

    def _make_rows(self):
        return [
            (1, "com.roblox.client", "deng1629", "Started"),
            (2, "com.moons.alt1", "AltAccount1", "Failed"),
        ]

    def test_start_table_has_expected_columns(self):
        rows = self._make_rows()
        table = build_start_table(rows)
        self.assertIn("#", table)
        self.assertIn("Package", table)
        self.assertIn("Username", table)
        self.assertNotIn("Launch", table)  # Launch column was merged into Status
        self.assertIn("Status", table)

    def test_start_table_shows_username_and_package_separately(self):
        rows = self._make_rows()
        table = build_start_table(rows)
        # Username and package appear as separate values, not "Username (package)"
        self.assertIn("deng1629", table)
        self.assertIn("com.roblox.client", table)
        self.assertIn("AltAccount1", table)
        self.assertIn("com.moons.alt1", table)

    def test_start_table_not_label_column(self):
        rows = [(1, "com.roblox.client", "Main", "Started")]
        table = build_start_table(rows)
        self.assertNotIn("Label", table)
        self.assertIn("Username", table)

    def test_start_table_shows_status(self):
        rows = self._make_rows()
        table = build_start_table(rows)
        self.assertIn("Started", table)
        self.assertIn("Failed", table)
        # Status column is the only status indicator (no separate launch column)
        self.assertNotIn("Roblox launch command sent", table)

    def test_start_table_has_box_borders(self):
        rows = [(1, "com.roblox.client", "Main", "Started")]
        table = build_start_table(rows)
        self.assertIn("┌", table)
        self.assertIn("┐", table)
        self.assertIn("└", table)
        self.assertIn("┘", table)
        self.assertIn("│", table)

    def test_start_table_is_one_table_not_four(self):
        rows = self._make_rows()
        table = build_start_table(rows)
        # 4-column table — still exactly one table
        self.assertEqual(table.count("┌"), 1)

    def test_unknown_username_shows_unknown_not_waiting(self):
        rows = [(1, "com.moons.litesc", "Unknown", "Started")]
        table = build_start_table(rows)
        self.assertIn("Unknown", table)
        self.assertNotIn("Waiting", table)
        self.assertNotIn("Username not set", table)

    def test_final_summary_is_one_line(self):
        entries = [
            {"package": "com.roblox.client", "account_username": "deng1629", "enabled": True, "username_source": "manual"},
        ]
        text = build_final_summary(entries, {"com.roblox.client": "Launched"})
        # New format is a one-liner, not a multi-row table
        self.assertNotIn("Final Summary:", text)
        self.assertIn("launched", text.lower())
        self.assertEqual(text.count("\n"), 0)

    def test_final_summary_reports_zero_on_all_failure(self):
        entries = [{"package": "com.roblox.client", "account_username": "Main", "enabled": True, "username_source": "manual"}]
        text = build_final_summary(entries, {"com.roblox.client": "Failed"})
        self.assertIn("0", text)

    def test_start_table_does_not_contain_monkey(self):
        rows = [(1, "com.roblox.client", "Main", "Started", "Roblox launch command sent")]
        combined = build_start_table(rows) + "\n" + build_final_summary(
            [{"package": "com.roblox.client", "account_username": "Main", "enabled": True, "username_source": "manual"}],
            {"com.roblox.client": "Launched"},
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

    def test_public_config_menu_has_no_manual_auto_resize_step(self):
        args = argparse.Namespace(no_color=True)
        cfg = validate_config(default_config())
        output = io.StringIO()
        with redirect_stdout(output), \
             unittest.mock.patch("agent.commands._is_interactive", return_value=False):
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


class SinglePackageLaunchTests(unittest.TestCase):
    """Verify that a single selected package still triggers the launcher."""

    def _make_cfg(self):
        cfg = default_config()
        cfg["first_setup_completed"] = True
        cfg["launch_mode"] = "app"
        return cfg

    def test_single_package_calls_launch_app(self):
        """One package must still call launch_app — no skipping."""
        from agent.launcher import perform_rejoin

        cfg = self._make_cfg()
        with unittest.mock.patch.object(amod, "launch_app") as mock_launch, \
             unittest.mock.patch.object(amod, "package_installed", return_value=True):
            mock_launch.return_value = amod.CommandResult(("am", "start"), 0, "Success", "")
            result = perform_rejoin(cfg, reason="start")
            mock_launch.assert_called_once_with("com.roblox.client")
        self.assertTrue(result.success)

    def test_single_package_start_table_row_says_started(self):
        """Table rows for a successful single-package launch say 'Started'."""
        rows = [(1, "com.roblox.client", "Main", "Started", "Roblox launch command sent")]
        table = build_start_table(rows)
        self.assertIn("Started", table)
        self.assertNotIn("Launch skipped", table)

    def test_launch_skipped_phrase_not_in_table(self):
        """The phrase 'Launch skipped' must never appear in a start table."""
        rows = [(1, "com.roblox.client", "Main", "Started", "Roblox launch command sent")]
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
        cfg["roblox_packages"] = ["com.roblox.client", "com.moons.alt1"]
        validated = validate_config(cfg)
        self.assertEqual(len(validated["roblox_packages"]), 2)
        self.assertEqual(validated["roblox_packages"][0]["package"], "com.roblox.client")

    def test_unknown_username_does_not_block_launch(self):
        """launch_app is not skipped when account_username is empty."""
        from agent.launcher import perform_rejoin

        cfg = default_config()
        cfg["first_setup_completed"] = True
        cfg["roblox_packages"] = [
            {"package": "com.roblox.client", "account_username": "", "enabled": True, "username_source": "not_set"}
        ]
        with unittest.mock.patch.object(amod, "launch_app") as mock_launch, \
             unittest.mock.patch.object(amod, "package_installed", return_value=True):
            mock_launch.return_value = amod.CommandResult(("am", "start"), 0, "Success", "")
            result = perform_rejoin(cfg, reason="start")
            mock_launch.assert_called_once()
        self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()

