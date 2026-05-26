from __future__ import annotations

import unittest
from unittest import mock

from agent import android
from agent.config import default_config
from agent.launcher import perform_rejoin


class LaunchTraceBlackScreenTests(unittest.TestCase):
    def _cfg(self) -> dict:
        cfg = default_config()
        cfg["first_setup_completed"] = True
        cfg["roblox_package"] = "com.moons.litesc"
        cfg["roblox_packages"] = [{
            "package": "com.moons.litesc",
            "account_username": "user1",
            "enabled": True,
            "username_source": "manual",
            "private_server_url": "",
        }]
        cfg["launch_wait_process_sec"] = 0.5
        cfg["launch_wait_activity_sec"] = 0.5
        cfg["launch_settle_before_layout_sec"] = 0
        return cfg

    def test_launch_trace_logged_for_app_only_success(self) -> None:
        import agent.launcher as launcher
        cfg = self._cfg()
        events: list[tuple[str, dict]] = []
        with mock.patch.object(android, "package_installed", return_value=True), \
             mock.patch.object(android, "force_stop_package", return_value=android.CommandResult(("am", "force-stop"), 0, "", "")), \
             mock.patch.object(android, "launch_package_with_bounds", return_value=(android.CommandResult(("am", "start", "-p", "com.moons.litesc"), 0, "OK", ""), "am_bounds_mode5")), \
             mock.patch.object(launcher, "_read_launch_state", return_value={"process_alive": True, "activity_visible": True, "surface_present": True, "black_screen_suspected": False}), \
             mock.patch.object(launcher, "log_event", side_effect=lambda _logger, _level, event, **kw: events.append((event, kw))), \
             mock.patch("agent.launcher.db"), \
             mock.patch("agent.launcher.time.sleep"):
            result = perform_rejoin(cfg, reason="start")
        self.assertTrue(result.success)
        trace = [kw for event, kw in events if event == "[DENG_REJOIN_LAUNCH_TRACE]"]
        self.assertTrue(trace)
        self.assertEqual(trace[-1]["launch_type"], "app_only")
        self.assertEqual(trace[-1]["process_alive"], "true")
        self.assertEqual(trace[-1]["surface_present"], "true")

    def test_launch_success_without_process_returns_clean_failure(self) -> None:
        import agent.launcher as launcher
        cfg = self._cfg()
        with mock.patch.object(android, "package_installed", return_value=True), \
             mock.patch.object(android, "force_stop_package", return_value=android.CommandResult(("am", "force-stop"), 0, "", "")), \
             mock.patch.object(android, "launch_package_with_bounds", return_value=(android.CommandResult(("am", "start"), 0, "OK", ""), "am_bounds_mode5")), \
             mock.patch.object(launcher, "_read_launch_state", return_value={"process_alive": False, "activity_visible": False, "surface_present": False, "black_screen_suspected": False}), \
             mock.patch("agent.launcher.db"), \
             mock.patch("agent.launcher.time.sleep"):
            result = perform_rejoin(cfg, reason="start")
        self.assertFalse(result.success)
        self.assertIn("process was not detected", result.error or "")

    def test_global_url_and_separate_blank_modes_preserved(self) -> None:
        import agent.launcher as launcher
        calls: list[tuple[str, str | None]] = []
        cfg = self._cfg()
        cfg["private_url_mode"] = "global"
        cfg["private_server_url"] = "roblox://placeId=GLOBAL"

        def fake_launch(pkg: str, rect, url: str | None = None):
            calls.append((pkg, url))
            return android.CommandResult(("am", "start"), 0, "OK", ""), "am_bounds_mode5"

        with mock.patch.object(android, "package_installed", return_value=True), \
             mock.patch.object(android, "force_stop_package", return_value=android.CommandResult(("am", "force-stop"), 0, "", "")), \
             mock.patch.object(android, "launch_package_with_bounds", side_effect=fake_launch), \
             mock.patch.object(launcher, "_read_launch_state", return_value={"process_alive": True, "activity_visible": True, "surface_present": True, "black_screen_suspected": False}), \
             mock.patch("agent.launcher.db"), \
             mock.patch("agent.launcher.time.sleep"):
            perform_rejoin(cfg, reason="start", package_entry=cfg["roblox_packages"][0])
        self.assertTrue(any("GLOBAL" in str(url) for _pkg, url in calls))

        calls.clear()
        cfg["private_url_mode"] = "separate"
        cfg["private_server_url"] = "roblox://GLOBAL"
        cfg["roblox_packages"][0]["private_server_url"] = ""
        with mock.patch.object(android, "package_installed", return_value=True), \
             mock.patch.object(android, "force_stop_package", return_value=android.CommandResult(("am", "force-stop"), 0, "", "")), \
             mock.patch.object(android, "launch_package_with_bounds", side_effect=fake_launch), \
             mock.patch.object(launcher, "_read_launch_state", return_value={"process_alive": True, "activity_visible": True, "surface_present": True, "black_screen_suspected": False}), \
             mock.patch("agent.launcher.db"), \
             mock.patch("agent.launcher.time.sleep"):
            perform_rejoin(cfg, reason="start", package_entry=cfg["roblox_packages"][0])
        self.assertEqual(calls[-1][1], None)


if __name__ == "__main__":
    unittest.main()
