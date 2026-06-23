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

    def test_resolve_autoexec_paths_includes_android_data(self) -> None:
        paths = ai.resolve_autoexec_paths(_PKG)
        self.assertIn(f"/sdcard/Android/data/{_PKG}/files/autoexec/", paths)

    def test_build_heartbeat_tracker_lua_embeds_package_and_urls(self) -> None:
        lua = ai.build_heartbeat_tracker_lua(_PKG)
        self.assertIn(_PKG, lua)
        self.assertIn("127.0.0.1:9999/heartbeat?package=", lua)
        self.assertIn(ai.PRIMARY_TRACKER_URL, lua)
        self.assertIn("task.spawn", lua)
        self.assertIn("loadstring", lua)

    def test_target_file_name_is_deng_heartbeat_lua(self) -> None:
        self.assertEqual(ai.TRACKER_FILENAME, "deng_heartbeat.lua")
        expected = os.path.join(
            f"/data/data/{_PKG}/files/autoexec/",
            ai.TRACKER_FILENAME,
        )
        self.assertIn(expected, [
            os.path.join(directory, ai.TRACKER_FILENAME)
            for directory in ai.resolve_autoexec_paths(_PKG)
        ])


class TestAutoexecUidGid(unittest.TestCase):
    def test_lookup_package_uid_gid_parses_stat_output(self) -> None:
        def _fake_root(args, **kwargs):
            self.assertEqual(args[0], "stat")
            self.assertEqual(args[1], "-c")
            self.assertEqual(args[2], "%u:%g")
            return type("Res", (), {"ok": True, "stdout": f"{_UID}:{_GID}\n", "stderr": ""})()

        with patch("agent.android.run_root_command", side_effect=_fake_root):
            pair = ai.lookup_package_uid_gid(_PKG, root_tool="su")
        self.assertEqual(pair, _UID_GID)

    def test_chown_internal_path_uses_discovered_uid_gid(self) -> None:
        calls: list[tuple[str, ...]] = []

        def _fake_root(args, **kwargs):
            calls.append(tuple(args))
            if len(args) >= 3 and args[0] == "stat" and args[1] == "-c" and args[2] == "%u:%g":
                return type("Res", (), {"ok": True, "stdout": f"{_UID}:{_GID}\n", "stderr": ""})()
            return type("Res", (), {"ok": True, "stdout": "", "stderr": ""})()

        directory = f"/data/data/{_PKG}/files/autoexec/"
        with patch("agent.android.run_root_command", side_effect=_fake_root):
            ok, err = ai.chown_autoexec_directory(directory, _PKG, root_tool="su")
        self.assertTrue(ok)
        self.assertEqual(err, "")
        chown_calls = [args for args in calls if args[:1] == ("chown",)]
        self.assertEqual(len(chown_calls), 1)
        self.assertEqual(chown_calls[0], ("chown", "-R", f"{_UID}:{_GID}", directory.rstrip("/")))

    def test_chown_external_path_failure_is_non_fatal(self) -> None:
        def _fake_root(args, **kwargs):
            if len(args) >= 3 and args[0] == "stat" and args[1] == "-c" and args[2] == "%u:%g":
                return type("Res", (), {"ok": True, "stdout": f"{_UID}:{_GID}\n", "stderr": ""})()
            if args[:1] == ("chown",):
                return type("Res", (), {"ok": False, "stdout": "", "stderr": "Operation not permitted"})()
            return type("Res", (), {"ok": True, "stdout": "", "stderr": ""})()

        directory = f"/sdcard/Android/data/{_PKG}/files/autoexec/"
        with patch("agent.android.run_root_command", side_effect=_fake_root):
            ok, err = ai.chown_autoexec_directory(directory, _PKG, root_tool="su", uid_gid=_UID_GID)
        self.assertTrue(ok)
        self.assertEqual(err, "")


class TestAutoexecInjection(unittest.TestCase):
    def test_inject_writes_to_all_autoexec_paths_via_root(self) -> None:
        calls: list[tuple[tuple[str, ...], dict]] = []

        def _fake_root(args, **kwargs):
            calls.append((tuple(args), kwargs))
            if len(args) >= 3 and args[0] == "stat" and args[1] == "-c" and args[2] == "%u:%g":
                return type("Res", (), {"ok": True, "stdout": f"{_UID}:{_GID}\n", "stderr": "", "returncode": 0})()
            return type("Res", (), {"ok": True, "stdout": "", "stderr": "", "returncode": 0})()

        with patch("agent.android.run_root_command", side_effect=_fake_root), \
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
        chown_calls = [args for args, _kwargs in calls if args[:1] == ("chown",)]
        self.assertGreaterEqual(len(chown_calls), len(ai.AUTOEXEC_PATH_TEMPLATES))
        self.assertTrue(
            all(f"{_UID}:{_GID}" in " ".join(str(a) for a in args) for args in chown_calls)
        )

    def test_inject_payload_content_matches_generated_lua(self) -> None:
        captured: list[tuple[str, str]] = []

        def _fake_write(
            dest_path: str,
            content: str,
            *,
            root_tool: str,
            package: str,
            uid_gid: tuple[str, str] | None = None,
        ) -> tuple[bool, str]:
            captured.append((dest_path, content))
            return True, ""

        expected = ai.build_heartbeat_tracker_lua(_PKG)
        with patch.object(ai, "_write_file_via_root", side_effect=_fake_write), \
             patch.object(ai, "lookup_package_uid_gid", return_value=_UID_GID):
            result = ai.inject_autoexec_tracker(_PKG, root_tool="su")
        self.assertTrue(result["success"])
        self.assertTrue(captured)
        self.assertEqual(captured[0][1], expected)
        self.assertTrue(all(dest.endswith(ai.TRACKER_FILENAME) for dest, _ in captured))

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
