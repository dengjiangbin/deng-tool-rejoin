"""resolve_install_api and first-run API defaults (no network)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.deferred_bundle_install import (  # noqa: E402
    DEFAULT_PUBLIC_INSTALL_API,
    _INSTALLER_UA,
    _is_cloudflare_block,
    _is_server_side_error,
    describe_install_authorize_failure,
    resolve_install_api,
)


class ResolveInstallApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.mkdtemp()
        self._home = Path(self._td) / "fake_home"
        self._app = self._home / ".deng-tool" / "rejoin"
        self._app.mkdir(parents=True)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self._td, ignore_errors=True)

    def test_env_wins(self) -> None:
        old = os.environ.get("DENG_REJOIN_INSTALL_API")
        try:
            os.environ["DENG_REJOIN_INSTALL_API"] = "https://custom.example/api"
            u = resolve_install_api(self._app)
        finally:
            if old is None:
                os.environ.pop("DENG_REJOIN_INSTALL_API", None)
            else:
                os.environ["DENG_REJOIN_INSTALL_API"] = old
        self.assertEqual(u, "https://custom.example/api")

    def test_file_used_when_env_missing(self) -> None:
        (self._app / ".install_api").write_text(
            "https://rejoin.deng.my.id\n", encoding="utf-8"
        )
        old = os.environ.pop("DENG_REJOIN_INSTALL_API", None)
        try:
            u = resolve_install_api(self._app)
        finally:
            if old is not None:
                os.environ["DENG_REJOIN_INSTALL_API"] = old
        self.assertEqual(u, "https://rejoin.deng.my.id")

    def test_default_public_url(self) -> None:
        old = os.environ.pop("DENG_REJOIN_INSTALL_API", None)
        try:
            u = resolve_install_api(self._app)
        finally:
            if old is not None:
                os.environ["DENG_REJOIN_INSTALL_API"] = old
        self.assertEqual(u, DEFAULT_PUBLIC_INSTALL_API.rstrip("/"))


class DescribeInstallAuthorizeFailureTests(unittest.TestCase):
    def test_uses_message_when_present(self) -> None:
        msg = "This key has not been redeemed yet. Redeem it in the panel first."
        s = describe_install_authorize_failure(
            403,
            {"result": "key_not_redeemed", "message": msg},
            "{}",
        )
        self.assertIn("Install denied:", s)
        self.assertIn("not been redeemed", s)

    def test_includes_error_when_no_message(self) -> None:
        s = describe_install_authorize_failure(
            429,
            {"error": "Too many requests. Try again later."},
            "",
        )
        self.assertIn("Too many requests", s)
        self.assertIn("HTTP 429", s)

    def test_non_json_body_snippet(self) -> None:
        raw = "<html><title>oops</title></html>"
        s = describe_install_authorize_failure(500, {}, raw)
        self.assertIn("response:", s)
        self.assertIn("html", s)

    def test_never_contains_sample_license_pattern(self) -> None:
        s = describe_install_authorize_failure(
            400,
            {"message": "bad", "result": "not_found"},
            "",
        )
        self.assertNotRegex(s, r"DENG-[A-Z0-9]{4}")

    def test_cloudflare_1010_returns_friendly_message(self) -> None:
        # Simulate a Cloudflare 1010 HTML block response (no valid JSON body)
        cf_html = (
            "<!DOCTYPE html><html><head><title>Access denied | rejoin.deng.my.id</title></head>"
            "<body><div>error code: 1010</div><div>Cloudflare</div></body></html>"
        )
        s = describe_install_authorize_failure(403, {}, cf_html)
        self.assertIn("blocked by server protection", s)
        self.assertIn("Cloudflare", s)
        self.assertNotIn("Install denied:", s)
        self.assertIn("HTTP 403", s)

    def test_cloudflare_block_message_does_not_loop_hint(self) -> None:
        # The message should clarify it is NOT a key issue
        cf_html = "<html>error code: 1010 cloudflare</html>"
        s = describe_install_authorize_failure(403, {}, cf_html)
        self.assertIn("NOT a license key issue", s)

    def test_non_cloudflare_403_still_shows_install_denied(self) -> None:
        # A 403 from our own backend (JSON body present) should NOT trigger CF detection
        s = describe_install_authorize_failure(
            403,
            {"result": "revoked", "message": "This key has been revoked."},
            '{"result":"revoked","message":"This key has been revoked."}',
        )
        self.assertIn("Install denied:", s)
        self.assertNotIn("Cloudflare", s)


class CloudflareBlockDetectionTests(unittest.TestCase):
    def test_detects_1010_html(self) -> None:
        raw = "<html>error code: 1010 cloudflare</html>"
        self.assertTrue(_is_cloudflare_block(403, {}, raw))

    def test_detects_cloudflare_marker(self) -> None:
        raw = "<!DOCTYPE html><html><body>attention required cloudflare</body></html>"
        self.assertTrue(_is_cloudflare_block(403, {}, raw))

    def test_only_triggers_on_403(self) -> None:
        raw = "<html>error code: 1010 cloudflare</html>"
        self.assertFalse(_is_cloudflare_block(500, {}, raw))
        self.assertFalse(_is_cloudflare_block(200, {}, raw))

    def test_json_body_prevents_cf_detection(self) -> None:
        # If we parsed a JSON body successfully, it is a backend response, not CF
        raw = "<html>cloudflare</html>"
        self.assertFalse(_is_cloudflare_block(403, {"result": "revoked"}, raw))

    def test_empty_raw_not_cf(self) -> None:
        self.assertFalse(_is_cloudflare_block(403, {}, ""))

    def test_html_403_no_json_triggers_cf(self) -> None:
        # Any 403 with HTML and no JSON body is treated as Cloudflare block
        raw = "<html><body>access denied</body></html>"
        self.assertTrue(_is_cloudflare_block(403, {}, raw))


class ServerSideErrorDetectionTests(unittest.TestCase):
    """_is_server_side_error must stop the key-prompt loop for non-retryable errors."""

    def test_500_is_server_side(self) -> None:
        self.assertTrue(_is_server_side_error(500, {}, ""))

    def test_503_is_server_side(self) -> None:
        self.assertTrue(_is_server_side_error(503, {"result": "server_unavailable"}, ""))

    def test_server_unavailable_result(self) -> None:
        self.assertTrue(
            _is_server_side_error(403, {"result": "server_unavailable"}, "")
        )

    def test_not_found_internal_build_is_server_side(self) -> None:
        body = {"result": "not_found", "message": "Internal build is not configured."}
        self.assertTrue(_is_server_side_error(404, body, ""))

    def test_not_found_no_public_release_is_server_side(self) -> None:
        body = {"result": "not_found", "message": "No public stable release is configured yet."}
        self.assertTrue(_is_server_side_error(404, body, ""))

    def test_not_found_key_not_found_is_retryable(self) -> None:
        body = {"result": "not_found", "message": "Key not found. Check the key and try again."}
        self.assertFalse(_is_server_side_error(404, body, ""))

    def test_wrong_device_is_retryable(self) -> None:
        self.assertFalse(_is_server_side_error(403, {"result": "wrong_device"}, ""))

    def test_key_not_redeemed_is_retryable(self) -> None:
        self.assertFalse(_is_server_side_error(403, {"result": "key_not_redeemed"}, ""))

    def test_forbidden_is_server_side(self) -> None:
        self.assertTrue(_is_server_side_error(403, {"result": "forbidden"}, ""))

    def test_no_release_is_server_side(self) -> None:
        self.assertTrue(_is_server_side_error(404, {"result": "no_release"}, ""))


class InstallerUserAgentTests(unittest.TestCase):
    def test_installer_ua_is_set(self) -> None:
        self.assertIsInstance(_INSTALLER_UA, str)
        self.assertGreater(len(_INSTALLER_UA), 0)

    def test_installer_ua_not_python_urllib(self) -> None:
        # The default Python-urllib UA is blocked by Cloudflare BIC
        self.assertNotIn("Python-urllib", _INSTALLER_UA)
        self.assertNotIn("python-urllib", _INSTALLER_UA.lower())

    def test_installer_ua_identifies_deng(self) -> None:
        self.assertIn("deng-rejoin-installer", _INSTALLER_UA)


if __name__ == "__main__":
    unittest.main()
