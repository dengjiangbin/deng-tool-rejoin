from __future__ import annotations

import unittest

from agent.resize_pb99 import (
    calculate_pb99_grid,
    pb99_columns,
    pb99_mode_from_rotation,
    pb99_prefs_paths,
)


class TestPb99Grid(unittest.TestCase):
    def test_portrait_two_apps_one_column_full_width(self) -> None:
        pkgs = ["com.moons.litesc", "com.moons.litesd"]
        rects, layout = calculate_pb99_grid(pkgs, wm_width=720, wm_height=1280, rotation=0)
        self.assertEqual(layout["mode"], "PORTRAIT")
        self.assertEqual(layout["columns"], 1)
        self.assertEqual(layout["rows"], 2)
        self.assertEqual(layout["left_offset"], 0)
        self.assertEqual(len(rects), 2)
        self.assertEqual(rects[0].left, layout["side_margin"])
        self.assertGreater(rects[0].right, rects[0].left)
        self.assertLessEqual(rects[0].bottom, rects[1].top)
        self.assertEqual(rects[0].package, "com.moons.litesc")
        self.assertEqual(rects[1].package, "com.moons.litesd")

    def test_landscape_rotation_uses_wide_grid(self) -> None:
        pkgs = [f"com.test.p{i}" for i in range(4)]
        rects, layout = calculate_pb99_grid(pkgs, wm_width=720, wm_height=1280, rotation=1)
        self.assertEqual(layout["mode"], "LANDSCAPE")
        self.assertEqual(layout["columns"], 2)
        self.assertEqual(len(rects), 4)

    def test_columns_match_pb99_rules(self) -> None:
        self.assertEqual(pb99_columns("PORTRAIT", 4), 1)
        self.assertEqual(pb99_columns("PORTRAIT", 5), 2)
        self.assertEqual(pb99_columns("LANDSCAPE", 6), 2)
        self.assertEqual(pb99_columns("LANDSCAPE", 7), 3)

    def test_mode_from_rotation(self) -> None:
        self.assertEqual(pb99_mode_from_rotation(0), "PORTRAIT")
        self.assertEqual(pb99_mode_from_rotation(1), "LANDSCAPE")
        self.assertEqual(pb99_mode_from_rotation(3), "LANDSCAPE")

    def test_prefs_paths(self) -> None:
        paths = pb99_prefs_paths("com.moons.litesc")
        self.assertTrue(any(p.endswith("com.moons.litesc_preferences.xml") for p in paths))


if __name__ == "__main__":
    unittest.main()
