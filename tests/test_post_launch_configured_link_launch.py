from __future__ import annotations

import shlex
import threading
import time
import unittest
from unittest import mock

from agent import android
from agent.config import (
    default_config,
    effective_private_server_url,
    validate_config,
)
from agent.supervisor import _PackageWorker, STATUS_LAUNCHING


class AndroidConfiguredLinkLaunchTests(unittest.TestCase):
    def test_open_configured_link_uses_root_package_view_intent(self) -> None:
        captured: dict[str, object] = {}

        def fake_root(args, *, root_tool=None, timeout=None):
            captured["args"] = list(args)
            captured["root_tool"] = root_tool
            return android.CommandResult(("su", "-c", shlex.join(args)), 0, "Status: ok", "")

        with mock.patch.object(android, "detect_root", return_value=android.RootInfo(True, "su", "uid=0")), \
             mock.patch.object(android, "run_root_command", side_effect=fake_root):
            result, method = android.launch_package_with_options(
                "com.moons.litesc",
                "https://www.roblox.com/share?code=abc&type=Server",
            )

        self.assertTrue(result.ok)
        self.assertEqual(method, "root_am_view_package")
        args = captured["args"]
        self.assertEqual(captured["root_tool"], "su")
        self.assertIn("-W", args)
        self.assertEqual(args[args.index("-a") + 1], "android.intent.action.VIEW")
        self.assertEqual(args[args.index("-p") + 1], "com.moons.litesc")
        self.assertEqual(args[args.index("-d") + 1], "roblox://navigation/share_links?code=abc&type=Server")

    def test_url_query_ampersand_is_preserved_and_shell_quoted(self) -> None:
        captured: list[list[str]] = []

        def fake_run(args, *, timeout=None):
            captured.append(list(args))
            return android.CommandResult(tuple(args), 0, "Status: ok", "")

        with mock.patch.object(android, "detect_root", return_value=android.RootInfo(True, "su", "uid=0")), \
             mock.patch.object(android, "run_command", side_effect=fake_run):
            result = android.launch_url(
                "com.moons.litesc",
                "https://www.roblox.com/share?code=abc&type=Server",
                "web_url",
            )

        self.assertTrue(result.ok)
        su_call = captured[-1]
        self.assertEqual(su_call[:2], ["su", "-c"])
        command = su_call[2]
        self.assertIn("roblox://navigation/share_links?code=abc&type=Server", command)
        self.assertIn(shlex.quote("roblox://navigation/share_links?code=abc&type=Server"), command)
        self.assertIn(" -p com.moons.litesc", command)

    def test_open_app_does_not_include_url(self) -> None:
        captured: list[list[str]] = []

        def fake_run(args, *, timeout=None):
            captured.append(list(args))
            return android.CommandResult(tuple(args), 0, "Starting", "")

        with mock.patch.object(android, "_find_command", return_value="am"), \
             mock.patch.object(android, "run_command", side_effect=fake_run):
            result, method = android.launch_package_with_options("com.moons.litesc", None)

        self.assertTrue(result.ok)
        self.assertEqual(method, "am_or_resolve")
        flat = [str(part) for call in captured for part in call]
        self.assertNotIn("-d", flat)
        self.assertFalse(any("roblox://" in part or "roblox.com" in part for part in flat))

    def test_invalid_url_is_rejected_without_app_fallback(self) -> None:
        with mock.patch.object(android, "launch_app") as launch_app:
            result, method = android.launch_package_with_options("com.moons.litesc", "not a url")
        self.assertFalse(result.ok)
        self.assertEqual(method, "invalid_url")
        launch_app.assert_not_called()


class ConfiguredLinkMigrationTests(unittest.TestCase):
    def test_legacy_launch_url_migrates_and_is_canonical_url(self) -> None:
        cfg = default_config()
        cfg["launch_mode"] = "web_url"
        cfg["launch_url"] = "https://www.roblox.com/share?code=abc&type=Server"
        cfg["private_server_url"] = ""
        validated = validate_config(cfg)
        entry = validated["roblox_packages"][0]
        self.assertNotIn("post" + "_launch_action", validated)
        self.assertEqual(
            effective_private_server_url(entry, validated),
            "https://www.roblox.com/share?code=abc&type=Server",
        )


class SupervisorConfiguredLinkGraceTests(unittest.TestCase):
    def _worker(self) -> _PackageWorker:
        entry = {
            "package": "com.moons.litesc",
            "enabled": True,
            "private_server_url": "https://www.roblox.com/share?code=abc&type=Server",
            "auto_reopen_enabled": True,
            "auto_reconnect_enabled": True,
        }
        cfg = {
            "supervisor": {"enabled": True},
        }
        worker = _PackageWorker(entry, cfg, {"com.moons.litesc": STATUS_LAUNCHING}, threading.Event())
        worker.has_private_url = True
        return worker

    def test_supervisor_blocks_immediate_relaunch_during_link_grace(self) -> None:
        worker = self._worker()
        with mock.patch("agent.supervisor.log_event"):
            worker._note_url_launch_grace("configured_link_launch", now=100.0, grace_seconds=60)
            self.assertFalse(worker._relaunch_allowed("process_missing", now=110.0))

    def test_supervisor_allows_relaunch_after_link_grace_timeout(self) -> None:
        worker = self._worker()
        with mock.patch("agent.supervisor.log_event"):
            worker._note_url_launch_grace("configured_link_launch", now=100.0, grace_seconds=10)
            self.assertTrue(worker._relaunch_allowed("process_missing", now=111.0))

    def test_force_stop_targets_only_selected_package_in_rejoin_path(self) -> None:
        import agent.launcher as launcher

        cfg = validate_config({
            **default_config(),
            "first_setup_completed": True,
            "private_server_url": "https://www.roblox.com/share?code=abc&type=Server",
            "roblox_packages": [{
                "package": "com.moons.litesc",
                "enabled": True,
                "account_username": "User",
                "username_source": "manual",
                "private_server_url": "",
            }],
        })
        stopped: list[str] = []

        def fake_stop(package, root_info=None):
            stopped.append(package)
            return android.CommandResult(("su", "-c", f"am force-stop {package}"), 0, "", "")

        with mock.patch.object(android, "package_installed", return_value=True), \
             mock.patch.object(android, "force_stop_package", side_effect=fake_stop), \
             mock.patch.object(android, "launch_package_with_options", return_value=(
                 android.CommandResult(("su", "-c", "am start"), 0, "Status: ok", ""),
                 "root_am_view_package",
             )), \
             mock.patch.object(launcher, "_proc_scan_alive", return_value=True):
            result = launcher.perform_rejoin(cfg, reason="start")

        self.assertTrue(result.success)
        self.assertEqual(stopped, ["com.moons.litesc"])


if __name__ == "__main__":
    unittest.main()
