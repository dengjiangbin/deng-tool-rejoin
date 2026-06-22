"""Tests for the hidden ``--diag-startup`` and ``--diag-startup-full`` commands."""

from __future__ import annotations

import argparse
import io
import sys
import time
import unittest
from pathlib import Path
from unittest import mock


class DiagStartupFastCommandTest(unittest.TestCase):
    """``--diag-startup`` is a fast rescue path — no license or network work."""

    def test_fast_path_exits_without_license_steps(self) -> None:
        from agent import commands

        buf = io.StringIO()
        with mock.patch("agent.safe_io.sys.stdout", buf), \
             self.assertRaises(SystemExit) as ctx:
            commands.cmd_diag_startup(argparse.Namespace())
        self.assertEqual(ctx.exception.code, 0)

        out = buf.getvalue()
        self.assertIn("STEP:entered", out)
        self.assertIn("STEP:check_crash_log", out)
        self.assertIn("STEP:finished", out)
        self.assertNotIn("STEP:license_remote_check", out)
        self.assertNotIn("STEP:load_config", out)

    def test_fast_path_prints_crash_notice(self) -> None:
        from agent import commands

        buf = io.StringIO()
        notice = "Previous crash detected. Crash log saved at: /tmp/crash.log"
        with mock.patch("agent.safe_io.sys.stdout", buf), \
             mock.patch.object(
                 commands.safe_io,
                 "check_and_report_crash_log",
                 return_value=notice,
             ), \
             self.assertRaises(SystemExit):
            commands.cmd_diag_startup(argparse.Namespace())
        self.assertIn("Previous crash detected", buf.getvalue())

    def test_fast_path_completes_under_one_second(self) -> None:
        from agent import commands

        started = time.monotonic()
        with mock.patch.object(commands, "ensure_app_dirs"), \
             mock.patch.object(
                 commands.safe_io,
                 "check_and_report_crash_log",
                 return_value=None,
             ), \
             mock.patch.object(commands.keystore, "DEV_MODE", False), \
             self.assertRaises(SystemExit):
            commands.cmd_diag_startup(argparse.Namespace())
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0)


class DiagStartupFullCommandTest(unittest.TestCase):
    """``--diag-startup-full`` walks every cmd_menu step in deterministic order."""

    def _run(self) -> str:
        from agent import commands

        buf = io.StringIO()
        with mock.patch.object(commands.sys, "stdout", buf):
            rc = commands.cmd_diag_startup_full(argparse.Namespace())
        self.assertEqual(rc, 0, f"diag-startup-full unexpectedly returned {rc}")
        return buf.getvalue()

    def test_emits_entered_and_finished_markers(self) -> None:
        out = self._run()
        self.assertIn("STEP:entered", out)
        self.assertIn("STEP:finished", out)

    def test_emits_known_intermediate_steps(self) -> None:
        out = self._run()
        for name in (
            "ensure_app_dirs",
            "check_crash_log",
            "keystore_dev_mode",
            "load_config",
            "license_section_read",
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
        from agent import commands

        with mock.patch.object(commands, "load_config", side_effect=RuntimeError("boom")):
            buf = io.StringIO()
            with mock.patch.object(commands.sys, "stdout", buf):
                rc = commands.cmd_diag_startup_full(argparse.Namespace())
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertRegex(out, r"ERROR:load_config\s+RuntimeError:")
        self.assertIn("STEP:sync_install_id", out)
        self.assertIn("STEP:finished", out)


class CaptureDiagStartupTest(unittest.TestCase):
    """``probe._capture_diag_startup`` isolates the full tracer child."""

    def test_captures_normal_exit(self) -> None:
        from agent import probe

        fake_stdout = "ok"
        fake_stderr = ""
        fake_rc = 0

        def _fake_run(args, **kwargs):
            del kwargs
            self.assertEqual(args, ["/usr/bin/deng-rejoin", "--diag-startup-full"])
            proc = mock.Mock()
            proc.returncode = fake_rc
            proc.stdout = "STEP:entered\nOK:ensure_app_dirs\nSTEP:finished\n"
            proc.stderr = fake_stderr
            return proc

        with mock.patch("agent.probe.shutil.which", return_value="/usr/bin/deng-rejoin"), \
             mock.patch(
                 "agent.subprocess_isolated.run_isolated_text",
                 side_effect=lambda args, **kw: (
                     fake_rc,
                     "STEP:entered\nOK:ensure_app_dirs\nSTEP:finished\n",
                     fake_stderr,
                     False,
                 ),
             ):
            errors: list[dict[str, str]] = []
            out = probe._capture_diag_startup(errors)

        self.assertEqual(errors, [])
        self.assertEqual(out["returncode"], 0)
        self.assertFalse(out["crashed"])
        self.assertEqual(out["last_step"], "STEP:finished")

    def test_captures_sigsegv(self) -> None:
        from agent import probe

        with mock.patch("agent.probe.shutil.which", return_value="/usr/bin/deng-rejoin"), \
             mock.patch(
                 "agent.subprocess_isolated.run_isolated_text",
                 return_value=(
                     -11,
                     "STEP:entered\nOK:ensure_app_dirs\nSTEP:license_remote_check\n",
                     "",
                     False,
                 ),
             ):
            errors: list[dict[str, str]] = []
            out = probe._capture_diag_startup(errors)

        self.assertEqual(out["returncode"], -11)
        self.assertTrue(out["sigsegv"])
        self.assertEqual(out["last_step"], "STEP:license_remote_check")

    def test_masks_secrets_in_stdout(self) -> None:
        from agent import probe

        with mock.patch("agent.probe.shutil.which", return_value="/usr/bin/deng-rejoin"), \
             mock.patch(
                 "agent.subprocess_isolated.run_isolated_text",
                 return_value=(
                     0,
                     "STEP:entered\nLICENSE_KEY_EXPORT_SECRET=topsecret123\nSTEP:finished\n",
                     "",
                     False,
                 ),
             ):
            errors: list[dict[str, str]] = []
            out = probe._capture_diag_startup(errors)

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
        from agent import probe

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "crash.log").write_text(
                "Fatal Python error: Segmentation fault\nThread 0x7fa: foo.py:42\n",
                encoding="utf-8",
            )
            (tmp / "agent.log").write_text("normal log entry\n", encoding="utf-8")

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
