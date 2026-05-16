"""Tests for Kaeru-style window layout engine.

Layout rules verified:
  1 package  → single window occupies right pane
  2 packages → side-by-side (landscape) or stacked (portrait)
  3 packages → 2+1 (two on top, one full-width on bottom)
  4 packages → 2×2 grid
  5 packages → 2-column compact
  6 packages → 2-column compact
  7 packages → 3-column Kaeru cascade (title bars offset)
  9 packages → 3-column Kaeru cascade
  Split layout → left 35% reserved, right 65% Kaeru

Regression tests for existing callers:
  calculate_grid_layout delegates to calculate_kaeru_layout
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.window_layout import (
    KAERU_TITLE_BAR_H,
    TERMUX_LOG_FRACTION,
    DisplayInfo,
    WindowRect,
    calculate_grid_layout,
    calculate_kaeru_layout,
    calculate_split_layout,
    parse_wm_density,
    parse_wm_size,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pkgs(n: int) -> list[str]:
    return [f"com.pkg.test{i}" for i in range(1, n + 1)]


W, H = 1080, 1920   # typical portrait phone
LW   = W - int(W * TERMUX_LOG_FRACTION)   # right-pane width after split


class TestParseHelpers(unittest.TestCase):
    def test_parse_wm_size_standard(self):
        self.assertEqual(parse_wm_size("Physical size: 1080x1920"), (1080, 1920))

    def test_parse_wm_size_override(self):
        self.assertEqual(parse_wm_size("Override size: 720x1280"), (720, 1280))

    def test_parse_wm_size_none_on_garbage(self):
        self.assertIsNone(parse_wm_size("no size here"))

    def test_parse_wm_density_standard(self):
        self.assertEqual(parse_wm_density("Physical density: 420"), 420)

    def test_parse_wm_density_none_on_garbage(self):
        self.assertIsNone(parse_wm_density("no density"))


class TestKaeruLayoutCount1(unittest.TestCase):
    """1 package → full right pane."""

    def test_count_is_1(self):
        rects = calculate_kaeru_layout(_pkgs(1), LW, H)
        self.assertEqual(len(rects), 1)

    def test_single_window_fills_right_pane(self):
        rects = calculate_kaeru_layout(_pkgs(1), LW, H, gap=0)
        r = rects[0]
        self.assertEqual(r.left, 0)
        self.assertEqual(r.top, 0)
        self.assertEqual(r.right, LW)
        self.assertEqual(r.bottom, H)

    def test_package_name_preserved(self):
        pkgs = ["com.test.one"]
        rects = calculate_kaeru_layout(pkgs, LW, H)
        self.assertEqual(rects[0].package, "com.test.one")


class TestKaeruLayoutCount2(unittest.TestCase):
    """2 packages → always side-by-side (wide-first policy)."""

    def test_two_landscape_side_by_side(self):
        # Landscape pane: side-by-side
        rects = calculate_kaeru_layout(_pkgs(2), 960, 540, gap=0)
        self.assertEqual(len(rects), 2)
        self.assertLess(rects[0].right, rects[1].right)     # left window is to the left
        self.assertEqual(rects[0].top, rects[1].top)        # same vertical start
        self.assertEqual(rects[0].bottom, rects[1].bottom)  # same vertical end

    def test_two_portrait_also_side_by_side(self):
        # Portrait pane: also side-by-side (wide-first policy)
        rects = calculate_kaeru_layout(_pkgs(2), 540, 960, gap=0)
        self.assertEqual(len(rects), 2)
        # Both windows should be side-by-side (same top/bottom, different left/right)
        self.assertEqual(rects[0].top, rects[1].top)        # same top
        self.assertEqual(rects[0].bottom, rects[1].bottom)  # same bottom
        self.assertLess(rects[0].right, rects[1].right)     # distinct X positions

    def test_two_no_overlap(self):
        # Side-by-side: right edge of first <= left edge of second
        rects = calculate_kaeru_layout(_pkgs(2), 540, 960, gap=4)
        self.assertLessEqual(rects[0].right, rects[1].left)

    def test_two_within_bounds(self):
        rects = calculate_kaeru_layout(_pkgs(2), LW, H, gap=8)
        for r in rects:
            self.assertGreaterEqual(r.left, 0)
            self.assertGreaterEqual(r.top, 0)
            self.assertLessEqual(r.right, LW)
            self.assertLessEqual(r.bottom, H)

    def test_two_unique_x_positions(self):
        """Both packages must have distinct X positions (no stacking)."""
        rects = calculate_kaeru_layout(_pkgs(2), LW, H, gap=8)
        self.assertNotEqual(rects[0].left, rects[1].left,
                            "Both packages share the same left coordinate — they are stacking")

    def test_two_wide_layout_on_portrait_screen(self):
        """2-package layout on a portrait right-pane (702×1920) must be side-by-side."""
        rects = calculate_kaeru_layout(_pkgs(2), LW, H, gap=8)
        self.assertEqual(rects[0].top, rects[1].top)
        self.assertLess(rects[0].right, rects[1].left + 1)


class TestKaeruLayoutCount3(unittest.TestCase):
    """3 packages → 2+1 (two on top ~55%, one full-width on bottom ~45%)."""

    def _rects(self, gap=0):
        return calculate_kaeru_layout(_pkgs(3), LW, H, gap=gap)

    def test_count_is_3(self):
        self.assertEqual(len(self._rects()), 3)

    def test_top_two_are_above_bottom_one(self):
        rects = self._rects(gap=0)
        max_top_bottom  = max(rects[0].bottom, rects[1].bottom)
        bottom_win_top  = rects[2].top
        self.assertLessEqual(max_top_bottom, bottom_win_top)

    def test_top_two_same_height(self):
        rects = self._rects()
        self.assertEqual(rects[0].bottom, rects[1].bottom)

    def test_top_two_side_by_side(self):
        rects = self._rects(gap=0)
        # Right edge of first ≤ left edge of second (they are side by side)
        self.assertLessEqual(rects[0].right, rects[1].left + 1)

    def test_bottom_window_spans_full_width(self):
        rects = self._rects(gap=0)
        self.assertEqual(rects[2].left, 0)
        self.assertEqual(rects[2].right, LW)

    def test_all_within_bounds(self):
        for r in self._rects(gap=4):
            self.assertGreaterEqual(r.left, 0)
            self.assertLessEqual(r.right, LW)
            self.assertLessEqual(r.bottom, H)


class TestKaeruLayoutCount4(unittest.TestCase):
    """4 packages → 2×2 grid."""

    def _rects(self, gap=0):
        return calculate_kaeru_layout(_pkgs(4), LW, H, gap=gap)

    def test_count_is_4(self):
        self.assertEqual(len(self._rects()), 4)

    def test_2x2_top_row_above_bottom_row(self):
        rects = self._rects(gap=0)
        self.assertLessEqual(rects[0].bottom, rects[2].top + 1)
        self.assertLessEqual(rects[1].bottom, rects[3].top + 1)

    def test_2x2_columns_dont_overlap(self):
        rects = self._rects(gap=0)
        # Col 0 right <= col 1 left
        self.assertLessEqual(rects[0].right, rects[1].left + 1)
        self.assertLessEqual(rects[2].right, rects[3].left + 1)

    def test_all_within_bounds(self):
        for r in self._rects(gap=8):
            self.assertGreaterEqual(r.left, 0)
            self.assertLessEqual(r.right, LW)
            self.assertLessEqual(r.bottom, H)


class TestKaeruLayoutCount56(unittest.TestCase):
    """5–6 packages → 2-column compact."""

    def test_five_packages_count(self):
        self.assertEqual(len(calculate_kaeru_layout(_pkgs(5), LW, H)), 5)

    def test_six_packages_count(self):
        self.assertEqual(len(calculate_kaeru_layout(_pkgs(6), LW, H)), 6)

    def test_five_all_within_bounds(self):
        for r in calculate_kaeru_layout(_pkgs(5), LW, H, gap=8):
            self.assertGreaterEqual(r.left, 0)
            self.assertLessEqual(r.right, LW)
            self.assertLessEqual(r.bottom, H)

    def test_six_two_columns(self):
        rects = calculate_kaeru_layout(_pkgs(6), LW, H, gap=0)
        # All windows should be in 2 columns — right half of a window should not exceed LW
        lefts = sorted(set(r.left for r in rects))
        self.assertEqual(len(lefts), 2, f"Expected 2 unique left positions, got {lefts}")


class TestKaeruLayoutCount7Plus(unittest.TestCase):
    """7+ packages → 3-column Kaeru cascade."""

    def _rects(self, n, gap=8):
        return calculate_kaeru_layout(_pkgs(n), LW, H, gap=gap)

    def test_seven_packages_count(self):
        self.assertEqual(len(self._rects(7)), 7)

    def test_nine_packages_count(self):
        self.assertEqual(len(self._rects(9)), 9)

    def test_three_columns_used_for_7(self):
        rects = self._rects(7, gap=0)
        lefts = sorted(set(r.left for r in rects))
        self.assertEqual(len(lefts), 3, f"Expected 3 unique left positions, got {lefts}")

    def test_title_bars_cascade_downward(self):
        """Each column in a row must start lower than the previous (cascade offset)."""
        rects = self._rects(9, gap=0)
        # First 3 windows are in the same row (index 0,1,2 = col 0,1,2 of row 0)
        tops = [rects[i].top for i in range(3)]
        self.assertLess(tops[0], tops[1], "col1 must start below col0")
        self.assertLess(tops[1], tops[2], "col2 must start below col1")
        # Cascade step must be at least KAERU_TITLE_BAR_H
        self.assertGreaterEqual(tops[1] - tops[0], KAERU_TITLE_BAR_H)

    def test_all_within_bounds_for_9(self):
        for r in self._rects(9, gap=4):
            self.assertGreaterEqual(r.left, 0)
            self.assertGreaterEqual(r.top, 0)
            self.assertLessEqual(r.right, LW)
            self.assertLessEqual(r.bottom, H)

    def test_twelve_packages_count(self):
        self.assertEqual(len(self._rects(12)), 12)


class TestSplitLayout(unittest.TestCase):
    """Split layout: left 35% for Termux, right 65% Kaeru."""

    def test_all_rects_start_in_right_pane(self):
        left_boundary = int(W * TERMUX_LOG_FRACTION)
        rects = calculate_split_layout(_pkgs(4), W, H)
        for r in rects:
            self.assertGreaterEqual(
                r.left, left_boundary - 1,
                f"Package {r.package} starts in left pane: left={r.left} < {left_boundary}",
            )

    def test_split_preserves_package_count(self):
        for n in (1, 2, 3, 4, 6, 9):
            rects = calculate_split_layout(_pkgs(n), W, H)
            self.assertEqual(len(rects), n, f"n={n}: expected {n} rects, got {len(rects)}")

    def test_empty_packages_returns_empty(self):
        self.assertEqual(calculate_split_layout([], W, H), [])

    def test_all_within_screen_bounds(self):
        rects = calculate_split_layout(_pkgs(6), W, H, gap=8)
        for r in rects:
            self.assertGreaterEqual(r.left, 0)
            self.assertLessEqual(r.right, W)
            self.assertLessEqual(r.bottom, H)

    def test_termux_left_pane_untouched(self):
        """No Roblox window should overlap with the left-pane Termux area."""
        left_boundary = int(W * TERMUX_LOG_FRACTION)
        rects = calculate_split_layout(_pkgs(4), W, H)
        for r in rects:
            self.assertGreaterEqual(r.left, left_boundary - 1)


class TestLegacyCompatibility(unittest.TestCase):
    """calculate_grid_layout must delegate to calculate_kaeru_layout (backward compat)."""

    def test_grid_layout_two_side_by_side(self):
        rects = calculate_grid_layout(["pkg.one", "pkg.two"], 1080, 960, gap=0)
        self.assertEqual(len(rects), 2)
        self.assertLess(rects[0].right, rects[1].right)

    def test_grid_layout_four(self):
        rects = calculate_grid_layout(["a.one", "a.two", "a.three", "a.four"], 1000, 1000, gap=10)
        self.assertEqual(len(rects), 4)
        self.assertGreaterEqual(rects[0].left, 0)
        self.assertLessEqual(rects[-1].right, 1000)
        self.assertLessEqual(rects[-1].bottom, 1000)

    def test_grid_empty_returns_empty(self):
        self.assertEqual(calculate_grid_layout([], 1080, 1920), [])


class TestWindowRectHelpers(unittest.TestCase):
    def test_as_dict_keys(self):
        r = WindowRect("com.test.pkg", 100, 200, 400, 800)
        d = r.as_dict()
        self.assertEqual(d["package"], "com.test.pkg")
        self.assertEqual(d["left"], 100)
        self.assertEqual(d["top"], 200)
        self.assertEqual(d["right"], 400)
        self.assertEqual(d["bottom"], 800)

    def test_preview_line_contains_package(self):
        r = WindowRect("com.test.pkg", 10, 20, 300, 600)
        line = r.preview_line(1)
        self.assertIn("com.test.pkg", line)

    def test_preview_line_contains_dimensions(self):
        r = WindowRect("com.pkg", 0, 0, 200, 400)
        line = r.preview_line(1)
        # 200×400
        self.assertIn("200", line)
        self.assertIn("400", line)


if __name__ == "__main__":
    unittest.main()
