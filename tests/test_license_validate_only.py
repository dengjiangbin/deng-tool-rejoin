"""Validate-only /api/license/check must never bind; manual bind uses /api/license/bind."""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import bot.license_api as api_mod
from agent import commands
from agent.config import default_config
from agent.license import HWID_RESET_REENTRY_MESSAGE, hash_license_key, normalize_license_key
from agent.license_store import (
    LocalJsonLicenseStore,
    RESULT_ACTIVE,
    RESULT_REQUIRES_MANUAL_REBIND,
)


def _tmp_store() -> LocalJsonLicenseStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    Path(tmp.name).unlink()
    return LocalJsonLicenseStore(Path(tmp.name))


def _args(**kw) -> argparse.Namespace:
    ns = argparse.Namespace(no_color=True, verbose=False, debug=False, lines=50)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _bound_store_after_reset(uid: str = "u_validate") -> tuple[LocalJsonLicenseStore, str, str]:
    store = _tmp_store()
    store.get_or_create_user(uid)
    full_key = store.create_key_for_user(uid)
    store.bind_or_check_device(full_key, "aa" * 32, "Pixel 6", "1.0")
    key_hash = hash_license_key(normalize_license_key(full_key))
    store.reset_hwid(uid, key_hash)
    return store, full_key, key_hash


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


def _wsgi_raw(
    path: str,
    body: bytes,
    *,
    headers: dict[str, str] | None = None,
    content_type: str = "application/json",
) -> tuple[int, dict]:
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": path,
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
        "REMOTE_ADDR": "127.0.0.1",
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8787",
    }
    for k, v in (headers or {}).items():
        environ[k] = v
    captured_status: list[str] = []

    def start_response(status: str, hdrs: list) -> None:
        captured_status.append(status)

    chunks = api_mod._wsgi_app(environ, start_response)
    raw = b"".join(chunks)
    return int(captured_status[0].split()[0]), json.loads(raw)


class TestStoreValidateOnly(unittest.TestCase):
    def tearDown(self) -> None:
        s = getattr(self, "_store", None)
        if s is not None and hasattr(s, "_path"):
            try:
                s._path.unlink()
            except FileNotFoundError:
                pass

    def test_check_never_binds_unbound_key(self) -> None:
        store = _tmp_store()
        self._store = store
        uid = "unbound1"
        store.get_or_create_user(uid)
        full_key = store.create_key_for_user(uid)
        before = store.get_binding_snapshot(full_key)
        result = store.validate_existing_binding(full_key, "bb" * 32, "Pixel", "1.0")
        after = store.get_binding_snapshot(full_key)
        self.assertEqual(result, RESULT_REQUIRES_MANUAL_REBIND)
        self.assertEqual(before, after)

    def test_check_never_reactivates_inactive_binding(self) -> None:
        store, full_key, key_hash = _bound_store_after_reset()
        self._store = store
        before = store._load()["bindings"][key_hash].copy()
        result = store.validate_existing_binding(full_key, "bb" * 32, "Pixel", "1.0")
        after = store._load()["bindings"][key_hash]
        self.assertEqual(result, RESULT_REQUIRES_MANUAL_REBIND)
        self.assertFalse(after.get("is_active"))
        self.assertEqual(before.get("install_id_hash"), after.get("install_id_hash"))
        self.assertEqual(before.get("bound_at"), after.get("bound_at"))

    def test_check_active_when_still_bound(self) -> None:
        store = _tmp_store()
        self._store = store
        uid = "bound2"
        store.get_or_create_user(uid)
        full_key = store.create_key_for_user(uid)
        store.bind_or_check_device(full_key, "cc" * 32, "Pixel", "1.0")
        self.assertEqual(
            store.validate_existing_binding(full_key, "cc" * 32, "Pixel", "1.0"),
            RESULT_ACTIVE,
        )

    def test_manual_bind_after_reset(self) -> None:
        store, full_key, key_hash = _bound_store_after_reset()
        self._store = store
        self.assertEqual(
            store.bind_or_check_device(full_key, "dd" * 32, "Pixel", "1.0"),
            RESULT_ACTIVE,
        )
        self.assertTrue(store._load()["bindings"][key_hash].get("is_active"))


