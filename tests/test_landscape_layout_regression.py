from __future__ import annotations

import unittest
from unittest import mock

from agent import window_layout
from agent import commands
from agent.resize_engine import ResizePipelineResult


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

    def test_start_layout_keeps_portrait_when_device_is_portrait(self) -> None:
        cfg = {
            "screen_mode": "auto",
            "roblox_packages": [{"package": "pkg1", "enabled": True}, {"package": "pkg2", "enabled": True}],
        }
        entries = [{"package": "pkg1", "enabled": True}, {"package": "pkg2", "enabled": True}]
        rect = window_layout.WindowRect("pkg1", 0, 25, 720, 1280)

        pipeline = ResizePipelineResult(
            ok=True,
            mode="PORTRAIT",
            confidence="HIGH",
            basis="logical display height exceeds width",
            signals={"wm_size_raw": "1080x1920"},
            layout={"screen_width": 1080, "screen_height": 1920, "columns": 1, "rows": 2},
            rects=[rect],
            summary={"resized": 1, "already_correct": 0, "skipped": 0, "failed": 0},
        )

        with mock.patch("agent.resize_engine.run_resize_pipeline", return_value=pipeline), \
             mock.patch("agent.commands.save_config", side_effect=lambda data: data), \
             mock.patch("agent.commands.android.detect_root", return_value=mock.Mock(available=False, tool=None)):
            out_cfg, _ = commands._prepare_automatic_layout(cfg, entries)

        self.assertEqual(out_cfg["screen_mode"], "portrait")


if __name__ == "__main__":
    unittest.main()
