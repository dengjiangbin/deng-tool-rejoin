"""Tests for launch verification and honest username scan reporting."""

from __future__ import annotations

import unittest
from unittest import mock

from agent import launch_verify, package_username


class LaunchVerifyTests(unittest.TestCase):
    def test_not_launchable_when_no_launcher(self) -> None:
        with mock.patch("agent.launch_verify.android.package_installed", return_value=True), \
             mock.patch("agent.launch_verify.android.is_launchable_package", return_value=False):
            result = launch_verify.verify_launch("com.test.nolaunch")
        self.assertFalse(result.success)
        self.assertIn("not launchable", result.failure_reason)

    def test_success_when_root_running_detected(self) -> None:
        fake_result = mock.Mock(ok=True, returncode=0, stdout="Starting", stderr="", args=("am", "start"))
        with mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("com.test/.Main", True, "")), \
             mock.patch("agent.launch_verify.collect_process_evidence", return_value={
                 "running": False,
                 "root_running": True,
                 "proc_scan": "1234",
             }), \
             mock.patch("agent.launch_verify._foreground_lines", return_value=(None, "", "")), \
             mock.patch("agent.launch_verify._recent_logcat_for_package", return_value=[]):
            result = launch_verify.verify_launch(
                "com.test.app",
                launch_result=fake_result,
                launch_method="am_or_resolve",
                wait_seconds=1.0,
                poll_interval=0.01,
            )
        self.assertTrue(result.success)

    def test_failure_includes_evidence_when_am_ok_but_no_process(self) -> None:
        fake_result = mock.Mock(ok=True, returncode=0, stdout="ok", stderr="", args=("am", "start"))
        with mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("", True, "")), \
             mock.patch("agent.launch_verify.collect_process_evidence", return_value={
                 "running": False,
                 "root_running": False,
                 "proc_scan": "rc=1",
                 "pidof": "rc=1",
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
        self.assertIn("process was not detected", result.failure_reason)
        self.assertIn("launch_rc: 0", result.failure_message())


class UsernameScanTests(unittest.TestCase):
    def test_manual_mapping_source(self) -> None:
        cfg = {
            "roblox_packages": [{
                "package": "com.moons.litesc",
                "account_username": "JBDENG8",
                "username_source": "manual",
            }],
            "package_username_cache": {},
        }
        report = package_username.scan_package_username("com.moons.litesc", cfg)
        self.assertEqual(report.username, "JBDENG8")
        self.assertEqual(report.source, "manual")

    def test_auto_detected_pref_source(self) -> None:
        with mock.patch("agent.package_username.detect_package_username_quick") as det:
            det.return_value = package_username.PackageUsernameResult(
                "autouser", "detected_safe_pref", True, 5,
            )
            with mock.patch("agent.package_username.root_access.detect") as rd:
                rd.return_value = mock.Mock(available=True, detail="ok")
                report = package_username.scan_package_username("com.test.app", {})
        self.assertEqual(report.username, "autouser")
        self.assertEqual(report.source, "detected_safe_pref")

    def test_unknown_without_root_is_honest(self) -> None:
        with mock.patch("agent.package_username.root_access.detect") as rd:
            rd.return_value = mock.Mock(available=False, detail="su not found")
            report = package_username.scan_package_username("com.test.app", {})
        self.assertEqual(report.username, "")
        self.assertEqual(report.source, "unknown")
        self.assertIn("username unavailable", report.reason)


if __name__ == "__main__":
    unittest.main()