class TestLicenseApiValidateOnly(unittest.TestCase):
    def tearDown(self) -> None:
        s = getattr(self, "_store", None)
        if s is not None and hasattr(s, "_path"):
            try:
                s._path.unlink()
            except FileNotFoundError:
                pass

    def test_check_ignores_bind_allowed_true(self) -> None:
        store, full_key, key_hash = _bound_store_after_reset()
        self._store = store
        before = store._load()["bindings"][key_hash].copy()
        with patch("agent.license_store.get_default_store", return_value=store):
            status, resp = _wsgi(
                "/api/license/check",
                {
                    "key": full_key,
                    "install_id_hash": "ee" * 32,
                    "device_model": "Pixel",
                    "app_version": "1.0",
                    "bind_allowed": True,
                    "manual_entry": True,
                },
            )
        after = store._load()["bindings"][key_hash]
        self.assertEqual(status, 200)
        self.assertEqual(resp.get("result"), RESULT_REQUIRES_MANUAL_REBIND)
        self.assertEqual(before.get("is_active"), after.get("is_active"))
        self.assertEqual(before.get("install_id_hash"), after.get("install_id_hash"))

    def test_check_missing_bind_allowed_does_not_bind(self) -> None:
        store, full_key, key_hash = _bound_store_after_reset()
        self._store = store
        before = store._load()["bindings"][key_hash].copy()
        with patch("agent.license_store.get_default_store", return_value=store):
            status, resp = _wsgi(
                "/api/license/check",
                {
                    "key": full_key,
                    "install_id_hash": "ff" * 32,
                    "device_model": "Pixel",
                    "app_version": "1.0",
                },
            )
        after = store._load()["bindings"][key_hash]
        self.assertEqual(resp.get("result"), RESULT_REQUIRES_MANUAL_REBIND)
        self.assertEqual(before, after)

    def test_bind_endpoint_requires_manual_flags(self) -> None:
        store, full_key, _ = _bound_store_after_reset()
        self._store = store
        with patch("agent.license_store.get_default_store", return_value=store):
            status, resp = _wsgi(
                "/api/license/bind",
                {
                    "key": full_key,
                    "install_id_hash": "11" * 32,
                    "device_model": "Pixel",
                    "app_version": "1.0",
                },
            )
        self.assertEqual(status, 403)
        self.assertEqual(resp.get("result"), RESULT_REQUIRES_MANUAL_REBIND)

    def test_bind_endpoint_binds_once(self) -> None:
        store, full_key, key_hash = _bound_store_after_reset()
        self._store = store
        with patch("agent.license_store.get_default_store", return_value=store):
            status, resp = _wsgi(
                "/api/license/bind",
                {
                    "key": full_key,
                    "install_id_hash": "22" * 32,
                    "device_model": "Pixel",
                    "app_version": "1.0",
                    "manual_entry": True,
                    "bind_allowed": True,
                },
            )
        self.assertEqual(status, 200)
        self.assertEqual(resp.get("result"), RESULT_ACTIVE)
        self.assertTrue(store._load()["bindings"][key_hash].get("is_active"))
        self.assertEqual(
            store._load()["bindings"][key_hash].get("install_id_hash"),
            "22" * 32,
        )
        self.assertIn("session", resp)
        self.assertTrue(resp["session"]["capabilities"]["probe_upload"])

    def test_explicit_too_old_protocol_rejects(self) -> None:
        store, full_key, _ = _bound_store_after_reset()
        self._store = store
        with patch("agent.license_store.get_default_store", return_value=store):
            status, resp = _wsgi(
                "/api/license/check",
                {
                    "key": full_key,
                    "install_id_hash": "aa" * 32,
                    "device_model": "Pixel",
                    "app_version": "0.9",
                    "client_protocol": 1,
                },
            )
        self.assertEqual(status, 426)
        self.assertEqual(resp.get("result"), "protocol_too_old")

    def test_probe_upload_requires_capability_session(self) -> None:
        payload = gzip.compress(json.dumps({"probe_version": 1}).encode("utf-8"))
        status, resp = _wsgi_raw(
            "/api/dev-probe/upload",
            payload,
            headers={"HTTP_CONTENT_ENCODING": "gzip"},
        )
        self.assertEqual(status, 401)
        self.assertIn("session", resp.get("error", ""))

    def test_probe_upload_accepts_server_session(self) -> None:
        session = api_mod._issue_capability_session(
            key="DENG-AAAA-BBBB-CCCC-DDDD",
            install_id_hash="33" * 32,
            client_protocol=2,
            build_id="p-test",
        )
        payload = gzip.compress(json.dumps({"probe_version": 1}).encode("utf-8"))
        with patch("agent.dev_probe_store.store_probe", return_value=("p-test", Path("probe.json"))):
            status, resp = _wsgi_raw(
                "/api/dev-probe/upload",
                payload,
                headers={
                    "HTTP_CONTENT_ENCODING": "gzip",
                    "HTTP_X_DENG_SESSION": session["session_id"],
                },
            )
        self.assertEqual(status, 201)
        self.assertEqual(resp.get("probe_id"), "p-test")


