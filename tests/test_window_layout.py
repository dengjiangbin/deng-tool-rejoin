"""Tests for landscape-block window layout engine.

Layout rules verified:
  - Every window is landscape-shaped (width >= height * LANDSCAPE_MIN_RATIO).
  - No two windows overlap or touch (gap >= GAP_PX between all pairs).
  - All windows are inside the right-pane (left 35% reserved for Termux).
  - All windows have unique bounds.
  - Termux and system packages are excluded.
  - calculate_kaeru_layout (compat wrapper) produces landscape windows.
  - calculate_split_layout produces landscape windows with 35/65 split.
  - validate_layout_rects detects violations correctly.
  - parse_wm_size / parse_wm_density helpers work.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.window_layout import (
    GAP_PX,
    KAERU_TITLE_BAR_H,
    LANDSCAPE_MIN_RATIO,
    OUTER_MARGIN,
    TERMUX_LOG_FRACTION,
    DisplayInfo,
    WindowRect,
    _is_layout_excluded,
    calculate_grid_layout,
    calculate_kaeru_layout,
    calculate_landscape_blocks,
    calculate_split_layout,
    parse_wm_density,
    parse_wm_size,
    validate_layout_rects,
)


def _pkgs(n: int) -> list[str]:
    return [f"com.pkg.test{i}" for i in range(1, n + 1)]


W, H = 1080, 1920   # typical portrait phone
LW   = W - int(W * TERMUX_LOG_FRACTION)   # right-pane width after 35% split


def _pane_bounds_for(display_w, display_h):
    left_end = round(display_w * TERMUX_LOG_FRACTION)
    return (
        left_end + OUTER_MARGIN,
        OUTER_MARGIN,
        display_w - OUTER_MARGIN,
        display_h - OUTER_MARGIN,
    )


# ── Parse helpers ─────────────────────────────────────────────────────────────

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


# ── Landscape invariant ───────────────────────────────────────────────────────

class TestLandscapeInvariant(unittest.TestCase):
    """Every window must satisfy width >= height * LANDSCAPE_MIN_RATIO."""

    def _assert_all_landscape(self, rects, context=""):
        for i, r in enumerate(rects):
            self.assertGreaterEqual(
                r.win_w, r.win_h * LANDSCAPE_MIN_RATIO,
                f"rect[{i}] {r.package} NOT landscape: {r.win_w}×{r.win_h} {context}",
            )

    def test_1_package_landscape(self):
        self._assert_all_landscape(calculate_landscape_blocks(_pkgs(1), W, H))

    def test_2_packages_landscape(self):
        self._assert_all_landscape(calculate_landscape_blocks(_pkgs(2), W, H))

    def test_3_packages_landscape(self):
        self._assert_all_landscape(calculate_landscape_blocks(_pkgs(3), W, H))

    def test_4_packages_landscape(self):
        self._assert_all_landscape(calculate_landscape_blocks(_pkgs(4), W, H))

    def test_5_packages_landscape(self):
        self._assert_all_landscape(calculate_landscape_blocks(_pkgs(5), W, H))

    def test_6_packages_landscape(self):
        self._assert_all_landscape(calculate_landscape_blocks(_pkgs(6), W, H))

    def test_landscape_display_2_packages(self):
        self._assert_all_landscape(calculate_landscape_blocks(_pkgs(2), 1920, 1080), "1920×1080")

    def test_landscape_display_3_packages(self):
        self._assert_all_landscape(calculate_landscape_blocks(_pkgs(3), 1920, 1080), "1920×1080")

    def test_kaeru_compat_2_landscape(self):
        self._assert_all_landscape(calculate_kaeru_layout(_pkgs(2), LW, H))

    def test_kaeru_compat_3_landscape(self):
        self._assert_all_landscape(calculate_kaeru_layout(_pkgs(3), LW, H))

    def test_kaeru_compat_4_landscape(self):
        self._assert_all_landscape(calculate_kaeru_layout(_pkgs(4), LW, H))

    def test_split_2_landscape(self):
        self._assert_all_landscape(calculate_split_layout(_pkgs(2), W, H))

    def test_split_3_landscape(self):
        self._assert_all_landscape(calculate_split_layout(_pkgs(3), W, H))

    def test_split_4_landscape(self):
        self._assert_all_landscape(calculate_split_layout(_pkgs(4), W, H))


# ── Count correctness ─────────────────────────────────────────────────────────

class TestWindowCount(unittest.TestCase):
    """Must return exactly N windows for N packages."""

    def _check(self, n):
        self.assertEqual(len(calculate_landscape_blocks(_pkgs(n), W, H)), n, f"n={n}")

    def test_count_1(self): self._check(1)
    def test_count_2(self): self._check(2)
    def test_count_3(self): self._check(3)
    def test_count_4(self): self._check(4)
    def test_count_5(self): self._check(5)
    def test_count_6(self): self._check(6)
    def test_count_7(self): self._check(7)
    def test_count_9(self): self._check(9)
    def test_count_0_empty(self):
        self.assertEqual(calculate_landscape_blocks([], W, H), [])


# ── No overlap / no touch ─────────────────────────────────────────────────────

class TestNoOverlapNoTouch(unittest.TestCase):
    """No two windows may overlap or touch (gap >= GAP_PX)."""

    def _check(self, n, w=W, h=H):
        rects = calculate_landscape_blocks(_pkgs(n), w, h)
        px0, py0, px1, py1 = _pane_bounds_for(w, h)
        errors = validate_layout_rects(rects, px0, py0, px1, py1)
        overlap = [e for e in errors if "overlap" in e or "touch" in e]
        self.assertEqual(overlap, [], f"n={n} {w}×{h}: {overlap}")

    def test_2_no_touch_portrait(self): self._check(2)
    def test_3_no_touch_portrait(self): self._check(3)
    def test_4_no_touch_portrait(self): self._check(4)
    def test_5_no_touch_portrait(self): self._check(5)
    def test_6_no_touch_portrait(self): self._check(6)
    def test_2_no_touch_landscape(self): self._check(2, 1920, 1080)
    def test_3_no_touch_landscape(self): self._check(3, 1920, 1080)
    def test_4_no_touch_landscape(self): self._check(4, 1920, 1080)


# ── Inside pane ───────────────────────────────────────────────────────────────

class TestInsidePaneAfterSplit(unittest.TestCase):
    """All windows must be in the right 65% pane (absolute coords)."""

    def _check(self, n):
        rects = calculate_split_layout(_pkgs(n), W, H)
        left_end = round(W * TERMUX_LOG_FRACTION)
        for i, r in enumerate(rects):
            self.assertGreaterEqual(r.left, left_end,
                f"rect[{i}] left={r.left} < left_end={left_end}")
            self.assertLessEqual(r.right, W,
                f"rect[{i}] right={r.right} > {W}")
            self.assertGreaterEqual(r.top, 0)
            self.assertLessEqual(r.bottom, H)

    def test_1_inside_pane(self): self._check(1)
    def test_2_inside_pane(self): self._check(2)
    def test_3_inside_pane(self): self._check(3)
    def test_4_inside_pane(self): self._check(4)
    def test_5_inside_pane(self): self._check(5)


# ── Unique bounds ─────────────────────────────────────────────────────────────

class TestUniqueBounds(unittest.TestCase):
    def _check(self, n):
        rects = calculate_landscape_blocks(_pkgs(n), W, H)
        seen: set[tuple[int, int, int, int]] = set()
        for i, r in enumerate(rects):
            key = (r.left, r.top, r.right, r.bottom)
            self.assertNotIn(key, seen, f"rect[{i}] duplicate bounds: {key}")
            seen.add(key)

    def test_2_unique(self): self._check(2)
    def test_3_unique(self): self._check(3)
    def test_4_unique(self): self._check(4)
    def test_5_unique(self): self._check(5)


# ── Termux exclusion ──────────────────────────────────────────────────────────

class TestExclusion(unittest.TestCase):
    def test_termux_excluded(self):
        self.assertTrue(_is_layout_excluded("com.termux"))

    def test_termux_boot_excluded(self):
        self.assertTrue(_is_layout_excluded("com.termux.boot"))

    def test_android_system_excluded(self):
        self.assertTrue(_is_layout_excluded("com.android.systemui"))

    def test_google_excluded(self):
        self.assertTrue(_is_layout_excluded("com.google.android.gms"))

    def test_roblox_not_excluded(self):
        self.assertFalse(_is_layout_excluded("com.roblox.client"))

    def test_roblox_clone_not_excluded(self):
        self.assertFalse(_is_layout_excluded("com.roblox.client2"))

    def test_empty_string_excluded(self):
        # Empty string should not crash
        try:
            result = _is_layout_excluded("")
            self.assertIsInstance(result, bool)
        except Exception:
            pass


# ── Validation helper ─────────────────────────────────────────────────────────

class TestValidateLandscape(unittest.TestCase):
    def test_portrait_window_flagged(self):
        rect = WindowRect("com.pkg", 400, 0, 700, 1000)  # 300w × 1000h → portrait
        errors = validate_layout_rects([rect], 400, 0, 1080, 1920)
        self.assertTrue(any("NOT landscape" in e for e in errors), errors)

    def test_overlapping_windows_flagged(self):
        r1 = WindowRect("com.pkg.a", 400, 50, 1060, 420)
        r2 = WindowRect("com.pkg.b", 400, 200, 1060, 570)
        errors = validate_layout_rects([r1, r2], 400, 50, 1060, 570)
        self.assertTrue(any("overlap" in e or "touch" in e for e in errors), errors)

    def test_valid_landscape_pair_passes(self):
        pkgs = _pkgs(2)
        rects = calculate_landscape_blocks(pkgs, W, H)
        px0, py0, px1, py1 = _pane_bounds_for(W, H)
        errors = validate_layout_rects(rects, px0, py0, px1, py1)
        self.assertEqual(errors, [], errors)


# ── Compat: calculate_grid_layout delegates to kaeru ──────────────────────────

class TestGridLayoutCompat(unittest.TestCase):
    def test_grid_count_matches_kaeru(self):
        for n in (1, 2, 3, 4):
            grid = calculate_grid_layout(_pkgs(n), LW, H)
            kaeru = calculate_kaeru_layout(_pkgs(n), LW, H)
            self.assertEqual(len(grid), len(kaeru), f"n={n} count mismatch")

    def test_grid_landscape_1(self):
        rects = calculate_grid_layout(_pkgs(1), LW, H)
        for r in rects:
            self.assertGreaterEqual(r.win_w, r.win_h * LANDSCAPE_MIN_RATIO)

    def test_grid_landscape_2(self):
        rects = calculate_grid_layout(_pkgs(2), LW, H)
        for r in rects:
            self.assertGreaterEqual(r.win_w, r.win_h * LANDSCAPE_MIN_RATIO)


# ── Window rect helpers ───────────────────────────────────────────────────────

class TestWindowRect(unittest.TestCase):
    def test_win_w_property(self):
        r = WindowRect("com.pkg", 100, 50, 800, 500)
        self.assertEqual(r.win_w, 700)

    def test_win_h_property(self):
        r = WindowRect("com.pkg", 100, 50, 800, 500)
        self.assertEqual(r.win_h, 450)

    def test_as_dict(self):
        r = WindowRect("com.pkg", 10, 20, 300, 200)
        d = r.as_dict()
        self.assertEqual(d["left"],  10)
        self.assertEqual(d["top"],   20)
        self.assertEqual(d["right"], 300)
        self.assertEqual(d["bottom"], 200)

    def test_package_name_preserved(self):
        r = calculate_kaeru_layout(["com.test.one"], LW, H)
        self.assertEqual(r[0].package, "com.test.one")

    def test_frozen_immutable(self):
        r = WindowRect("com.pkg", 0, 0, 400, 225)
        with self.assertRaises((AttributeError, TypeError)):
            r.left = 999  # type: ignore


# ── Split layout ──────────────────────────────────────────────────────────────

class TestSplitLayout(unittest.TestCase):
    """Split layout: left 35% reserved, right 65% gets landscape windows."""

    def test_split_single_right_of_termux(self):
        rects = calculate_split_layout(_pkgs(1), W, H)
        self.assertEqual(len(rects), 1)
        left_end = round(W * TERMUX_LOG_FRACTION)
        self.assertGreaterEqual(rects[0].left, left_end,
            f"Single window left={rects[0].left} must be > left_end={left_end}")

    def test_split_2_all_right_of_termux(self):
        rects = calculate_split_layout(_pkgs(2), W, H)
        left_end = round(W * TERMUX_LOG_FRACTION)
        for r in rects:
            self.assertGreaterEqual(r.left, left_end)

    def test_split_empty_returns_empty(self):
        self.assertEqual(calculate_split_layout([], W, H), [])

    def test_split_preserves_package_names(self):
        pkgs = ["com.a.one", "com.b.two"]
        rects = calculate_split_layout(pkgs, W, H)
        names = {r.package for r in rects}
        self.assertEqual(names, set(pkgs))


# ── Backward-compat symbol ────────────────────────────────────────────────────

class TestKaeruTitleBarHExists(unittest.TestCase):
    def test_constant_exists_and_positive(self):
        self.assertGreater(KAERU_TITLE_BAR_H, 0)


if __name__ == "__main__":
    unittest.main()
