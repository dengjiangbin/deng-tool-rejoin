"""Regression: Termux TTY must never block indefinitely on upload/menu/launch."""

from __future__ import annotations

import io
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from agent import launcher, package_username, probe, safe_http, safe_io
from agent.commands import _config_menu_package_loop
from agent.config import default_config, package_entry, validate_config


class SafeHttpTimeoutTests(unittest.TestCase):
    def test_curl_invocation_uses_connect_and_max_time_and_retry(self) -> None:
        captured: list[list[str]] = []

        def _fake_run(*_a, **_k):
            captured.append(list(_a[0]) if _a else [])
            return 0, b"{}\n200", b"", False

        with mock.patch("agent.safe_http._http_backend", return_value="curl"), \
             mock.patch("agent.safe_http._curl_available", return_value=True), \
             mock.patch("agent.safe_http._iso.run_isolated_bytes", side_effect=_fake_run), \
             mock.patch("agent.safe_http.json.loads", return_value={}):
            safe_http.post_json("https://example.test/x", {"a": 1}, timeout=(5, 15))

        self.assertTrue(captured)
        cmd = captured[0]
        self.assertIn("--connect-timeout", cmd)
        self.assertIn("5", cmd)
        self.assertIn("--max-time", cmd)
        self.assertIn("15", cmd)
        self.assertIn("--retry", cmd)
        self.assertIn("1", cmd)


class UploadProbeTimeoutTests(unittest.TestCase):
    def test_upload_probe_returns_on_network_timeout_without_hanging(self) -> None:
        def _slow_post(*_a, **_k):
            time.sleep(30)
            return 200, b'{"probe_id":"p-never"}'

        with mock.patch("agent.probe._resolve_install_api", return_value="https://rejoin.test"), \
             mock.patch("agent.probe.trim_probe_for_upload", return_value=({"k": 1}, {})), \
             mock.patch("agent.probe.sanitize_probe", side_effect=lambda x: x), \
             mock.patch("agent.safe_http.post_raw", side_effect=_slow_post), \
             mock.patch("agent.safe_http.DEFAULT_CONNECT_TIMEOUT", 5), \
             mock.patch("agent.safe_http.SafeHttpNetworkError", Exception):
            started = time.monotonic()
            # Force the mock to behave like a timeout by raising network error quickly.
            with mock.patch(
                "agent.safe_http.post_raw",
                side_effect=safe_http.SafeHttpNetworkError("timed out"),
            ):
                ok, info = probe.upload_probe({"probe": True}, timeout=15.0)
            elapsed = time.monotonic() - started

        self.assertFalse(ok)
        self.assertIn("network error", info)
        self.assertLess(elapsed, 2.0)


class PackageMenuScanBudgetTests(unittest.TestCase):
    def test_menu_scan_aborts_slow_package_and_continues(self) -> None:
        packages = [f"com.moons.lites{c}" for c in "abcdef"]

        def _slow_root(pkg: str, *, timeout_seconds: float = 3.0):
            time.sleep(0.45)
            return package_username.UsernameScanReport(
                package=pkg,
                username="user",
                source="root_shared_prefs",
                supported=True,
                reason="",
                duration_ms=350,
                root_used=True,
            )

        with mock.patch(
            "agent.package_username.scan_package_username_root",
            side_effect=_slow_root,
        ):
            started = time.monotonic()
            for pkg in packages:
                report = package_username.scan_package_username_for_menu(
                    pkg, None, timeout_seconds=0.3,
                )
                self.assertIn("Timeout", report.reason)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 4.5)

    def test_config_menu_package_loop_finishes_under_15_seconds_with_slow_root(self) -> None:
        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [
            package_entry(f"com.moons.lites{c}", "", True, "not_set") for c in "abcdef"
        ]

        def _slow_scan(pkg: str, config_data=None, **kwargs):
            time.sleep(0.25)
            return package_username.UsernameScanReport(
                package=pkg,
                username="",
                source="root_scan_no_account",
                supported=True,
                reason="no account",
                duration_ms=250,
                root_used=True,
            )

        with mock.patch("agent.commands._is_interactive", return_value=True), \
             mock.patch(
                 "agent.package_username.scan_package_username_for_menu",
                 side_effect=_slow_scan,
             ), \
             mock.patch("agent.commands.safe_io.safe_prompt", return_value="0"), \
             mock.patch("agent.commands.safe_io.tty_session"), \
             redirect_stdout(io.StringIO()):
            started = time.monotonic()
            result = _config_menu_package_loop(cfg)
            elapsed = time.monotonic() - started

        self.assertIs(result, cfg)
        self.assertLess(elapsed, 15.0)


class LaunchPollEscapeTests(unittest.TestCase):
    def test_wait_for_launch_ready_uses_bounded_iterations(self) -> None:
        calls = {"n": 0}

        def _never_alive(_pkg: str) -> dict:
            calls["n"] += 1
            return {
                "process_alive": False,
                "activity_visible": False,
                "surface_present": False,
            }

        cfg = {"launch_settle_before_layout_sec": 0}
        with mock.patch("agent.launcher._read_launch_state", side_effect=_never_alive), \
             mock.patch("agent.launcher.time.sleep", return_value=None):
            launcher._wait_for_launch_ready("com.test.pkg", cfg)

        # Two phases × up to 15 polls each (+ initial read).
        self.assertLessEqual(calls["n"], 31)
        self.assertGreaterEqual(calls["n"], 15)


class TtySessionTests(unittest.TestCase):
    def test_tty_session_restores_on_exception(self) -> None:
        with mock.patch("agent.safe_io._save_tty_attrs", return_value=["saved"]), \
             mock.patch("agent.safe_io._restore_tty_attrs") as restore, \
             mock.patch("agent.safe_io._run_stty_sane") as sane:
            with self.assertRaises(RuntimeError):
                with safe_io.tty_session():
                    raise RuntimeError("boom")
        restore.assert_called_once()
        sane.assert_called_once()


if __name__ == "__main__":
    unittest.main()
