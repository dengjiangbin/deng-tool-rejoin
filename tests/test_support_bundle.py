"""Tests for the hardened ``--support-bundle`` hidden command.

SAFETY CONTRACT (must not regress):
  * READ-ONLY: support-bundle must NOT mutate any pkg_preferences.xml.
  * NO segfault text reaches the public terminal under any circumstance.
  * After running support-bundle, ``deng-rejoin`` itself must still parse
    arguments and route to a normal command.
  * Output: ONE concise success/failure line.  No traceback, no debug spam.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import agent.commands as cmd


class _Args:
    """Argparse-namespace-like stand-in."""

    def __init__(self):
        self.no_color = True


class TestSupportBundleIsReadOnly(unittest.TestCase):
    """Support-bundle MUST NOT touch any pkg_preferences.xml."""

    def test_does_not_call_update_app_cloner_xml(self):
        from agent import window_layout, window_apply
        from agent import layout_discovery

        write_calls: list = []
        write_root_calls: list = []

        with (
            patch("agent.commands.load_config",
                  return_value={"roblox_packages": [{"package": "com.roblox.client", "enabled": True}]}),
            patch.object(window_layout, "update_app_cloner_xml",
                         side_effect=lambda *a, **kw: write_calls.append(a) or (True, "ok")),
            patch.object(window_layout, "update_app_cloner_xml_root",
                         side_effect=lambda *a, **kw: write_root_calls.append(a) or (True, "ok")),
            patch.object(window_apply, "apply_window_layout", return_value=[]),
            patch.object(layout_discovery, "run_discovery_and_log",
                         return_value=(Path("/tmp/disc.log"), {})),
            redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
        ):
            cmd.cmd_support_bundle(_Args())

        self.assertEqual(write_calls, [], "support-bundle wrote to App Cloner XML!")
        self.assertEqual(write_root_calls, [], "support-bundle wrote to App Cloner XML via root!")

    def test_does_not_save_config(self):
        with (
            patch("agent.commands.load_config",
                  return_value={"roblox_packages": [{"package": "com.roblox.client", "enabled": True}]}),
            patch("agent.commands.save_config") as save_mock,
            redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
        ):
            cmd.cmd_support_bundle(_Args())
        self.assertFalse(save_mock.called,
            "support-bundle must not call save_config (config is read-only)")


class TestSupportBundleNoSegfaultText(unittest.TestCase):
    """No 'Segmentation fault' / traceback reaches the public terminal."""

    def test_no_segfault_or_traceback_in_output(self):
        out = io.StringIO()
        err = io.StringIO()
        with (
            patch("agent.commands.load_config", return_value=None),
            redirect_stdout(out), redirect_stderr(err),
        ):
            cmd.cmd_support_bundle(_Args())
        combined = out.getvalue() + err.getvalue()
        for forbidden in ("Segmentation fault", "Traceback", "core dumped"):
            self.assertNotIn(forbidden, combined,
                f"forbidden text '{forbidden}' leaked: {combined!r}")

    def test_subprocess_segfault_does_not_kill_parent(self):
        """A child process that returns non-zero (simulated SIGSEGV exit) does
        NOT crash the parent or leak 'Segmentation fault' to stdout."""
        from agent.commands import _run_diag_in_subprocess

        # Patch subprocess.run to simulate a crashing child.
        class _FakeCompleted:
            returncode = -11   # -SIGSEGV
            stdout = ""
            stderr = "Segmentation fault (core dumped)"

        out = io.StringIO()
        err = io.StringIO()
        with (
            patch("agent.commands.subprocess.run", return_value=_FakeCompleted()),
            redirect_stdout(out), redirect_stderr(err),
        ):
            ok, body = _run_diag_in_subprocess(["dumpsys", "window"])
        self.assertFalse(ok)
        # The child's "Segmentation fault" output may appear in the captured
        # body string, but it must NOT reach stdout/stderr of the parent.
        self.assertNotIn("Segmentation fault", out.getvalue())
        self.assertNotIn("Segmentation fault", err.getvalue())


class TestSupportBundleSurvivesInternalErrors(unittest.TestCase):
    """Every internal exception is caught — only ONE result line is printed."""

    def test_load_config_failure_does_not_crash(self):
        out = io.StringIO()
        err = io.StringIO()
        with (
            patch("agent.commands.load_config",
                  side_effect=RuntimeError("config explodes")),
            redirect_stdout(out), redirect_stderr(err),
        ):
            rc = cmd.cmd_support_bundle(_Args())
        # Either 0 (bundle was still written without config) or 1 (write
        # also failed) — never a traceback.
        self.assertIn(rc, (0, 1))
        self.assertNotIn("Traceback", out.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


class TestPostBundleNormalStartup(unittest.TestCase):
    """After support-bundle, ``deng-rejoin`` must still parse arguments."""

    def test_parse_args_still_works_after_bundle(self):
        out = io.StringIO()
        err = io.StringIO()
        with (
            patch("agent.commands.load_config", return_value=None),
            redirect_stdout(out), redirect_stderr(err),
        ):
            cmd.cmd_support_bundle(_Args())

        # Now simulate a fresh deng-rejoin invocation.
        ns = cmd.parse_args(["version", "--no-color"])
        self.assertEqual(ns.resolved_command, "version")

    def test_main_after_bundle_does_not_print_traceback(self):
        with patch("agent.commands.load_config", return_value=None):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                cmd.cmd_support_bundle(_Args())

        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cmd.main(["version", "--no-color"])
        self.assertEqual(rc, 0)
        self.assertNotIn("Traceback", out.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        self.assertNotIn("Segmentation fault", out.getvalue())
        self.assertNotIn("Segmentation fault", err.getvalue())


class TestDiscoverLayoutKeysCommand(unittest.TestCase):
    """Hidden --discover-layout-keys command prints exactly ONE concise line."""

    def test_prints_one_line_and_returns_zero(self):
        out = io.StringIO()
        err = io.StringIO()
        with (
            patch("agent.commands.load_config", return_value=None),
            patch("agent.layout_discovery.run_discovery_and_log",
                  return_value=(Path("/tmp/disc.log"), {})),
            redirect_stdout(out), redirect_stderr(err),
        ):
            rc = cmd.cmd_discover_layout_keys(_Args())
        self.assertEqual(rc, 0)
        self.assertIn("Layout key discovery saved:", out.getvalue())
        # Exactly one line of output to terminal
        non_empty_lines = [
            ln for ln in (out.getvalue() + err.getvalue()).splitlines()
            if ln.strip()
        ]
        self.assertEqual(len(non_empty_lines), 1, non_empty_lines)


class TestParseArgsHiddenFlags(unittest.TestCase):
    """Hidden flags map to internal commands; not visible in public help."""

    def test_discover_layout_keys_flag(self):
        ns = cmd.parse_args(["--discover-layout-keys"])
        self.assertEqual(ns.resolved_command, "discover-layout-keys")

    def test_support_bundle_flag(self):
        ns = cmd.parse_args(["--support-bundle"])
        self.assertEqual(ns.resolved_command, "support-bundle")

    def test_support_bundle_positional_alias(self):
        ns = cmd.parse_args(["support-bundle"])
        self.assertEqual(ns.resolved_command, "support-bundle")

    def test_discover_layout_keys_positional_alias(self):
        ns = cmd.parse_args(["discover-layout-keys"])
        self.assertEqual(ns.resolved_command, "discover-layout-keys")


if __name__ == "__main__":
    unittest.main()
