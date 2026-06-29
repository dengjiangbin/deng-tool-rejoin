"""Start/recovery cache-clear segfault regression.

Probes: p-7dac7cb6c4, p-536c439c42, p-22bfe0518a, p-9d6d6a8cc3, p-70897e1166.

The two cache-clear types must share ONE proven primitive
(``clear_package_cache_for_start`` — one locked root shell per package using
``find -delete``) and must never spawn a nested Python child or a detached
launcher during Start (those forks SIGSEGV'd Termux/Python 3.13).
"""

from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import android, cache_clear_phases, commands
from agent import lockfile


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

    def test_mass_batch_loops_proven_per_package_primitive(self) -> None:
        """TYPE A must call clear_package_cache_for_start once per package."""
        root = android.RootInfo(True, "su", "uid=0")
        packages = ["com.moons.litesc", "com.moons.litesd"]
        with mock.patch.object(android, "is_termux", return_value=True), \
             mock.patch.object(
                 android, "clear_package_cache_for_start", return_value="Cleared",
             ) as per_pkg, \
             mock.patch("subprocess.Popen") as popen, \
             mock.patch.object(android, "run_root_command") as root_cmd:
            out = android.clear_packages_cache_mass_batch(packages, root_info=root)
        self.assertEqual(per_pkg.call_count, 2)
        # No nested Python child, no giant combined root shell.
        popen.assert_not_called()
        root_cmd.assert_not_called()
        self.assertEqual(out["com.moons.litesc"], "Cleared")
        self.assertEqual(out["com.moons.litesd"], "Cleared")

    def test_mass_batch_one_failure_does_not_abort_batch(self) -> None:
        root = android.RootInfo(True, "su", "uid=0")
        packages = ["com.moons.litesc", "com.moons.litesd"]

        def _side(pkg, *, root_tool):  # noqa: ANN001, ANN202
            if pkg == "com.moons.litesc":
                raise RuntimeError("boom")
            return "Cleared"

        with mock.patch.object(android, "is_termux", return_value=True), \
             mock.patch.object(
                 android, "clear_package_cache_for_start", side_effect=_side,
             ):
            out = android.clear_packages_cache_mass_batch(packages, root_info=root)
        self.assertEqual(out["com.moons.litesc"], "Failed")
        self.assertEqual(out["com.moons.litesd"], "Cleared")

    def test_mass_batch_skips_when_no_root(self) -> None:
        root = android.RootInfo(False, "", "")
        packages = ["com.moons.litesc"]
        with mock.patch.object(android, "clear_package_cache_for_start") as per_pkg:
            out = android.clear_packages_cache_mass_batch(packages, root_info=root)
        per_pkg.assert_not_called()
        self.assertEqual(out["com.moons.litesc"], "Skipped")

    def test_start_phase_delegates_to_mass_batch_no_child(self) -> None:
        packages = ["com.moons.litesc", "com.moons.litesd"]
        root = android.RootInfo(True, "su", "uid=0")
        with mock.patch.object(android, "is_termux", return_value=True), \
             mock.patch.object(cache_clear_phases, "_settle_before_start_cache_clear"), \
             mock.patch.object(
                 android,
                 "clear_packages_cache_mass_batch",
                 return_value={"com.moons.litesc": "Cleared", "com.moons.litesd": "Cleared"},
             ) as mass, \
             mock.patch("subprocess.Popen") as popen:
            out = cache_clear_phases.run_start_mass_cache_clear(packages, root_info=root)
        mass.assert_called_once_with(packages, root_info=root)
        popen.assert_not_called()
        self.assertEqual(out["com.moons.litesc"], "Cleared")

    def test_run_start_batch_cache_clear_passes_root_info(self) -> None:
        root = android.RootInfo(True, "su", "uid=0")
        with mock.patch.object(
            cache_clear_phases,
            "run_start_mass_cache_clear",
            return_value={"com.moons.litesc": "Cleared"},
        ) as mass:
            out = commands._run_start_batch_cache_clear(
                ["com.moons.litesc"], root_info=root,
            )
        mass.assert_called_once_with(["com.moons.litesc"], root_info=root)
        self.assertEqual(out["com.moons.litesc"], "Cleared")

    def test_recovery_clear_is_single_package_same_primitive(self) -> None:
        """TYPE B clears exactly the one dead package, no Python child."""
        root = android.RootInfo(True, "su", "uid=0")
        payload = {
            "success": True,
            "skipped": False,
            "skipped_reason": "",
            "method": "recovery_single",
            "error": "",
        }
        with mock.patch.object(
            android, "clear_package_cache_recovery", return_value=payload,
        ) as recovery, mock.patch("subprocess.Popen") as popen:
            out = cache_clear_phases.run_recovery_cache_clear(
                "com.moons.litesc", root_info=root,
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

    def test_no_experimental_fork_helpers_remain(self) -> None:
        """The crashy Start-only variants must be gone for good."""
        for name in (
            "clear_packages_cache_mass_batch_termux",
            "write_termux_start_mass_cache_script",
            "dispatch_termux_start_mass_cache_script",
            "_build_start_mass_cache_clear_shell",
        ):
            self.assertFalse(
                hasattr(android, name), f"{name} should be removed (fork SIGSEGV risk)"
            )
        self.assertFalse(
            hasattr(cache_clear_phases, "_run_start_mass_cache_clear_termux_isolated")
        )


class TestStopRunningAgentEscalation(unittest.TestCase):
    """A stale old-build watchdog that ignores SIGTERM must be SIGKILLed."""

    def test_sigterm_then_sigkill_for_stale_process(self) -> None:
        sigterm = lockfile.signal.SIGTERM
        sigkill = getattr(lockfile.signal, "SIGKILL", sigterm)
        sent: list[int] = []
        alive = {"v": True}

        def _kill(pid, sig):  # noqa: ANN001, ANN202
            sent.append(sig)
            # Second escalation signal is what finally clears the stale process.
            if len(sent) >= 2:
                alive["v"] = False

        def _alive(pid):  # noqa: ANN001, ANN202
            return alive["v"]

        with mock.patch.object(lockfile, "read_pid", return_value=4242), \
             mock.patch.object(lockfile, "is_process_alive", side_effect=_alive), \
             mock.patch.object(lockfile, "is_deng_process", return_value=True), \
             mock.patch.object(lockfile.os, "kill", side_effect=_kill), \
             mock.patch.object(lockfile.LockManager, "cleanup"):
            ok, msg = lockfile.stop_running_agent(timeout=1)
        self.assertTrue(ok)
        self.assertEqual(sent[0], sigterm)
        self.assertIn(sigkill, sent)
        self.assertIn("force-killed", msg)


if __name__ == "__main__":
    unittest.main()
