"""Tests for actual-bounds readback (package-correct selection).

Verifies:
  * Bounds are picked from the window block that ACTUALLY mentions the
    requested package — never the first match in the file.
  * mHasSurface=true wins over plain Window{...} entries.
  * Multiple packages in the same dumpsys output don't cross-contaminate.
  * Activity dump fallback works when the window dump has no bounds.
  * read_actual_bounds returns ``unavailable`` gracefully on subprocess error.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import android, window_apply as wa


_DUMPSYS_WINDOW_TWO_PKGS = """\
Window #0 Window{a1 u0 com.other.pkg/com.other.MainActivity}:
  mFrame=[0,0][540,960]
  mHasSurface=true
  taskId=11
Window #1 Window{b2 u0 com.roblox.client/com.roblox.client.startup.ActivityProtocolLauncher}:
  mFrame=[378,16][1904,874]
  mHasSurface=true
  taskId=42
Window #2 Window{c3 u0 com.another.pkg/com.another.MainActivity}:
  mFrame=[0,1000][1080,1500]
  mHasSurface=true
  taskId=20
"""


_DUMPSYS_WINDOW_NO_SURFACE = """\
Window #0 Window{a1 u0 com.roblox.client/com.roblox.client.MainActivity}:
  mFrame=[100,100][800,500]
  mHasSurface=false
  taskId=42
"""


_DUMPSYS_WINDOW_FOCUSED = """\
mCurrentFocus=Window{abc u0 com.roblox.client/com.roblox.client.MainActivity}
Window #0 Window{abc u0 com.roblox.client/com.roblox.client.MainActivity}:
  mFrame=[123,200][1100,750]
  mHasSurface=false
  taskId=1
