"""Key Stats store, formatting, and export helpers (no live Discord)."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import license_key_export
from agent.key_stats_format import (
    build_key_stats_description,
    build_key_stats_download_body,
    format_key_block,
    format_stats_embed_title,
)
from agent.license_store import LocalJsonLicenseStore, SupabaseLicenseStore


class TestKeyStatsFormat(unittest.TestCase):
    def test_title_shows_total_and_page(self) -> None:
        t = format_stats_embed_title(total=6, page=0, total_pages=2)
        self.assertIn("Total: 6", t)
        self.assertIn("Page 1/2", t)

    def test_format_unused_no_device_lines(self) -> None:
        text = format_key_block(
            {
                "masked_key": "DENG-AA...BB",
                "full_key_plaintext": None,
                "has_stored_ciphertext": False,
                "license_status": "active",
                "used": False,
                "device_display": None,
                "last_seen_at": None,
                "created_at": "2026-01-15T12:00:00+00:00",
                "tags_label": None,
            }
        )
        self.assertIn("Unused", text)
        self.assertNotIn("Device:", text)
        self.assertIn("Not Available For Old Hashed Key", text)

    def test_format_used_shows_device(self) -> None:
        text = format_key_block(
            {
                "masked_key": "DENG-AA...BB",
                "full_key_plaintext": None,
                "has_stored_ciphertext": False,
                "license_status": "active",
                "used": True,
                "device_display": "SM-S9160",
                "last_seen_at": "2026-05-10T12:00:00+00:00",
                "created_at": "2026-01-15T12:00:00+00:00",
                "tags_label": "BANK",
            }
        )
        self.assertIn("Used", text)
        self.assertIn("SM-S9160", text)
        self.assertIn("Tags:", text)
        self.assertIn("BANK", text)

    def test_full_key_when_plain_present(self) -> None:
        text = format_key_block(
            {
                "masked_key": "DENG-AA...BB",
                "full_key_plaintext": "DENG-1111-2222-3333-4444",
                "has_stored_ciphertext": True,
                "license_status": "active",
                "used": False,
                "device_display": None,
                "last_seen_at": None,
                "created_at": "2026-01-15T12:00:00+00:00",
                "tags_label": None,
            }
        )
        self.assertIn("DENG-1111-2222-3333-4444", text)
        self.assertNotIn("Not Available For Old Hashed Key", text)

    def test_description_joins_blocks(self) -> None:
        rows = [
            {"masked_key": "A...1", "full_key_plaintext": None, "has_stored_ciphertext": False,
             "license_status": "active", "used": False, "device_display": None,
             "last_seen_at": None, "created_at": "2026-01-01T00:00:00+00:00", "tags_label": None},
        ]
        d = build_key_stats_description(rows)
        self.assertIn("License Key", d)


class TestLocalStoreStats(unittest.TestCase):
    def test_list_user_keys_for_stats_no_id_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            store = LocalJsonLicenseStore(Path(tmp) / "db.json")
            store.get_or_create_user("42")
            store.create_key_for_user("42")
            rows = store.list_user_keys_for_stats("42")
            self.assertEqual(len(rows), 1)
            for r in rows:
                self.assertNotIn("id", r)
                self.assertNotIn("key_id", r)
                self.assertIn("masked_key", r)

    def test_stats_unused_vs_used(self) -> None:
        with TemporaryDirectory() as tmp:
            store = LocalJsonLicenseStore(Path(tmp) / "db.json")
            store.get_or_create_user("7")
            store.create_key_for_user("7")
            rows = store.list_user_keys_for_stats("7")
            self.assertFalse(rows[0]["used"])
            raw = json.loads((Path(tmp) / "db.json").read_text())
            kid = next(iter(raw["keys"]))
            db = raw
            db["bindings"][kid] = {
                "install_id_hash": "h",
                "device_label": "",
                "device_model": "Pixel 9",
                "bound_at": "2026-01-01T00:00:00+00:00",
                "last_seen_at": "2026-05-01T00:00:00+00:00",
                "last_status": "active",
                "is_active": True,
            }
            (Path(tmp) / "db.json").write_text(json.dumps(db, indent=2))
            rows2 = store.list_user_keys_for_stats("7")
            self.assertTrue(rows2[0]["used"])
            self.assertIn("Pixel", rows2[0].get("device_display") or "")

    def test_get_user_key_export_rows_matches_stats(self) -> None:
        with TemporaryDirectory() as tmp:
            store = LocalJsonLicenseStore(Path(tmp) / "db.json")
            store.get_or_create_user("8")
            store.create_key_for_user("8")
            a = store.list_user_keys_for_stats("8")
            b = store.get_user_key_export_rows("8")
            self.assertEqual(len(a), len(b))


class TestDownloadBody(unittest.TestCase):
    def test_filename_pattern_in_content_user_line(self) -> None:
        rows = [
            {
                "masked_key": "DENG-EF95...DCD2",
                "full_key_plaintext": None,
                "has_stored_ciphertext": False,
                "license_status": "active",
                "used": False,
                "device_display": None,
                "last_seen_at": None,
                "created_at": "2026-01-01T00:00:00+00:00",
                "tags_label": None,
            }
        ]
        body = build_key_stats_download_body(discord_user_id="110184213604499456", rows=rows)
        self.assertIn("License Keys For User ID: 110184213604499456", body)
        self.assertIn("Total Keys: 1", body)
        self.assertIn("Not Available For Old Hashed Key", body)
        self.assertNotIn("key_hash", body.lower())

    def test_no_other_user_keys_in_slice(self) -> None:
        rows = [{"masked_key": "K1", "full_key_plaintext": None, "has_stored_ciphertext": False,
                 "license_status": "active", "used": True, "device_display": "D",
                 "last_seen_at": None, "created_at": "2026-01-01T00:00:00+00:00", "tags_label": None}]
        body = build_key_stats_download_body(discord_user_id="999", rows=rows)
        self.assertNotIn("888", body)


class TestLicenseKeyExport(unittest.TestCase):
    def tearDown(self) -> None:
        license_key_export.clear_export_key_cache()

    def test_missing_secret_does_not_crash_encrypt(self) -> None:
        with patch.dict(os.environ, {"LICENSE_KEY_EXPORT_SECRET": ""}, clear=False):
            license_key_export.clear_export_key_cache()
            self.assertIsNone(license_key_export.encrypt_license_key_plaintext("DENG-A-B-C-D"))

    def test_roundtrip_with_secret(self) -> None:
        with patch.dict(os.environ, {"LICENSE_KEY_EXPORT_SECRET": "unit-test-secret-value"}, clear=False):
            license_key_export.clear_export_key_cache()
            ct = license_key_export.encrypt_license_key_plaintext("DENG-AAAA-BBBB-CCCC-DDDD")
            self.assertIsNotNone(ct)
            plain = license_key_export.decrypt_license_key_ciphertext(ct or "")
            self.assertEqual(plain, "DENG-AAAA-BBBB-CCCC-DDDD")


class TestLocalStoreEncryptedExport(unittest.TestCase):
    def tearDown(self) -> None:
        license_key_export.clear_export_key_cache()

    def test_generated_key_stores_ciphertext_when_configured(self) -> None:
        with patch.dict(os.environ, {"LICENSE_KEY_EXPORT_SECRET": "export-secret-test"}, clear=False):
            license_key_export.clear_export_key_cache()
            with TemporaryDirectory() as tmp:
                store = LocalJsonLicenseStore(Path(tmp) / "db.json")
                store.get_or_create_user("1")
                full = store.create_key_for_user("1")
                raw = json.loads((Path(tmp) / "db.json").read_text())
                rec = next(iter(raw["keys"].values()))
                self.assertTrue(rec.get("key_export_available"))
                self.assertIsNotNone(rec.get("key_ciphertext"))
                rows = store.list_user_keys_for_stats("1")
                self.assertEqual(rows[0].get("full_key_plaintext"), full)


class TestSupabaseStatsMocked(unittest.TestCase):
    def test_stats_output_has_no_internal_id(self) -> None:
        store = SupabaseLicenseStore.__new__(SupabaseLicenseStore)

        def table_fn(name: str) -> MagicMock:
            m = MagicMock()
            if name == "license_keys":
                sel = m.select.return_value.eq.return_value
                sel.execute.return_value = MagicMock(
                    data=[
                        {
                            "id": "SUPER_SECRET_HASH",
                            "prefix": "DENG-8F3A",
                            "suffix": "44F0",
                            "status": "active",
                            "plan": "standard",
                            "created_at": "2026-03-01T00:00:00+00:00",
                            "key_ciphertext": None,
                            "key_export_available": False,
                        }
                    ]
                )
            elif name == "device_bindings":
                sel = m.select.return_value.eq.return_value
                sel.execute.return_value = MagicMock(
                    data=[
                        {
                            "device_model": "Phone",
                            "device_label": "",
                            "last_seen_at": "2026-05-01T00:00:00+00:00",
                            "is_active": True,
                        }
                    ]
                )
            elif name == "hwid_reset_logs":
                sel = m.select.return_value.eq.return_value.gte.return_value
                sel.execute.return_value = MagicMock(count=0)
            return m

        mock_client = MagicMock()
        mock_client.table.side_effect = table_fn
        store._client = mock_client  # type: ignore[attr-defined]

        rows = store.list_user_keys_for_stats("user-a")
        self.assertEqual(len(rows), 1)
        dumped = json.dumps(rows[0])
        self.assertNotIn("SUPER_SECRET_HASH", dumped)


if __name__ == "__main__":
    unittest.main()
