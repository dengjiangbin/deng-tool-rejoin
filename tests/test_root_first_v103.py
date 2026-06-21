"""Regression tests for root-first v1.0.3 behavior."""

from __future__ import annotations

import unittest
from unittest import mock

from agent import launch_verify, package_username, probe, root_access, selftest


class RootPreflightTests(unittest.TestCase):
    def test_root_unavailable_fails_preflight(self) -> None:
        cap = root_access.RootCheckReport(
            ok=False,
            tool=None,
            uid="",
            whoami="",
            data_dir_readable=False,
            steps=(),
            detail="su not found",
            error="su not found",
        )
        with mock.patch("agent.root_access.root_check", return_value=cap):
            report = root_access.root_required_preflight()
        self.assertFalse(report.ok)
        self.assertIn("unsupported: root is required", report.public_error())

    def test_root_available_preflight_ok(self) -> None:
        cap = root_access.RootCheckReport(
            ok=True,
            tool="su",
            uid="uid=0(root)",
            whoami="root",
            data_dir_readable=True,
            steps=(),
            detail="ok",
        )
        with mock.patch("agent.root_access.root_check", return_value=cap):
            report = root_access.root_required_preflight()
        self.assertTrue(report.ok)


class UsernameRootScanTests(unittest.TestCase):
    def test_username_from_root_shared_prefs(self) -> None:
        pre = root_access.RootCheckReport(
            ok=True, tool="su", uid="uid=0(root)", whoami="root",
            data_dir_readable=True, steps=(), detail="ok",
        )
        xml = '<map><string name="username">JBDENG8</string></map>'
        with mock.patch("agent.package_username.root_access.root_required_preflight", return_value=pre), \
             mock.patch("agent.package_username.root_access.list_root_glob", return_value=["/data/data/com.moons.litesc/shared_prefs/prefs.xml"]), \
             mock.patch("agent.package_username.root_access.read_root_file", return_value=xml), \
             mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("", True, "")), \
             mock.patch("agent.android.get_application_label", return_value="Lite"), \
             mock.patch("agent.android.package_installed", return_value=True):
            report = package_username.scan_package_username_root("com.moons.litesc")
        self.assertEqual(report.username, "JBDENG8")
        self.assertEqual(report.source, "root_shared_prefs")
        self.assertTrue(report.root_used)

    def test_username_unknown_with_reason(self) -> None:
        pre = root_access.RootCheckReport(
            ok=True, tool="su", uid="uid=0(root)", whoami="root",
            data_dir_readable=True, steps=(), detail="ok",
        )
        with mock.patch("agent.package_username.root_access.root_required_preflight", return_value=pre), \
             mock.patch("agent.package_username.root_access.list_root_glob", return_value=[]), \
             mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("", True, "")), \
             mock.patch("agent.android.package_installed", return_value=True):
            report = package_username.scan_package_username_root("com.test.app")
        self.assertEqual(report.username, "")
        self.assertIn("no username key found", report.reason)


