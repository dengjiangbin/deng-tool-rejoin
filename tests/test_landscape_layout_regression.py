from __future__ import annotations

import unittest
from unittest import mock

from agent import window_layout


class TestLandscapeLayoutRegression(unittest.TestCase):
    def test_required_six_package_landscape_order_is_unchanged(self) -> None:
        packages = [f"pkg{i}" for i in range(1, 7)]
        with mock.patch("agent.window_layout._detect_status_bar_height", return_value=25):
            rects = window_layout.calculate_split_layout(
                packages, 1280, 720,
                termux_log_fraction=0.50,
                screen_mode="landscape",
            )

        grid = [[0, 1, 2], [0, 3, 4], [0, 5, 6]]
        for index, rect in enumerate(rects, start=1):
            row = rect.top // ((720 - 25) // 3)
            col = 1 if rect.left == 853 else 2
            self.assertEqual(grid[row][col], index)

    def test_required_nine_package_landscape_order_is_unchanged(self) -> None:
        packages = [f"pkg{i}" for i in range(1, 10)]
        with mock.patch("agent.window_layout._detect_status_bar_height", return_value=25):
            rects = window_layout.calculate_split_layout(
                packages, 1280, 720,
                termux_log_fraction=0.50,
                screen_mode="landscape",
            )

        expected_positions = [
            (640, 25), (853, 25), (1066, 25),
            (640, 256), (853, 256), (1066, 256),
            (640, 487), (853, 487), (1066, 487),
        ]
        self.assertEqual([(r.left, r.top) for r in rects], expected_positions)


if __name__ == "__main__":
    unittest.main()
