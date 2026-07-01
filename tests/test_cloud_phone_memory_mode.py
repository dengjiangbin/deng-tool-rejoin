from __future__ import annotations

import inspect
import unittest
from unittest import mock


class CloudPhoneMemoryModeTests(unittest.TestCase):
    def test_cloud_phone_extreme_is_default_only_behavior(self) -> None:
        import agent.android as android
        import agent.commands as commands

        cmd_src = inspect.getsource(commands.cmd_start)
        self.assertIn("optimize_cloud_phone_memory", cmd_src)
        self.assertNotIn("Normal Mode", cmd_src)
        self.assertNotIn("Extreme Mode", cmd_src)
        self.assertNotIn("memory mode selection", cmd_src.lower())
        self.assertEqual(android.cloud_phone_memory_recovery_command(), "pm enable com.google.android.gms")

    def test_disable_user_allowed_for_google_packages_no_uninstall(self) -> None:
        import agent.android as android

        commands_seen: list[list[str]] = []

        def fake_run_android(cmd, timeout=None, prefer_root=False, **kwargs):
            class _R:
                ok = True
                stderr = ""
                returncode = 0
            commands_seen.append(list(cmd))
            if cmd[:3] == ["pm", "list", "packages"]:
                _R.stdout = "\n".join([
                    "package:com.google.android.gms",
                    "package:com.android.vending",
                    "package:com.google.android.googlequicksearchbox",
                    "package:com.termux",
                    "package:com.roblox.client",
                    "package:com.discord",
                ])
            elif cmd[:4] == ["cmd", "package", "resolve-activity", "--brief"]:
                _R.stdout = "com.android.launcher3/.Launcher"
            else:
                _R.stdout = ""
            return _R()

        def fake_root(cmd, root_tool=None, timeout=None):
            class _R:
                ok = True
                stdout = ""
                stderr = ""
                returncode = 0
            commands_seen.append(list(cmd))
            return _R()

        root = type("RI", (), {"available": True, "tool": "su"})()
        with mock.patch.object(android, "_CLOUD_MEMORY_LAST_RUN", 0.0), \
             mock.patch.object(android, "detect_root", return_value=root), \
             mock.patch.object(android, "run_android_command", side_effect=fake_run_android), \
             mock.patch.object(android, "run_root_command", side_effect=fake_root), \
             mock.patch.object(android, "current_foreground_package", return_value="com.termux"):
            result = android.optimize_cloud_phone_memory(
                ["com.termux", "com.roblox.client"],
                cooldown_seconds=0,
                disable_google_packages=True,
            )

        flat = [" ".join(cmd) for cmd in commands_seen]
        self.assertIn("com.google.android.gms", result["disabled"])
        self.assertIn("com.android.vending", result["disabled"])
        self.assertIn("com.google.android.googlequicksearchbox", result["disabled"])
        self.assertTrue(any("pm disable-user --user 0 com.google.android.gms" in s for s in flat))
        self.assertFalse(any("uninstall" in s for s in flat))
        self.assertFalse(any("com.termux" in s and ("force-stop" in s or "disable-user" in s) for s in flat))
        self.assertFalse(any("com.roblox.client" in s and ("force-stop" in s or "disable-user" in s) for s in flat))
        self.assertEqual(result["recovery_command"], "pm enable com.google.android.gms")

    def test_google_packages_left_running_by_default(self) -> None:
        import agent.android as android

        root = type("RI", (), {"available": True, "tool": "su"})()

        def fake_run_android(cmd, timeout=None, prefer_root=False, **kwargs):
            class _R:
                ok = True
                stderr = ""
                returncode = 0
            if cmd[:3] == ["pm", "list", "packages"]:
                _R.stdout = "package:com.google.android.gms\npackage:com.termux\n"
            elif cmd[:4] == ["cmd", "package", "resolve-activity", "--brief"]:
                _R.stdout = "com.android.launcher3/.Launcher"
            else:
                _R.stdout = ""
            return _R()

        with mock.patch.object(android, "_CLOUD_MEMORY_LAST_RUN", 0.0), \
             mock.patch.object(android, "detect_root", return_value=root), \
             mock.patch.object(android, "run_android_command", side_effect=fake_run_android), \
             mock.patch.object(android, "run_root_command") as root_cmd, \
             mock.patch.object(android, "current_foreground_package", return_value="com.termux"):
            result = android.optimize_cloud_phone_memory(["com.termux"], cooldown_seconds=0)

        self.assertEqual(result["disabled"], [])
        self.assertFalse(any("disable-user" in " ".join(c) for c in root_cmd.call_args_list))

    def test_repeated_start_cooldown_avoids_spam(self) -> None:
        import agent.android as android

        root = type("RI", (), {"available": True, "tool": "su"})()
        with mock.patch.object(android, "_CLOUD_MEMORY_LAST_RUN", 1234.0), \
             mock.patch("agent.android.time.monotonic", return_value=1235.0), \
             mock.patch.object(android, "detect_root", return_value=root) as detect:
            result = android.optimize_cloud_phone_memory(["com.termux"], cooldown_seconds=600)

        self.assertTrue(result["cooldown_skipped"])
        detect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
