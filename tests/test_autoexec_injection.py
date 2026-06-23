"""Tests for automatic Lua heartbeat auto-exec injection."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import autoexec_injection as ai

_PKG = "com.moons.litesc"
_UID = "10234"
_GID = "10234"
_UID_GID = (_UID, _GID)


class TestAutoexecPayload(unittest.TestCase):
    def test_resolve_autoexec_paths_uses_package_name(self) -> None:
        paths = ai.resolve_autoexec_paths(_PKG)
        self.assertEqual(len(paths), len(ai.AUTOEXEC_PATH_TEMPLATES))
        for path in paths:
            self.assertIn(_PKG, path)
            self.assertTrue(path.endswith("/"))

    def test_resolve_autoexec_paths_includes_executor_specific_dirs(self) -> None:
        paths = ai.resolve_autoexec_paths(_PKG)
        self.assertIn(f"/sdcard/Android/data/{_PKG}/files/Delta/autoexec/", paths)
        self.assertIn(f"/sdcard/Android/data/{_PKG}/files/Codex/autoexec/", paths)
        self.assertIn(f"/sdcard/Android/data/{_PKG}/files/VegaX/autoexec/", paths)
        self.assertIn(f"/sdcard/Android/media/{_PKG}/spdm_scripts/autoexec/", paths)
        self.assertIn(f"/data/data/{_PKG}/files/autoexec/", paths)

    def test_build_heartbeat_tracker_lua_embeds_package_and_urls(self) -> None:
        lua = ai.build_heartbeat_tracker_lua(_PKG)
        self.assertIn(_PKG, lua)
        self.assertIn("127.0.0.1:9999/heartbeat?package=", lua)
        self.assertIn(ai.PRIMARY_TRACKER_URL, lua)
        self.assertIn("dengjiangbin/fish-it/main/tracker.lua", lua)
        self.assertNotIn("pgen0x/kaeru", lua)
        self.assertIn("task.spawn", lua)
        self.assertIn("loadstring", lua)

    def test_build_heartbeat_tracker_lua_waits_for_game_load_and_local_player(self) -> None:
        lua = ai.build_heartbeat_tracker_lua(_PKG)
        self.assertIn("game:IsLoaded()", lua)
        self.assertIn("game.Loaded:Wait()", lua)
        self.assertIn('game:GetService("Players")', lua)
        self.assertIn("Players.LocalPlayer", lua)
        self.assertIn("Deng Local Heartbeat (Robust Detection Logic)", lua)

    def test_target_file_name_is_deng_rejoin_heartbeat_lua(self) -> None:
        self.assertEqual(ai.TRACKER_FILENAME, "deng_rejoin_heartbeat.lua")


class TestAutoexecUidGid(unittest.TestCase):
    def test_lookup_package_uid_gid_uses_mount_master_root(self) -> None:
        calls: list[tuple[tuple[str, ...], dict]] = []

        def _fake_mm(args, **kwargs):
            calls.append((tuple(args), kwargs))
            return type("Res", (), {"ok": True, "stdout": f"{_UID}:{_GID}\n", "stderr": ""})()

        with patch("agent.android.run_mount_master_root_command", side_effect=_fake_mm):
            pair = ai.lookup_package_uid_gid(_PKG, root_tool="su")
        self.assertEqual(pair, _UID_GID)
        self.assertTrue(calls)

    def test_build_injection_shell_script_writes_all_paths(self) -> None:
        paths = ai.resolve_autoexec_paths(_PKG)
        payload = ai.build_heartbeat_tracker_lua(_PKG)
        script = ai.build_injection_shell_script(_PKG, paths, payload, uid_gid=_UID_GID)
        self.assertIn(ai.TRACKER_FILENAME, script)
        self.assertIn("printf %s", script)
        self.assertIn(f"chown -R {_UID}:{_GID}", script)
        for path in paths:
            self.assertIn(path.rstrip("/"), script)


class TestAutoexecInjection(unittest.TestCase):
    def test_inject_uses_single_mount_master_bash_block(self) -> None:
        captured: list[tuple[tuple[str, ...], dict]] = []

        def _fake_mm(args, **kwargs):
            captured.append((tuple(args), kwargs))
            if len(args) >= 3 and args[0] == "stat" and args[1] == "-c":
                return type("Res", (), {"ok": True, "stdout": f"{_UID}:{_GID}\n", "stderr": ""})()
            return type("Res", (), {"ok": True, "stdout": "", "stderr": ""})()

        with patch("agent.android.run_mount_master_root_command", side_effect=_fake_mm), \
             patch("agent.android.detect_root") as mock_root:
            mock_root.return_value = type(
                "Root",
                (),
                {"available": True, "tool": "su"},
            )()
            result = ai.inject_autoexec_tracker(_PKG, root_tool="su")

        self.assertTrue(result["success"])
        self.assertEqual(result["uid_gid"], f"{_UID}:{_GID}")
        self.assertEqual(len(result["paths_written"]), len(ai.AUTOEXEC_PATH_TEMPLATES))
        inject_calls = [
            args for args, _kwargs in captured
            if len(args) >= 2 and args[:2] == ("sh", "-c")
        ]
        self.assertEqual(len(inject_calls), 1)
        script = inject_calls[0][2]
        self.assertIn("Delta/autoexec", script)
        self.assertIn("Codex/autoexec", script)
        self.assertIn("VegaX/autoexec", script)
        self.assertIn("spdm_scripts/autoexec", script)
        self.assertIn(ai.TRACKER_FILENAME, script)

    def test_inject_without_root_reports_failure(self) -> None:
        with patch("agent.android.detect_root") as mock_root:
            mock_root.return_value = type(
                "Root",
                (),
                {"available": False, "tool": None},
            )()
            result = ai.inject_autoexec_tracker(_PKG)
        self.assertFalse(result["success"])
        self.assertIn("root unavailable", result["errors"][0])


if __name__ == "__main__":
    unittest.main()