"""


_DUMPSYS_ACTIVITY_TWO_PKGS = """\
  Display #0
    Stack #0
      * TaskRecord{aa #11 A=com.other.pkg U=0 visible=true}
        mBounds=[0,0][540,960]
        userBounds=[0,0][540,960]
      * TaskRecord{bb #42 A=com.roblox.client U=0 visible=true}
        mBounds=[378,16][1904,874]
        mLastNonFullscreenBounds=[378,16][1904,874]
"""


class TestWindowReadbackPackageCorrect(unittest.TestCase):
    """The bounds returned must belong to the requested package — not the first."""

    def test_picks_correct_window_for_roblox(self):
        def _mock_run(args, *a, **kw):
            if args[0] == "dumpsys" and args[1] == "window":
                return android.CommandResult(tuple(args), 0, _DUMPSYS_WINDOW_TWO_PKGS, "")
            return android.CommandResult(tuple(args), 1, "", "")

        with patch.object(wa.android, "run_command", side_effect=_mock_run):
            bounds, src = wa.read_actual_bounds("com.roblox.client")
        self.assertEqual(bounds, (378, 16, 1904, 874))
        self.assertEqual(src, "dumpsys_window")

    def test_picks_correct_window_for_other_package(self):
        def _mock_run(args, *a, **kw):
            if args[0] == "dumpsys" and args[1] == "window":
                return android.CommandResult(tuple(args), 0, _DUMPSYS_WINDOW_TWO_PKGS, "")
            return android.CommandResult(tuple(args), 1, "", "")

        with patch.object(wa.android, "run_command", side_effect=_mock_run):
            bounds, src = wa.read_actual_bounds("com.other.pkg")
        self.assertEqual(bounds, (0, 0, 540, 960))
        self.assertEqual(src, "dumpsys_window")

    def test_focused_window_preferred_when_no_surface(self):
        def _mock_run(args, *a, **kw):
            if args[0] == "dumpsys" and args[1] == "window":
                return android.CommandResult(tuple(args), 0, _DUMPSYS_WINDOW_FOCUSED, "")
            return android.CommandResult(tuple(args), 1, "", "")

        with patch.object(wa.android, "run_command", side_effect=_mock_run):
            bounds, src = wa.read_actual_bounds("com.roblox.client")
        self.assertEqual(bounds, (123, 200, 1100, 750))
        self.assertEqual(src, "dumpsys_window")

    def test_no_surface_no_focus_still_returns_block_bounds(self):
        """When the only candidate has mHasSurface=false and no focus, we
        still return its bounds rather than 'unavailable'."""
        def _mock_run(args, *a, **kw):
            if args[0] == "dumpsys" and args[1] == "window":
                return android.CommandResult(tuple(args), 0, _DUMPSYS_WINDOW_NO_SURFACE, "")
            return android.CommandResult(tuple(args), 1, "", "")

        with patch.object(wa.android, "run_command", side_effect=_mock_run):
            bounds, src = wa.read_actual_bounds("com.roblox.client")
        self.assertEqual(bounds, (100, 100, 800, 500))
        self.assertEqual(src, "dumpsys_window")


class TestActivityReadbackFallback(unittest.TestCase):
    """When dumpsys window has no usable bounds, fall back to activity tasks."""

    def test_activity_fallback_finds_correct_task(self):
        def _mock_run(args, *a, **kw):
            if args[0] == "dumpsys" and args[1] == "window":
                # No window data
                return android.CommandResult(tuple(args), 1, "", "")
            if args[0] == "dumpsys" and args[1] == "activity":
                return android.CommandResult(tuple(args), 0, _DUMPSYS_ACTIVITY_TWO_PKGS, "")
            return android.CommandResult(tuple(args), 1, "", "")

        with patch.object(wa.android, "run_command", side_effect=_mock_run):
            bounds, src = wa.read_actual_bounds("com.roblox.client")
        self.assertEqual(bounds, (378, 16, 1904, 874))
        self.assertEqual(src, "dumpsys_activity")

    def test_activity_fallback_finds_correct_task_for_other_pkg(self):
        def _mock_run(args, *a, **kw):
            if args[0] == "dumpsys" and args[1] == "window":
                return android.CommandResult(tuple(args), 1, "", "")
            if args[0] == "dumpsys" and args[1] == "activity":
                return android.CommandResult(tuple(args), 0, _DUMPSYS_ACTIVITY_TWO_PKGS, "")
            return android.CommandResult(tuple(args), 1, "", "")

        with patch.object(wa.android, "run_command", side_effect=_mock_run):
            bounds, src = wa.read_actual_bounds("com.other.pkg")
        self.assertEqual(bounds, (0, 0, 540, 960))
        self.assertEqual(src, "dumpsys_activity")


class TestReadbackUnavailable(unittest.TestCase):
    """All commands fail → returns (None, 'unavailable') without raising."""

    def test_no_subprocess_output_returns_unavailable(self):
        with patch.object(wa.android, "run_command",
                          return_value=android.CommandResult(("dumpsys", "window"), 1, "", "")):
            bounds, src = wa.read_actual_bounds("com.roblox.client")
        self.assertIsNone(bounds)
        self.assertEqual(src, "unavailable")

    def test_subprocess_exception_returns_unavailable(self):
        with patch.object(wa.android, "run_command",
                          side_effect=RuntimeError("boom")):
            bounds, src = wa.read_actual_bounds("com.roblox.client")
        self.assertIsNone(bounds)
        self.assertEqual(src, "unavailable")


class TestParseWindowDumpsys(unittest.TestCase):
    """Internal parser exposed for unit testing."""

    def test_parses_multiple_blocks(self):
        cands = wa._parse_window_dumpsys(_DUMPSYS_WINDOW_TWO_PKGS, "com.roblox.client")
        # Only blocks mentioning the requested package are kept
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].bounds, (378, 16, 1904, 874))
        self.assertTrue(cands[0].has_surface)

    def test_falls_back_to_loose_text(self):
        """When no proper Window{} block exists but text mentions package +
        bounds, parser yields a weak candidate."""
        loose = "package=com.roblox.client somewhere\nmBounds=[10,20][100,200]"
        cands = wa._parse_window_dumpsys(loose, "com.roblox.client")
        self.assertTrue(cands)
        self.assertEqual(cands[0].bounds, (10, 20, 100, 200))


if __name__ == "__main__":
    unittest.main()
