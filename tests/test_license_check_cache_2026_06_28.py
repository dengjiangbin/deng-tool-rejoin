"""Regression: /api/license/check caches POSITIVE results to protect the shared
Supabase pool during a key-gen rush, while negatives stay live (instant state
changes) and every response keeps a fresh server clock + fresh session.

Root incident: the check/heartbeat endpoint ran 2-3 Supabase queries with NO
cache, so a flood of agent polls exhausted the PostgREST connection pool and took
key generation + history down. The cache makes repeat checks instant and collapses
DB load under load.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import bot.license_api as api_mod
from agent.license_store import LocalJsonLicenseStore


def _tmp_store() -> LocalJsonLicenseStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    Path(tmp.name).unlink()
    return LocalJsonLicenseStore(Path(tmp.name))


def _wsgi(path: str, body: dict) -> tuple[int, dict]:
    body_bytes = json.dumps(body).encode()
    environ = {
        "REQUEST_METHOD": "POST",
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
    captured_status: list[str] = []

    def start_response(status: str, headers: list) -> None:
        captured_status.append(status)

    chunks = api_mod._wsgi_app(environ, start_response)
    raw = b"".join(chunks)
    return int(captured_status[0].split()[0]), json.loads(raw)


class _CountingStore:
    """Wraps a real store and counts validate_existing_binding (DB) calls."""

    def __init__(self, inner: LocalJsonLicenseStore) -> None:
        self._inner = inner
        self.calls = 0

    def validate_existing_binding(self, *a, **k):
        self.calls += 1
        return self._inner.validate_existing_binding(*a, **k)


class TestLicenseCheckCache(unittest.TestCase):
    def setUp(self) -> None:
        # Isolate the module-level cache + ensure caching is on for the test.
        api_mod._license_check_cache.clear()
        self._ttl_backup = api_mod._LICENSE_CHECK_CACHE_TTL
        api_mod._LICENSE_CHECK_CACHE_TTL = 45.0

    def tearDown(self) -> None:
        api_mod._license_check_cache.clear()
        api_mod._LICENSE_CHECK_CACHE_TTL = self._ttl_backup
        s = getattr(self, "_store", None)
        if s is not None and hasattr(s, "_path"):
            try:
                s._path.unlink()
            except FileNotFoundError:
                pass

    def _active_store(self, device: str) -> tuple[LocalJsonLicenseStore, str]:
        store = _tmp_store()
        self._store = store
        uid = "cache_user"
        store.get_or_create_user(uid)
        full_key = store.create_key_for_user(uid)
        store.bind_or_check_device(full_key, device, "Pixel 7", "1.0")
        return store, full_key

    def test_active_result_cached_skips_db_on_repeat(self) -> None:
        device = "aa" * 32
        store, full_key = self._active_store(device)
        counting = _CountingStore(store)
        body = {"key": full_key, "install_id_hash": device, "client_protocol": 2}

        with patch("agent.license_store.get_default_store", return_value=counting):
            s1, r1 = _wsgi("/api/license/check", body)
            s2, r2 = _wsgi("/api/license/check", body)

        self.assertEqual((s1, s2), (200, 200))
        self.assertEqual(r1["result"], "active")
        self.assertEqual(r2["result"], "active")
        # Second call must be a cache hit → store hit exactly once total.
        self.assertEqual(counting.calls, 1, "active result should be served from cache on repeat")

    def test_cache_hit_issues_fresh_session_and_fresh_server_now(self) -> None:
        device = "aa" * 32
        store, full_key = self._active_store(device)
        counting = _CountingStore(store)
        body = {"key": full_key, "install_id_hash": device, "client_protocol": 2}

        with patch("agent.license_store.get_default_store", return_value=counting):
            _, r1 = _wsgi("/api/license/check", body)
            _, r2 = _wsgi("/api/license/check", body)

        self.assertIn("session", r1)
        self.assertIn("session", r2)
        # Fresh session per request even on a cache hit.
        self.assertNotEqual(r1["session"]["session_id"], r2["session"]["session_id"])
        # Fresh server clock on every response (never the stale cached one).
        self.assertIn("server_now", r1)
        self.assertIn("server_now", r2)

    def test_negative_result_not_cached(self) -> None:
        device = "aa" * 32
        store, full_key = self._active_store(device)
        counting = _CountingStore(store)
        # Query from a DIFFERENT device → wrong_device (a negative).
        body = {"key": full_key, "install_id_hash": "bb" * 32, "client_protocol": 2}

        with patch("agent.license_store.get_default_store", return_value=counting):
            _, r1 = _wsgi("/api/license/check", body)
            _, r2 = _wsgi("/api/license/check", body)

        self.assertEqual(r1["result"], "wrong_device")
        self.assertEqual(r2["result"], "wrong_device")
        # Negatives must NOT be cached → store consulted every time.
        self.assertEqual(counting.calls, 2, "negative results must never be cached")

    def test_ttl_zero_disables_cache(self) -> None:
        api_mod._LICENSE_CHECK_CACHE_TTL = 0.0
        device = "aa" * 32
        store, full_key = self._active_store(device)
        counting = _CountingStore(store)
        body = {"key": full_key, "install_id_hash": device, "client_protocol": 2}

        with patch("agent.license_store.get_default_store", return_value=counting):
            _wsgi("/api/license/check", body)
            _wsgi("/api/license/check", body)

        self.assertEqual(counting.calls, 2, "TTL=0 must disable caching entirely")


if __name__ == "__main__":
    unittest.main()
