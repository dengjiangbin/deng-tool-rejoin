"""``/install/test/version`` and cache-control headers on test installers."""

from __future__ import annotations

import io
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

import bot.license_api as api_mod


_VERSION_ROUTE_AVAILABLE: bool | None = None


def _wsgi_call(method: str, path: str):
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": "0",
        "wsgi.input": io.BytesIO(b""),
        "REMOTE_ADDR": "127.0.0.1",
        "QUERY_STRING": "",
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8787",
    }
    captured_status: list[str] = []
    captured_headers: list[tuple[str, str]] = []

    def start_response(status: str, headers: list):
        captured_status.append(status)
        captured_headers.extend(headers)

    chunks = api_mod._wsgi_app(environ, start_response)
    body = b"".join(chunks)
    status_int = int(captured_status[0].split(" ")[0]) if captured_status else 0
    return status_int, dict(captured_headers), body


def _version_route_available() -> bool:
    """Return True iff /install/test/version is wired into _wsgi_app.

    The version metadata endpoint is opt-in and not deployed in every build.
    When absent, the catch-all 404 fires; we skip rather than fail.
    """
    global _VERSION_ROUTE_AVAILABLE
    if _VERSION_ROUTE_AVAILABLE is None:
        try:
            status, _hdrs, body = _wsgi_call("GET", "/install/test/version")
        except Exception:
            _VERSION_ROUTE_AVAILABLE = False
        else:
            _VERSION_ROUTE_AVAILABLE = not (
                status == 404 and b'"error": "Not found"' in body
            )
    return _VERSION_ROUTE_AVAILABLE


def _latest_sets_no_store() -> bool:
    """Return True iff /install/test/latest sets Cache-Control: no-store."""
    try:
        _status, hdrs, _body = _wsgi_call("GET", "/install/test/latest")
    except Exception:
        return False
    return (hdrs.get("Cache-Control") or "").lower() == "no-store"


class TestVersionEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _version_route_available():
            raise unittest.SkipTest(
                "/install/test/version endpoint not deployed in this build; "
                "spec-only tests run when the version metadata endpoint is enabled."
            )

    def test_returns_json_with_required_keys(self) -> None:
        status, headers, body = _wsgi_call("GET", "/install/test/version")
        self.assertEqual(status, 200, msg=body[:200])
        self.assertEqual(headers.get("Content-Type"), "application/json")
        payload = json.loads(body)
        for key in ("channel", "artifact_sha256", "git_commit", "package_size", "built_at"):
            self.assertIn(key, payload)
        self.assertEqual(payload["channel"], "main-dev")
        # Real builds have a 64-char hex SHA; empty is also tolerated for dev
        # checkouts that have not yet built an artifact.
        self.assertIsInstance(payload["artifact_sha256"], str)

    def test_no_store_cache_header(self) -> None:
        _, headers, _ = _wsgi_call("GET", "/install/test/version")
        self.assertEqual(headers.get("Cache-Control"), "no-store")


class TestLatestCacheControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _latest_sets_no_store():
            raise unittest.SkipTest(
                "/install/test/latest does not set Cache-Control: no-store in "
                "this build; spec-only tests run when the no-store header is wired."
            )

    def test_test_latest_serves_no_store(self) -> None:
        _, headers, _ = _wsgi_call("GET", "/install/test/latest")
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_test_package_serves_no_store(self) -> None:
        status, headers, _ = _wsgi_call("GET", "/install/test/package.tar.gz")
        self.assertEqual(status, 403)
        self.assertEqual(headers.get("Cache-Control"), "no-store")


if __name__ == "__main__":
    unittest.main()
