from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent import resize_engine, resize_packages, resize_trace
from agent.window_layout import WindowRect


class TestResizeIntegration(unittest.TestCase):
    def test_trusted_packages_from_config_only(self):
        cfg = {
            "roblox_packages": [
                {"package": "com.moons.litesc", "enabled": True},
                {"package": "com.termux", "enabled": True},
                {"package": "com.moons.litesd", "enabled": True},
            ],
        }
        trusted, skipped = resize_packages.get_trusted_resize_packages(cfg, None)
        self.assertEqual(trusted, ["com.moons.litesc", "com.moons.litesd"])
        self.assertTrue(any(s["package"] == "com.termux" for s in skipped))

    def test_no_trusted_packages_skips_honestly(self):
        cfg = {"roblox_packages": [{"package": "com.termux", "enabled": True}]}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(resize_trace, "DATA_DIR", Path(tmp)), \
                 mock.patch.object(resize_trace, "TRACE_PATH", Path(tmp) / "resize-debug.jsonl"):
                result = resize_engine.run_resize_pipeline(cfg, cfg["roblox_packages"], trigger="manual", force=True)
        self.assertTrue(result.skipped)
        self.assertEqual(result.skipped_reason, "no trusted packages")

    def test_pipeline_uses_mode_detection_and_writes_trace(self):
        cfg = {
            "roblox_packages": [{"package": "com.moons.litesc", "enabled": True}],
        }
        entries = cfg["roblox_packages"]
        rect = WindowRect("com.moons.litesc", 100, 50, 900, 500)
        fake_mode = {
            "mode": "LANDSCAPE",
            "confidence": "HIGH",
            "basis": "logical or home window width exceeds height",
            "signals": {
                "wm_size_raw": "1080x1920",
                "physical_size_normalized": {"major": 1920, "minor": 1080},
                "home_landscape_wm_portrait_conflict": True,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "resize-debug.jsonl"
            with mock.patch.object(resize_trace, "DATA_DIR", Path(tmp)), \
                 mock.patch.object(resize_trace, "TRACE_PATH", trace_path), \
                 mock.patch("agent.resize_engine.resolve_runtime_screen_mode", return_value=("landscape", fake_mode)), \
                 mock.patch("agent.resize_engine.calculate_resize_grid", return_value=([rect], {
                     "screen_width": 1920, "screen_height": 1080, "columns": 2, "rows": 1,
                     "left_offset": 0, "top_margin": 25, "side_margin": 0, "bottom_margin": 0,
                     "usable_width": 1920, "usable_height": 1055, "empty_slots": 1,
                 })), \
                 mock.patch("agent.resize_engine.validate_grid_bounds", return_value=[]), \
                 mock.patch("agent.resize_engine.safe_write_resize_bounds", return_value={
                     "package": "com.moons.litesc",
                     "status": "resized",
                     "reason": "ok",
                     "backup_created": True,
                     "owner_restored": True,
                     "permission_restored": True,
                     "force_stop_ok": True,
                     "bounds_valid": True,
                     "before_bounds": {"left": 0, "top": 0, "right": 100, "bottom": 100},
                     "after_bounds": {"left": 100, "top": 50, "right": 900, "bottom": 500},
                 }):
                result = resize_engine.run_resize_pipeline(cfg, entries, trigger="startup", force=True)
                latest = resize_trace.read_latest_resize_event()
        self.assertEqual(result.mode, "LANDSCAPE")
        self.assertEqual(result.summary.get("resized"), 1)
        self.assertEqual(latest.get("mode"), "LANDSCAPE")
        self.assertTrue(latest.get("signals", {}).get("home_landscape_wm_portrait_conflict"))

    def test_probe_resize_debug_structure(self):
        event = {
            "last_resize_at": "2026-06-27T08:00:00Z",
            "trigger": "startup",
            "mode": "LANDSCAPE",
            "confidence": "HIGH",
            "basis": "effective window/home layout is landscape; raw wm size is portrait",
            "signals": {"home_landscape_wm_portrait_conflict": True},
            "layout": {"screen_width": 1920, "screen_height": 1080},
            "packages": [],
            "summary": {"resized": 1, "already_correct": 0, "skipped": 0, "failed": 0},
        }
        dbg = resize_trace.build_resize_debug_from_event(event)
        self.assertEqual(dbg["mode"], "LANDSCAPE")
        self.assertTrue(dbg["signals"]["home_landscape_wm_portrait_conflict"])


if __name__ == "__main__":
    unittest.main()
