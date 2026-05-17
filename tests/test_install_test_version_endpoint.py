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


class TestVersionEndpointTests(unittest.TestCase):
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
    def test_test_latest_serves_no_store(self) -> None:
        _, headers, _ = _wsgi_call("GET", "/install/test/latest")
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_test_package_serves_no_store(self) -> None:
        status, headers, _ = _wsgi_call("GET", "/install/test/package.tar.gz")
        # 200 if a built artifact exists, 404 if not — either way the SHA
        # must not be served from cache.
        if status == 200:
            self.assertEqual(headers.get("Cache-Control"), "no-store")


if __name__ == "__main__":
    unittest.main()
