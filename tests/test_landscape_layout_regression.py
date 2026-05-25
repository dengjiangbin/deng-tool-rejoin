from __future__ import annotations

import unittest
from unittest import mock

from agent import window_layout
from agent import commands


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

    def test_start_layout_forces_landscape_when_old_config_says_portrait(self) -> None:
        cfg = {
            "screen_mode": "portrait",
            "roblox_packages": [{"package": "pkg1", "enabled": True}, {"package": "pkg2", "enabled": True}],
        }
        entries = [{"package": "pkg1", "enabled": True}, {"package": "pkg2", "enabled": True}]
        captured: list[str] = []

        class _Display:
            width = 1280
            height = 720
            density = 320

        with mock.patch("agent.commands.window_layout.detect_display_info", return_value=_Display()), \
             mock.patch("agent.commands.window_layout.calculate_split_layout") as calc, \
             mock.patch("agent.commands.window_layout.layout_exclusion_reason", return_value=""), \
             mock.patch("agent.commands.window_layout.detect_layout_orientation", return_value="landscape"), \
             mock.patch("agent.commands.window_layout._detect_status_bar_height", return_value=25), \
             mock.patch("agent.window_apply.apply_window_layout") as apply, \
             mock.patch("agent.commands.save_config", side_effect=lambda data: data), \
             mock.patch("agent.commands.android.detect_root", return_value=mock.Mock(available=False, tool=None)):
            calc.side_effect = lambda packages, width, height, **kwargs: (
                captured.append(kwargs.get("screen_mode")) or [
                    window_layout.WindowRect(pkg, 640 + i * 100, 25, 720 + i * 100, 225)
                    for i, pkg in enumerate(packages)
                ]
            )
            apply.return_value = [
                mock.Mock(desired=window_layout.WindowRect("pkg1", 640, 25, 720, 225), pre_write_ok=True, pre_write_method="mock", status="ok", attempts=[]),
                mock.Mock(desired=window_layout.WindowRect("pkg2", 740, 25, 820, 225), pre_write_ok=True, pre_write_method="mock", status="ok", attempts=[]),
            ]
            out_cfg, _ = commands._prepare_automatic_layout(cfg, entries)

        self.assertEqual(out_cfg["screen_mode"], "landscape")
        self.assertEqual(captured, ["landscape"])


if __name__ == "__main__":
    unittest.main()
