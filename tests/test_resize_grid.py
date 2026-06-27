from __future__ import annotations

import math
import unittest

from agent.resize_grid import (
    calculate_resize_grid,
    columns_for_mode,
    normalize_screen_dimensions,
    validate_grid_bounds,
)


class TestResizeGrid(unittest.TestCase):
    def test_landscape_columns(self):
        self.assertEqual(columns_for_mode("LANDSCAPE", 1), 2)
        self.assertEqual(columns_for_mode("LANDSCAPE", 6), 2)
        self.assertEqual(columns_for_mode("LANDSCAPE", 7), 3)
        self.assertEqual(columns_for_mode("LANDSCAPE", 10), 4)

    def test_portrait_columns(self):
        self.assertEqual(columns_for_mode("PORTRAIT", 1), 1)
        self.assertEqual(columns_for_mode("PORTRAIT", 4), 1)
        self.assertEqual(columns_for_mode("PORTRAIT", 5), 2)
        self.assertEqual(columns_for_mode("PORTRAIT", 11), 3)

    def test_normalize_dimensions(self):
        self.assertEqual(normalize_screen_dimensions(1920, 1080, "LANDSCAPE"), (1920, 1080))
        self.assertEqual(normalize_screen_dimensions(1920, 1080, "PORTRAIT"), (1080, 1920))

    def test_all_bounds_inside_screen_landscape_counts(self):
        major, minor = 1920, 1080
        for n in (1, 6, 7, 9, 10):
            pkgs = [f"com.test.pkg{i}" for i in range(n)]
            rects, layout = calculate_resize_grid(pkgs, mode="LANDSCAPE", major=major, minor=minor)
            self.assertEqual(len(rects), n)
            self.assertEqual(layout["rows"], math.ceil(n / layout["columns"]))
            errors = validate_grid_bounds(rects, layout["screen_width"], layout["screen_height"])
            self.assertEqual(errors, [], msg=f"n={n} errors={errors}")

    def test_all_bounds_inside_screen_portrait_counts(self):
        major, minor = 1920, 1080
        for n in (1, 4, 5, 10, 11):
            pkgs = [f"com.test.pkg{i}" for i in range(n)]
            rects, layout = calculate_resize_grid(pkgs, mode="PORTRAIT", major=major, minor=minor)
            self.assertEqual(len(rects), n)
            errors = validate_grid_bounds(rects, layout["screen_width"], layout["screen_height"])
            self.assertEqual(errors, [], msg=f"n={n} errors={errors}")

    def test_no_negative_or_zero_size_bounds(self):
        pkgs = ["com.test.a", "com.test.b", "com.test.c"]
        rects, layout = calculate_resize_grid(pkgs, mode="LANDSCAPE", major=1920, minor=1080, left_offset=200)
        for r in rects:
            self.assertGreaterEqual(r.left, 0)
            self.assertGreaterEqual(r.top, 0)
            self.assertGreater(r.right, r.left)
            self.assertGreater(r.bottom, r.top)
            self.assertLessEqual(r.right, layout["screen_width"])
            self.assertLessEqual(r.bottom, layout["screen_height"])


if __name__ == "__main__":
    unittest.main()