class TestStartupClientGate(unittest.TestCase):
    def setUp(self) -> None:
        commands._license_session_validated = False

    def test_cached_key_after_reset_blocks_menu_and_clears_key(self) -> None:
        cfg = default_config()
        cfg.setdefault("license", {})["key"] = "DENG-AAAA-BBBB-CCCC-DDDD"
        cfg["license"]["last_status"] = "active"
        saved: list[dict] = []

        def fake_save(updated):
            lic = updated.setdefault("license", {})
            saved.append(
                {
                    "key": lic.get("key", ""),
                    "last_status": lic.get("last_status"),
                    "last_check_at": lic.get("last_check_at"),
                }
            )
            return updated

        out = io.StringIO()
        with patch("agent.commands.load_config", side_effect=lambda: dict(cfg)), \
             patch("agent.commands.save_config", side_effect=fake_save), \
             patch("agent.commands._ensure_install_id_saved", side_effect=lambda x: x), \
             patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands._remote_license_run_check", return_value=(RESULT_REQUIRES_MANUAL_REBIND, HWID_RESET_REENTRY_MESSAGE)), \
             patch("agent.commands.safe_io.safe_prompt", return_value=None), \
             redirect_stdout(out):
            ok = commands._ensure_remote_license_menu_loop(cfg, _args(), False)

        self.assertFalse(ok)
        self.assertIn(HWID_RESET_REENTRY_MESSAGE, out.getvalue())
        self.assertTrue(saved)
        self.assertEqual(saved[-1]["key"], "")
        self.assertIsNone(saved[-1]["last_status"])
        self.assertIsNone(saved[-1]["last_check_at"])

    def test_startup_uses_validate_only_not_bind(self) -> None:
        cfg = default_config()
        cfg.setdefault("license", {})["key"] = "DENG-1111-2222-3333-4444"
        calls: list[str] = []

        with patch("agent.commands.load_config", return_value=cfg), \
             patch("agent.commands.save_config", side_effect=lambda x: x), \
             patch("agent.commands._ensure_install_id_saved", side_effect=lambda x: x), \
             patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands._remote_license_run_check", side_effect=lambda _c: calls.append("check") or ("active", "ok")), \
             patch("agent.commands._remote_license_run_bind", side_effect=lambda _c: calls.append("bind") or ("active", "ok")):
            ok = commands._ensure_remote_license_menu_loop(cfg, _args(), False)
        self.assertTrue(ok)
        self.assertEqual(calls, ["check"])

    def test_manual_key_entry_calls_bind_endpoint(self) -> None:
        cfg = default_config()
        calls: list[str] = []
        prompts = iter(["DENG-AAAA-BBBB-CCCC-DDDD"])

        with patch("agent.commands.load_config", return_value=default_config()), \
             patch("agent.commands.save_config", side_effect=lambda x: x), \
             patch("agent.commands._ensure_install_id_saved", side_effect=lambda x: x), \
             patch("agent.commands._is_interactive", return_value=True), \
             patch("agent.commands._remote_license_run_check", side_effect=lambda _c: calls.append("check") or ("active", "ok")), \
             patch("agent.commands._remote_license_run_bind", side_effect=lambda _c: calls.append("bind") or ("active", "ok")), \
             patch("agent.commands.safe_io.safe_prompt", side_effect=lambda *a, **k: next(prompts)):
            ok = commands._ensure_remote_license_menu_loop(cfg, _args(), False)
        self.assertTrue(ok)
        self.assertEqual(calls, ["bind"])

    def test_second_startup_after_reset_still_blocked(self) -> None:
        cfg = default_config()
        cfg.setdefault("license", {})["key"] = "DENG-AAAA-BBBB-CCCC-DDDD"
        cfg["license"]["last_status"] = "active"
        check_calls = 0

        def fake_check(_cfg):
            nonlocal check_calls
            check_calls += 1
            return (RESULT_REQUIRES_MANUAL_REBIND, HWID_RESET_REENTRY_MESSAGE)

        with patch("agent.commands.load_config", return_value=cfg), \
             patch("agent.commands.save_config", side_effect=lambda x: x), \
             patch("agent.commands._ensure_install_id_saved", side_effect=lambda x: x), \
             patch("agent.commands._is_interactive", return_value=False), \
             patch("agent.commands._remote_license_run_check", side_effect=fake_check), \
             patch("agent.commands._remote_license_run_bind") as mock_bind:
            ok1 = commands._ensure_remote_license_menu_loop(cfg, _args(), False)
            ok2 = commands._ensure_remote_license_menu_loop(cfg, _args(), False)
        self.assertFalse(ok1)
        self.assertFalse(ok2)
        self.assertEqual(check_calls, 1)
        mock_bind.assert_not_called()

    def test_offline_grace_blocked_without_session_validation(self) -> None:
        cfg = default_config()
        lic = cfg.setdefault("license", {})
        lic["key"] = "DENG-2222-3333-4444-5555"
        lic["last_status"] = "active"
        lic["last_check_at"] = "2026-05-01T00:00:00+00:00"
        commands._license_session_validated = False

        with patch("agent.commands.load_config", return_value=cfg), \
             patch("agent.commands.save_config", side_effect=lambda x: x), \
             patch("agent.commands._ensure_install_id_saved", side_effect=lambda x: x), \
             patch("agent.commands._is_interactive", return_value=False), \
             patch("agent.commands._remote_license_run_check", return_value=("server_unavailable", "down")), \
             patch("agent.commands._license_should_offline_grace", return_value=True):
            ok = commands._ensure_remote_license_menu_loop(cfg, _args(), False)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
