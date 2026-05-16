"""Tests for agent/safe_http.py.

Covers:
  - Backend selection (Termux → curl, desktop → python, env overrides).
  - curl path: successful POST/GET, timeout, network error, signal kill,
    HTTP 4xx/5xx, invalid JSON, empty body, missing curl binary.
  - python path: successful POST/GET, URLError, HTTPError, invalid JSON.
  - License key is never logged in plaintext.
  - Dispatch (post_json / get_json) routes to correct backend.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from unittest.mock import MagicMock, patch


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_run_result(returncode: int, stdout: bytes, stderr: bytes = b"") -> MagicMock:
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


class TestBackendSelection(unittest.TestCase):
    """_http_backend() returns the correct string."""

    def _backend(self, env: dict) -> str:
        with patch.dict(os.environ, env, clear=False):
            from importlib import reload
            import agent.safe_http as sh
            reload(sh)
            return sh._http_backend()

    def test_override_curl(self):
        with patch.dict(os.environ, {"DENG_HTTP_BACKEND": "curl", "TERMUX_VERSION": ""}, clear=False):
            import agent.safe_http as sh
            self.assertEqual(sh._http_backend(), "curl")

    def test_override_python(self):
        with patch.dict(os.environ, {"DENG_HTTP_BACKEND": "python", "TERMUX_VERSION": "0.118"}, clear=False):
            import agent.safe_http as sh
            self.assertEqual(sh._http_backend(), "python")

    def test_termux_auto_curl(self):
        with patch.dict(os.environ, {"DENG_HTTP_BACKEND": "auto", "TERMUX_VERSION": "0.118"}, clear=False):
            import agent.safe_http as sh
            self.assertEqual(sh._http_backend(), "curl")

    def test_non_termux_auto_python(self):
        env = {"DENG_HTTP_BACKEND": "auto"}
        # Ensure TERMUX_VERSION is not set.
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("TERMUX_VERSION", None)
            import agent.safe_http as sh
            self.assertEqual(sh._http_backend(), "python")

    def test_unknown_backend_defaults_to_python(self):
        with patch.dict(os.environ, {"DENG_HTTP_BACKEND": "httpx"}, clear=False):
            os.environ.pop("TERMUX_VERSION", None)
            import agent.safe_http as sh
            # unknown value → falls through to non-Termux → "python"
            self.assertEqual(sh._http_backend(), "python")


class TestCurlMissingBinary(unittest.TestCase):
    def test_raises_network_error_when_curl_missing(self):
        import agent.safe_http as sh
        with patch("agent.safe_http.shutil.which", return_value=None):
            with self.assertRaises(sh.SafeHttpNetworkError) as ctx:
                sh._run_curl(["-X", "GET", "https://example.com"])
        self.assertIn("curl is required", str(ctx.exception))


class TestRunCurlSuccess(unittest.TestCase):
    def setUp(self):
        import agent.safe_http as sh
        self.sh = sh

    def _run(self, stdout: bytes):
        with patch("agent.safe_http.shutil.which", return_value="/usr/bin/curl"), \
             patch("subprocess.run", return_value=_make_run_result(0, stdout)):
            return self.sh._run_curl(["https://example.com"])

    def test_parses_status_from_last_line(self):
        body = b'{"result":"active"}'
        http_status, body_bytes = self._run(body + b"\n200")
        self.assertEqual(http_status, 200)
        self.assertEqual(body_bytes, body)

    def test_body_with_newlines_preserved(self):
        # Body that itself contains newlines — only the LAST is the status code.
        body = b'{"a":1,\n"b":2}'
        http_status, body_bytes = self._run(body + b"\n200")
        self.assertEqual(http_status, 200)
        self.assertIn(b'"a":1', body_bytes)


class TestRunCurlErrors(unittest.TestCase):
    def setUp(self):
        import agent.safe_http as sh
        self.sh = sh

    def _patch(self, **kwargs):
        return patch("agent.safe_http.shutil.which", return_value="/usr/bin/curl"), \
               patch("subprocess.run", **kwargs)

    def test_network_error_on_nonzero_exit(self):
        with patch("agent.safe_http.shutil.which", return_value="/usr/bin/curl"), \
             patch("subprocess.run", return_value=_make_run_result(7, b"")):
            with self.assertRaises(self.sh.SafeHttpNetworkError):
                self.sh._run_curl(["https://example.com"])

    def test_network_error_on_timeout_exit(self):
        with patch("agent.safe_http.shutil.which", return_value="/usr/bin/curl"), \
             patch("subprocess.run", return_value=_make_run_result(28, b"")):
            with self.assertRaises(self.sh.SafeHttpNetworkError) as ctx:
                self.sh._run_curl(["https://example.com"])
            self.assertIn("timed out", str(ctx.exception).lower())

    def test_network_error_on_signal_kill(self):
        """Negative returncode means child killed by signal (e.g. SIGSEGV in curl)."""
        with patch("agent.safe_http.shutil.which", return_value="/usr/bin/curl"), \
             patch("subprocess.run", return_value=_make_run_result(-11, b"")):
            with self.assertRaises(self.sh.SafeHttpNetworkError) as ctx:
                self.sh._run_curl(["https://example.com"])
            msg = str(ctx.exception)
            self.assertIn("signal", msg.lower())
            self.assertIn("safely", msg.lower())

    def test_network_error_on_subprocess_timeout(self):
        with patch("agent.safe_http.shutil.which", return_value="/usr/bin/curl"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("curl", 30)):
            with self.assertRaises(self.sh.SafeHttpNetworkError):
                self.sh._run_curl(["https://example.com"])

    def test_network_error_on_os_error(self):
        with patch("agent.safe_http.shutil.which", return_value="/usr/bin/curl"), \
             patch("subprocess.run", side_effect=OSError("exec fail")):
            with self.assertRaises(self.sh.SafeHttpNetworkError):
                self.sh._run_curl(["https://example.com"])


class TestCurlPostJson(unittest.TestCase):
    def setUp(self):
        import agent.safe_http as sh
        self.sh = sh

    def _mock_run(self, body_dict: dict, status: int = 200) -> bytes:
        body = json.dumps(body_dict).encode()
        return body + f"\n{status}".encode()

    def test_success_returns_dict(self):
        resp = {"result": "active", "message": "ok"}
        stdout = self._mock_run(resp)
        with patch("agent.safe_http.shutil.which", return_value="/usr/bin/curl"), \
             patch("subprocess.run", return_value=_make_run_result(0, stdout)):
            result = self.sh._curl_post_json("https://example.com/api", {"key": "DENG-XXXX-XXXX-XXXX-XXXX"})
        self.assertEqual(result["result"], "active")

    def test_http_404_raises_status_error(self):
        stdout = b'{"error":"not found"}\n404'
        with patch("agent.safe_http.shutil.which", return_value="/usr/bin/curl"), \
             patch("subprocess.run", return_value=_make_run_result(0, stdout)):
            with self.assertRaises(self.sh.SafeHttpStatusError) as ctx:
                self.sh._curl_post_json("https://example.com/api", {})
        self.assertEqual(ctx.exception.status_code, 404)

    def test_http_200_empty_body_returns_empty_dict(self):
        stdout = b"\n200"
        with patch("agent.safe_http.shutil.which", return_value="/usr/bin/curl"), \
             patch("subprocess.run", return_value=_make_run_result(0, stdout)):
            result = self.sh._curl_post_json("https://example.com/api", {})
        self.assertEqual(result, {})

    def test_invalid_json_raises_json_error(self):
        stdout = b"not-json\n200"
        with patch("agent.safe_http.shutil.which", return_value="/usr/bin/curl"), \
             patch("subprocess.run", return_value=_make_run_result(0, stdout)):
            with self.assertRaises(self.sh.SafeHttpJsonError):
                self.sh._curl_post_json("https://example.com/api", {})

    def test_http_403_with_result_field_returns_dict(self):
        """4xx with a valid JSON result field is returned as-is (used by license API)."""
        payload = {"result": "wrong_device", "message": "Wrong device"}
        stdout = json.dumps(payload).encode() + b"\n403"
        with patch("agent.safe_http.shutil.which", return_value="/usr/bin/curl"), \
             patch("subprocess.run", return_value=_make_run_result(0, stdout)):
            result = self.sh._curl_post_json("https://example.com/api", {})
        self.assertEqual(result["result"], "wrong_device")

    def test_license_key_not_passed_as_shell_arg(self):
        """JSON body must be passed via stdin, not shell arguments (key injection risk)."""
        captured_args = []

        def fake_run(cmd, *a, **kw):
            captured_args.extend(cmd)
            return _make_run_result(0, b'{"result":"active"}\n200')

        with patch("agent.safe_http.shutil.which", return_value="/usr/bin/curl"), \
             patch("subprocess.run", side_effect=fake_run):
            self.sh._curl_post_json("https://example.com", {"key": "DENG-SEKR-ETKE-YVAL-UEXX"})

        # The license key must NOT appear in any command-line argument.
        joined = " ".join(str(a) for a in captured_args)
        self.assertNotIn("DENG-SEKR-ETKE-YVAL-UEXX", joined)


class TestPythonBackend(unittest.TestCase):
    def setUp(self):
        import agent.safe_http as sh
        self.sh = sh

    def test_success_post(self):
        import io
        import urllib.error
        resp_body = json.dumps({"result": "active"}).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = resp_body

        with patch("agent.safe_http.urllib.request.urlopen", return_value=mock_resp):
            result = self.sh._python_post_json("https://example.com", {"key": "test"})
        self.assertEqual(result["result"], "active")

    def test_url_error_raises_network_error(self):
        import urllib.error
        with patch("agent.safe_http.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("network down")):
            with self.assertRaises(self.sh.SafeHttpNetworkError):
                self.sh._python_post_json("https://example.com", {})

    def test_http_error_raises_status_error(self):
        import io
        import urllib.error
        exc = urllib.error.HTTPError("url", 403, "Forbidden", {}, io.BytesIO(b"{}"))
        with patch("agent.safe_http.urllib.request.urlopen", side_effect=exc):
            with self.assertRaises(self.sh.SafeHttpStatusError) as ctx:
                self.sh._python_post_json("https://example.com", {})
        self.assertEqual(ctx.exception.status_code, 403)

    def test_empty_body_returns_empty_dict(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b""

        with patch("agent.safe_http.urllib.request.urlopen", return_value=mock_resp):
            result = self.sh._python_post_json("https://example.com", {})
        self.assertEqual(result, {})


class TestDispatch(unittest.TestCase):
    """post_json / get_json dispatch to correct backend."""

    def setUp(self):
        import agent.safe_http as sh
        self.sh = sh

    def test_post_json_uses_curl_on_termux(self):
        with patch.dict(os.environ, {"TERMUX_VERSION": "0.118", "DENG_HTTP_BACKEND": "auto"}):
            with patch("agent.safe_http._curl_post_json", return_value={"ok": True}) as mock:
                result = self.sh.post_json("https://x.com", {"a": 1})
            mock.assert_called_once()
        self.assertEqual(result, {"ok": True})

    def test_post_json_uses_python_off_termux(self):
        env = {"DENG_HTTP_BACKEND": "python"}
        with patch.dict(os.environ, env):
            with patch("agent.safe_http._python_post_json", return_value={"ok": True}) as mock:
                result = self.sh.post_json("https://x.com", {"a": 1})
            mock.assert_called_once()
        self.assertEqual(result, {"ok": True})

    def test_get_json_uses_curl_when_forced(self):
        with patch.dict(os.environ, {"DENG_HTTP_BACKEND": "curl"}):
            with patch("agent.safe_http._curl_get_json", return_value={"ok": True}) as mock:
                result = self.sh.get_json("https://x.com")
            mock.assert_called_once()
        self.assertEqual(result, {"ok": True})

    def test_termux_license_check_never_calls_python_requests(self):
        """On Termux, the Python urllib/requests path must never be called for
        license verification, as Python ssl can SIGSEGV on Termux."""
        with patch.dict(os.environ, {"TERMUX_VERSION": "0.118", "DENG_HTTP_BACKEND": "auto"}):
            with patch("agent.safe_http._curl_post_json", return_value={"result": "active"}) as curl_mock, \
                 patch("agent.safe_http._python_post_json") as py_mock:
                self.sh.post_json("https://license.example.com/api/license/check", {"key": "k"})
            curl_mock.assert_called_once()
            py_mock.assert_not_called()


class TestLicenseModule(unittest.TestCase):
    """Integration: agent/license.py uses safe_http, not urllib directly."""

    def test_license_check_uses_safe_http_post(self):
        from agent.license import check_remote_license_status
        with patch("agent.safe_http.post_json", return_value={"result": "active", "message": "ok"}) as mock:
            result, msg = check_remote_license_status(
                "https://example.com",
                license_key="DENG-1234-5678-9ABC-DEF0",
                install_id="a" * 32,
                device_model="Pixel",
                app_version="1.0.0",
            )
        mock.assert_called_once()
        self.assertEqual(result, "active")

    def test_license_check_network_error_returns_unavailable(self):
        import agent.safe_http as sh
        from agent.license import check_remote_license_status
        with patch("agent.safe_http.post_json", side_effect=sh.SafeHttpNetworkError("no network")):
            result, msg = check_remote_license_status(
                "https://example.com",
                license_key="DENG-1234-5678-9ABC-DEF0",
                install_id="b" * 32,
                device_model="SM-A",
                app_version="1.0.0",
            )
        self.assertEqual(result, "server_unavailable")
        self.assertIn("no network", msg.lower())

    def test_license_check_curl_signal_kill_returns_unavailable(self):
        import agent.safe_http as sh
        from agent.license import check_remote_license_status
        with patch("agent.safe_http.post_json",
                   side_effect=sh.SafeHttpNetworkError("Network check crashed safely (signal 11). Please retry.")):
            result, msg = check_remote_license_status(
                "https://example.com",
                license_key="DENG-AAAA-BBBB-CCCC-DDDD",
                install_id="c" * 32,
                device_model="SM-B",
                app_version="1.0.0",
            )
        self.assertEqual(result, "server_unavailable")

    def test_license_check_wrong_device_returned(self):
        from agent.license import check_remote_license_status, WRONG_DEVICE_USER_MESSAGE
        with patch("agent.safe_http.post_json",
                   return_value={"result": "wrong_device", "message": "bound to another device"}):
            result, msg = check_remote_license_status(
                "https://example.com",
                license_key="DENG-1234-5678-9ABC-DEF0",
                install_id="d" * 32,
                device_model="Pixel",
                app_version="1.0.0",
            )
        self.assertEqual(result, "wrong_device")
        self.assertIn("Reset HWID", msg)

    def test_license_key_not_in_http_backend_logs(self):
        """License key is passed as payload data, not as a raw debug-log string."""
        import logging
        import io
        from agent.license import check_remote_license_status

        log_buf = io.StringIO()
        handler = logging.StreamHandler(log_buf)
        handler.setLevel(logging.DEBUG)
        root_log = logging.getLogger("deng.rejoin.safe_http")
        root_log.addHandler(handler)
        root_log.setLevel(logging.DEBUG)

        try:
            # Valid key format: DENG-XXXX-XXXX-XXXX-XXXX (16 hex chars after DENG-)
            with patch("agent.safe_http.post_json", return_value={"result": "active"}):
                check_remote_license_status(
                    "https://example.com",
                    license_key="DENG-ABCD-EF01-2345-6789",
                    install_id="e" * 32,
                    device_model="SM-C",
                    app_version="1.0.0",
                )
            log_output = log_buf.getvalue()
        finally:
            root_log.removeHandler(handler)

        # The raw license key must NOT appear in safe_http debug logs.
        self.assertNotIn("ABCD-EF01-2345-6789", log_output)


class TestFaulthandlerFileOnly(unittest.TestCase):
    """setup_faulthandler must write to a file; never fall back to stderr."""

    def test_faulthandler_uses_file_not_stderr(self):
        """setup_faulthandler must never call faulthandler.enable() without a file arg.

        Calling faulthandler.enable() with no file sends crash stacks to stderr,
        which would appear on the user's terminal — this is forbidden.
        """
        import faulthandler
        import agent.safe_io as sio

        called_without_file = []

        original_enable = faulthandler.enable

        def track_enable(*args, file=None, **kw):
            if file is None:
                called_without_file.append(True)
            # Don't actually enable faulthandler in tests.

        # Reset any stored file reference from a previous call.
        if hasattr(sio.setup_faulthandler, "_crash_file"):
            del sio.setup_faulthandler._crash_file

        with patch("faulthandler.enable", side_effect=track_enable):
            sio.setup_faulthandler()

        self.assertEqual(
            called_without_file, [],
            "setup_faulthandler must never call faulthandler.enable() without a file argument",
        )

    def test_crash_log_notice_shown_when_recent(self):
        import time
        import tempfile
        import agent.safe_io as sio
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            f.write(b"crash\n")
            tmp_path = Path(f.name)

        try:
            import time as _time
            mtime = tmp_path.stat().st_mtime
            with patch("agent.safe_io.time") as mock_time, \
                 patch("agent.constants.CRASH_LOG_PATH", tmp_path):
                mock_time.time.return_value = mtime + 30  # 30s after crash
                result = sio.check_and_report_crash_log(max_age_seconds=3600)
            self.assertIsNotNone(result)
            self.assertIn("crash", result.lower())
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_no_crash_notice_when_log_old(self):
        import tempfile
        import agent.safe_io as sio
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            f.write(b"old crash\n")
            tmp_path = Path(f.name)

        try:
            with patch("agent.safe_io.time") as mock_time, \
                 patch("agent.constants.CRASH_LOG_PATH", tmp_path):
                mock_time.time.return_value = tmp_path.stat().st_mtime + 7200  # 2 hours later
                result = sio.check_and_report_crash_log(max_age_seconds=3600)
            self.assertIsNone(result)
        finally:
            tmp_path.unlink(missing_ok=True)


class TestLicenseGateNetworkResilience(unittest.TestCase):
    """License gate must survive network failure without crashing."""

    def _make_args(self):
        import argparse
        ns = argparse.Namespace()
        ns.no_color = True
        ns.verbose = False
        ns.debug = False
        return ns

    def _cfg_with_key(self, key="DENG-AAAA-BBBB-CCCC-DDDD"):
        return {"license": {"key": key, "mode": "remote"}, "install_id": "a" * 32}

    def test_gate_continues_after_network_failure(self):
        """After a network failure, user can choose Exit without crash."""
        from agent.commands import _ensure_remote_license_menu_loop
        cfg = self._cfg_with_key()

        with patch("agent.commands.load_config", return_value=cfg), \
             patch("agent.commands._ensure_install_id_saved", return_value=cfg), \
             patch("agent.commands._remote_license_run_check",
                   return_value=("server_unavailable", "timeout")), \
             patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands.print_beginner_menu_license_prompt"), \
             patch("agent.safe_io.safe_prompt", side_effect=["2"]):  # choose Exit
            result = _ensure_remote_license_menu_loop(cfg, self._make_args(), False)

        self.assertFalse(result)  # chose Exit cleanly, no crash

    def test_gate_retries_then_succeeds(self):
        """After choosing Try Again, the second check succeeds."""
        import copy
        from agent.commands import _ensure_remote_license_menu_loop
        base_cfg = self._cfg_with_key()
        cfg = copy.deepcopy(base_cfg)

        responses = iter([
            ("server_unavailable", "timeout"),
            ("active", "License active."),
        ])

        # Return fresh cfg copy each time so key is always present after reload.
        with patch("agent.commands.load_config", side_effect=lambda: copy.deepcopy(base_cfg)), \
             patch("agent.commands._ensure_install_id_saved", side_effect=lambda c: c), \
             patch("agent.commands.save_config", side_effect=lambda c: c), \
             patch("agent.commands._remote_license_run_check", side_effect=lambda c: next(responses)), \
             patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands._persist_license_status", side_effect=lambda c, r: c), \
             patch("agent.commands.print_beginner_menu_license_prompt"), \
             patch("agent.safe_io.safe_prompt", side_effect=["1"]):  # Try Again once
            result = _ensure_remote_license_menu_loop(cfg, self._make_args(), False)

        self.assertTrue(result)

    def test_wrong_device_enter_different_key_reprompts(self):
        """Wrong-device then valid key succeeds."""
        from agent.commands import _ensure_remote_license_menu_loop
        cfg = self._cfg_with_key()
        new_key = "DENG-1111-2222-3333-4444"
        cfg_no_key = dict(cfg)
        cfg_no_key["license"] = {"key": "", "mode": "remote"}

        responses = iter([
            ("wrong_device", "Wrong device."),
            ("active", "License active."),
        ])
        # After clearing the key, load_config should return cfg with new key.
        load_cfg_sequence = iter([cfg, cfg_no_key, {**cfg, "license": {"key": new_key, "mode": "remote"}}])

        with patch("agent.commands.load_config", side_effect=lambda: next(load_cfg_sequence)), \
             patch("agent.commands._ensure_install_id_saved", side_effect=lambda c: c), \
             patch("agent.commands.save_config", return_value={**cfg, "license": {"key": new_key, "mode": "remote"}}), \
             patch("agent.commands._remote_license_run_check", side_effect=lambda c: next(responses)), \
             patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands._persist_license_status", return_value=cfg), \
             patch("agent.commands.print_beginner_menu_license_prompt"), \
             patch("agent.safe_io.safe_prompt", side_effect=["1", new_key]):
            result = _ensure_remote_license_menu_loop(cfg, self._make_args(), False)

        self.assertTrue(result)


class TestBannerNotSpammedOnRetry(unittest.TestCase):
    """Banner must not be printed on each license retry iteration."""

    def _make_args(self):
        import argparse
        ns = argparse.Namespace()
        ns.no_color = True
        ns.verbose = False
        ns.debug = False
        return ns

    def test_no_banner_in_license_retry_loop(self):
        """Banner must NOT be printed on each retry iteration of the license loop."""
        import copy
        from agent.commands import _ensure_remote_license_menu_loop

        base_cfg = {"license": {"key": "DENG-AAAA-BBBB-CCCC-DDDD", "mode": "remote"}, "install_id": "a" * 32}
        cfg = copy.deepcopy(base_cfg)

        responses = iter([
            ("server_unavailable", "down"),
            ("server_unavailable", "down"),
            ("active", "ok"),
        ])

        # Return a fresh copy on each load_config() call so key is always set.
        with patch("agent.commands.load_config", side_effect=lambda: copy.deepcopy(base_cfg)), \
             patch("agent.commands._ensure_install_id_saved", side_effect=lambda c: c), \
             patch("agent.commands.save_config", side_effect=lambda c: c), \
             patch("agent.commands._remote_license_run_check", side_effect=lambda c: next(responses)), \
             patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands._persist_license_status", side_effect=lambda c, r: c), \
             patch("agent.commands.print_banner") as mock_banner, \
             patch("agent.commands.print_beginner_menu_license_prompt"), \
             patch("agent.safe_io.safe_prompt", side_effect=["1", "1"]):  # retry twice
            _ensure_remote_license_menu_loop(cfg, self._make_args(), False)

        # Banner must NOT be called inside the license retry loop itself.
        mock_banner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
