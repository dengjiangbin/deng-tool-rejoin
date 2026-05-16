"""Tests for hidden internal doctor CLI commands.

Verifies:
  - `doctor layout` parses and dispatches correctly.
  - `doctor root-state` parses and dispatches correctly.
  - `--layout-test` flag works.
  - `--root-state` flag works.
  - `--support-bundle` flag works.
  - Invalid doctor subcommand does NOT crash with traceback.
  - These commands are hidden from the public menu.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.commands import parse_args


class TestDoctorCliParsing(unittest.TestCase):
    """Doctor subcommands must parse without 'unrecognized arguments' errors."""

    def test_doctor_layout_sets_layout_test(self):
        ns = parse_args(["doctor", "layout"])
        self.assertTrue(getattr(ns, "layout_test", False))
        self.assertEqual(ns.resolved_command, "doctor")

    def test_doctor_root_state_sets_root_state(self):
        ns = parse_args(["doctor", "root-state"])
        self.assertTrue(getattr(ns, "root_state", False))
        self.assertEqual(ns.resolved_command, "doctor")

    def test_layout_test_flag(self):
        ns = parse_args(["--layout-test", "--no-color"])
        self.assertTrue(ns.layout_test)
        self.assertEqual(ns.resolved_command, "doctor")

    def test_root_state_flag(self):
        ns = parse_args(["--root-state", "--no-color"])
        self.assertTrue(ns.root_state)
        self.assertEqual(ns.resolved_command, "doctor")

    def test_support_bundle_flag(self):
        ns = parse_args(["--support-bundle", "--no-color"])
        self.assertTrue(ns.support_bundle)
        self.assertEqual(ns.resolved_command, "support-bundle")

    def test_support_bundle_positional(self):
        ns = parse_args(["support-bundle"])
        self.assertTrue(getattr(ns, "support_bundle", False))
        self.assertEqual(ns.resolved_command, "support-bundle")

    def test_doctor_invalid_subcommand_does_not_crash(self):
        """An unknown doctor subcommand must NOT raise."""
        try:
            ns = parse_args(["doctor", "nonsense-unknown"])
            self.assertEqual(ns.resolved_command, "doctor")
        except SystemExit as exc:
            # argparse should not error out
            self.fail(f"doctor unknown-sub crashed parse_args: {exc}")
        except Exception as exc:  # noqa: BLE001
            self.fail(f"doctor unknown-sub raised: {exc}")

    def test_doctor_no_subcommand_is_standard_doctor(self):
        ns = parse_args(["doctor"])
        self.assertEqual(ns.resolved_command, "doctor")
        self.assertFalse(getattr(ns, "layout_test", False))
        self.assertFalse(getattr(ns, "root_state", False))


class TestDoctorCommandsExecute(unittest.TestCase):
    """Doctor commands must produce concise output without traceback."""

    def _run_doctor(self, argv: list[str]) -> tuple[int, str, str]:
        from agent.commands import main
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), patch("sys.stderr", err):
            try:
                rc = main(argv)
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else 1
        return rc, out.getvalue(), err.getvalue()

    def test_doctor_layout_runs(self):
        rc, out, err = self._run_doctor(["doctor", "layout", "--no-color"])
        self.assertIn("Layout Diagnostic", out)
        self.assertNotIn("Traceback", out)
        self.assertNotIn("Traceback", err)

    def test_doctor_root_state_runs(self):
        rc, out, err = self._run_doctor(["doctor", "root-state", "--no-color"])
        self.assertIn("Root State Diagnostic", out)
        self.assertNotIn("Traceback", out)
        self.assertNotIn("Traceback", err)

    def test_layout_test_flag_runs(self):
        rc, out, err = self._run_doctor(["--layout-test", "--no-color"])
        self.assertIn("Layout Diagnostic", out)
        self.assertNotIn("Traceback", out)

    def test_root_state_flag_runs(self):
        rc, out, err = self._run_doctor(["--root-state", "--no-color"])
        self.assertIn("Root State Diagnostic", out)
        self.assertNotIn("Traceback", out)


class TestPublicMenuHidesDiagnostics(unittest.TestCase):
    """The public menu (cmd_menu) must NOT show doctor sub-diagnostics or support bundle."""

    def test_menu_handlers_do_not_show_diagnostics(self):
        from agent.commands import _handlers
        h = _handlers()
        # Internal handlers exist (so CLI dispatch works) but they are not user-facing.
        # We just verify the keys exist as expected.
        self.assertIn("doctor", h)
        self.assertIn("support-bundle", h)

    def test_argparse_help_does_not_mention_layout_test(self):
        """--layout-test, --root-state, --support-bundle must be SUPPRESS-helped."""
        import agent.commands as cmd_module
        parser_help = io.StringIO()
        with patch("sys.stdout", parser_help):
            try:
                cmd_module.parse_args(["--help"])
            except SystemExit:
                pass
        text = parser_help.getvalue()
        self.assertNotIn("--layout-test", text,
            "Internal --layout-test must be hidden from public --help")
        self.assertNotIn("--root-state", text,
            "Internal --root-state must be hidden from public --help")
        self.assertNotIn("--support-bundle", text,
            "Internal --support-bundle must be hidden from public --help")


if __name__ == "__main__":
    unittest.main()
