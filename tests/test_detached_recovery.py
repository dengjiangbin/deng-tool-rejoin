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
    def test_build_detached_script_batches_force_stop_and_explicit_activity(self) -> None:
        script = android.build_detached_force_stop_relaunch_script(_PKG)
        shell = android.build_detached_force_stop_relaunch_shell(_PKG, root_tool="su")
        self.assertIn("#!/system/bin/sh", script)
        self.assertIn("am force-stop", script)
        self.assertIn("sleep 3.5", script)
        self.assertIn("cmd package resolve-activity --brief", script)
        self.assertIn("am start -n \"$LAUNCHER_ACT\"", script)
        self.assertNotIn("monkey", script)
        self.assertIn(_PKG, script)
        self.assertIn("su -c", shell)
        self.assertIn(f"/data/local/tmp/relaunch_{_PKG}.sh", shell)
        self.assertIn("setsid nohup sh", shell)
        self.assertIn("< /dev/null", shell)
        self.assertIn("&'", shell)

    def test_dispatch_detached_uses_spawn_detached_once(self) -> None:
        with patch("agent.android._write_detached_force_stop_relaunch_script", return_value=True), \
             patch("agent.subprocess_isolated.spawn_detached", return_value=True) as mock_spawn:
            ok = android.dispatch_detached_force_stop_relaunch(_PKG, root_tool="su")
        self.assertTrue(ok)
        mock_spawn.assert_called_once()
        args = mock_spawn.call_args.args[0]
        self.assertEqual(args[0], "sh")
        self.assertEqual(args[1], "-c")
        self.assertIn(f"/data/local/tmp/relaunch_{_PKG}.sh", args[2])

    def test_dispatch_writes_root_owned_tmp_script_before_detaching(self) -> None:
        with patch("agent.android.run_root_command") as root_cmd, \
             patch("agent.subprocess_isolated.spawn_detached", return_value=True):
            root_cmd.return_value = type("Res", (), {"ok": True})()
            ok = android.dispatch_detached_force_stop_relaunch(_PKG, root_tool="su")
        self.assertTrue(ok)
        write_args = root_cmd.call_args.args[0]
        self.assertEqual(write_args[:2], ["sh", "-c"])
        self.assertIn(f"/data/local/tmp/relaunch_{_PKG}.sh", write_args[2])
        self.assertIn("am force-stop", write_args[2])
        self.assertIn("cmd package resolve-activity --brief", write_args[2])
        self.assertIn("am start -n \"$LAUNCHER_ACT\"", write_args[2])
        self.assertNotIn("monkey", write_args[2])
        self.assertIn("chmod 700", write_args[2])

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
        self.assertIn("private_url_configured", src)
        self.assertIn("not url_configured", src)

    def test_perform_rejoin_app_only_recovery_uses_detached_dispatch(self) -> None:
        src = inspect.getsource(launcher.perform_rejoin)
        self.assertIn("dispatch_detached_force_stop_relaunch", src)
        self.assertIn("_DETACHED_RECOVERY_REASONS", src)


class NonBlockingRecoveryTests(unittest.TestCase):
    """probe p-765bbcc3d3: recovery must relaunch once and continue round-robin.

    The blocking recovery gate halted the whole round-robin and locked onto a
    single dead package in a perpetual "Launching" state instead of moving on to
    the next dead account.  Recovery is now non-blocking: a dead package is
    relaunched once and the watchdog advances to the next package.
    """

    def _supervisor(self):
        entry = {"package": _PKG, "account_username": "MainUser"}
        cfg = {
            "device_name": "TestPhone",
            "webhook_mode": "none",
            "webhook_enabled": False,
            "webhook_url": "",
            "roblox_packages": [entry],
        }
        return supervisor.WatchdogSupervisor([entry], cfg), entry

    def test_handle_state_dead_recovery_is_non_blocking(self) -> None:
        sup, entry = self._supervisor()
        with patch.object(sup, "_do_launch", return_value=True) as do_launch, \
             patch(
                 "agent.cache_clear_phases.run_recovery_cache_clear",
                 return_value={"success": True, "method": "test", "error": ""},
             ):
            result = sup._handle_state(
                _PKG, entry, supervisor.STATUS_DISCONNECTED,
                supervisor.STATUS_ONLINE, 0.0,
            )
        # No blocking recovery gate: _handle_state returns False so the
        # watchdog round-robin keeps moving to the next package.
        self.assertFalse(result)
        # The dead package is still relaunched exactly once (targeted).
        do_launch.assert_called_once()
        self.assertEqual(do_launch.call_args.args[0], _PKG)
        self.assertEqual(sup.status_map.get(_PKG), supervisor.STATUS_RELAUNCHING)

    def test_watchdog_loop_does_not_enter_blocking_recovery_gate(self) -> None:
        loop_src = inspect.getsource(supervisor.WatchdogSupervisor._run_watchdog_loop)
        # The loop must no longer halt the round-robin in the blocking gate.
        self.assertNotIn("_run_blocking_recovery_gate", loop_src)
        self.assertIn("[DENG_REJOIN_NONBLOCKING_RECOVERY]", loop_src)

    def test_handle_state_dead_branch_documents_non_blocking(self) -> None:
        src = inspect.getsource(supervisor.WatchdogSupervisor._handle_state)
        self.assertIn("[DENG_REJOIN_NONBLOCKING_RECOVERY]", src)


if __name__ == "__main__":
    unittest.main()
