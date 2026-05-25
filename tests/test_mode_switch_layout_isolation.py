from __future__ import annotations

import unittest
from unittest import mock

from agent import commands, window_apply, window_layout


def _entry(pkg: str) -> dict:
    return {"package": pkg, "enabled": True}


class TestModeSwitchLayoutIsolation(unittest.TestCase):
    def test_landscape_ignores_stale_portrait_bounds_on_verify(self) -> None:
        cfg = {
            "screen_mode": "landscape",
            "last_layout_mode": "portrait",
            "last_layout_preview": [
                {"package": "pkg1", "left": 0, "top": 512, "right": 360, "bottom": 768},
                {"package": "pkg2", "left": 360, "top": 512, "right": 720, "bottom": 768},
            ],
            "termux_dock_fraction": 0.0,
        }
        entries = [_entry("pkg1"), _entry("pkg2")]
        applied: list[tuple[int, int, int, int]] = []

        def fake_apply(rects, **kwargs):
            applied.extend((r.left, r.top, r.right, r.bottom) for r in rects)
            return []

        with mock.patch.object(window_layout, "detect_display_info", return_value=window_layout.DisplayInfo(1280, 720, 164)), \
             mock.patch.object(window_layout, "_detect_status_bar_height", return_value=25), \
             mock.patch("agent.commands.window_layout.layout_exclusion_reason", return_value=""), \
             mock.patch.object(window_apply, "apply_window_layout", side_effect=fake_apply):
            commands._verify_layout_post_launch(cfg, entries)

        self.assertEqual(applied, [(426, 25, 852, 256), (852, 25, 1280, 256)])

    def test_old_portrait_config_is_forced_to_landscape_on_verify(self) -> None:
        cfg = {
            "screen_mode": "portrait",
            "last_layout_mode": "landscape",
            "last_layout_preview": [
                {"package": "pkg1", "left": 426, "top": 25, "right": 852, "bottom": 256},
            ],
            "termux_dock_fraction": 0.0,
        }
        entries = [_entry("pkg1")]
        applied: list[tuple[int, int, int, int]] = []

        def fake_apply(rects, **kwargs):
            applied.extend((r.left, r.top, r.right, r.bottom) for r in rects)
            return []

        with mock.patch.object(window_layout, "detect_display_info", return_value=window_layout.DisplayInfo(1280, 720, 164)), \
             mock.patch.object(window_layout, "_detect_status_bar_height", return_value=25), \
             mock.patch("agent.commands.window_layout.layout_exclusion_reason", return_value=""), \
             mock.patch.object(window_apply, "apply_window_layout", side_effect=fake_apply):
            commands._verify_layout_post_launch(cfg, entries)

        self.assertEqual(applied, [(426, 25, 852, 256)])

    def test_landscape_slot_order_is_unchanged_after_portrait_changes(self) -> None:
        packages = [f"pkg{i}" for i in range(1, 7)]
        with mock.patch.object(window_layout, "_detect_status_bar_height", return_value=25):
            rects = window_layout.calculate_split_layout(
                packages,
                1280,
                720,
                termux_log_fraction=0.50,
                screen_mode="landscape",
            )

        self.assertEqual(
            [(r.left, r.top, r.right, r.bottom) for r in rects],
            [
                (853, 25, 1066, 195),
                (1066, 25, 1280, 195),
                (853, 256, 1066, 426),
                (1066, 256, 1280, 426),
                (853, 487, 1066, 657),
                (1066, 487, 1280, 657),
            ],
        )


if __name__ == "__main__":
    unittest.main()
