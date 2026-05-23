from __future__ import annotations

import unittest

from agent import window_layout


class TestPortraitLayoutBounds(unittest.TestCase):
    def test_portrait_slot_rectangles_are_safe_for_probe_screen(self) -> None:
        packages = [f"com.moons.lite{i}" for i in range(1, 11)]
        rects = window_layout.calculate_split_layout(
            packages, 720, 1280, termux_log_fraction=0.50, screen_mode="portrait",
        )

        self.assertEqual(
            window_layout.validate_portrait_touch_layout(rects, 720, 1280),
            [],
        )
        for rect in rects:
            self.assertGreaterEqual(rect.win_w, int(720 * 0.45))
            self.assertGreaterEqual(rect.win_h, max(int(1280 * 0.16), 180))
            cx, cy = window_layout.rect_center(rect)
            self.assertGreaterEqual(cx, rect.left)
            self.assertLess(cx, rect.right)
            self.assertGreaterEqual(cy, rect.top)
            self.assertLess(cy, rect.bottom)

    def test_termux_reserved_bounds_must_not_overlap_package_slots(self) -> None:
        packages = ["com.moons.litesc", "com.moons.litesd"]
        rects = window_layout.calculate_split_layout(
            packages, 720, 1280, termux_log_fraction=0.50, screen_mode="portrait",
        )

        self.assertEqual(
            window_layout.validate_portrait_touch_layout(
                rects, 720, 1280, termux_bounds=(0, 0, 720, 256),
            ),
            [],
        )
        self.assertTrue(window_layout.validate_portrait_touch_layout(
            rects, 720, 1280, termux_bounds=(0, 512, 720, 768),
        ))


if __name__ == "__main__":
    unittest.main()
