import argparse
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agent.commands import _print_config_summary, _run_edit_config_menu, build_start_table
from agent.config import default_config, validate_config


class StartFlowUxTests(unittest.TestCase):
    def test_start_table_shows_label_and_package(self):
        entries = [
            {"package": "com.roblox.client", "label": "Main", "enabled": True},
            {"package": "com.moons.alt1", "label": "Alt 1", "enabled": True},
        ]
        table = build_start_table(entries, {"com.roblox.client": "Preparation", "com.moons.alt1": "Waiting"})

        self.assertIn("Main (com.roblox.client)", table)
        self.assertIn("Alt 1 (com.moons.alt1)", table)
        self.assertIn("Preparation", table)

    def test_settings_summary_is_not_raw_json(self):
        cfg = validate_config(default_config())
        output = io.StringIO()
        with redirect_stdout(output):
            _print_config_summary(cfg)

        text = output.getvalue()
        self.assertIn("DENG Tool: Rejoin Settings", text)
        self.assertIn("Roblox Packages:", text)
        self.assertNotIn('{"', text)

    def test_public_config_menu_has_no_manual_auto_resize_step(self):
        args = argparse.Namespace(no_color=True)
        cfg = validate_config(default_config())
        output = io.StringIO()
        with redirect_stdout(output):
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


if __name__ == "__main__":
    unittest.main()
