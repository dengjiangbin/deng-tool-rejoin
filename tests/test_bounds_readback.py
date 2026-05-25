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

_DUMPSYS_WINDOW_WITH_INPUT = """\
mCurrentFocus=Window{abc u0 com.roblox.client/com.roblox.client.MainActivity}
Window #0 Window{abc u0 com.roblox.client/com.roblox.client.MainActivity}:
  mFrame=[0,512][360,768]
  mContentFrame=[0,536][360,768]
  mStableFrame=[0,512][360,768]
  mHasSurface=true
  mTouchableRegion=Region([0,536][360,792])
  taskId=42
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

    def test_get_task_id_prefers_visible_real_task_over_stale(self):
        activity = """\
Display #0
  Stack #0
    * TaskRecord{old #11 A=com.roblox.client U=0 visible=false}
      mBounds=[0,0][1,1]
    * TaskRecord{new #42 A=com.roblox.client U=0 visible=true}
      mBounds=[378,16][1904,874]
"""

        def _mock_run(args, *a, **kw):
            if args[:2] == ["dumpsys", "activity"]:
                return android.CommandResult(tuple(args), 0, activity, "")
            return android.CommandResult(tuple(args), 1, "", "")

        with patch.object(wa.android, "run_command", side_effect=_mock_run):
            self.assertEqual(wa._get_task_id("com.roblox.client"), 42)

    def test_wait_for_window_ignores_stale_task_only(self):
        calls = [{"task": True, "window": False, "running": False, "surface": False, "foreground": False}]

        with patch.object(wa.android, "get_package_alive_evidence", side_effect=calls), \
             patch.object(wa.time, "sleep", return_value=None):
            self.assertFalse(wa._wait_for_window("com.roblox.client", timeout=0.01))


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

    def test_parses_input_and_content_frames_for_portrait_offset(self):
        cands = wa._parse_window_dumpsys(_DUMPSYS_WINDOW_WITH_INPUT, "com.roblox.client")
        self.assertEqual(len(cands), 1)
        entry = cands[0]
        self.assertEqual(entry.bounds, (0, 512, 360, 768))
        self.assertEqual(entry.content_frame, (0, 536, 360, 768))
        self.assertEqual(entry.input_region, (0, 536, 360, 792))
        self.assertEqual(entry.task_id, 42)


class TestPortraitLayerReadback(unittest.TestCase):
    def test_collects_input_mismatch_and_title_bar_evidence(self):
        from agent.window_layout import WindowRect

        activity = """\
Display #0
  Stack #0
    * TaskRecord{bb #42 A=com.roblox.client U=0 visible=true}
      mBounds=[0,512][360,768]
"""
        surface = """\
Layer com.roblox.client/com.roblox.client.MainActivity
  bounds=[0,512][360,768]
"""

        def _mock_run(args, *a, **kw):
            if args[:2] == ["dumpsys", "window"]:
                return android.CommandResult(tuple(args), 0, _DUMPSYS_WINDOW_WITH_INPUT, "")
            if args[:2] == ["dumpsys", "activity"]:
                return android.CommandResult(tuple(args), 0, activity, "")
            return android.CommandResult(tuple(args), 1, "", "")

        def _mock_android(args, *a, **kw):
            if args[:2] == ["dumpsys", "SurfaceFlinger"]:
                return android.CommandResult(tuple(args), 0, surface, "")
            if args[:2] == ["wm", "size"]:
                return android.CommandResult(tuple(args), 0, "Physical size: 720x1280", "")
            if args[:2] == ["wm", "density"]:
                return android.CommandResult(tuple(args), 0, "Physical density: 420", "")
            if args[:2] == ["dumpsys", "display"]:
                return android.CommandResult(tuple(args), 0, "", "")
            return android.CommandResult(tuple(args), 1, "", "")

        desired = WindowRect("com.roblox.client", 0, 512, 360, 768)
        with patch.object(wa.android, "run_command", side_effect=_mock_run), \
             patch.object(wa.android, "run_android_command", side_effect=_mock_android), \
             patch.object(wa, "_display_bounds", return_value=(0, 0, 720, 1280)):
            readback = wa.collect_portrait_layer_readback("com.roblox.client", desired, tolerance=8)

        self.assertEqual(readback["task_bounds"], [0, 512, 360, 768])
        self.assertEqual(readback["surface_bounds"], [0, 512, 360, 768])
        self.assertEqual(readback["input_region"], [0, 536, 360, 792])
        self.assertEqual(readback["title_bar_height"], 24)
        self.assertIn("visual_correct_input_wrong", readback["mismatch_classification"])
        self.assertIn("decor_title_bar_offset", readback["mismatch_classification"])

    def test_fullscreen_input_region_classified_as_failure(self):
        from agent.window_layout import WindowRect

        classes = wa._classify_layer_readback(
            desired=WindowRect("pkg", 0, 512, 360, 768),
            task_bounds=(0, 512, 360, 768),
            surface_bounds=(0, 512, 360, 768),
            input_region=(0, 0, 720, 1280),
            content_frame=None,
            display_bounds=(0, 0, 720, 1280),
            title_bar_height=0,
            tolerance=8,
        )
        self.assertIn("fullscreen_readback", classes)
        self.assertIn("visual_correct_input_wrong", classes)


if __name__ == "__main__":
    unittest.main()
