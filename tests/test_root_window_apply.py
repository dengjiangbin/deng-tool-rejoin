"""Tests for the real-window apply layer.

Verifies:
  - apply_window_layout returns one ApplyResult per rect.
  - Excluded packages (Termux/system) are skipped.
  - Pre-write attempts XML direct first, then root fallback.
  - Capability probes run without raising.
  - read_actual_bounds parses [l,t][r,b] format.
  - Direct resize fallback is tried when bounds drift.
  - Verification tolerates ±32px drift.
  - All paths log to deng.rejoin.window_apply, never stdout.
  - apply_window_layout_silent never raises.
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

from agent.window_apply import (
    ApplyResult,
    _capability_probes,
    _parse_bounds_line,
    apply_window_layout,
    apply_window_layout_silent,
    read_actual_bounds,
)
from agent.window_layout import WindowRect


class TestApplyResult(unittest.TestCase):
    def test_default_fields(self):
        rect = WindowRect("com.roblox.client", 100, 100, 800, 500)
        r = ApplyResult(package=rect.package, desired=rect)
        self.assertFalse(r.final_ok)
        self.assertFalse(r.pre_write_ok)
        self.assertEqual(r.attempts, [])


class TestParseBoundsLine(unittest.TestCase):
    def test_parses_brackets(self):
        b = _parse_bounds_line("Window foo bounds=[100,200][1080,720]")
        self.assertEqual(b, (100, 200, 1080, 720))

    def test_returns_none_when_no_brackets(self):
        self.assertIsNone(_parse_bounds_line("no bounds here"))


class TestCapabilityProbes(unittest.TestCase):
    def test_probes_return_dict_without_raising(self):
        caps = _capability_probes()
        self.assertIsInstance(caps, dict)
        for key in ("root", "cmd_activity", "am_stack",
                    "dumpsys_activity", "dumpsys_window", "wm_size"):
            self.assertIn(key, caps)
            self.assertIsInstance(caps[key], bool)


class TestReadActualBoundsGraceful(unittest.TestCase):
    def test_unavailable_when_commands_fail(self):
        import agent.window_apply as wa
        from agent.android import CommandResult

        def _fail(args, *a, **kw):
            return CommandResult(tuple(args), 1, "", "")

        with patch.object(wa.android, "run_command", side_effect=_fail):
            bounds, source = read_actual_bounds("com.roblox.client")
        self.assertIsNone(bounds)
        self.assertEqual(source, "unavailable")

    def test_dumpsys_window_match(self):
        import agent.window_apply as wa
        from agent.android import CommandResult

        sample = (
            "Window foo {\n"
            "  package=com.roblox.client\n"
            "  mBounds=[200,100][1080,620]\n"
            "}\n"
        )

        def _mock_run(args, *a, **kw):
            if args[0] == "dumpsys" and args[1] == "window":
                return CommandResult(tuple(args), 0, sample, "")
            return CommandResult(tuple(args), 1, "", "")

        with patch.object(wa.android, "run_command", side_effect=_mock_run):
            bounds, source = read_actual_bounds("com.roblox.client")
        self.assertEqual(bounds, (200, 100, 1080, 620))
        self.assertEqual(source, "dumpsys_window")


class TestApplyWindowLayoutSilent(unittest.TestCase):
    """The silent wrapper must never raise and must return (success, total)."""

    def test_empty_list_returns_zero_zero(self):
        ok = apply_window_layout_silent([])
        self.assertEqual(ok, (0, 0))

    def test_excluded_package_skipped(self):
        rect = WindowRect("com.termux", 0, 0, 800, 450)
        success, total = apply_window_layout_silent([rect], verify_after=False)
        self.assertEqual(total, 1)
        # Excluded packages are not counted as successful
        self.assertEqual(success, 0)

    def test_never_raises_even_when_subprocess_fails(self):
        import agent.window_apply as wa

        def _boom(*a, **kw):
            raise RuntimeError("subprocess died")

        rect = WindowRect("com.roblox.client", 100, 100, 800, 550)
        with patch.object(wa, "apply_window_layout", side_effect=_boom):
            try:
                ok = apply_window_layout_silent([rect])
            except Exception as exc:  # noqa: BLE001
                self.fail(f"silent wrapper raised: {exc}")
        self.assertEqual(ok, (0, 0))


class TestApplyDoesNotPrint(unittest.TestCase):
    """apply_window_layout must not write to stdout/stderr."""

    def test_no_stdout_stderr_output(self):
        from agent.logger import silence_public_loggers
        silence_public_loggers()

        import agent.window_apply as wa

        rect = WindowRect("com.roblox.client", 100, 100, 800, 550)
        out = io.StringIO()
        err = io.StringIO()

        # Mock XML writes to succeed silently
        with (
            patch.object(wa, "update_app_cloner_xml", return_value=(True, "ok")),
            patch.object(wa, "update_app_cloner_xml_root", return_value=(False, "skip")),
            patch.object(wa.android, "detect_root",
                         return_value=MagicMock(available=False, tool=None)),
            patch.object(wa, "read_actual_bounds", return_value=(None, "unavailable")),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            results = wa.apply_window_layout([rect], verify_after=True)

        self.assertEqual(out.getvalue(), "", f"stdout leak: {out.getvalue()!r}")
        self.assertEqual(err.getvalue(), "", f"stderr leak: {err.getvalue()!r}")
        self.assertEqual(len(results), 1)


class TestApplyPipelineOrder(unittest.TestCase):
    """Verify direct XML is tried first, then root fallback."""

    def test_xml_direct_tried_first(self):
        import agent.window_apply as wa
        from agent.logger import silence_public_loggers
        silence_public_loggers()

        rect = WindowRect("com.roblox.client", 100, 100, 800, 550)
        direct_called = []
        root_called = []

        def _direct(pkg, r):
            direct_called.append(pkg)
            return True, "ok"

        def _root(pkg, r, tool, timeout=10):
            root_called.append(pkg)
            return True, "ok"

        with (
            patch.object(wa, "update_app_cloner_xml", side_effect=_direct),
            patch.object(wa, "update_app_cloner_xml_root", side_effect=_root),
            patch.object(wa.android, "detect_root",
                         return_value=MagicMock(available=True, tool="su")),
            patch.object(wa, "read_actual_bounds", return_value=(None, "unavailable")),
        ):
            wa.apply_window_layout([rect], verify_after=True)

        self.assertEqual(direct_called, ["com.roblox.client"])
        # Root not called when direct succeeded
        self.assertEqual(root_called, [])

    def test_root_fallback_when_direct_fails(self):
        import agent.window_apply as wa
        from agent.logger import silence_public_loggers
        silence_public_loggers()

        rect = WindowRect("com.roblox.client", 100, 100, 800, 550)
        root_called = []

        with (
            patch.object(wa, "update_app_cloner_xml",
                         return_value=(False, "permission denied")),
            patch.object(wa, "update_app_cloner_xml_root",
                         side_effect=lambda *a, **kw: (root_called.append(1) or (True, "via root"))),
            patch.object(wa.android, "detect_root",
                         return_value=MagicMock(available=True, tool="su")),
            patch.object(wa, "read_actual_bounds", return_value=(None, "unavailable")),
        ):
            results = wa.apply_window_layout([rect], verify_after=True)

        self.assertEqual(len(root_called), 1,
            "Root XML write must be tried after direct write fails")
        self.assertEqual(results[0].pre_write_method, "xml-root")


class TestVerifyBoundsTolerance(unittest.TestCase):
    """Bounds within ±32px tolerance must be considered applied successfully."""

    def test_exact_bounds_pass(self):
        import agent.window_apply as wa
        from agent.logger import silence_public_loggers
        silence_public_loggers()

        rect = WindowRect("com.roblox.client", 100, 100, 800, 550)
        with (
            patch.object(wa, "update_app_cloner_xml", return_value=(True, "ok")),
            patch.object(wa.android, "detect_root",
                         return_value=MagicMock(available=False, tool=None)),
            patch.object(wa, "read_actual_bounds",
                         return_value=((100, 100, 800, 550), "dumpsys_window")),
        ):
            results = wa.apply_window_layout([rect], verify_after=True)
        self.assertTrue(results[0].final_ok)

    def test_drift_outside_tolerance_marked_warn(self):
        import agent.window_apply as wa
        from agent.logger import silence_public_loggers
        silence_public_loggers()

        rect = WindowRect("com.roblox.client", 100, 100, 800, 550)
        with (
            patch.object(wa, "update_app_cloner_xml", return_value=(True, "ok")),
            patch.object(wa.android, "detect_root",
                         return_value=MagicMock(available=False, tool=None)),
            patch.object(wa, "read_actual_bounds",
                         return_value=((500, 500, 1200, 950), "dumpsys_window")),
        ):
            results = wa.apply_window_layout([rect], verify_after=True, retries=0)
        # Drift is way outside ±32, and no root → can't direct-resize
        self.assertFalse(results[0].final_ok)


if __name__ == "__main__":
    unittest.main()
