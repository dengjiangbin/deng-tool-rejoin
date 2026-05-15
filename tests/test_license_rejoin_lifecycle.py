"""License lifecycle: redemption required before tool binds HWID (Rejoin)."""

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
from agent.license import (
    REDEEM_IN_PANEL_HINT,
    normalize_license_key,
    hash_license_key,
    check_remote_license_status,
)
from agent.key_stats_format import build_key_stats_embed_dict
from agent.license_panel import build_generate_success_response, build_redeem_success_response
from agent.license_store import (
    RESULT_ACTIVE,
    RESULT_KEY_NOT_REDEEMED,
    RESULT_WRONG_DEVICE,
    KeyOwnershipError,
    LocalJsonLicenseStore,
)


def _tmp_store() -> LocalJsonLicenseStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    Path(tmp.name).unlink()
    return LocalJsonLicenseStore(Path(tmp.name))


def _wsgi_check(body: dict) -> tuple[int, dict]:
    body_bytes = json.dumps(body).encode()
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/api/license/check",
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
    captured_headers: list[tuple[str, str]] = []

    def start_response(status: str, headers: list) -> None:
        captured_status.append(status)
        captured_headers.extend(headers)

    chunks = api_mod._wsgi_app(environ, start_response)
    raw = b"".join(chunks)
    return int(captured_status[0].split()[0]), json.loads(raw)


