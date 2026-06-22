"""Regression tests for root state scanner hotfix (user-facing 1.0.0)."""

from __future__ import annotations

import time
import unittest
from unittest import mock

from agent import constants, package_state, package_username, root_access


class PackageStateScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._pre = root_access.RootCheckReport(
            ok=True, tool="su", uid="uid=0(root)", whoami="root",
            data_dir_readable=True, steps=(), detail="ok",
        )
        self._preflight = mock.patch(
            "agent.package_state.root_access.root_required_preflight",
            return_value=self._pre,
        )
        self._preflight.start()

    def tearDown(self) -> None:
        self._preflight.stop()
        package_state._launch_meta.clear()
    def test_all_packages_scanned_independently(self) -> None:
        def _fake_scan(pkg: str, **kwargs):  # noqa: ANN003
            if pkg == "com.a":
                return package_state.PackageStateRow(
                    pkg, package_state.STATE_ONLINE, True, True, "alive"
                )
            return package_state.PackageStateRow(
                pkg, package_state.STATE_OFFLINE, False, False, "dead"
            )

        with mock.patch("agent.package_state.scan_package_state_root", side_effect=_fake_scan):
            out = package_state.scan_all_package_states_root(["com.a", "com.b"])
        self.assertEqual(out["com.a"].state, package_state.STATE_ONLINE)
        self.assertEqual(out["com.b"].state, package_state.STATE_OFFLINE)

    def test_selected_online_does_not_make_others_online(self) -> None:
        pe_online = {"root_running": True, "foreground": True, "root_pidof": "123"}
        pe_offline = {"root_running": False, "foreground": False}
        with mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("", True, "")), \
             mock.patch("agent.launch_verify.collect_process_evidence") as cpe, \
             mock.patch("agent.package_username.username_display_for_package") as udisp:
            udisp.side_effect = lambda pkg, **kwargs: package_username.UsernameDisplayRow(
                pkg,
                "UserA" if pkg == "com.a" else package_username.NO_ACCOUNT_LABEL,
                "logged_in" if pkg == "com.a" else "no_account",
                "root_shared_prefs" if pkg == "com.a" else "root_scan_no_account",
            )
            cpe.side_effect = lambda pkg: pe_online if pkg == "com.a" else pe_offline
            rows = package_state.scan_all_package_states_root(["com.a", "com.b"])
        self.assertEqual(rows["com.a"].state, package_state.STATE_ONLINE)
        self.assertEqual(rows["com.b"].state, package_state.STATE_NO_ACCOUNT)

    def test_launching_ttl_expires_to_offline(self) -> None:
        pkg = "com.test.app"
        meta = package_state.get_launch_meta(pkg)
        meta.last_launch_at = time.time() - 40
        meta.launch_lock_until = time.time() - 5
        with mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("", True, "")), \
             mock.patch("agent.launch_verify.collect_process_evidence", return_value={"root_running": False, "foreground": False}), \
             mock.patch("agent.package_username.username_display_for_package") as udisp:
            udisp.return_value = package_username.UsernameDisplayRow(
                pkg, "No Account", "no_account", "root_scan_no_account"
            )
            row = package_state.scan_package_state_root(pkg, meta=meta)
        self.assertEqual(row.state, package_state.STATE_NO_ACCOUNT)

    def test_killed_package_clears_launching_lock(self) -> None:
        pkg = "com.test.app"
        package_state.record_launch_attempt(pkg, command="am start", rc=0, ok=True)
        row = package_state.PackageStateRow(
            pkg, package_state.STATE_OFFLINE, False, False, "process_gone"
        )
        self.assertFalse(package_state.launch_lock_blocks_relaunch(pkg, row=row))

    def test_relaunch_allowed_after_kill(self) -> None:
        pkg = "com.test.app"
        package_state.record_launch_attempt(pkg, command="am start", rc=0, ok=True)
        self.assertTrue(package_state.get_launch_meta(pkg).launch_lock_until > 0)
        package_state.clear_launch_lock(pkg, "killed")
        self.assertFalse(package_state.launch_lock_blocks_relaunch(pkg))

    def test_am_ok_without_root_proof_not_online(self) -> None:
        with mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("", True, "")), \
             mock.patch(
                 "agent.launch_verify.collect_process_evidence",
                 return_value={"root_running": False, "foreground": False},
             ), \
             mock.patch("agent.package_username.username_display_for_package") as udisp:
            udisp.return_value = package_username.UsernameDisplayRow(
                "com.test.app", "No Account", "no_account", "root_scan_no_account"
            )
            row = package_state.scan_package_state_root("com.test.app")
        self.assertNotEqual(row.state, package_state.STATE_ONLINE)

    def test_root_process_alive_is_online(self) -> None:
        with mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("", True, "")), \
             mock.patch(
                 "agent.launch_verify.collect_process_evidence",
                 return_value={"root_running": True, "foreground": False, "root_pidof": "999"},
             ):
            row = package_state.scan_package_state_root("com.test.app")
        self.assertEqual(row.state, package_state.STATE_ONLINE)

    def test_foreground_target_is_online(self) -> None:
        with mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("", True, "")), \
             mock.patch(
                 "agent.launch_verify.collect_process_evidence",
                 return_value={"root_running": False, "foreground": True, "window_line": "focus com.test.app"},
             ):
            row = package_state.scan_package_state_root("com.test.app")
        self.assertEqual(row.state, package_state.STATE_ONLINE)


