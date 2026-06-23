"""Regression tests for Start dashboard crash visibility."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import commands


class TestStartDashboardCrashGuard(unittest.TestCase):
    def test_report_start_dashboard_crash_writes_log_and_returns_one(self) -> None:
        exc = RuntimeError("dashboard boom")
        session = MagicMock()
        with patch.object(commands, "_write_cli_crash_log") as mock_log, \
             patch.object(commands, "_is_interactive", return_value=False), \
             patch("builtins.print") as mock_print:
            rc = commands._report_start_dashboard_crash(exc, session=session)
        self.assertEqual(rc, 1)
        mock_log.assert_called_once()
        session.mark.assert_called_once()
        printed = " ".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)
        self.assertIn("Dashboard crashed", printed)
        self.assertIn("crash.log", printed)

    def test_cmd_start_source_has_dashboard_fatal_return_path(self) -> None:
        import inspect

        source = inspect.getsource(commands.cmd_start)
        self.assertIn("_report_start_dashboard_crash", source)
        self.assertIn("return _report_start_dashboard_crash", source)


if __name__ == "__main__":
    unittest.main()