class TestRejoinLicenseLifecycle(unittest.TestCase):
    def tearDown(self) -> None:
        s = getattr(self, "_store", None)
        if s is not None and hasattr(s, "_path"):
            try:
                s._path.unlink()
            except FileNotFoundError:
                pass

    def test_1_unowned_key_tool_verify_rejected(self) -> None:
        store = _tmp_store()
        self._store = store
        owner = "100000000000000001"
        store.get_or_create_user(owner)
        full = store.create_key_for_user(owner)
        kh = hash_license_key(normalize_license_key(full))
        db = store._load()
        db["keys"][kh]["owner_discord_id"] = None
        store._save(db)

        self.assertEqual(
            store.bind_or_check_device(full, "aa" * 32, "X", "1"),
            RESULT_KEY_NOT_REDEEMED,
        )
        self.assertNotIn(kh, store._load().get("bindings", {}))

    def test_2_owned_unbound_first_verify_binds(self) -> None:
        store = _tmp_store()
        self._store = store
        uid = "200000000000000002"
        store.get_or_create_user(uid)
        full = store.create_key_for_user(uid)
        self.assertEqual(
            store.bind_or_check_device(full, "bb" * 32, "Pixel", "1"),
            RESULT_ACTIVE,
        )
        kh = hash_license_key(normalize_license_key(full))
        b = store._load()["bindings"][kh]
        self.assertTrue(b.get("is_active"))
        self.assertEqual(b.get("install_id_hash"), "bb" * 32)

    def test_3_4_bound_same_vs_wrong_hwid(self) -> None:
        store = _tmp_store()
        self._store = store
        uid = "300000000000000003"
        store.get_or_create_user(uid)
        full = store.create_key_for_user(uid)
        h1 = "cc" * 32
        h2 = "dd" * 32
        self.assertEqual(store.bind_or_check_device(full, h1, "A", "1"), RESULT_ACTIVE)
        self.assertEqual(store.bind_or_check_device(full, h1, "A", "1"), RESULT_ACTIVE)
        self.assertEqual(store.bind_or_check_device(full, h2, "B", "1"), RESULT_WRONG_DEVICE)

    def test_5_generate_owned_unbound_until_verify(self) -> None:
        store = _tmp_store()
        self._store = store
        uid = "400000000000000004"
        store.get_or_create_user(uid)
        full = store.create_key_for_user(uid)
        kh = hash_license_key(normalize_license_key(full))
        rec = store._load()["keys"][kh]
        self.assertEqual(rec.get("owner_discord_id"), uid)
        self.assertNotIn(kh, store._load().get("bindings", {}))

    def test_6_redeem_sets_owner(self) -> None:
        store = _tmp_store()
        self._store = store
        admin = "500000000000000005"
        target = "500000000000000006"
        store.get_or_create_user(admin)
        store.get_or_create_user(target)
        full = store.create_key_for_user(admin)
        kh = hash_license_key(normalize_license_key(full))
        db = store._load()
        db["keys"][kh]["owner_discord_id"] = None
        store._save(db)
        store.redeem_key_for_user(target, full)
        self.assertEqual(store._load()["keys"][kh]["owner_discord_id"], target)

    def test_7_reset_hwid_random_user_unowned_raises(self) -> None:
        store = _tmp_store()
        self._store = store
        store.get_or_create_user("610")
        full = store.create_key_for_user("610")
        kh = hash_license_key(normalize_license_key(full))
        db = store._load()
        db["keys"][kh]["owner_discord_id"] = None
        db["bindings"][kh] = {
            "install_id_hash": "ee" * 32,
            "device_model": "Z",
            "device_label": "",
            "bound_at": "2026-01-01T00:00:00+00:00",
            "last_seen_at": None,
            "last_status": "active",
            "is_active": True,
        }
        store._save(db)
        with self.assertRaises(KeyOwnershipError):
            store.reset_hwid("999999999999999999", kh)

    def test_8_key_stats_unused_ready_copy_text(self) -> None:
        row = {
            "masked_key": "DENG-1111...4444",
            "full_key_plaintext": "DENG-1111-2222-3333-4444",
            "has_stored_ciphertext": True,
            "export_storage_configured": True,
            "license_status": "active",
            "used": False,
            "device_display": None,
            "last_seen_at": None,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        desc = build_key_stats_embed_dict(row)["description"]
        self.assertIn("Unused / Ready for first device", desc)
        self.assertIn("copy block", desc.lower())
        self.assertNotIn("`DENG-1111-2222-3333-4444`", desc)
        self.assertNotIn("DENG-1111...4444", desc)

    def test_9_panel_copy_views_full_key_no_mask(self) -> None:
        k = "DENG-AAAA-BBBB-CCCC-DDDD"
        for payload in (
            build_generate_success_response(k),
            build_redeem_success_response(k),
        ):
            content = payload.get("content") or ""
            self.assertIn(k, content)
            self.assertNotIn("...", content)
            self.assertNotIn(k, payload["embed"]["description"])

    def test_10_one_user_one_key_one_device_rule(self) -> None:
        store = _tmp_store()
        self._store = store
        uid = "700000000000000007"
        store.get_or_create_user(uid)
        full = store.create_key_for_user(uid)
        h_ok = "ff" * 32
        h_bad = "00" * 32
        self.assertEqual(store.bind_or_check_device(full, h_ok, "Phone1", "1"), RESULT_ACTIVE)
        self.assertEqual(store.bind_or_check_device(full, h_bad, "Phone2", "1"), RESULT_WRONG_DEVICE)
        self.assertEqual(store.count_user_keys(uid), 1)

    def test_license_api_json_key_not_redeemed(self) -> None:
        store = _tmp_store()
        self._store = store
        uid = "800000000000000008"
        store.get_or_create_user(uid)
        full = store.create_key_for_user(uid)
        kh = hash_license_key(normalize_license_key(full))
        db = store._load()
        db["keys"][kh]["owner_discord_id"] = None
        store._save(db)
        body = {
            "key": full,
            "install_id_hash": "11" * 32,
            "device_model": "Pixel",
            "app_version": "1.0",
        }
        with patch("agent.license_store.get_default_store", return_value=store):
            status, data = _wsgi_check(body)
        self.assertEqual(status, 200)
        self.assertEqual(data.get("result"), "key_not_redeemed")
        self.assertIn("not been redeemed", (data.get("message") or "").lower())

    def test_remote_client_maps_key_not_redeemed_hint(self) -> None:
        with patch("agent.license._license_api_post_json") as mock_post:
            mock_post.return_value = {
                "result": "key_not_redeemed",
                "message": "ignored-by-client",
            }
            r, msg = check_remote_license_status(
                "https://example.invalid",
                license_key="DENG-1111-2222-3333-4444",
                install_id="a" * 32,
                device_model="x",
                app_version="1",
            )
        self.assertEqual(r, "key_not_redeemed")
        self.assertEqual(msg, REDEEM_IN_PANEL_HINT)


if __name__ == "__main__":
    unittest.main()
