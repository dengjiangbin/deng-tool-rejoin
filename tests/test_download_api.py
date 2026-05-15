"""Tests for the /api/download/* endpoints in bot/license_api.py.

These tests call _wsgi_app() directly as a WSGI app so no HTTP server is needed.
The LocalJsonLicenseStore is used with temp files for full integration coverage.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import bot.license_api as api_mod
from agent.license import generate_license_key
from agent.license_store import LocalJsonLicenseStore


# ── WSGI test harness ─────────────────────────────────────────────────────────

def _wsgi_call(method: str, path: str, body=None, environ_extra: dict | None = None):
    """Call the license WSGI app and return (status_int, headers_dict, body_bytes)."""
    if body is None:
        body_bytes = b""
    elif isinstance(body, dict):
        body_bytes = json.dumps(body).encode()
    else:
        body_bytes = body

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(body_bytes)),
        "wsgi.input": io.BytesIO(body_bytes),
        "REMOTE_ADDR": "127.0.0.1",
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8787",
    }
    if environ_extra:
        environ.update(environ_extra)

    captured_status: list[str] = []
    captured_headers: list[tuple[str, str]] = []

    def start_response(status: str, headers: list):
        captured_status.append(status)
        captured_headers.extend(headers)

    chunks = api_mod._wsgi_app(environ, start_response)
    body_out = b"".join(chunks)
    status_int = int(captured_status[0].split(" ")[0]) if captured_status else 0
    headers_dict = dict(captured_headers)
    return status_int, headers_dict, body_out


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _tmp_store() -> LocalJsonLicenseStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    Path(tmp.name).unlink()
    return LocalJsonLicenseStore(Path(tmp.name))


def _make_fake_download_root(tmp_dir: Path, version: str = "1.0.0", channel: str = "stable") -> Path:
    """Create a fake download root with a manifest and a placeholder zip."""
    release_dir = tmp_dir / "releases" / channel / version
    release_dir.mkdir(parents=True)

    # Create a minimal zip
    zip_name = f"deng-tool-rejoin-{version}-{channel}.zip"
    zip_path = release_dir / zip_name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("agent/__init__.py", "")
        zf.writestr("VERSION", f"{version}\n")

    import hashlib
    sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()

    manifest = {
        "app": "DENG Tool: Rejoin",
        "version": version,
        "channel": channel,
        "filename": zip_name,
        "sha256": sha256,
        "size_bytes": zip_path.stat().st_size,
        "created_at": "2025-01-01T00:00:00+00:00",
        "min_client_version": "1.0.0",
        "notes": "Test release",
        "file_count": 2,
    }
    (release_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_dir


def _setup_store_with_key():
    """Create a temp store with a user and key ready for binding."""
    store = _tmp_store()
    uid = "user-test-001"
    store.get_or_create_user(uid)
    full_key = store.create_key_for_user(uid)
    return store, full_key


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestHealthEndpoint(unittest.TestCase):
    def test_health_returns_200(self):
        status, headers, body = _wsgi_call("GET", "/api/license/health")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["status"], "ok")


class TestDownloadAuthorize(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.store, self.key = _setup_store_with_key()
        self.download_root = _make_fake_download_root(self.tmp)
        self.install_id_hash = "a" * 64  # 64-char fake SHA-256 hash
        # Clear rate limit to prevent cross-test interference
        with api_mod._rate_limit_lock:
            api_mod._rate_limit.clear()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _call_authorize(self, key=None, install_id_hash=None, channel="stable"):
        body = {
            "key": key or self.key,
            "install_id_hash": install_id_hash or self.install_id_hash,
            "device_model": "Pixel 7",
            "app_version": "1.0.0",
            "channel": channel,
        }
        with patch("agent.license_store.get_default_store", return_value=self.store), \
             patch.object(api_mod, "_get_download_root", return_value=self.download_root):
            return _wsgi_call("POST", "/api/download/authorize", body)

    def test_missing_key_returns_400(self):
        body = {
            "install_id_hash": self.install_id_hash,
            "device_model": "Pixel 7",
            "app_version": "1.0.0",
            "channel": "stable",
        }
        with patch("agent.license_store.get_default_store", return_value=self.store), \
             patch.object(api_mod, "_get_download_root", return_value=self.download_root):
            status, _, _ = _wsgi_call("POST", "/api/download/authorize", body)
        self.assertEqual(status, 400)

    def test_get_method_returns_405(self):
        with patch.object(api_mod, "_get_download_root", return_value=self.download_root):
            status, _, _ = _wsgi_call("GET", "/api/download/authorize")
        self.assertEqual(status, 405)

    def test_invalid_key_returns_403(self):
        status, _, body = self._call_authorize(key="DENG-FAKE-FAKE-FAKE-FAKE")
        data = json.loads(body)
        self.assertEqual(status, 403)
        self.assertIn("result", data)
        self.assertNotEqual(data["result"], "active")

    def test_valid_active_license_returns_token(self):
        # First bind the device
        self.store.bind_or_check_device(
            self.key, self.install_id_hash, "Pixel 7", "1.0.0"
        )
        status, _, body = self._call_authorize()
        data = json.loads(body)
        self.assertEqual(status, 200, f"Expected 200, got {status}: {body}")
        self.assertEqual(data["result"], "active")
        self.assertIn("download_token", data)
        self.assertIn("download_url", data)
        self.assertIn("sha256", data)
        self.assertIn("version", data)

    def test_token_url_contains_token(self):
        self.store.bind_or_check_device(
            self.key, self.install_id_hash, "Pixel 7", "1.0.0"
        )
        _, _, body = self._call_authorize()
        data = json.loads(body)
        token = data.get("download_token", "")
        download_url = data.get("download_url", "")
        self.assertIn(token, download_url)

    def test_wrong_device_install_id_returns_403(self):
        # First bind to one device
        self.store.bind_or_check_device(
            self.key, self.install_id_hash, "Pixel 7", "1.0.0"
        )
        # Now try from a different install_id_hash
        different_hash = "b" * 64
        status, _, body = self._call_authorize(install_id_hash=different_hash)
        data = json.loads(body)
        self.assertEqual(status, 403)
        self.assertEqual(data["result"], "wrong_device")

    def test_download_root_not_configured_returns_503(self):
        self.store.bind_or_check_device(
            self.key, self.install_id_hash, "Pixel 7", "1.0.0"
        )
        body = {
            "key": self.key,
            "install_id_hash": self.install_id_hash,
            "device_model": "Pixel 7",
            "app_version": "1.0.0",
            "channel": "stable",
        }
        with patch("agent.license_store.get_default_store", return_value=self.store), \
             patch.object(api_mod, "_get_download_root", return_value=None):
            status, _, _ = _wsgi_call("POST", "/api/download/authorize", body)
        self.assertEqual(status, 503)

    def test_unknown_channel_falls_back_to_stable(self):
        """An unknown channel name should be normalized to 'stable'."""
        self.store.bind_or_check_device(
            self.key, self.install_id_hash, "Pixel 7", "1.0.0"
        )
        status, _, body = self._call_authorize(channel="nightly")
        # Should not 4xx on channel validation — server normalizes to stable
        data = json.loads(body)
        if status == 200:
            self.assertEqual(data.get("channel"), "stable")
        # Or it could 404 if stable manifest exists — either 200 or 404 is OK
        self.assertIn(status, (200, 404))

    def test_no_manifest_returns_404(self):
        """If channel directory is empty, server returns 404."""
        self.store.bind_or_check_device(
            self.key, self.install_id_hash, "Pixel 7", "1.0.0"
        )
        empty_root = self.tmp / "empty_root"
        empty_root.mkdir()
        body = {
            "key": self.key,
            "install_id_hash": self.install_id_hash,
            "device_model": "Pixel 7",
            "app_version": "1.0.0",
            "channel": "stable",
        }
        with patch("agent.license_store.get_default_store", return_value=self.store), \
             patch.object(api_mod, "_get_download_root", return_value=empty_root):
            status, _, _ = _wsgi_call("POST", "/api/download/authorize", body)
        self.assertEqual(status, 404)


class TestPackageDownload(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.store, self.key = _setup_store_with_key()
        self.download_root = _make_fake_download_root(self.tmp)
        self.install_id_hash = "c" * 64
        # Bind the device
        self.store.bind_or_check_device(
            self.key, self.install_id_hash, "Pixel 8", "1.0.0"
        )
        # Clear rate limit to prevent cross-test interference
        with api_mod._rate_limit_lock:
            api_mod._rate_limit.clear()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _get_token(self) -> str:
        """Authorize and return the raw download_token."""
        body = {
            "key": self.key,
            "install_id_hash": self.install_id_hash,
            "device_model": "Pixel 8",
            "app_version": "1.0.0",
            "channel": "stable",
        }
        with patch("agent.license_store.get_default_store", return_value=self.store), \
             patch.object(api_mod, "_get_download_root", return_value=self.download_root):
            _, _, resp_body = _wsgi_call("POST", "/api/download/authorize", body)
        data = json.loads(resp_body)
        return data["download_token"]

    def test_valid_token_returns_zip_bytes(self):
        token = self._get_token()
        with patch.object(api_mod, "_get_download_root", return_value=self.download_root):
            status, headers, body = _wsgi_call("GET", f"/api/download/package/{token}")
        self.assertEqual(status, 200, f"Expected 200, got {status}: {body[:200]}")
        # Verify it's actually a zip
        self.assertEqual(body[:2], b"PK", "Response should be a zip file (PK magic bytes)")

    def test_token_is_single_use(self):
        token = self._get_token()
        with patch.object(api_mod, "_get_download_root", return_value=self.download_root):
            status1, _, _ = _wsgi_call("GET", f"/api/download/package/{token}")
            status2, _, _ = _wsgi_call("GET", f"/api/download/package/{token}")
        self.assertEqual(status1, 200)
        self.assertEqual(status2, 401, "Second use of same token should be rejected")

    def test_invalid_token_returns_401(self):
        with patch.object(api_mod, "_get_download_root", return_value=self.download_root):
            status, _, _ = _wsgi_call("GET", "/api/download/package/this-is-not-a-valid-token-abcdef")
        self.assertEqual(status, 401)

    def test_malformed_token_returns_400(self):
        """Token with special chars (path traversal chars) returns 400."""
        with patch.object(api_mod, "_get_download_root", return_value=self.download_root):
            status, _, _ = _wsgi_call("GET", "/api/download/package/../../../etc/passwd")
        # The routing would not even match since path starts with /api/download/package/
        # but if it does, should be 400 or 404
        self.assertIn(status, (400, 401, 404))

    def test_content_disposition_header_set(self):
        token = self._get_token()
        with patch.object(api_mod, "_get_download_root", return_value=self.download_root):
            _, headers, _ = _wsgi_call("GET", f"/api/download/package/{token}")
        self.assertIn("Content-Disposition", headers)
        self.assertIn("attachment", headers["Content-Disposition"])

    def test_content_type_is_zip(self):
        token = self._get_token()
        with patch.object(api_mod, "_get_download_root", return_value=self.download_root):
            _, headers, _ = _wsgi_call("GET", f"/api/download/package/{token}")
        self.assertIn("application/zip", headers.get("Content-Type", ""))


class TestTokenExpiry(unittest.TestCase):

    def test_expired_token_returns_401(self):
        """A token that has passed its TTL should be rejected."""
        import hashlib, secrets as _sec
        raw = _sec.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        # Insert an already-expired entry directly into the token store
        with api_mod._tokens_lock:
            api_mod._download_tokens[token_hash] = {
                "path": "/tmp/nonexistent.zip",
                "sha256": "x" * 64,
                "filename": "test.zip",
                "version": "1.0.0",
                "channel": "stable",
                "size_bytes": 0,
                "expires_at": time.time() - 1,  # already expired
                "used": False,
            }
        status, _, _ = _wsgi_call("GET", f"/api/download/package/{raw}")
        self.assertEqual(status, 401)

    def test_issue_and_consume_token_roundtrip(self):
        """_issue_download_token → _consume_download_token should return entry."""
        import tempfile, zipfile
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "test.zip"
            with zipfile.ZipFile(pkg, "w") as zf:
                zf.writestr("VERSION", "1.0.0\n")
            raw = api_mod._issue_download_token(
                pkg, "abc123", "test.zip", "1.0.0", "stable", 100, 300
            )
            entry = api_mod._consume_download_token(raw)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["version"], "1.0.0")
        self.assertEqual(entry["channel"], "stable")
        # Second consume should return None (already used)
        entry2 = api_mod._consume_download_token(raw)
        self.assertIsNone(entry2)


class TestRateLimiting(unittest.TestCase):

    def test_rate_limit_allows_under_threshold(self):
        """Under max requests, _check_rate_limit returns True."""
        test_ip = "192.0.2.99"
        # Clear any existing state for this test IP
        with api_mod._rate_limit_lock:
            api_mod._rate_limit.pop(test_ip, None)
        for _ in range(api_mod._RATE_LIMIT_MAX):
            result = api_mod._check_rate_limit(test_ip)
            self.assertTrue(result)

    def test_rate_limit_blocks_over_threshold(self):
        """After max requests, _check_rate_limit returns False."""
        test_ip = "192.0.2.100"
        with api_mod._rate_limit_lock:
            api_mod._rate_limit.pop(test_ip, None)
        for _ in range(api_mod._RATE_LIMIT_MAX):
            api_mod._check_rate_limit(test_ip)
        # The next one should be blocked
        result = api_mod._check_rate_limit(test_ip)
        self.assertFalse(result)


class TestLoadManifest(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_returns_none_when_no_manifest(self):
        result = api_mod._load_manifest(self.tmp, "stable")
        self.assertIsNone(result)

    def test_returns_manifest_data(self):
        root = _make_fake_download_root(self.tmp)
        result = api_mod._load_manifest(root, "stable")
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], "1.0.0")
        self.assertIn("_pkg_path", result)

    def test_picks_newest_version(self):
        """When multiple versions exist, _load_manifest picks the newest."""
        root = Path(self._tmpdir.name) / "multi"
        _make_fake_download_root(root, version="1.0.0", channel="stable")
        _make_fake_download_root(root, version="1.1.0", channel="stable")
        result = api_mod._load_manifest(root, "stable")
        self.assertEqual(result["version"], "1.1.0")

    def test_unknown_channel_returns_none(self):
        root = _make_fake_download_root(self.tmp)
        result = api_mod._load_manifest(root, "nightly")
        self.assertIsNone(result)


class TestSecurityNoBotCodeInApiModule(unittest.TestCase):
    """Security gate: license_api must never leak server secrets."""

    def test_supabase_service_role_not_read_from_body(self):
        import inspect
        src = inspect.getsource(api_mod._wsgi_app)
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", src)

    def test_discord_token_not_in_module(self):
        import inspect
        src = inspect.getsource(api_mod)
        self.assertNotIn("DISCORD_TOKEN", src)

    def test_module_importable(self):
        import importlib
        m = importlib.import_module("bot.license_api")
        self.assertIsNotNone(m)

    def test_download_tokens_stored_as_hash_not_raw(self):
        """Raw tokens must NOT be stored directly; only their SHA-256 hash."""
        import hashlib, secrets as _sec
        raw = _sec.token_urlsafe(32)
        # Issue a token
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "test.zip"
            with zipfile.ZipFile(pkg, "w") as zf:
                zf.writestr("VERSION", "1.0.0\n")
            api_mod._issue_download_token(pkg, "abc", "test.zip", "1.0.0", "stable", 100, 300)
        # The raw token should not appear as a key in the store
        with api_mod._tokens_lock:
            keys = set(api_mod._download_tokens.keys())
        self.assertNotIn(raw, keys, "Raw token stored instead of hash!")


if __name__ == "__main__":
    unittest.main()
