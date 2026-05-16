"""Tests for the landscape-block window layout engine.

Covers (per user requirements):
  1.  1-package rectangle is landscape.
  2.  2-package rectangles are landscape.
  3.  3-package rectangles are landscape.
  4.  4-package rectangles are landscape.
  5.  5+ package rectangles are landscape.
  6.  No rectangles overlap.
  7.  No rectangles touch (gap >= GAP_PX).
  8.  Every rectangle is inside the right 65% pane.
  9.  Each rectangle has unique bounds.
  10. Layout works when display dimensions are swapped (portrait ↔ landscape).
  11. Layout works on 1080×1920 (portrait phone) and 1920×1080 (landscape).
  12. Layout validates and rejects bad rectangles.
  13. Termux/system packages are excluded.
  14. Kaeru-layout compat wrapper returns landscape windows.
  15. calculate_split_layout returns landscape windows.
  16. 2-package windows do NOT touch each other.
  17. 3-package windows do NOT touch each other.
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
    LANDSCAPE_MIN_RATIO,
    OUTER_MARGIN,
    TERMUX_LOG_FRACTION,
    WindowRect,
    calculate_kaeru_layout,
    calculate_landscape_blocks,
    calculate_split_layout,
    validate_layout_rects,
    _is_layout_excluded,
)


def _pkgs(n: int) -> list[str]:
    return [f"com.roblox.clone{i}" for i in range(1, n + 1)]


# Common display configurations to test.
_PORTRAIT  = (1080, 1920)  # typical cloud phone (portrait)
_LANDSCAPE = (1920, 1080)  # cloud phone landscape or tablet


def _pane_bounds(display_w: int, display_h: int):
    """Return (px0, py0, px1, py1) for the right pane."""
    left_end = round(display_w * TERMUX_LOG_FRACTION)
    return (
        left_end + OUTER_MARGIN,
        OUTER_MARGIN,
        display_w - OUTER_MARGIN,
        display_h - OUTER_MARGIN,
    )


class TestLandscapeShape(unittest.TestCase):
    """Every window must be landscape: width >= height * LANDSCAPE_MIN_RATIO."""

    def _check_landscape(self, n: int, w: int, h: int, msg: str = "") -> None:
        pkgs = _pkgs(n)
        rects = calculate_landscape_blocks(pkgs, w, h)
        self.assertEqual(len(rects), n, f"wrong count for n={n} {msg}")
        for i, r in enumerate(rects):
            self.assertGreaterEqual(
                r.win_w, r.win_h * LANDSCAPE_MIN_RATIO,
                f"rect[{i}] is NOT landscape: {r.win_w}×{r.win_h} on {w}×{h} {msg}",
            )

    def test_1_package_landscape_portrait_display(self):
        self._check_landscape(1, *_PORTRAIT, "portrait")

    def test_2_packages_landscape_portrait_display(self):
        self._check_landscape(2, *_PORTRAIT, "portrait")

    def test_3_packages_landscape_portrait_display(self):
        self._check_landscape(3, *_PORTRAIT, "portrait")

    def test_4_packages_landscape_portrait_display(self):
        self._check_landscape(4, *_PORTRAIT, "portrait")

    def test_5_packages_landscape_portrait_display(self):
        self._check_landscape(5, *_PORTRAIT, "portrait")

    def test_6_packages_landscape_portrait_display(self):
        self._check_landscape(6, *_PORTRAIT, "portrait")

    def test_1_package_landscape_landscape_display(self):
        self._check_landscape(1, *_LANDSCAPE, "landscape")

    def test_2_packages_landscape_landscape_display(self):
        self._check_landscape(2, *_LANDSCAPE, "landscape")

    def test_3_packages_landscape_landscape_display(self):
        self._check_landscape(3, *_LANDSCAPE, "landscape")

    def test_4_packages_landscape_landscape_display(self):
        self._check_landscape(4, *_LANDSCAPE, "landscape")

    def test_5_packages_landscape_landscape_display(self):
        self._check_landscape(5, *_LANDSCAPE, "landscape")


class TestNoOverlapNoTouch(unittest.TestCase):
    """No two windows may overlap or touch (gap between them >= GAP_PX)."""

    def _check_no_overlap_touch(self, n: int, w: int, h: int, msg: str = "") -> None:
        pkgs = _pkgs(n)
        rects = calculate_landscape_blocks(pkgs, w, h)
        px0, py0, px1, py1 = _pane_bounds(w, h)
        errors = validate_layout_rects(rects, px0, py0, px1, py1)
        overlap_errors = [e for e in errors if "overlap" in e or "touch" in e]
        self.assertEqual(
            overlap_errors, [],
            f"Overlap/touch detected for n={n} {w}×{h} {msg}: {overlap_errors}",
        )

    def test_2_packages_no_touch_portrait(self):
        self._check_no_overlap_touch(2, *_PORTRAIT)

    def test_3_packages_no_touch_portrait(self):
        self._check_no_overlap_touch(3, *_PORTRAIT)

    def test_4_packages_no_touch_portrait(self):
        self._check_no_overlap_touch(4, *_PORTRAIT)

    def test_2_packages_no_touch_landscape(self):
        self._check_no_overlap_touch(2, *_LANDSCAPE)

    def test_3_packages_no_touch_landscape(self):
        self._check_no_overlap_touch(3, *_LANDSCAPE)

    def test_4_packages_no_touch_landscape(self):
        self._check_no_overlap_touch(4, *_LANDSCAPE)

    def test_5_packages_no_touch_portrait(self):
        self._check_no_overlap_touch(5, *_PORTRAIT)

    def test_6_packages_no_touch_portrait(self):
        self._check_no_overlap_touch(6, *_PORTRAIT)


class TestInsidePane(unittest.TestCase):
    """Every rectangle must be inside the right 65% pane."""

    def _check_inside_pane(self, n: int, w: int, h: int) -> None:
        pkgs = _pkgs(n)
        rects = calculate_split_layout(pkgs, w, h)
        left_end = round(w * TERMUX_LOG_FRACTION)
        for i, r in enumerate(rects):
            self.assertGreaterEqual(r.left, left_end,
                f"rect[{i}] left={r.left} is in Termux pane (left_end={left_end})")
            self.assertGreaterEqual(r.top, 0, f"rect[{i}] top={r.top} < 0")
            self.assertLessEqual(r.right, w, f"rect[{i}] right={r.right} > {w}")
            self.assertLessEqual(r.bottom, h, f"rect[{i}] bottom={r.bottom} > {h}")

    def test_1_package_inside_pane_portrait(self):
        self._check_inside_pane(1, *_PORTRAIT)

    def test_2_packages_inside_pane_portrait(self):
        self._check_inside_pane(2, *_PORTRAIT)

    def test_3_packages_inside_pane_portrait(self):
        self._check_inside_pane(3, *_PORTRAIT)

    def test_4_packages_inside_pane_portrait(self):
        self._check_inside_pane(4, *_PORTRAIT)

    def test_2_packages_inside_pane_landscape(self):
        self._check_inside_pane(2, *_LANDSCAPE)

    def test_3_packages_inside_pane_landscape(self):
        self._check_inside_pane(3, *_LANDSCAPE)


class TestUniqueBounds(unittest.TestCase):
    """Each window must have unique (left, top, right, bottom) bounds."""

    def _check_unique(self, n: int, w: int, h: int) -> None:
        pkgs = _pkgs(n)
        rects = calculate_landscape_blocks(pkgs, w, h)
        seen: set[tuple[int, int, int, int]] = set()
        for i, r in enumerate(rects):
            key = (r.left, r.top, r.right, r.bottom)
            self.assertNotIn(key, seen, f"rect[{i}] {r.package} has duplicate bounds {key}")
            seen.add(key)

    def test_2_unique_bounds(self):
        self._check_unique(2, *_PORTRAIT)

    def test_3_unique_bounds(self):
        self._check_unique(3, *_PORTRAIT)

    def test_4_unique_bounds(self):
        self._check_unique(4, *_PORTRAIT)

    def test_5_unique_bounds(self):
        self._check_unique(5, *_PORTRAIT)


class TestSwappedDimensions(unittest.TestCase):
    """Layout must work whether display_w > display_h or display_w < display_h."""

    def test_portrait_and_swapped_landscape_both_landscape_windows(self):
        pkgs = _pkgs(2)
        r_portrait   = calculate_landscape_blocks(pkgs, 1080, 1920)
        r_landscape  = calculate_landscape_blocks(pkgs, 1920, 1080)
        for i, r in enumerate(r_portrait):
            self.assertGreaterEqual(r.win_w, r.win_h * LANDSCAPE_MIN_RATIO,
                f"portrait: rect[{i}] not landscape: {r.win_w}×{r.win_h}")
        for i, r in enumerate(r_landscape):
            self.assertGreaterEqual(r.win_w, r.win_h * LANDSCAPE_MIN_RATIO,
                f"landscape: rect[{i}] not landscape: {r.win_w}×{r.win_h}")

    def test_swapped_dimensions_give_different_results(self):
        """Different display orientations should give different block placements."""
        pkgs = _pkgs(2)
        r1 = calculate_landscape_blocks(pkgs, 1080, 1920)
        r2 = calculate_landscape_blocks(pkgs, 1920, 1080)
        # The windows themselves may differ (different pane size)
        # At minimum the pane width should differ
        self.assertNotEqual(r1[0].right - r1[0].left, r2[0].right - r2[0].left)


class TestTermuxExcluded(unittest.TestCase):
    """Termux and system packages must be excluded from layout."""

    def test_termux_excluded(self):
        self.assertTrue(_is_layout_excluded("com.termux"))

    def test_termux_boot_excluded(self):
        self.assertTrue(_is_layout_excluded("com.termux.boot"))

    def test_android_systemui_excluded(self):
        self.assertTrue(_is_layout_excluded("com.android.systemui"))

    def test_roblox_not_excluded(self):
        self.assertFalse(_is_layout_excluded("com.roblox.client"))

    def test_roblox_clone_not_excluded(self):
        self.assertFalse(_is_layout_excluded("com.roblox.client2"))


class TestValidationRejectsBadRects(unittest.TestCase):
    """validate_layout_rects must detect violations."""

    def test_portrait_window_rejected(self):
        """A window taller than wide must fail landscape check."""
        rect = WindowRect("com.roblox.client", 400, 0, 700, 1000)  # 300×1000 → portrait
        errors = validate_layout_rects([rect], 400, 0, 1080, 1920)
        self.assertTrue(any("NOT landscape" in e for e in errors), errors)

    def test_overlapping_windows_rejected(self):
        r1 = WindowRect("com.pkg.a", 400, 50, 1060, 420)   # 660×370
        r2 = WindowRect("com.pkg.b", 400, 200, 1060, 570)  # 660×370 — overlaps r1
        errors = validate_layout_rects([r1, r2], 400, 50, 1060, 570)
        self.assertTrue(any("overlap" in e or "touch" in e for e in errors), errors)

    def test_touching_windows_rejected(self):
        """Windows where right of r1 + GAP_PX > left of r2 must fail."""
        r1 = WindowRect("com.pkg.a", 400,  50, 1060, 420)
        r2 = WindowRect("com.pkg.b", 400, 421, 1060, 791)  # gap = 1 < GAP_PX
        errors = validate_layout_rects([r1, r2], 400, 50, 1060, 800)
        self.assertTrue(any("overlap" in e or "touch" in e for e in errors), errors)

    def test_valid_landscape_layout_passes(self):
        """Two properly spaced landscape windows must pass validation."""
        pkgs = _pkgs(2)
        rects = calculate_landscape_blocks(pkgs, *_PORTRAIT)
        px0, py0, px1, py1 = _pane_bounds(*_PORTRAIT)
        errors = validate_layout_rects(rects, px0, py0, px1, py1)
        self.assertEqual(errors, [], errors)


class TestKaeruCompatWrapper(unittest.TestCase):
    """calculate_kaeru_layout must produce landscape windows for all n."""

    def _landscape_for_n(self, n: int) -> None:
        pkgs = _pkgs(n)
        W, H = 700, 1920  # right-pane width, full height
        rects = calculate_kaeru_layout(pkgs, W, H)
        self.assertEqual(len(rects), n)
        for i, r in enumerate(rects):
            self.assertGreaterEqual(
                r.win_w, r.win_h * LANDSCAPE_MIN_RATIO,
                f"kaeru_layout n={n} rect[{i}] not landscape: {r.win_w}×{r.win_h}",
            )

    def test_1_landscape(self): self._landscape_for_n(1)
    def test_2_landscape(self): self._landscape_for_n(2)
    def test_3_landscape(self): self._landscape_for_n(3)
    def test_4_landscape(self): self._landscape_for_n(4)
    def test_5_landscape(self): self._landscape_for_n(5)


class TestSplitLayoutLandscape(unittest.TestCase):
    """calculate_split_layout must produce landscape windows in the right pane."""

    def _split_landscape(self, n: int, w: int, h: int) -> None:
        pkgs = _pkgs(n)
        rects = calculate_split_layout(pkgs, w, h)
        self.assertEqual(len(rects), n)
        for i, r in enumerate(rects):
            self.assertGreaterEqual(
                r.win_w, r.win_h * LANDSCAPE_MIN_RATIO,
                f"split_layout n={n} rect[{i}] not landscape: {r.win_w}×{r.win_h} on {w}×{h}",
            )

    def test_2_split_portrait(self): self._split_landscape(2, *_PORTRAIT)
    def test_3_split_portrait(self): self._split_landscape(3, *_PORTRAIT)
    def test_4_split_portrait(self): self._split_landscape(4, *_PORTRAIT)
    def test_2_split_landscape(self): self._split_landscape(2, *_LANDSCAPE)
    def test_3_split_landscape(self): self._split_landscape(3, *_LANDSCAPE)


if __name__ == "__main__":
    unittest.main()
