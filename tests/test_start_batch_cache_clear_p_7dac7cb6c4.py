"""Start/recovery cache clear segfault regression (probes p-7dac7cb6c4, p-536c439c42, p-22bfe0518a, p-9d6d6a8cc3)."""

from __future__ import annotations

import inspect
import shlex
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import android, cache_clear_phases, commands
from agent import subprocess_isolated as _iso


class TestStartBatchCacheClear(unittest.TestCase):
    def test_start_runs_cache_clear_before_ui_labels(self) -> None:
        src = inspect.getsource(commands.cmd_start)
        batch_idx = src.find("batch_clear_cache_begin")
        done_idx = src.find("batch_clear_cache_done", batch_idx)
        block = src[batch_idx:done_idx]
        clear_idx = block.find("_run_start_batch_cache_clear")
        label_idx = block.find('_set_all_phase_labels("Clear Cache")')
        self.assertGreater(clear_idx, -1)
        self.assertGreater(label_idx, -1)
        self.assertLess(clear_idx, label_idx)

    def test_start_uses_fast_batch_cache_clear_not_verified(self) -> None:
        src = inspect.getsource(commands.cmd_start)
        batch_idx = src.find("batch_clear_cache_begin")
        done_idx = src.find("batch_clear_cache_done", batch_idx)
        block = src[batch_idx:done_idx]
        self.assertIn("_run_start_batch_cache_clear", block)
        self.assertNotIn("clear_package_cache_verified", block)

    def test_mass_batch_cache_clear_writes_script_and_tiny_nohup_on_termux(self) -> None:
        root = android.RootInfo(True, "su", "uid=0")
        packages = ["com.moons.litesc", "com.moons.litesd"]
        script_path = Path("/tmp/deng_start_mass_cache.sh")
        with mock.patch.object(android, "is_termux", return_value=True), \
             mock.patch.object(
                 android,
                 "write_termux_start_mass_cache_script",
                 return_value=script_path,
             ) as write_script, \
             mock.patch.object(
                 android,
                 "dispatch_termux_start_mass_cache_script",
                 return_value=True,
             ) as dispatch, \
             mock.patch.object(android, "run_root_command") as root_cmd:
            out = android.clear_packages_cache_mass_batch(packages, root_info=root)
        write_script.assert_called_once_with(packages, root_tool="su")
        dispatch.assert_called_once_with(script_path)
        root_cmd.assert_not_called()
        self.assertEqual(out["com.moons.litesc"], "Dispatched")

    def test_dispatch_termux_script_uses_locked_spawn_detached(self) -> None:
        script_path = Path("/tmp/deng_start_mass_cache.sh")
        lock = mock.Mock()
        with mock.patch.object(android, "subprocess_lock", return_value=lock), \
             mock.patch.object(_iso, "spawn_detached", return_value=True) as detached:
            ok = android.dispatch_termux_start_mass_cache_script(script_path)
        self.assertTrue(ok)
        detached.assert_called_once()
        args, kwargs = detached.call_args
        expected_shell = f"nohup sh {shlex.quote(str(script_path))} </dev/null >/dev/null 2>&1 &"
        self.assertEqual(args[0], ["sh", "-c", expected_shell])
        self.assertIs(kwargs.get("lock"), lock)

    def test_mass_batch_cache_clear_inline_off_termux(self) -> None:
        root = android.RootInfo(True, "su", "uid=0")
        packages = ["com.moons.litesc", "com.moons.litesd"]
        with mock.patch.object(android, "is_termux", return_value=False), \
             mock.patch.object(android, "run_root_command") as root_cmd:
            root_cmd.return_value = android.CommandResult(("su",), 0, "", "")
            out = android.clear_packages_cache_mass_batch(packages, root_info=root)
        self.assertEqual(root_cmd.call_count, 1)
        script = root_cmd.call_args.args[0][2]
        self.assertIn("com.moons.litesc", script)
        self.assertIn("rm -rf", script)
        self.assertEqual(out["com.moons.litesc"], "Cleared")

    def test_build_mass_cache_shell_includes_all_packages(self) -> None:
        script = android._build_start_mass_cache_clear_shell(
            ["com.moons.litesc", "com.moons.litesd"],
        )
        self.assertIn("com.moons.litesc", script)
        self.assertIn("com.moons.litesd", script)
        self.assertIn("rm -rf", script)

    def test_start_mass_cache_clear_uses_isolated_child_on_termux(self) -> None:
        packages = ["com.moons.litesc", "com.moons.litesd"]
        root = android.RootInfo(True, "su", "uid=0")
        with mock.patch.object(android, "is_termux", return_value=True), \
             mock.patch.object(
                 cache_clear_phases,
                 "_run_start_mass_cache_clear_termux_isolated",
                 return_value={"com.moons.litesc": "Dispatched", "com.moons.litesd": "Dispatched"},
             ) as isolated, \
             mock.patch.object(cache_clear_phases, "_settle_before_start_cache_clear"), \
             mock.patch.object(cache_clear_phases, "_background_cache_settle_after_dispatch"), \
             mock.patch.object(android, "clear_packages_cache_mass_batch") as mass:
            out = cache_clear_phases.run_start_mass_cache_clear(
                packages,
                root_info=root,
            )
        self.assertEqual(out["com.moons.litesc"], "Dispatched")
        isolated.assert_called_once_with(packages, root_info=root)
        mass.assert_not_called()

    def test_run_start_batch_cache_clear_passes_root_info(self) -> None:
        root = android.RootInfo(True, "su", "uid=0")
        with mock.patch.object(
            cache_clear_phases,
            "run_start_mass_cache_clear",
            return_value={"com.moons.litesc": "Dispatched"},
        ) as mass:
            out = commands._run_start_batch_cache_clear(
                ["com.moons.litesc"],
                root_info=root,
            )
        mass.assert_called_once_with(["com.moons.litesc"], root_info=root)
        self.assertEqual(out["com.moons.litesc"], "Dispatched")

    def test_recovery_cache_clear_runs_inline_without_python_child(self) -> None:
        root = android.RootInfo(True, "su", "uid=0")
        payload = {
            "success": True,
            "skipped": False,
            "skipped_reason": "",
            "method": "recovery_single",
            "error": "",
        }
        with mock.patch.object(
            android,
            "clear_package_cache_recovery",
            return_value=payload,
        ) as recovery, mock.patch("subprocess.Popen") as popen:
            out = cache_clear_phases.run_recovery_cache_clear(
                "com.moons.litesc",
                root_info=root,
            )
        recovery.assert_called_once_with("com.moons.litesc", root_info=root)
        popen.assert_not_called()
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