class LaunchVerifyRootTests(unittest.TestCase):
    def test_launch_success_from_root_process(self) -> None:
        fake = mock.Mock(ok=True, returncode=0, stdout="ok", stderr="", args=("su",))
        with mock.patch("agent.launch_verify.root_preflight_error", return_value=""), \
             mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("", True, "")), \
             mock.patch("agent.launch_verify.collect_process_evidence", return_value={"root_running": True}), \
             mock.patch("agent.launch_verify._foreground_lines", return_value=(None, "", "")), \
             mock.patch("agent.launch_verify._recent_logcat_for_package", return_value=[]):
            result = launch_verify.verify_launch(
                "com.test.app",
                launch_result=fake,
                wait_seconds=1.0,
                poll_interval=0.01,
            )
        self.assertTrue(result.success)

    def test_launch_failure_when_am_ok_but_no_root_proof(self) -> None:
        fake = mock.Mock(ok=True, returncode=0, stdout="ok", stderr="", args=("su",))
        with mock.patch("agent.launch_verify.root_preflight_error", return_value=""), \
             mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("", True, "")), \
             mock.patch("agent.launch_verify.collect_process_evidence", return_value={"root_running": False}), \
             mock.patch("agent.launch_verify._foreground_lines", return_value=(None, "", "")), \
             mock.patch("agent.launch_verify._recent_logcat_for_package", return_value=[]):
            result = launch_verify.verify_launch(
                "com.test.app",
                launch_result=fake,
                wait_seconds=0.5,
                poll_interval=0.01,
            )
        self.assertFalse(result.success)
        self.assertEqual(result.failure_reason, "launch_accepted_but_not_alive")

    def test_crash_detection_from_logcat(self) -> None:
        fake = mock.Mock(ok=True, returncode=0, stdout="ok", stderr="", args=("su",))
        with mock.patch("agent.launch_verify.root_preflight_error", return_value=""), \
             mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("", True, "")), \
             mock.patch("agent.launch_verify.collect_process_evidence", return_value={"root_running": False}), \
             mock.patch("agent.launch_verify._foreground_lines", return_value=(None, "", "")), \
             mock.patch(
                 "agent.launch_verify._recent_logcat_for_package",
                 return_value=["FATAL EXCEPTION: main Process: com.test.app"],
             ):
            result = launch_verify.verify_launch(
                "com.test.app",
                launch_result=fake,
                wait_seconds=0.5,
                poll_interval=0.01,
            )
        self.assertFalse(result.success)
        self.assertEqual(result.failure_reason, "launched_then_crashed")


class ProbeSummaryTests(unittest.TestCase):
    def test_probe_summary_contains_useful_fields(self) -> None:
        out = {
            "build": {"product_version": "1.0.3", "artifact_sha256_short": "abc123"},
            "device": {"root": {"available": True}},
            "package_menu_diagnostics": [
                {"display_username": "JBDENG8"},
            ],
            "errors": [],
        }
        summary = probe._build_probe_summary(out, last_command="selftest")
        self.assertEqual(summary["product_version"], "1.0.3")
        self.assertTrue(summary["root_required_mode"])
        self.assertEqual(summary["usernames_found"], 1)


class SelftestTests(unittest.TestCase):
    def test_selftest_upload_prints_url(self) -> None:
        pre = root_access.RootCheckReport(
            ok=True, tool="su", uid="uid=0(root)", whoami="root",
            data_dir_readable=True, steps=(), detail="ok",
        )
        scan = package_username.UsernameScanReport(
            package="com.moons.litesc",
            username="JBDENG8",
            source="root_shared_prefs",
            supported=True,
            reason="",
            root_used=True,
            confidence="high",
            root_read_status="ok",
        )
        verification = launch_verify.LaunchVerificationResult(
            package="com.moons.litesc",
            success=True,
        )
        with mock.patch("agent.selftest.load_config", return_value={}), \
             mock.patch("agent.selftest.root_access.root_required_preflight", return_value=pre), \
             mock.patch("agent.selftest.package_username.scan_package_username_root", return_value=scan), \
             mock.patch("agent.selftest.launch_verify.doctor_package_report", return_value=["ok"]), \
             mock.patch("agent.selftest.launch_verify.launch_package_root", return_value=(mock.Mock(ok=True), "root_monkey")), \
             mock.patch("agent.selftest.launch_verify.verify_launch", return_value=verification), \
             mock.patch("agent.selftest.os.name", "linux"), \
             mock.patch("agent.selftest.build_info.collect_version_info", return_value={"product_version": "1.0.3"}), \
             mock.patch("agent.probe.collect_probe", return_value={"summary": {}}), \
             mock.patch("agent.probe.upload_probe", return_value=(True, "p-test123")):
            result = selftest.run_selftest(package="com.moons.litesc", upload=True)
        self.assertEqual(result.probe_id, "p-test123")
        self.assertIn("p-test123", result.probe_url)


if __name__ == "__main__":
    unittest.main()
