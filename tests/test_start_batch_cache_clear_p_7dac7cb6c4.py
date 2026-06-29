"""Start batch cache clear segfault regression (probe p-7dac7cb6c4)."""

from __future__ import annotations

import inspect
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import android, commands


class TestStartBatchCacheClear(unittest.TestCase):
    def test_start_uses_fast_batch_cache_clear_not_verified(self) -> None:
        src = inspect.getsource(commands.cmd_start)
        batch_idx = src.find("batch_clear_cache_begin")
        done_idx = src.find("batch_clear_cache_done", batch_idx)
        block = src[batch_idx:done_idx]
        self.assertIn("_run_start_batch_cache_clear", block)
        self.assertNotIn("clear_package_cache_verified", block)

    def test_clear_package_cache_for_start_uses_single_root_shell(self) -> None:
        with mock.patch.object(android, "run_root_command") as root_cmd:
            root_cmd.return_value = android.CommandResult(("su",), 0, "", "")
            label = android.clear_package_cache_for_start(
                "com.moons.litesc",
                root_tool="su",
            )
        self.assertEqual(label, "Cleared")
        self.assertEqual(root_cmd.call_count, 1)
        args = root_cmd.call_args.args[0]
        self.assertEqual(args[0], "sh")
        script = args[2]
        self.assertIn("find", script)
        self.assertIn("code_cache", script)

    def test_clear_packages_cache_batch_detects_root_once(self) -> None:
        root = android.RootInfo(True, "su", "uid=0")
        with mock.patch.object(android, "detect_root", return_value=root) as detect, \
             mock.patch.object(
                 android,
                 "clear_package_cache_for_start",
                 side_effect=["Cleared", "Skipped"],
             ) as clear_one:
            out = android.clear_packages_cache_batch(
                ["com.moons.litesc", "com.moons.litesd"],
            )
        detect.assert_called_once()
        self.assertEqual(clear_one.call_count, 2)
        self.assertEqual(out["com.moons.litesc"], "Cleared")
        self.assertEqual(out["com.moons.litesd"], "Skipped")

    def test_isolated_batch_cache_clear_runs_in_child_on_termux(self) -> None:
        packages = ["com.moons.litesc", "com.moons.litesd"]
        payload = json.dumps(
            {"com.moons.litesc": "Cleared", "com.moons.litesd": "Cleared"}
        ).encode("utf-8")
        proc = mock.Mock()
        proc.communicate.return_value = (payload, b"")
        proc.returncode = 0
        with mock.patch.dict("os.environ", {"TERMUX_VERSION": "0.118.0"}, clear=False), \
             mock.patch("subprocess.Popen", return_value=proc) as popen:
            out = commands._start_batch_cache_clear_isolated(packages)
        self.assertEqual(out, {"com.moons.litesc": "Cleared", "com.moons.litesd": "Cleared"})
        popen.assert_called_once()
        self.assertIn("-c", popen.call_args.args[0])

    def test_run_start_batch_cache_clear_uses_isolation_on_termux(self) -> None:
        with mock.patch.object(commands, "_should_isolate_start_cache_clear", return_value=True), \
             mock.patch.object(
                 commands,
                 "_start_batch_cache_clear_isolated",
                 return_value={"com.moons.litesc": "Cleared"},
             ) as isolated:
            out = commands._run_start_batch_cache_clear(["com.moons.litesc"])
        isolated.assert_called_once_with(["com.moons.litesc"])
        self.assertEqual(out["com.moons.litesc"], "Cleared")


if __name__ == "__main__":
    unittest.main()
