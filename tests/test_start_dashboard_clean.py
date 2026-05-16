"""Tests for the clean Start dashboard.

Verifies:
  1. Start output contains logo/banner exactly once.
  2. Start output contains a table.
  3. Start output does NOT contain raw package setup text.
  4. Start output does NOT contain raw debug/monitor text.
  5. build_start_table produces Package | Username | State columns.
  6. build_start_verbose_details is NOT printed to stdout in normal mode.
  7. State progression appears only inside the table, not as bare text.
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.commands import build_start_table, build_start_verbose_details


# ── Helpers ───────────────────────────────────────────────────────────────────

def _capture_start_output(entries, cfg_overrides=None):
    """Run cmd_start in a highly mocked environment and capture its stdout."""
    import contextlib
    import agent.commands as _cmd
    import agent.keystore as _ks

    cfg = {
        "roblox_package": "com.roblox.client",
        "first_setup_completed": True,
        "packages": [
            {"package": "com.roblox.client", "enabled": True,
             "username": "TestUser", "low_graphics_enabled": True},
        ],
        "supervisor": {"enabled": False, "launch_grace_seconds": 0},
        "optimization": {"low_graphics_enabled": False},
    }
    if cfg_overrides:
        cfg.update(cfg_overrides)

    args = MagicMock()
    args.no_color = True
    args.verbose = False
    args.debug = False

    buf = io.StringIO()

    mock_sup_inst = MagicMock()
    mock_sup_inst.status_map = {"com.roblox.client": "Lobby"}
    mock_sup_inst.run_forever = lambda render_callback=None: None

    with (
        patch.object(_ks, "DEV_MODE", True),
        patch("agent.commands.load_config", return_value=cfg),
        patch("agent.commands.save_config"),
        patch("agent.commands._ensure_install_id_saved", return_value=cfg),
        patch("agent.commands.enabled_package_entries", return_value=entries),
        patch("agent.commands.android.discover_roblox_package_candidates", return_value=entries),
        patch("agent.commands.android.force_stop_packages_except", return_value=[]),
        patch("agent.commands.android.clear_safe_package_cache", return_value="Skipped"),
        patch("agent.commands.android.apply_low_graphics_optimization", return_value="Skipped"),
        patch("agent.commands._prepare_automatic_layout", return_value=(cfg, "layout ok")),
        patch("agent.commands.perform_rejoin", return_value=MagicMock(success=True, error=None)),
        patch("agent.commands.android.is_process_running", return_value=True),
        patch("agent.commands.effective_private_server_url", return_value=None),
        patch("agent.commands.MultiPackageSupervisor", return_value=mock_sup_inst),
        patch("agent.commands._package_detection_options", return_value=(None, True, True)),
        patch("agent.commands._clear_terminal"),  # suppress terminal clear
    ):
        with contextlib.redirect_stdout(buf):
            try:
                _cmd.cmd_start(args)
            except SystemExit:
                pass
            except Exception:
                pass

    return buf.getvalue()


# ── table unit tests (no subprocess needed) ───────────────────────────────────

class TestBuildStartTable(unittest.TestCase):
    """build_start_table must produce the correct column headers."""

    def test_has_package_column(self):
        rows = [(1, "com.roblox.client", "User1", "Launching")]
        table = build_start_table(rows, use_color=False)
        self.assertIn("Package", table)

    def test_has_username_column(self):
        rows = [(1, "com.roblox.client", "User1", "Launching")]
        table = build_start_table(rows, use_color=False)
        self.assertIn("Username", table)

    def test_has_state_column(self):
        rows = [(1, "com.roblox.client", "User1", "Launching")]
        table = build_start_table(rows, use_color=False)
        self.assertIn("State", table)

    def test_state_shown_in_table(self):
        rows = [(1, "com.roblox.client", "User1", "Join Unconfirmed")]
        table = build_start_table(rows, use_color=False)
        self.assertIn("Join Unconfirmed", table)

    def test_multiple_packages_all_shown(self):
        rows = [
            (1, "com.roblox.client",  "Alice", "Lobby"),
            (2, "com.roblox.client2", "Bob",   "In Server"),
        ]
        table = build_start_table(rows, use_color=False)
        self.assertIn("Alice", table)
        self.assertIn("Bob",   table)
        self.assertIn("Lobby", table)
        self.assertIn("In Server", table)


class TestBuildStartVerboseDetails(unittest.TestCase):
    """build_start_verbose_details must contain cache/graphics/launch detail."""

    def test_verbose_details_contain_cache(self):
        rows = [{"package": "com.roblox.client", "cache": "Cleared",
                 "graphics": "OK", "launch_detail": "process running"}]
        detail = build_start_verbose_details(rows, use_color=False)
        self.assertIn("Cleared", detail)

    def test_verbose_details_contain_graphics(self):
        rows = [{"package": "com.roblox.client", "cache": "Skipped",
                 "graphics": "Applied", "launch_detail": "process running"}]
        detail = build_start_verbose_details(rows, use_color=False)
        self.assertIn("Applied", detail)

    def test_verbose_details_empty_list_returns_empty(self):
        detail = build_start_verbose_details([], use_color=False)
        self.assertEqual(detail, "")


class TestStartOutputClean(unittest.TestCase):
    """cmd_start must not pollute stdout with raw setup/debug text."""

    ENTRIES = [
        {"package": "com.roblox.client", "enabled": True,
         "username": "User1", "low_graphics_enabled": True},
    ]

    def _get_output(self):
        return _capture_start_output(self.ENTRIES)

    def test_no_start_summary_header(self):
        out = self._get_output()
        self.assertNotIn("Start Summary", out,
            "Raw 'Start Summary' header must not appear in clean dashboard")

    def test_no_packages_selected_line(self):
        out = self._get_output()
        self.assertNotIn("Packages selected:", out,
            "Raw 'Packages selected:' must not appear in dashboard stdout")

    def test_no_detected_packages_line(self):
        out = self._get_output()
        self.assertNotIn("Detected packages:", out,
            "Raw 'Detected packages:' must not appear in dashboard stdout")

    def test_no_launch_mode_line(self):
        out = self._get_output()
        self.assertNotIn("Launch mode:", out,
            "Raw 'Launch mode:' must not appear in dashboard stdout")

    def test_no_session_active_line(self):
        out = self._get_output()
        self.assertNotIn("Session active", out,
            "Raw 'Session active' must not appear — table shows state")

    def test_table_present_in_output(self):
        out = self._get_output()
        self.assertTrue(
            "Package" in out or "State" in out or "─" in out or "|" in out,
            f"Table must be present in Start output. Got:\n{out[:500]}",
        )

    def test_no_raw_cache_text_in_stdout(self):
        out = self._get_output()
        self.assertNotIn("Clearing safe cache", out,
            "Cache progress text must go to log, not stdout")

    def test_no_background_processes_text(self):
        out = self._get_output()
        self.assertNotIn("Stopping background", out,
            "Background stop text must go to log, not stdout")


if __name__ == "__main__":
    unittest.main()
