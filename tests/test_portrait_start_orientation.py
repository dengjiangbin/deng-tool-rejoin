from __future__ import annotations

import unittest
from unittest import mock

from agent import android, commands


class TestPortraitStartOrientation(unittest.TestCase):
    def test_enforce_screen_orientation_honors_portrait(self):
        with mock.patch(
            "agent.android.get_display_orientation_state",
            side_effect=[
                {"orientation": "landscape", "width": 1920, "height": 1080, "rotation": 1},
                {"orientation": "portrait", "width": 1080, "height": 1920, "rotation": 0},
            ],
        ), mock.patch("agent.android._apply_user_rotation", return_value=[{"cmd": "lock 0", "ok": True}]), \
             mock.patch("agent.android.detect_root", return_value=android.RootInfo(False, "", "")), \
             mock.patch("agent.android.detect_orientation_override_apps", return_value=[]):
            result = android.enforce_screen_orientation("portrait", protected_packages=["com.termux"])

        self.assertEqual(result["requested"], "portrait")
        self.assertTrue(result["success"])

    def test_enforce_configured_screen_mode_detects_portrait_on_start(self):
        cfg = {"screen_mode": "auto", "roblox_packages": [{"package": "pkg1", "enabled": True}]}
        with mock.patch(
            "agent.resize_mode.detect_effective_resize_mode",
            return_value={
                "mode": "PORTRAIT",
                "confidence": "HIGH",
                "basis": "logical display height exceeds width",
                "signals": {"wm_size_raw": "1080x1920"},
            },
        ), mock.patch("agent.commands.android.enforce_landscape_home_state", return_value={
            "phase": "before_start",
            "final_layout_mode": "portrait",
            "screen_mode_config": "portrait",
            "correction_applied": [],
            "wm_size": {},
            "wm_density": {},
            "user_rotation": "0",
            "accelerometer_rotation": "0",
            "display_rect": {"width": 1080, "height": 1920, "orientation": "portrait"},
            "launcher_bounds": {},
            "black_bar_suspected": "no",
        }), mock.patch("agent.commands.android.enforce_screen_orientation", return_value={
            "requested": "portrait",
            "actual_before": "portrait",
            "actual_after": "portrait",
            "success": True,
            "root_available": False,
            "override_detected": False,
            "override_package": "",
            "override_action": "none",
            "error": "",
        }), mock.patch("agent.commands.enabled_package_names", return_value=["pkg1"]), \
             mock.patch("agent.commands.validate_config", side_effect=lambda c: c), \
             mock.patch("agent.logger.configure_logging"), \
             mock.patch("agent.logger.log_event"):
            commands._enforce_configured_screen_mode(cfg, phase="before_start")

        self.assertEqual(cfg["screen_mode"], "portrait")


if __name__ == "__main__":
    unittest.main()
