"""Tests for detached recovery command batching."""

from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import android, launcher, supervisor


_PKG = "com.moons.litesc"


class TestDetachedRecovery(unittest.TestCase):
    def test_build_detached_shell_batches_force_stop_and_monkey(self) -> None:
        shell = android.build_detached_force_stop_relaunch_shell(_PKG, root_tool="su")
        self.assertIn("nohup", shell)
        self.assertIn("am force-stop", shell)
        self.assertIn("sleep", shell)
        self.assertIn("monkey -p", shell)
        self.assertIn(_PKG, shell)
        self.assertIn("su -mm -c", shell)
        self.assertIn("|| su -c", shell)
        self.assertTrue(shell.rstrip().endswith("&"))

    def test_dispatch_detached_uses_spawn_detached_once(self) -> None:
        with patch("agent.subprocess_isolated.spawn_detached", return_value=True) as mock_spawn:
            ok = android.dispatch_detached_force_stop_relaunch(_PKG, root_tool="su")
        self.assertTrue(ok)
        mock_spawn.assert_called_once()
        args = mock_spawn.call_args.args[0]
        self.assertEqual(args[0], "sh")
        self.assertEqual(args[1], "-c")
        self.assertIn("nohup", args[2])

    def test_mount_master_root_tries_mm_before_fallback(self) -> None:
        calls: list[list[str]] = []

        def _fake_run(args, **kwargs):
            calls.append(list(args))
            if len(args) >= 2 and args[1] == "-mm":
                return type("Res", (), {"ok": False, "stdout": "", "stderr": "fail", "returncode": 1})()
            return type("Res", (), {"ok": True, "stdout": "ok", "stderr": "", "returncode": 0})()

        with patch("agent.android.run_command", side_effect=_fake_run):
            res = android.run_mount_master_root_command(["echo", "test"], root_tool="su")
        self.assertTrue(res.ok)
        self.assertTrue(any(cmd[:2] == ["su", "-mm"] for cmd in calls))
        self.assertTrue(any(cmd[:2] == ["su", "-c"] for cmd in calls))

    def test_recovery_gate_cycle_prefers_detached_dispatch(self) -> None:
        src = inspect.getsource(supervisor.WatchdogSupervisor._deploy_gate_recovery_cycle)
        self.assertIn("dispatch_detached_force_stop_relaunch", src)
        self.assertIn("[DENG_REJOIN_RECOVERY_DETACHED_DISPATCH]", src)

    def test_perform_rejoin_app_only_recovery_uses_detached_dispatch(self) -> None:
        src = inspect.getsource(launcher.perform_rejoin)
        self.assertIn("dispatch_detached_force_stop_relaunch", src)
        self.assertIn("_DETACHED_RECOVERY_REASONS", src)


if __name__ == "__main__":
    unittest.main()
