"""Start/recovery cache clear segfault regression (probes p-7dac7cb6c4, p-f499f7533a)."""

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

from agent import android, cache_clear_phases, commands


class TestStartBatchCacheClear(unittest.TestCase):
    def test_start_uses_fast_batch_cache_clear_not_verified(self) -> None:
        src = inspect.getsource(commands.cmd_start)
        batch_idx = src.find("batch_clear_cache_begin")
        done_idx = src.find("batch_clear_cache_done", batch_idx)
        block = src[batch_idx:done_idx]
        self.assertIn("_run_start_batch_cache_clear", block)
        self.assertNotIn("clear_package_cache_verified", block)

    def test_mass_batch_cache_clear_uses_single_root_shell(self) -> None:
        root = android.RootInfo(True, "su", "uid=0")
        packages = ["com.moons.litesc", "com.moons.litesd"]
        with mock.patch.object(android, "detect_root", return_value=root), \
             mock.patch.object(android, "run_root_command") as root_cmd:
            root_cmd.return_value = android.CommandResult(("su",), 0, "", "")
            out = android.clear_packages_cache_mass_batch(packages)
        self.assertEqual(root_cmd.call_count, 1)
        script = root_cmd.call_args.args[0][2]
        self.assertIn("com.moons.litesc", script)
        self.assertIn("com.moons.litesd", script)
        self.assertEqual(out["com.moons.litesc"], "Cleared")
        self.assertEqual(out["com.moons.litesd"], "Cleared")

    def test_start_mass_cache_clear_runs_one_child_on_termux(self) -> None:
        packages = ["com.moons.litesc", "com.moons.litesd"]
        payload = json.dumps(
            {"com.moons.litesc": "Cleared", "com.moons.litesd": "Cleared"}
        ).encode("utf-8")
        proc = mock.Mock()
        proc.communicate.return_value = (payload, b"")
        proc.returncode = 0
        with mock.patch.object(cache_clear_phases, "should_isolate_cache_clear", return_value=True), \
             mock.patch("subprocess.Popen", return_value=proc) as popen:
            out = cache_clear_phases.run_start_mass_cache_clear(packages)
        self.assertEqual(out, {"com.moons.litesc": "Cleared", "com.moons.litesd": "Cleared"})
        popen.assert_called_once()
        self.assertIn("-c", popen.call_args.args[0])

    def test_run_start_batch_cache_clear_delegates_to_phase1(self) -> None:
        with mock.patch.object(
            cache_clear_phases,
            "run_start_mass_cache_clear",
            return_value={"com.moons.litesc": "Cleared"},
        ) as mass:
            out = commands._run_start_batch_cache_clear(["com.moons.litesc"])
        mass.assert_called_once_with(["com.moons.litesc"])
        self.assertEqual(out["com.moons.litesc"], "Cleared")

    def test_recovery_cache_clear_runs_one_child_on_termux(self) -> None:
        payload = json.dumps(
            {
                "success": True,
                "skipped": False,
                "skipped_reason": "",
                "method": "recovery_single",
                "error": "",
            }
        ).encode("utf-8")
        proc = mock.Mock()
        proc.communicate.return_value = (payload, b"")
        proc.returncode = 0
        with mock.patch.object(cache_clear_phases, "should_isolate_cache_clear", return_value=True), \
             mock.patch("subprocess.Popen", return_value=proc) as popen:
            out = cache_clear_phases.run_recovery_cache_clear("com.moons.litesc")
        popen.assert_called_once()
        self.assertTrue(out.get("success"))

    def test_supervisor_dead_recovery_uses_phase2_recovery_clear(self) -> None:
        from agent.supervisor import WatchdogSupervisor

        src = inspect.getsource(WatchdogSupervisor._handle_state)
        dead_idx = src.find("[DENG_REJOIN_DEAD_PACKAGE_CACHE_CLEAR]")
        block = src[max(0, dead_idx - 500):dead_idx]
        self.assertIn("run_recovery_cache_clear", block)
        self.assertNotIn("clear_package_cache_verified", block)


if __name__ == "__main__":
    unittest.main()
