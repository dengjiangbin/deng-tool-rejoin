from __future__ import annotations

import unittest
from unittest import mock

from agent import android


class TestPortraitRotationLock(unittest.TestCase):
    def test_portrait_orientation_skips_rotation_lock(self) -> None:
        commands: list[list[str]] = []

        def run_cmd(args, **_kwargs):
            commands.append(list(args))
            return android.CommandResult(tuple(args), 0, "", "")

        with mock.patch(
            "agent.android.get_display_orientation_state",
            return_value={"orientation": "portrait", "width": 1080, "height": 1920, "rotation": 0},
        ), mock.patch("agent.android.run_android_command", side_effect=run_cmd), \
             mock.patch("agent.android.detect_root", return_value=android.RootInfo(True, "su", "")), \
             mock.patch("agent.android.detect_orientation_override_apps", return_value=[]):
            result = android.enforce_screen_orientation("portrait", protected_packages=["com.termux"])

        self.assertEqual(result["requested"], "portrait")
        self.assertTrue(result.get("rotation_lock_skipped"))
        self.assertTrue(result["success"])
        self.assertEqual(commands, [])

    def test_landscape_orientation_does_not_enable_strict_fix(self) -> None:
        commands: list[list[str]] = []

        def run_cmd(args, **_kwargs):
            commands.append(list(args))
            return android.CommandResult(tuple(args), 0, "", "")

        with mock.patch("agent.android.get_display_orientation_state", side_effect=[
            {"orientation": "portrait", "width": 1080, "height": 1920, "rotation": 0},
            {"orientation": "landscape", "width": 1920, "height": 1080, "rotation": 1},
        ]), mock.patch("agent.android.run_android_command", side_effect=run_cmd), \
             mock.patch("agent.android.detect_root", return_value=android.RootInfo(True, "su", "")), \
             mock.patch("agent.android.detect_orientation_override_apps", return_value=[]):
            android.enforce_screen_orientation("landscape", protected_packages=["com.termux"])

        joined = [" ".join(c) for c in commands]
        self.assertFalse(any("set-fix-to-user-rotation enabled" in c for c in joined))


if __name__ == "__main__":
    unittest.main()