class UsernameDisplayTests(unittest.TestCase):
    def test_username_from_root_prefs(self) -> None:
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
            row = package_username.username_display_for_package("com.moons.litesc")
        self.assertEqual(row.username_display, "JBDENG8")
        self.assertEqual(row.account_status, "logged_in")

    def test_no_account_when_no_profile(self) -> None:
        pre = root_access.RootCheckReport(
            ok=True, tool="su", uid="uid=0(root)", whoami="root",
            data_dir_readable=True, steps=(), detail="ok",
        )
        with mock.patch("agent.package_username.root_access.root_required_preflight", return_value=pre), \
             mock.patch("agent.package_username.root_access.list_root_glob", return_value=[]), \
             mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("", True, "")), \
             mock.patch("agent.android.package_installed", return_value=True):
            row = package_username.username_display_for_package("com.test.app")
        self.assertEqual(row.username_display, package_username.NO_ACCOUNT_LABEL)
        self.assertEqual(row.account_status, "no_account")

    def test_unknown_never_in_display_helpers(self) -> None:
        pre = root_access.RootCheckReport(
            ok=True, tool="su", uid="uid=0(root)", whoami="root",
            data_dir_readable=True, steps=(), detail="ok",
        )
        with mock.patch("agent.package_username.root_access.root_required_preflight", return_value=pre), \
             mock.patch("agent.package_username.root_access.list_root_glob", return_value=[]), \
             mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("", True, "")), \
             mock.patch("agent.android.package_installed", return_value=True):
            text = package_username.safe_detect_username_for_package("com.test.app")
        self.assertNotEqual(text, "Unknown")

    def test_manual_mapping_does_not_override_root_scan(self) -> None:
        pre = root_access.RootCheckReport(
            ok=True, tool="su", uid="uid=0(root)", whoami="root",
            data_dir_readable=True, steps=(), detail="ok",
        )
        xml = '<map><string name="username">REALUSER</string></map>'
        cfg = {
            "roblox_packages": [
                {
                    "package": "com.test.app",
                    "account_username": "ManualUser",
                    "username_source": "manual",
                    "enabled": True,
                }
            ]
        }
        with mock.patch("agent.package_username.root_access.root_required_preflight", return_value=pre), \
             mock.patch("agent.package_username.root_access.list_root_glob", return_value=["/data/data/com.test.app/shared_prefs/prefs.xml"]), \
             mock.patch("agent.package_username.root_access.read_root_file", return_value=xml), \
             mock.patch("agent.launch_verify.resolve_launcher_activity", return_value=("", True, "")), \
             mock.patch("agent.android.package_installed", return_value=True):
            report = package_username.scan_package_username("com.test.app", cfg)
        self.assertEqual(report.username, "REALUSER")


class VersionTests(unittest.TestCase):
    def test_user_facing_version_is_1_0_0(self) -> None:
        self.assertEqual(constants.VERSION, "1.0.0")

    def test_installer_latest_points_to_1_0_0(self) -> None:
        from agent.install_registry import resolve_latest_public_stable

        row = resolve_latest_public_stable()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(str(row.get("version") or ""), "v1.0.0")


if __name__ == "__main__":
    unittest.main()
