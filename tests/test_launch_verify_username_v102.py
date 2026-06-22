"""Tests for launch verification and honest username scan reporting."""

from __future__ import annotations

import unittest
from unittest import mock

from agent import launch_verify, package_username, root_access


class LaunchVerifyTests(unittest.TestCase):
    def test_not_launchable_when_no_launcher(self) -> None:
        with mock.patch("agent.launch_verify.root_preflight_error", return_value=""), \
             mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("", False, "not launchable")):
            result = launch_verify.verify_launch("com.test.nolaunch")
        self.assertFalse(result.success)
        self.assertIn("not launchable", result.failure_reason)

    def test_success_when_root_running_detected(self) -> None:
        fake_result = mock.Mock(ok=True, returncode=0, stdout="Starting", stderr="", args=("su", "start"))
        with mock.patch("agent.launch_verify.root_preflight_error", return_value=""), \
             mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("com.test/.Main", True, "")), \
             mock.patch("agent.launch_verify.collect_process_evidence", return_value={
                 "root_running": True,
             }), \
             mock.patch("agent.launch_verify._foreground_lines", return_value=(None, "", "")), \
             mock.patch("agent.launch_verify._recent_logcat_for_package", return_value=[]):
            result = launch_verify.verify_launch(
                "com.test.app",
                launch_result=fake_result,
                launch_method="root_monkey",
                wait_seconds=1.0,
                poll_interval=0.01,
            )
        self.assertTrue(result.success)

    def test_failure_includes_evidence_when_am_ok_but_no_process(self) -> None:
        fake_result = mock.Mock(ok=True, returncode=0, stdout="ok", stderr="", args=("su", "start"))
        with mock.patch("agent.launch_verify.root_preflight_error", return_value=""), \
             mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("", True, "")), \
             mock.patch("agent.launch_verify.collect_process_evidence", return_value={
                 "root_running": False,
             }), \
             mock.patch("agent.launch_verify._foreground_lines", return_value=(None, "", "")), \
             mock.patch("agent.launch_verify._recent_logcat_for_package", return_value=[]):
            result = launch_verify.verify_launch(
                "com.test.app",
                launch_result=fake_result,
                wait_seconds=0.5,
                poll_interval=0.01,
            )
        self.assertFalse(result.success)
        self.assertEqual(result.failure_reason, "launch_accepted_but_not_alive")
        self.assertIn("launch_rc: 0", result.failure_message())


class UsernameScanTests(unittest.TestCase):
    def test_manual_mapping_does_not_override_root_scan(self) -> None:
        cfg = {
            "roblox_packages": [{
                "package": "com.moons.litesc",
                "account_username": "JBDENG8",
                "username_source": "manual",
            }],
            "package_username_cache": {},
        }
        with mock.patch("agent.package_username.scan_package_username_root") as scan_fn:
            scan_fn.return_value = package_username.UsernameScanReport(
                package="com.moons.litesc",
                username="REALUSER",
                source="root_shared_prefs",
                supported=True,
                reason="",
                root_used=True,
            )
            report = package_username.scan_package_username("com.moons.litesc", cfg)
        self.assertEqual(report.username, "REALUSER")
        self.assertEqual(report.source, "root_shared_prefs")

    def test_auto_detected_pref_source(self) -> None:
        pre = root_access.RootCheckReport(
            ok=True, tool="su", uid="uid=0(root)", whoami="root",
            data_dir_readable=True, steps=(), detail="ok",
        )
        with mock.patch("agent.package_username.scan_package_username_root") as scan_fn:
            scan_fn.return_value = package_username.UsernameScanReport(
                package="com.test.app",
                username="autouser",
                source="root_shared_prefs",
                supported=True,
                reason="",
                root_used=True,
            )
            report = package_username.scan_package_username("com.test.app", {})
        self.assertEqual(report.username, "autouser")
        self.assertEqual(report.source, "root_shared_prefs")

    def test_unknown_without_root_is_honest(self) -> None:
        pre = root_access.RootCheckReport(
            ok=False, tool=None, uid="", whoami="", data_dir_readable=False,
            steps=(), detail="su not found", error="su not found",
        )
        with mock.patch("agent.package_username.scan_package_username_root") as scan_fn:
            scan_fn.return_value = package_username.UsernameScanReport(
                package="com.test.app",
                username="",
                source="unknown",
                supported=False,
                reason=pre.public_error(),
                root_used=False,
            )
            report = package_username.scan_package_username("com.test.app", {})
        self.assertEqual(report.username, "")
        self.assertIn("unsupported: root is required", report.reason)


if __name__ == "__main__":
    unittest.main()
