"""Isolated test/latest2 channel — install routing, keyless gate, Lime gating."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.bootstrap_installer import render_direct_install_bootstrap  # noqa: E402
from agent.install_registry import get_exact_registry_row, load_registry_rows  # noqa: E402
from agent.lime_channel import is_lime_detection_channel, lime_detection_enabled  # noqa: E402


class TestLatest2ManifestTests(unittest.TestCase):
    def test_manifest_has_isolated_row(self) -> None:
        row = get_exact_registry_row("test-latest2")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.get("installer_endpoint"), "/install/test/latest2")
        self.assertEqual(row.get("source_version"), "v1.3.0")
        self.assertTrue(str(row.get("artifact_sha256") or ""))

    def test_channel_pointers_include_test_latest2(self) -> None:
        rows = load_registry_rows()
        pointers = next((r for r in rows if r.get("kind") == "channel_pointers"), {})
        self.assertEqual(pointers.get("test_latest2"), "test-latest2")
        self.assertEqual(pointers.get("test_latest"), "main-dev")

    def test_test_latest2_endpoint_differs_from_test_latest_and_stable(self) -> None:
        t2 = get_exact_registry_row("test-latest2")
        md = get_exact_registry_row("main-dev")
        v13 = get_exact_registry_row("v1.3.0")
        self.assertIsNotNone(t2)
        self.assertIsNotNone(md)
        self.assertIsNotNone(v13)
        assert t2 and md and v13
        self.assertNotEqual(t2.get("installer_endpoint"), md.get("installer_endpoint"))
        self.assertNotEqual(t2.get("installer_endpoint"), v13.get("installer_endpoint"))


class TestLatest2InstallerTests(unittest.TestCase):
    def test_installer_is_keyless_and_stamps_channel(self) -> None:
        script = render_direct_install_bootstrap(
            base_url="https://rejoin.deng.my.id",
            package_sha256="a" * 64,
            version_label="test-latest2",
            channel="test-latest2",
            token_endpoint="/install/test/latest2/package-token",
            installer_endpoint="/install/test/latest2",
            requested_channel="test/latest2",
        )
        self.assertIn(".test-license-bypass", script)
        self.assertIn("test-latest2", script)
        self.assertIn("/install/test/latest2", script)
        self.assertNotIn("/install/test/latest", script.split("installer_url")[0][-200:])


class LimeChannelGateTests(unittest.TestCase):
    def test_lime_only_on_test_latest2(self) -> None:
        self.assertTrue(is_lime_detection_channel("test-latest2"))
        self.assertFalse(is_lime_detection_channel("main-dev"))
        self.assertFalse(is_lime_detection_channel("stable"))
        self.assertFalse(is_lime_detection_channel("v1.3.0"))

    def test_lime_disabled_on_main_dev_installed_channel(self) -> None:
        with patch("agent.lime_channel.installed_channel", return_value="main-dev", create=True):
            with patch("agent.license.installed_channel", return_value="main-dev"):
                self.assertFalse(lime_detection_enabled())

    def test_lime_enabled_on_test_latest2_installed_channel(self) -> None:
        with patch("agent.license.installed_channel", return_value="test-latest2"):
            self.assertTrue(lime_detection_enabled())

    def test_tracker_does_not_start_off_channel(self) -> None:
        from unittest.mock import MagicMock

        from agent.lime_detection_speed import LimeDetectionSpeedTracker

        monitor = MagicMock()
        monitor.packages = ["com.moons.litesc"]
        with patch("agent.lime_channel.lime_detection_enabled", return_value=False):
            tracker = LimeDetectionSpeedTracker(["com.moons.litesc"], monitor=monitor)
            tracker.start()
            snap = tracker.probe_snapshot()
            self.assertFalse(snap.get("enabled"))


class LicenseApiRouteTests(unittest.TestCase):
    def test_test_latest2_route_returns_installer(self) -> None:
        from bot import license_api

        row = get_exact_registry_row("test-latest2")
        if row is None or not str(row.get("artifact_sha256") or "").strip():
            self.skipTest("test-latest2 row or sha not configured")
        pkg = PROJECT / "releases/test-latest2/deng-tool-rejoin-test-latest2.tar.gz"
        if not pkg.is_file():
            self.skipTest("test-latest2 artifact not built yet")
        env = {"REQUEST_METHOD": "GET", "QUERY_STRING": ""}
        out = license_api._route_public_install(env, "/install/test/latest2", "GET")
        self.assertIsNotNone(out)
        assert out is not None
        body, status, ctype, _ = out
        self.assertEqual(status, 200)
        self.assertIn("shellscript", ctype)
        text = body.decode("utf-8")
        self.assertIn("test-latest2", text)
        self.assertIn(str(row.get("artifact_sha256")), text)

    def test_test_latest2_package_token_route(self) -> None:
        from bot import license_api

        row = get_exact_registry_row("test-latest2")
        pkg = PROJECT / "releases/test-latest2/deng-tool-rejoin-test-latest2.tar.gz"
        if row is None or not pkg.is_file():
            self.skipTest("test-latest2 artifact not built yet")
        env = {"REQUEST_METHOD": "GET", "QUERY_STRING": ""}
        out = license_api._route_public_install(env, "/install/test/latest2/package-token", "GET")
        self.assertIsNotNone(out)
        assert out is not None
        body, status, _, _ = out
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("url", data)
        self.assertEqual(data.get("sha256"), str(row.get("artifact_sha256")))


class TestLatest2KeylessBypassTests(unittest.TestCase):
    def test_enable_bypass_on_test_latest2_channel(self) -> None:
        from agent import license as lic

        with patch.object(lic, "installed_channel", return_value="test-latest2"):
            with tempfile.TemporaryDirectory() as tmp:
                marker = Path(tmp) / ".test-license-bypass"
                with patch.object(lic, "TEST_BYPASS_MARKER_PATH", marker):
                    self.assertTrue(lic.enable_test_license_bypass())
                    self.assertTrue(lic.is_test_license_bypass_active())

    def test_stable_rejects_bypass_marker(self) -> None:
        from agent import license as lic

        with patch.object(lic, "installed_channel", return_value="stable"):
            with tempfile.TemporaryDirectory() as tmp:
                marker = Path(tmp) / ".test-license-bypass"
                marker.write_text('{"enabled": true}\n', encoding="utf-8")
                with patch.object(lic, "TEST_BYPASS_MARKER_PATH", marker):
                    self.assertFalse(lic.is_test_license_bypass_active())


if __name__ == "__main__":
    unittest.main()
