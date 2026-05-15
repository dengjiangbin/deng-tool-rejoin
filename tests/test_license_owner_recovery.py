"""Tests for agent/license_owner_recovery.py and maintenance helpers."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent import license_key_export
from agent.license import generate_license_key, hash_license_key, normalize_license_key
from agent.license_owner_recovery import (
    OwnerRecoveryError,
    backfill_plaintext_for_owner_key,
    fetch_owner_snapshot,
    inspect_summary,
    reset_unrecoverable_owner_keys,
    verify_supabase_export_columns,
    visible_license_rows_for_panel,
)
from agent.license_store import LocalJsonLicenseStore


def _tmp_store() -> LocalJsonLicenseStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    Path(tmp.name).unlink()
    return LocalJsonLicenseStore(Path(tmp.name))


class TestMigrationProbe(unittest.TestCase):
    def test_export_columns_detected_when_select_succeeds(self) -> None:
        client = MagicMock()
        chain = MagicMock()
        client.table.return_value = chain
        chain.select.return_value = chain
        chain.limit.return_value = chain
        chain.execute.return_value = MagicMock()
        ok, msg = verify_supabase_export_columns(client)
        self.assertTrue(ok)
        self.assertIn("present", msg.lower())

    def test_export_columns_missing_message(self) -> None:
        client = MagicMock()
        chain = MagicMock()
        client.table.return_value = chain
        chain.select.return_value = chain
        chain.limit.return_value = chain
        chain.execute.side_effect = Exception("PGRST204 column key_ciphertext")
        ok, msg = verify_supabase_export_columns(client)
        self.assertFalse(ok)
        self.assertIn("002", msg)


class TestVisibleRows(unittest.TestCase):
    def test_revoked_hidden_from_panel_rows(self) -> None:
        rows = [
            {"license_status": "active", "masked_key": "A"},
            {"license_status": "revoked", "masked_key": "B"},
        ]
        vis = visible_license_rows_for_panel(rows)
        self.assertEqual(len(vis), 1)
        self.assertEqual(vis[0]["masked_key"], "A")


class TestOwnerRecoveryLocal(unittest.TestCase):
    def tearDown(self) -> None:
        license_key_export.clear_export_key_cache()

    def test_backfill_stores_ciphertext(self) -> None:
        with patch.dict(os.environ, {"LICENSE_KEY_EXPORT_SECRET": "owner-rec-test"}, clear=False):
            license_key_export.clear_export_key_cache()
            store = _tmp_store()
            uid = "900000000000000001"
            store.get_or_create_user(uid)
            full = store.create_key_for_user(uid)
            kh = hash_license_key(normalize_license_key(full))
            db = store._load()
            db["keys"][kh]["key_ciphertext"] = ""
            db["keys"][kh]["key_export_available"] = False
            store._save(db)

            backfill_plaintext_for_owner_key(store, uid, full, key_id=None)
            db2 = store._load()
            ct = (db2["keys"][kh].get("key_ciphertext") or "").strip()
            self.assertTrue(ct)
            plain = license_key_export.decrypt_license_key_ciphertext(ct)
            self.assertEqual(plain, normalize_license_key(full))

        try:
            store._path.unlink()
        except FileNotFoundError:
            pass

    def test_backfill_rejects_wrong_full_key(self) -> None:
        with patch.dict(os.environ, {"LICENSE_KEY_EXPORT_SECRET": "wrong-key-test"}, clear=False):
            license_key_export.clear_export_key_cache()
            store = _tmp_store()
            uid = "900000000000000002"
            store.get_or_create_user(uid)
            full = store.create_key_for_user(uid)
            other = generate_license_key()
            while hash_license_key(normalize_license_key(other)) == hash_license_key(
                normalize_license_key(full)
            ):
                other = generate_license_key()

            with self.assertRaises(OwnerRecoveryError):
                backfill_plaintext_for_owner_key(store, uid, other, key_id=None)
        try:
            store._path.unlink()
        except FileNotFoundError:
            pass

    def test_backfill_rejects_masked_placeholder(self) -> None:
        with patch.dict(os.environ, {"LICENSE_KEY_EXPORT_SECRET": "mask-test"}, clear=False):
            license_key_export.clear_export_key_cache()
            store = _tmp_store()
            uid = "900000000000000003"
            store.get_or_create_user(uid)
            store.create_key_for_user(uid)
            with self.assertRaises(OwnerRecoveryError):
                backfill_plaintext_for_owner_key(store, uid, "DENG-AAAA...ZZZZ", key_id=None)
        try:
            store._path.unlink()
        except FileNotFoundError:
            pass

    def test_reset_unrecoverable_frees_key_slot(self) -> None:
        store = _tmp_store()
        uid = "900000000000000004"
        store.get_or_create_user(uid)
        store.create_key_for_user(uid)
        self.assertEqual(store.count_user_keys(uid), 1)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reset_unrecoverable_owner_keys(
                store,
                uid,
                confirm_token="RESET_OWNER_KEY",
                project_root=root,
            )
            backups = list((root / "data" / "backups").glob("*.json"))
            self.assertEqual(len(backups), 1)

        self.assertEqual(store.count_user_keys(uid), 0)
        store.create_key_for_user(uid)
        self.assertEqual(store.count_user_keys(uid), 1)

        try:
            store._path.unlink()
        except FileNotFoundError:
            pass

    def test_reset_only_targets_owner_rows(self) -> None:
        store = _tmp_store()
        u_a = "900000000000000005"
        u_b = "900000000000000006"
        store.get_or_create_user(u_a)
        store.get_or_create_user(u_b)
        k_a = store.create_key_for_user(u_a)
        k_b = store.create_key_for_user(u_b)
        ha = hash_license_key(normalize_license_key(k_a))
        hb = hash_license_key(normalize_license_key(k_b))

        with tempfile.TemporaryDirectory() as tmp:
            reset_unrecoverable_owner_keys(
                store,
                u_a,
                confirm_token="RESET_OWNER_KEY",
                project_root=Path(tmp),
            )

        self.assertEqual(store._load()["keys"][ha]["status"], "revoked")
        self.assertEqual(store._load()["keys"][hb]["status"], "active")

        try:
            store._path.unlink()
        except FileNotFoundError:
            pass

    def test_wrong_confirm_raises(self) -> None:
        store = _tmp_store()
        uid = "900000000000000007"
        store.get_or_create_user(uid)
        store.create_key_for_user(uid)
        with self.assertRaises(OwnerRecoveryError):
            reset_unrecoverable_owner_keys(
                store,
                uid,
                confirm_token="NOPE",
                project_root=None,
            )
        try:
            store._path.unlink()
        except FileNotFoundError:
            pass

    def test_inspect_reports_active_binding(self) -> None:
        store = _tmp_store()
        uid = "900000000000000008"
        store.get_or_create_user(uid)
        full = store.create_key_for_user(uid)
        store.bind_or_check_device(full, "aa" * 32, "Pixel", "1.0")
        summary = inspect_summary(store, uid)
        self.assertEqual(summary["active_owned_key_count"], 1)
        self.assertTrue(summary["keys"][0]["active_binding"])

        try:
            store._path.unlink()
        except FileNotFoundError:
            pass


class TestBackupSerialization(unittest.TestCase):
    def test_backup_has_no_raw_ciphertext(self) -> None:
        store = _tmp_store()
        uid = "910000000000000001"
        store.get_or_create_user(uid)
        full = store.create_key_for_user(uid)
        kh = hash_license_key(normalize_license_key(full))
        db = store._load()
        db["keys"][kh]["key_ciphertext"] = "dummy-token-not-real"
        store._save(db)

        snap = fetch_owner_snapshot(store, uid)
        raw_json = json.dumps(snap)
        self.assertNotIn("dummy-token-not-real", raw_json)
        self.assertTrue(snap["license_keys"][0].get("_key_ciphertext_present"))

        try:
            store._path.unlink()
        except FileNotFoundError:
            pass


class TestRevokedStatsEmbed(unittest.TestCase):
    def test_revoked_row_not_masked_as_full_copy(self) -> None:
        from agent.key_stats_format import build_key_stats_embed_dict

        row = {
            "masked_key": "DENG-AA...ZZ",
            "full_key_plaintext": None,
            "has_stored_ciphertext": False,
            "export_storage_configured": True,
            "license_status": "revoked",
            "used": False,
            "device_display": None,
            "last_seen_at": None,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        desc = build_key_stats_embed_dict(row)["description"]
        self.assertIn("Revoked", desc)
        self.assertNotIn("`DENG-", desc)


if __name__ == "__main__":
    unittest.main()
