from __future__ import annotations

import unittest
from unittest import mock

from agent import android, window_apply, window_layout


class TestLandscapeHomeOrientation(unittest.TestCase):
    def test_landscape_enforcement_does_not_reset_wm_size_when_sensor_portrait(self):
        """Portrait wm size with landscape display is normal — must not override."""
        commands: list[list[str]] = []

        def run_cmd(args, **_kwargs):
            cmd = list(args)
            commands.append(cmd)
            if cmd == ["wm", "size"]:
                raw = "Physical size: 720x1280"
                return android.CommandResult(tuple(cmd), 0, raw, "")
            if cmd == ["wm", "density"]:
                return android.CommandResult(tuple(cmd), 0, "Physical density: 192", "")
            if cmd[:3] == ["settings", "get", "system"]:
                return android.CommandResult(tuple(cmd), 0, "1", "")
            if cmd[:3] == ["cmd", "package", "resolve-activity"]:
                return android.CommandResult(tuple(cmd), 0, "com.android.launcher3/.Launcher", "")
            if cmd[:3] == ["dumpsys", "activity", "activities"]:
                text = "TaskRecord #4 A=com.android.launcher3 mBounds=Rect(0, 0 - 1280, 720)"
                return android.CommandResult(tuple(cmd), 0, text, "")
            return android.CommandResult(tuple(cmd), 0, "", "")

        with mock.patch("agent.android.get_display_orientation_state",
                        return_value={"orientation": "landscape", "width": 1280, "height": 720, "rotation": 1}), \
             mock.patch("agent.android.run_android_command", side_effect=run_cmd), \
             mock.patch("agent.android.detect_root", return_value=android.RootInfo(True, "su", "")):
            state = android.enforce_landscape_home_state(phase="before_start")

        self.assertNotIn(["wm", "size", "reset"], commands)
        self.assertEqual(state["final_layout_mode"], "landscape")
        self.assertEqual(state["black_bar_suspected"], "no")

    def test_landscape_enforcement_no_wm_change_when_state_already_landscape(self):
        commands: list[list[str]] = []

        def run_cmd(args, **_kwargs):
            cmd = list(args)
            commands.append(cmd)
            if cmd == ["wm", "size"]:
                return android.CommandResult(tuple(cmd), 0, "Physical size: 1280x720", "")
            if cmd == ["wm", "density"]:
                return android.CommandResult(tuple(cmd), 0, "Physical density: 192", "")
            if cmd[:3] == ["settings", "get", "system"]:
                return android.CommandResult(tuple(cmd), 0, "1", "")
            if cmd[:3] == ["cmd", "package", "resolve-activity"]:
                return android.CommandResult(tuple(cmd), 0, "com.android.launcher3/.Launcher", "")
            if cmd[:3] == ["dumpsys", "activity", "activities"]:
                return android.CommandResult(tuple(cmd), 0, "mBounds=Rect(0, 0 - 1280, 720) com.android.launcher3", "")
            return android.CommandResult(tuple(cmd), 0, "", "")

        with mock.patch("agent.android.get_display_orientation_state",
                        return_value={"orientation": "landscape", "width": 1280, "height": 720, "rotation": 1}), \
             mock.patch("agent.android.run_android_command", side_effect=run_cmd), \
             mock.patch("agent.android.detect_root", return_value=android.RootInfo(True, "su", "")):
            state = android.enforce_landscape_home_state(phase="before_start")

        self.assertNotIn(["wm", "size", "reset"], commands)
        self.assertEqual(state["correction_applied"], [])

    def test_home_termux_and_system_packages_are_layout_excluded(self):
        for package in (
            "com.android.launcher3",
            "com.sec.android.app.launcher",
            "com.termux",
            "android",
            "com.android.settings",
            "com.google.android.gms",
            "com.samsung.android.game.gamehome",
        ):
            with self.subTest(package=package):
                self.assertTrue(window_layout._is_layout_excluded(package))

    def test_layout_targets_only_selected_roblox_packages(self):
        selected = ["com.moons.litesc", "com.android.launcher3", "com.termux"]
        targets = [p for p in selected if not window_layout._is_layout_excluded(p)]
        self.assertEqual(targets, ["com.moons.litesc"])

    def test_portrait_runtime_path_uses_native_coordinate_space(self):
        resolved = window_layout.resolve_layout_mode(720, 1280, "portrait")
        self.assertEqual(resolved.final_layout_mode, "portrait")
        self.assertEqual(resolved.coordinate_space, "android_reported")
        self.assertEqual((resolved.normalized_width, resolved.normalized_height), (720, 1280))
        with mock.patch("agent.window_layout._detect_status_bar_height", return_value=25):
            rects = window_layout.calculate_split_layout(
                ["com.moons.litesc"], 720, 1280, screen_mode="portrait"
            )
        self.assertGreater(rects[0].left, 0)
        self.assertLessEqual(rects[0].right, 720)
        self.assertLessEqual(rects[0].bottom, 1280)

        with mock.patch.object(
            window_apply.window_layout if hasattr(window_apply, "window_layout") else window_layout,
            "detect_display_info",
            return_value=window_layout.DisplayInfo(width=720, height=1280, density=192),
        ):
            self.assertEqual(window_apply._display_bounds("portrait"), (0, 0, 720, 1280))

    def test_apply_user_rotation_default_is_not_strict(self):
        commands: list[list[str]] = []

        def run_cmd(args, **_kwargs):
            commands.append(list(args))
            return android.CommandResult(tuple(args), 0, "", "")

        with mock.patch("agent.android.run_android_command", side_effect=run_cmd), \
             mock.patch("agent.android.detect_root", return_value=android.RootInfo(True, "su", "")):
            android._apply_user_rotation("landscape", strict=False)

        joined = [" ".join(c) for c in commands]
        self.assertTrue(any("set-user-rotation lock 1" in c for c in joined))
        self.assertFalse(any("set-fix-to-user-rotation enabled" in c for c in joined))

    def test_restore_display_defaults_resets_wm_and_rotation(self):
        commands: list[list[str]] = []

        def run_cmd(args, **_kwargs):
            commands.append(list(args))
            if cmd := list(args):
                if cmd == ["wm", "size"]:
                    return android.CommandResult(tuple(cmd), 0, "Physical size: 720x1280", "")
                if cmd == ["wm", "density"]:
                    return android.CommandResult(tuple(cmd), 0, "Physical density: 192", "")
                if cmd[:3] == ["settings", "get", "system"]:
                    return android.CommandResult(tuple(cmd), 0, "0", "")
            return android.CommandResult(tuple(args), 0, "", "")

        with mock.patch("agent.android.run_android_command", side_effect=run_cmd), \
             mock.patch("agent.android.detect_root", return_value=android.RootInfo(True, "su", "")), \
             mock.patch("agent.android.get_display_orientation_state",
                        return_value={"orientation": "portrait", "width": 720, "height": 1280, "rotation": 0}), \
             mock.patch("agent.android.get_wm_size",
                        return_value={"width": 720, "height": 1280, "orientation": "portrait", "ok": True}), \
             mock.patch("agent.android.get_rotation_settings",
                        return_value={"user_rotation": "0", "accelerometer_rotation": "1"}):
            result = android.restore_display_defaults(portrait=True)

        self.assertIn(["wm", "size", "reset"], commands)
        self.assertIn(["cmd", "window", "set-fix-to-user-rotation", "disabled"], commands)
        self.assertTrue(result.get("success"))


if __name__ == "__main__":
    unittest.main()
