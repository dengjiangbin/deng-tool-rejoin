from __future__ import annotations

import unittest
from unittest import mock

from agent import commands, window_apply, window_layout


def _entry(pkg: str) -> dict:
    return {
        "package": pkg,
        "enabled": True,
        "account_username": pkg.rsplit(".", 1)[-1],
        "private_server_url": "https://www.roblox.com/share?code=abc&type=Server",
        "auto_reopen_enabled": True,
        "auto_reconnect_enabled": True,
        "roblox_user_id": 0,
    }


class TestProbeB8a026e11ePostLaunchVerifyReadonly(unittest.TestCase):
    def test_post_launch_verify_skips_destructive_layout_apply(self) -> None:
        packages = [
            "com.moons.litesc",
            "com.moons.litesd",
            "com.moons.litese",
            "com.moons.litesf",
            "com.moons.litesg",
            "com.moons.litesh",
        ]
        entries = [_entry(pkg) for pkg in packages]
        cfg = {
            "screen_mode": "landscape",
            "last_layout_mode": "landscape",
            "last_layout_preview": [
                {"package": pkg, "left": 0, "top": 25, "right": 426, "bottom": 256}
                for pkg in packages[:3]
            ],
            "_layout_rects": [
                {"package": pkg, "left": 0, "top": 25, "right": 426, "bottom": 256}
                for pkg in packages[:3]
            ],
            "termux_dock_fraction": 0.0,
        }
        captured: dict[str, object] = {}

        def fake_apply(rects, **kwargs):
            captured["package_count"] = len(rects)
            captured["kwargs"] = kwargs
            return []

        with mock.patch.object(window_layout, "detect_display_info", return_value=window_layout.DisplayInfo(1280, 720, 164)), \
             mock.patch.object(window_layout, "_detect_status_bar_height", return_value=25), \
             mock.patch.object(window_apply, "apply_window_layout", side_effect=fake_apply):
            commands._verify_layout_post_launch(cfg, entries)

        self.assertEqual(captured["package_count"], 6)
        kwargs = captured["kwargs"]
        self.assertIs(kwargs["pre_write"], False)
        self.assertIs(kwargs["allow_direct_resize"], False)
        self.assertEqual(kwargs["retries"], 0)
        self.assertIs(kwargs["force_stop_before"], False)

    def test_verify_only_mode_does_not_call_direct_resize(self) -> None:
        rect = window_layout.WindowRect("com.moons.litesc", 426, 25, 852, 256)
        fullscreen = (0, 0, 1280, 720)

        with mock.patch.object(window_apply, "read_actual_bounds", return_value=(fullscreen, "dumpsys")), \
             mock.patch.object(window_apply, "_wait_for_window"), \
             mock.patch.object(window_apply, "_discover_known_keys", return_value={"com.moons.litesc": []}), \
             mock.patch.object(window_apply, "_capability_probes", return_value={}), \
             mock.patch("agent.window_apply.android.detect_root", return_value=mock.Mock(available=True, tool="su")), \
             mock.patch("agent.window_apply.setup_freeform_capabilities", create=True), \
             mock.patch.object(window_apply, "_direct_resize_via_root") as direct_resize, \
             mock.patch.object(window_apply, "_write_one_package") as pre_write:
            from agent.freeform_enable import setup_freeform_capabilities

            with mock.patch("agent.window_apply.setup_freeform_capabilities", setup_freeform_capabilities, create=True):
                results = window_apply.apply_window_layout(
                    [rect],
                    pre_write=False,
                    allow_direct_resize=False,
                    verify_after=True,
                    retries=0,
                )

        pre_write.assert_not_called()
        direct_resize.assert_not_called()
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].final_ok)
        self.assertIn("verify-only: pre-write skipped", results[0].attempts)
        self.assertIn("verify-only: direct resize skipped", results[0].attempts)


if __name__ == "__main__":
    unittest.main()
