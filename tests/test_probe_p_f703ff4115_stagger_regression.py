"""Regression: staggered Start must not block; UI must reflect hot-lane (p-f703ff4115, p-492231a805)."""

from __future__ import annotations

import inspect
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import launcher
from agent.android import CommandResult


def _cfg() -> dict:
    return {
        "roblox_package": "com.moons.litesc",
        "roblox_packages": [{"package": "com.moons.litesc", "enabled": True}],
        "launch_mode": "app",
        "launch_url": "",
        "root_mode_enabled": True,
        "reconnect_delay_seconds": 8,
        "log_level": "INFO",
    }


class TestStartStaggerFastLaunch(unittest.TestCase):
    def test_start_reason_uses_fast_stagger_path(self) -> None:
        src = inspect.getsource(launcher.perform_rejoin)
        self.assertIn('start_stagger_fast = reason == "start"', src)
        self.assertIn("_start_stagger_launch_settle", src)
        self.assertIn("[DENG_REJOIN_START_STAGGER_FAST]", src)
        self.assertNotIn("_wait_for_start_stagger_launch_ready", src)

    def test_start_stagger_settle_has_no_dumpsys_poll(self) -> None:
        src = inspect.getsource(launcher._start_stagger_launch_settle)
        self.assertNotIn("_read_launch_state", src)
        self.assertNotIn("run_command", src)

    def test_start_perform_rejoin_skips_heavy_verify_launch(self) -> None:
        cfg = _cfg()
        called: list[str] = []

        def _on_sent(pkg: str) -> None:
            called.append(pkg)

        cfg["__on_launch_sent"] = _on_sent
        launch_ok = (CommandResult(("am", "start"), 0, "OK", ""), "am_bounds_mode5")
        slow_verify = patch(
            "agent.launch_verify.verify_launch",
            side_effect=AssertionError("verify_launch must not run for reason=start"),
        )
        slow_ready = patch(
            "agent.launcher._wait_for_launch_ready",
            side_effect=AssertionError("_wait_for_launch_ready must not run for reason=start"),
        )
        dumpsys_read = patch(
            "agent.launcher._read_launch_state",
            side_effect=AssertionError("_read_launch_state must not run for reason=start"),
        )
        with patch("agent.launch_verify.root_preflight_error", return_value=None), \
             patch.object(launcher.android, "package_installed", return_value=True), \
             patch.object(launcher.android, "force_stop_package", return_value=CommandResult(("am",), 0, "", "")), \
             patch.object(launcher.android, "launch_package_with_bounds", return_value=launch_ok), \
             patch.object(launcher.android, "launch_package_with_options", return_value=launch_ok), \
             slow_verify, slow_ready, dumpsys_read:
            result = launcher.perform_rejoin(cfg, reason="start")
        self.assertTrue(result.success)
        self.assertEqual(called, ["com.moons.litesc"])

    def test_render_prefers_hot_lane_for_opened_packages(self) -> None:
        import agent.commands as commands

        src = inspect.getsource(commands.cmd_start)
        self.assertIn("def _phase_table_state(pkg: str)", src)
        self.assertIn("sync_stagger_display_status", src)
        self.assertIn("__on_launch_sent", src)
        self.assertIn("finally:", src)
        self.assertIn("mark_all_launches_completed", src)

    def test_supervisor_sync_stagger_display_status(self) -> None:
        from agent.supervisor import STATUS_ONLINE, WatchdogSupervisor
        from agent.rjn_lifecycle_monitor import STATE_ONLINE_CONFIRMED, PackageRjnState

        sup = WatchdogSupervisor(
            [{"package": "com.moons.litesc"}, {"package": "com.moons.litesd"}],
            {"roblox_packages": [{"package": "com.moons.litesc"}, {"package": "com.moons.litesd"}]},
        )
        sup._package_opened.add("com.moons.litesc")
        row = PackageRjnState(package="com.moons.litesc")
        row.internal_state = STATE_ONLINE_CONFIRMED
        row.last_ingame_hb_wall_at = time.time()
        row.ingame_hb_ever = True
        sup._rjn_monitor._states["com.moons.litesc"] = row
        with patch.object(sup, "_push_fresh", return_value=True), patch.object(
            sup,
            "_detect_android_package_state",
            side_effect=AssertionError("slow detect must not run during stagger sync"),
        ):
            sup.sync_stagger_display_status()
        self.assertEqual(sup.status_map.get("com.moons.litesc"), STATUS_ONLINE)

    def test_stagger_recovery_deferred_until_all_launches_completed(self) -> None:
        from agent.supervisor import STATUS_DEAD, WatchdogSupervisor

        sup = WatchdogSupervisor([{"package": "com.moons.litesc"}], {})
        sup._all_launches_completed = False
        with patch.object(sup, "_do_launch") as launch:
            sup._handle_state(
                "com.moons.litesc",
                {"package": "com.moons.litesc"},
                STATUS_DEAD,
                "Online",
                time.time(),
            )
        launch.assert_not_called()

    def test_start_prep_keeps_google_and_restores_background_stop(self) -> None:
        import agent.android as android
        import agent.commands as commands

        src = inspect.getsource(commands.cmd_start)
        self.assertIn("force_stop_packages_except", src)
        self.assertIn("trim_page_cache_after_mass_clear", src)
        self.assertIn("_fast_force_stop_selected_packages", src)
        self.assertIn("disable_google_packages", inspect.getsource(android.optimize_cloud_phone_memory))


if __name__ == "__main__":
    unittest.main()
