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


class TestAutoexecPayload(unittest.TestCase):
    def test_resolve_autoexec_paths_uses_package_name(self) -> None:
        paths = ai.resolve_autoexec_paths(_PKG)
        self.assertEqual(len(paths), len(ai.AUTOEXEC_PATH_TEMPLATES))
        for path in paths:
            self.assertIn(_PKG, path)
            self.assertTrue(path.endswith("/"))

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


class TestAutoexecInjection(unittest.TestCase):
    def test_inject_writes_to_all_autoexec_paths_via_root(self) -> None:
        calls: list[tuple[tuple[str, ...], dict]] = []

        def _fake_root(args, **kwargs):
            calls.append((tuple(args), kwargs))
            return type("Res", (), {"ok": True, "stderr": "", "returncode": 0})()

        with patch("agent.android.run_root_command", side_effect=_fake_root), \
             patch("agent.android.detect_root") as mock_root:
            mock_root.return_value = type(
                "Root",
                (),
                {"available": True, "tool": "su"},
            )()
            result = ai.inject_autoexec_tracker(_PKG, root_tool="su")

        self.assertTrue(result["success"])
        self.assertEqual(len(result["paths_written"]), len(ai.AUTOEXEC_PATH_TEMPLATES))
        self.assertTrue(
            any(
                ai.TRACKER_FILENAME in " ".join(str(a) for a in args)
                for args, _kwargs in calls
            )
        )
        chmod_calls = [args for args, _kwargs in calls if args[:1] == ("chmod",)]
        self.assertEqual(len(chmod_calls), len(ai.AUTOEXEC_PATH_TEMPLATES))

    def test_inject_payload_content_matches_generated_lua(self) -> None:
        captured: list[tuple[str, str]] = []

        def _fake_write(dest_path: str, content: str, *, root_tool: str) -> tuple[bool, str]:
            captured.append((dest_path, content))
            return True, ""

        expected = ai.build_heartbeat_tracker_lua(_PKG)
        with patch.object(ai, "_write_file_via_root", side_effect=_fake_write):
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
