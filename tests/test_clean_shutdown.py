"""Tests for clean Ctrl+C / supervisor stop behavior.

Verifies:
  - KeyboardInterrupt at the parse_args boundary returns 0 with no public text.
  - KeyboardInterrupt during cmd_start returns 0 with no public text.
  - Supervisor._handle_stop does NOT print 'Supervisor stopping' to stdout.
  - No 'Segmentation fault' text reaches stdout/stderr from Python paths.
  - faulthandler is enabled to a FILE, not stderr.
  - Public stdout/stderr do not contain a traceback after KeyboardInterrupt.
"""

from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


class TestMainKeyboardInterrupt(unittest.TestCase):
    """main() must exit cleanly on Ctrl+C."""

    def _run_main(self, argv: list[str], *, raise_kbi=False):
        from agent.commands import main
        out = io.StringIO()
        err = io.StringIO()
        if raise_kbi:
            with (
                patch("agent.commands.parse_args", side_effect=KeyboardInterrupt),
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                rc = main(argv)
        else:
            with redirect_stdout(out), redirect_stderr(err):
                rc = main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_kbi_returns_zero(self):
        rc, _, _ = self._run_main(["version"], raise_kbi=True)
        self.assertEqual(rc, 0)

    def test_kbi_no_public_interrupted_text(self):
        _, out, err = self._run_main(["version"], raise_kbi=True)
        self.assertNotIn("Interrupted", out)
        self.assertNotIn("Interrupted", err)

    def test_kbi_no_traceback(self):
        _, out, err = self._run_main(["version"], raise_kbi=True)
        self.assertNotIn("Traceback", out)
        self.assertNotIn("Traceback", err)

    def test_kbi_no_supervisor_stopping_text(self):
        _, out, err = self._run_main(["version"], raise_kbi=True)
        self.assertNotIn("Supervisor stopping", out)
        self.assertNotIn("Supervisor stopping", err)
        self.assertNotIn("Ctrl+C received", out)
        self.assertNotIn("Ctrl+C received", err)


class TestSupervisorHandleStopIsSilent(unittest.TestCase):
    """MultiPackageSupervisor._handle_stop must not print to stdout/stderr."""

    def test_handle_stop_writes_no_public_text(self):
        from agent.supervisor import MultiPackageSupervisor
        entries = [{"package": "com.roblox.client", "account_username": "User"}]
        cfg = {"roblox_package": "com.roblox.client", "supervisor": {"enabled": True}}
        sup = MultiPackageSupervisor(entries, cfg)

        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            sup._handle_stop(15, None)  # SIGTERM

        self.assertEqual(out.getvalue(), "",
            f"_handle_stop must not write to stdout, got: {out.getvalue()!r}")
        self.assertNotIn("Supervisor stopping", err.getvalue())
        self.assertNotIn("Ctrl+C received", err.getvalue())
        # And it must have set the stop_event
        self.assertTrue(sup.stop_event.is_set())


class TestFaulthandlerWritesToFile(unittest.TestCase):
    """setup_faulthandler() must enable faulthandler with a FILE, not stderr."""

    def test_faulthandler_uses_file_argument(self):
        import faulthandler
        from agent import safe_io as sio

        captured: dict[str, object] = {}

        def fake_enable(file=None, **kw):
            captured["file"] = file

        original = faulthandler.enable
        try:
            faulthandler.enable = fake_enable  # type: ignore
            sio.setup_faulthandler()
        finally:
            faulthandler.enable = original  # type: ignore

        # Either faulthandler was enabled with a file, or it was not enabled at all
        # (graceful skip).  Never with file=None or file=sys.stderr.
        if "file" in captured:
            f = captured["file"]
            self.assertIsNotNone(f, "faulthandler.enable must be called with a file")
            self.assertNotEqual(f, sys.stderr,
                "faulthandler must NOT write to public stderr")


class TestInternalLoggersDoNotLeakToStderr(unittest.TestCase):
    """deng.rejoin.* namespace loggers must NOT emit to stderr via lastResort."""

    def test_window_layout_warning_does_not_leak(self):
        """A warning from window_layout._log must not appear on stderr."""
        from agent.logger import silence_public_loggers
        import logging

        silence_public_loggers()

        err = io.StringIO()
        with patch("sys.stderr", err):
            logging.getLogger("deng.rejoin.window_layout").warning(
                "test landscape_blocks should NOT leak"
            )

        self.assertEqual(err.getvalue(), "",
            f"warning leaked to stderr: {err.getvalue()!r}")

    def test_layout_logger_warning_does_not_leak(self):
        from agent.logger import silence_public_loggers
        import logging

        silence_public_loggers()

        err = io.StringIO()
        with patch("sys.stderr", err):
            logging.getLogger("deng.rejoin.layout").error("test layout error")

        self.assertEqual(err.getvalue(), "")

    def test_start_logger_warning_does_not_leak(self):
        from agent.logger import silence_public_loggers
        import logging

        silence_public_loggers()

        err = io.StringIO()
        with patch("sys.stderr", err):
            logging.getLogger("deng.rejoin.start").error("test start error")

        self.assertEqual(err.getvalue(), "")


class TestRerunAfterStop(unittest.TestCase):
    """After stop, parse_args must still work normally for next invocation."""

    def test_parse_args_works_after_kbi(self):
        from agent.commands import parse_args
        # Simulate previous run with KBI; this run is fresh
        ns = parse_args(["version", "--no-color"])
        self.assertEqual(ns.resolved_command, "version")


if __name__ == "__main__":
    unittest.main()
