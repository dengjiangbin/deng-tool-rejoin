"""Tests for authorized Discord license embed formatting and active-key filtering."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.key_stats_format import (
    build_license_admin_stats_description,
    build_reset_hwid_log_description,
    filter_active_visible_license_rows,
    format_authorized_active_key_line,
    is_active_visible_license_row,
)
from agent.license import hash_license_key, normalize_license_key
from agent.license_store import LocalJsonLicenseStore, get_license_stats_for_discord_user


def _tmp_store() -> LocalJsonLicenseStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    Path(tmp.name).unlink()
    return LocalJsonLicenseStore(Path(tmp.name))


def _insert_key(
    store: LocalJsonLicenseStore,
    uid: str,
    *,
    status: str = "active",
    redeemed_at: str | None = None,
    expires_at: str | None = None,
    device_model: str | None = None,
    active_binding: bool = False,
) -> tuple[str, str]:
    raw = store.create_key_for_user(uid)
    key_hash = hash_license_key(normalize_license_key(raw))
    db = store._load()
    rec = db["keys"][key_hash]
    rec["status"] = status
    if redeemed_at is not None:
        rec["redeemed_at"] = redeemed_at
    if expires_at is not None:
        rec["expires_at"] = expires_at
    db["keys"][key_hash] = rec
    if active_binding:
        db.setdefault("bindings", {})[key_hash] = {
            "is_active": True,
            "install_id_hash": "aa" * 32,
            "device_model": device_model or "SM-N9810",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    store._save(db)
    return raw, key_hash


class TestResetHwidLogFormat(unittest.TestCase):
    def test_reset_hwid_log_description_format(self) -> None:
        stats = {
            "key_generated_count": 2,
            "key_redeemed_count": 0,
            "unbound_key_count": 2,
            "bound_key_count": 0,
            "reset_hwid_count": 7,
        }
        description = build_reset_hwid_log_description(
            user_mention="@DENG",
            reset_key="DENG-68C9-0BA2-F745-E506",
            stats=stats,
        )
        self.assertIn("**User:** @DENG", description)
        self.assertIn("**Reset Key:** DENG-68C9-0BA2-F745-E506", description)
        self.assertIn("**Current Key Generated:** 2", description)
        self.assertIn("**Current Key Redeemed:** 0", description)
        self.assertIn("**Current Unbound Key:** 2", description)
        self.assertIn("**Current Bound Key:** 0", description)
        self.assertIn("**Current Reset HWID:** 7 times", description)
        self.assertNotIn("DENG-68C9...E506", description)
        self.assertNotIn("\nUser\n", description)
        self.assertNotIn("\nReset Key\n", description)

    def test_post_license_log_source_uses_description_not_fields(self) -> None:
        source = (PROJECT / "bot" / "cog_license_panel.py").read_text(encoding="utf-8")
        fn_start = source.index("async def _post_license_log")
        fn_end = source.index("# ── Redeem modal", fn_start)
        fn_body = source[fn_start:fn_end]
        self.assertIn("description=description", fn_body)
        self.assertIn("build_reset_hwid_log_description", fn_body)
        self.assertNotIn("embed.add_field", fn_body)


class TestLicenseAdminStatsFormat(unittest.TestCase):
    def test_license_admin_description_format(self) -> None:
        stats = {
            "key_generated_count": 2,
            "key_redeemed_count": 0,
            "unbound_key_count": 2,
            "bound_key_count": 0,
            "reset_hwid_count": 7,
            "key_executed_count": 0,
        }
        active_rows = [
            {
                "full_key_plaintext": "DENG-68C9-0BA2-F745-E506",
                "used": True,
                "device_display": "SM-N9810",
            },
            {
                "full_key_plaintext": "DENG-E132-C484-51E0-96A7",
                "used": False,
            },
        ]
        description = build_license_admin_stats_description(
            user_label="@DENG (110184213604499456)",
            stats=stats,
            active_rows=active_rows,
        )
        self.assertIn("**User:** @DENG (110184213604499456)", description)
        self.assertIn("**Generated (Active):** 2", description)
        self.assertIn("**Redeemed:** 0", description)
        self.assertIn("**Unbound:** 2", description)
        self.assertIn("**Bound:** 0", description)
        self.assertIn("**HWID Resets:** 7 times", description)
        self.assertIn("**Key Executed (Public):** 0", description)
        self.assertIn("**Keys (2)**", description)
        self.assertIn("DENG-68C9-0BA2-F745-E506 — active — SM-N9810", description)
        self.assertIn("DENG-E132-C484-51E0-96A7 — active — (unbound)", description)
        self.assertNotIn("revoked", description.lower())
        self.assertNotIn("...", description)

    def test_license_command_source_uses_description_not_fields(self) -> None:
        source = (PROJECT / "bot" / "cog_license_panel.py").read_text(encoding="utf-8")
        fn_start = source.index("async def cmd_license_user")
        fn_end = source.index("# ── Persistent view restoration", fn_start)
        fn_body = source[fn_start:fn_end]
        self.assertIn("build_license_admin_stats_description", fn_body)
        self.assertIn("description=description", fn_body)
        self.assertNotIn("embed.add_field", fn_body)


class TestActiveVisibleFiltering(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patch = mock.patch.dict(
            os.environ,
            {"LICENSE_KEY_EXPORT_SECRET": "discord-format-test-secret"},
            clear=False,
        )
        self._env_patch.start()
        self.store = _tmp_store()
        self.uid = "110184213604499456"
        self.store.get_or_create_user(self.uid)

    def tearDown(self) -> None:
        self._env_patch.stop()

    def test_revoked_key_hidden(self) -> None:
        import agent.license_store as ls

        old_cd = ls.GENERATION_COOLDOWN_SECONDS
        ls.GENERATION_COOLDOWN_SECONDS = 0
        try:
            active_raw, _ = _insert_key(self.store, self.uid)
            revoked_raw, _ = _insert_key(self.store, self.uid, status="revoked")
        finally:
            ls.GENERATION_COOLDOWN_SECONDS = old_cd
        rows = self.store.list_user_keys_for_stats(self.uid)
        active = filter_active_visible_license_rows(rows)
        active_full = [r["full_key_plaintext"] for r in active]
        self.assertIn(active_raw, active_full)
        self.assertNotIn(revoked_raw, active_full)
        stats = get_license_stats_for_discord_user(self.store, self.uid)
        self.assertEqual(stats["key_generated_count"], 1)

    def test_expired_unredeemed_key_hidden(self) -> None:
        import agent.license_store as ls

        old_cd = ls.GENERATION_COOLDOWN_SECONDS
        ls.GENERATION_COOLDOWN_SECONDS = 0
        try:
            active_raw, _ = _insert_key(self.store, self.uid)
            expired_raw, _ = _insert_key(self.store, self.uid, status="expired")
        finally:
            ls.GENERATION_COOLDOWN_SECONDS = old_cd
        rows = self.store.list_user_keys_for_stats(self.uid)
        active = filter_active_visible_license_rows(rows)
        active_full = [r.get("full_key_plaintext") for r in active]
        self.assertEqual(len(active), 1)
        self.assertIn(active_raw, active_full)
        self.assertNotIn(expired_raw, active_full)

    def test_unredeemed_not_expired_shown(self) -> None:
        raw, _ = _insert_key(self.store, self.uid, status="active")
        row = self.store.list_user_keys_for_stats(self.uid)[0]
        self.assertTrue(is_active_visible_license_row(row))
        line = format_authorized_active_key_line(row)
        self.assertEqual(line, f"{raw} — active — (unbound)")

    def test_bound_active_key_shown_with_device(self) -> None:
        raw, _ = _insert_key(
            self.store,
            self.uid,
            active_binding=True,
            device_model="SM-N9810",
        )
        row = self.store.list_user_keys_for_stats(self.uid)[0]
        line = format_authorized_active_key_line(row)
        self.assertEqual(line, f"{raw} — active — SM-N9810")

    def test_redeemed_unbound_active_key_shown(self) -> None:
        raw, _ = _insert_key(
            self.store,
            self.uid,
            redeemed_at="2026-01-02T00:00:00+00:00",
        )
        row = self.store.list_user_keys_for_stats(self.uid)[0]
        self.assertTrue(is_active_visible_license_row(row))
        line = format_authorized_active_key_line(row)
        self.assertEqual(line, f"{raw} — active — (unbound)")

    def test_disabled_key_hidden(self) -> None:
        raw, _ = _insert_key(self.store, self.uid, status="disabled")
        row = self.store.list_user_keys_for_stats(self.uid)[0]
        self.assertFalse(is_active_visible_license_row(row))

    def test_keys_count_matches_displayed_lines(self) -> None:
        import agent.license_store as ls

        old_cd = ls.GENERATION_COOLDOWN_SECONDS
        ls.GENERATION_COOLDOWN_SECONDS = 0
        try:
            _insert_key(self.store, self.uid)
            _insert_key(self.store, self.uid, active_binding=True, device_model="SM-N9810")
            _insert_key(self.store, self.uid, status="revoked")
            _insert_key(self.store, self.uid, status="expired")
        finally:
            ls.GENERATION_COOLDOWN_SECONDS = old_cd
        rows = filter_active_visible_license_rows(
            self.store.list_user_keys_for_stats(self.uid)
        )
        stats = get_license_stats_for_discord_user(self.store, self.uid)
        description = build_license_admin_stats_description(
            user_label=f"@DENG ({self.uid})",
            stats=stats,
            active_rows=rows,
        )
        self.assertIn(f"**Keys ({len(rows)})**", description)
        self.assertEqual(stats["key_generated_count"], len(rows))
        self.assertEqual(description.count(" — active — "), len(rows))

    def test_no_active_keys_message(self) -> None:
        _insert_key(self.store, self.uid, status="revoked")
        rows = filter_active_visible_license_rows(
            self.store.list_user_keys_for_stats(self.uid)
        )
        stats = get_license_stats_for_discord_user(self.store, self.uid)
        description = build_license_admin_stats_description(
            user_label=f"@DENG ({self.uid})",
            stats=stats,
            active_rows=rows,
        )
        self.assertIn("**Keys (0)**", description)
        self.assertIn("No active keys.", description)


if __name__ == "__main__":
    unittest.main()
