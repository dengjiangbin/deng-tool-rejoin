"""Tests for the hidden ``--diag-startup`` startup-tracer command.

These cover the two halves of the live debug loop:

1. ``cmd_diag_startup`` itself: prints ``STEP:<name>`` markers in order,
   continues past per-step exceptions, and reports an OK / ERROR line
   for every step (so a SIGSEGV in any step would leave a clear "last
   successful STEP" in the captured stdout).
2. ``probe._capture_diag_startup``: invokes the diag-startup child via
   ``subprocess.run``, captures the returncode (including SIGSEGV =
   ``-11``), masks secrets, and surfaces the last step.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import textwrap
import unittest
from pathlib import Path
from unittest import mock


class DiagStartupCommandTest(unittest.TestCase):
    """``cmd_diag_startup`` walks every cmd_menu step in deterministic order."""

    def _run(self) -> str:
        from agent import commands

        ns = argparse.Namespace()
        buf = io.StringIO()
        # cmd_diag_startup writes to sys.stdout and flushes; redirect via mock.
        with mock.patch.object(commands.sys, "stdout", buf):
            rc = commands.cmd_diag_startup(ns)
        self.assertEqual(rc, 0, f"diag-startup unexpectedly returned {rc}")
        return buf.getvalue()

    def test_emits_entered_and_finished_markers(self) -> None:
        out = self._run()
        self.assertIn("STEP:entered", out)
        self.assertIn("STEP:finished", out)

    def test_emits_known_intermediate_steps(self) -> None:
        out = self._run()
        # The cmd_menu chain we're tracing.  All of these should appear
        # at least once in the output so a probe consumer can tell which
        # one ran last before a hypothetical crash.
        for name in (
            "ensure_app_dirs",
            "check_crash_log",
            "keystore_dev_mode",
            "load_config",
            "license_section_read",
            # Granular sub-steps replaced the monolithic "license_remote_check"
            # so the next on-device segfault points to the exact failing line.
            "license_cache_fast_path",
            "license_sync_install_id",
            "license_get_device_model",
            "license_safe_http_backend",
            "license_curl_available",
            "license_remote_check_isolated",
            "license_remote_check_direct",
            "import_supervisor",
            "import_window_layout",
            "detect_display_info",
        ):
            self.assertIn(f"STEP:{name}\n", out, f"missing STEP:{name}")

    def test_continues_past_step_exception(self) -> None:
        """A raise inside one step must NOT abort the whole tracer."""
        from agent import commands

        # Force load_config to raise; subsequent steps must still run.
        with mock.patch.object(commands, "load_config", side_effect=RuntimeError("boom")):
            buf = io.StringIO()
            with mock.patch.object(commands.sys, "stdout", buf):
                rc = commands.cmd_diag_startup(argparse.Namespace())
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        # ERROR line printed for the failing step …
        self.assertRegex(out, r"ERROR:load_config\s+RuntimeError:")
        # … and the next step still ran.
        self.assertIn("STEP:sync_install_id", out)
        self.assertIn("STEP:finished", out)


class CaptureDiagStartupTest(unittest.TestCase):
    """``probe._capture_diag_startup`` isolates the child via subprocess."""

    def test_captures_normal_exit(self) -> None:
        from agent import probe

        fake_proc = mock.Mock()
        fake_proc.returncode = 0
        fake_proc.stdout = "STEP:entered\nOK:ensure_app_dirs\nSTEP:finished\n"
        fake_proc.stderr = ""

        with mock.patch("agent.probe.shutil.which", return_value="/usr/bin/deng-rejoin"):
            with mock.patch("subprocess.run", return_value=fake_proc) as mrun:
                errors: list[dict[str, str]] = []
                out = probe._capture_diag_startup(errors)

        # Subprocess invoked with the wrapper + --diag-startup flag.
        self.assertEqual(mrun.call_args.args[0], ["/usr/bin/deng-rejoin", "--diag-startup"])
        self.assertEqual(errors, [])
        self.assertEqual(out["returncode"], 0)
        self.assertFalse(out["crashed"])
        self.assertFalse(out["sigsegv"])
        self.assertEqual(out["last_step"], "STEP:finished")
        self.assertIn("OK:ensure_app_dirs", out["stdout"])

    def test_captures_sigsegv(self) -> None:
        """Child SIGSEGV ⇒ returncode == -11 surfaces cleanly, last_step usable."""
        from agent import probe

        fake_proc = mock.Mock()
        fake_proc.returncode = -11
        fake_proc.stdout = (
            "STEP:entered\nOK:ensure_app_dirs\n"
            "STEP:license_remote_check\n"
        )
        fake_proc.stderr = ""

        with mock.patch("agent.probe.shutil.which", return_value="/usr/bin/deng-rejoin"):
            with mock.patch("subprocess.run", return_value=fake_proc):
                errors: list[dict[str, str]] = []
                out = probe._capture_diag_startup(errors)

        self.assertEqual(out["returncode"], -11)
        self.assertTrue(out["sigsegv"])
        self.assertTrue(out["crashed"])
        self.assertEqual(out["last_step"], "STEP:license_remote_check")

    def test_masks_secrets_in_stdout(self) -> None:
        from agent import probe

        fake_proc = mock.Mock()
        fake_proc.returncode = 0
        fake_proc.stdout = "STEP:entered\nLICENSE_KEY_EXPORT_SECRET=topsecret123\nSTEP:finished\n"
        fake_proc.stderr = ""

        with mock.patch("agent.probe.shutil.which", return_value="/usr/bin/deng-rejoin"):
            with mock.patch("subprocess.run", return_value=fake_proc):
                errors: list[dict[str, str]] = []
                out = probe._capture_diag_startup(errors)

        # The HMAC-signing secret pattern must mask the value.
        self.assertNotIn("topsecret123", out["stdout"])
        self.assertIn("<masked:", out["stdout"])

    def test_missing_wrapper_records_error(self) -> None:
        from agent import probe

        with mock.patch("agent.probe.shutil.which", return_value=None):
            errors: list[dict[str, str]] = []
            out = probe._capture_diag_startup(errors)

        self.assertEqual(out, {})
        self.assertTrue(any(e["step"] == "diag_startup" for e in errors))


class CaptureAllLogsTest(unittest.TestCase):
    """``probe._capture_all_logs`` reads every file under logs/, with tail + size."""

    def test_collects_crash_log_tail(self) -> None:
        import tempfile
        import time
        from agent import probe

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "crash.log").write_text(
                "Fatal Python error: Segmentation fault\nThread 0x7fa: foo.py:42\n",
                encoding="utf-8",
            )
            (tmp / "agent.log").write_text("normal log entry\n", encoding="utf-8")

            # Point LOG_PATH at this temp dir so log_dir = tmp.
            fake_log = tmp / "agent.log"
            with mock.patch("agent.probe.LOG_PATH", fake_log):
                errors: list[dict[str, str]] = []
                out = probe._capture_all_logs(errors)

        self.assertIn("crash.log", out)
        self.assertIn("agent.log", out)
        self.assertIn("Segmentation fault", out["crash.log"]["tail"])
        self.assertGreater(out["crash.log"]["size"], 0)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
