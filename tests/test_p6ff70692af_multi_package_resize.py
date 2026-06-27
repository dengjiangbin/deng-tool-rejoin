"""Regression for probe p-6ff70692af — multi-package portrait grid + pb99 write."""

from __future__ import annotations

import unittest
from unittest import mock

from agent import commands, window_apply, window_layout
from agent.resize_engine import compute_layout_rects, rects_cover_packages
from agent.resize_pb99 import write_pb99_bounds_root
from agent.window_layout import WindowRect


def _entry(pkg: str) -> dict:
    return {"package": pkg, "enabled": True}


class TestPb99ForceStopRootInfo(unittest.TestCase):
    def test_write_pb99_uses_rootinfo_for_force_stop(self) -> None:
        rect = WindowRect("com.moons.litesc", 1, 51, 360, 460)
        root_info = mock.Mock(available=True, tool="su")
        calls: list[tuple] = []

        def fake_run_root_command(args, **kwargs):
            result = mock.Mock(ok=True, stdout='Y\n', stderr='')
            if args == ["sh", "-c"] or (args and args[0] == "sh"):
                cmd = args[-1] if len(args) > 1 else ""
                if "test -f" in cmd:
                    result.stdout = "Y"
                elif "cat '" in cmd:
                    result.stdout = (
                        '<?xml version="1.0" encoding="utf-8" standalone="yes" ?>'
                        '<map><int name="left" value="0"/></map>'
                    )
            return result

        with mock.patch("agent.resize_pb99.android.run_root_command", side_effect=fake_run_root_command), \
             mock.patch("agent.resize_pb99.android.detect_root", return_value=root_info), \
             mock.patch("agent.resize_pb99.android.force_stop_package", side_effect=lambda pkg, info=None: calls.append((pkg, info))) as stop:
            ok, msg = write_pb99_bounds_root("com.moons.litesc", rect, "su")

        self.assertTrue(ok)
        self.assertIn("pb99 root write", msg)
        stop.assert_called_once()
        self.assertEqual(calls[0][0], "com.moons.litesc")
        self.assertIs(calls[0][1], root_info)


class TestStalePartialLayoutRecompute(unittest.TestCase):
    def test_verify_recomputes_when_stored_layout_missing_packages(self) -> None:
        cfg = {
            "screen_mode": "portrait",
            "last_layout_mode": "portrait",
            "last_layout_preview": [
                {"package": "com.moons.litesc", "left": 0, "top": 25, "right": 720, "bottom": 652},
                {"package": "com.moons.litesd", "left": 0, "top": 652, "right": 720, "bottom": 1280},
            ],
        }
        entries = [_entry(f"com.moons.lites{c}") for c in "cdefgh"]
        applied_pkgs: list[str] = []

        six_rects = [
            WindowRect(f"com.moons.lites{c}", i * 10, 25, i * 10 + 100, 200)
            for i, c in enumerate("cdefgh")
        ]

        def fake_apply(rects, **kwargs):
            applied_pkgs.extend(r.package for r in rects)
            return []

        with mock.patch("agent.commands.save_config", side_effect=lambda c: c), \
             mock.patch("agent.resize_engine.compute_layout_rects", return_value=(six_rects, {"screen_width": 720, "screen_height": 1280}, "portrait")), \
             mock.patch.object(window_apply, "apply_window_layout", side_effect=fake_apply):
            commands._verify_layout_post_launch(cfg, entries)

        self.assertEqual(len(applied_pkgs), 6)
        self.assertEqual(set(applied_pkgs), {e["package"] for e in entries})
        self.assertEqual(len(cfg.get("_layout_rects") or []), 6)


class TestComputeLayoutRectsSixPackages(unittest.TestCase):
    def test_portrait_pb99_grid_has_slot_for_each_package(self) -> None:
        entries = [_entry(f"com.moons.lites{c}") for c in "cdefgh"]
        cfg = {"screen_mode": "portrait"}
        mode_info = {
            "signals": {
                "physical_size_normalized": {"major": 1280, "minor": 720},
                "logical_size": "720x1280",
                "rotation": "0",
            }
        }
        with mock.patch("agent.resize_engine.resolve_runtime_screen_mode", return_value=("portrait", mode_info)), \
             mock.patch("agent.resize_engine.android.get_wm_size", return_value={"width": 720, "height": 1280}), \
             mock.patch("agent.resize_engine.read_display_rotation", return_value=0):
            rects, layout, mode = compute_layout_rects(cfg, entries)

        self.assertEqual(mode, "portrait")
        self.assertEqual(len(rects), 6)
        self.assertEqual(layout["columns"], 2)
        self.assertEqual(layout["rows"], 3)
        self.assertTrue(
            rects_cover_packages(rects, {e["package"] for e in entries})
        )


if __name__ == "__main__":
    unittest.main()
