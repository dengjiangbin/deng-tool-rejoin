"""Tests for Rejoin key-slot and HWID reset enforcement (store layer)."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from agent.license_store import (
    HWID_RESET_LIMIT_MESSAGE,
    KEY_SLOT_LIMIT_MESSAGE,
    LocalJsonLicenseStore,
    NoActiveBindingError,
    PanelLimitError,
    UserLimitError,
    _utc_now,
)
from tests.test_license_max_key import (
    _add_expired_unredeemed_key,
    _add_owned_bound_key,
    _add_owned_unbound_key,
    _add_revoked_key,
    _clear_cooldown,
)


def _tmp_store() -> LocalJsonLicenseStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    Path(tmp.name).unlink()
    return LocalJsonLicenseStore(Path(tmp.name))


class TestKeySlotEnforcement(unittest.TestCase):
    def setUp(self):
        self.store = _tmp_store()
        self.uid = "900001"
        self.store.get_or_create_user(self.uid)

    def test_zero_active_can_generate(self):
        key = self.store.create_key_for_user(self.uid)
        self.assertTrue(key.startswith("DENG-"))

    def test_one_active_can_generate(self):
        _add_owned_unbound_key(self.store, self.uid)
        _clear_cooldown(self.store, self.uid)
        key = self.store.create_key_for_user(self.uid)
        self.assertTrue(key.startswith("DENG-"))

    def test_two_active_cannot_generate(self):
        _add_owned_unbound_key(self.store, self.uid)
        _add_owned_bound_key(self.store, self.uid)
        _clear_cooldown(self.store, self.uid)
        with self.assertRaises(UserLimitError) as ctx:
            self.store.create_key_for_user(self.uid)
        self.assertIn("maximum of 2 key slots", str(ctx.exception))

    def test_limit_message_constant(self):
        self.assertIn("maximum of 2 key slots", KEY_SLOT_LIMIT_MESSAGE)

    def test_two_redeemed_cannot_redeem_third(self):
        from agent.license import generate_license_key, hash_license_key

        _add_owned_unbound_key(self.store, self.uid)
        _add_owned_bound_key(self.store, self.uid)
        raw = generate_license_key()
        db = self.store._load()
        db["keys"][hash_license_key(raw)] = {
            "id": hash_license_key(raw),
            "prefix": raw[:9],
            "suffix": raw[-4:],
            "owner_discord_id": None,
            "status": "active",
            "plan": "standard",
            "expires_at": None,
            "redeemed_at": None,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        self.store._save(db)
        with self.assertRaises(UserLimitError):
            self.store.redeem_key_for_user(self.uid, raw)

    def test_expired_unredeemed_does_not_count(self):
        _add_expired_unredeemed_key(self.store, self.uid)
        _add_owned_unbound_key(self.store, self.uid)
        _clear_cooldown(self.store, self.uid)
        key = self.store.create_key_for_user(self.uid)
        self.assertTrue(key.startswith("DENG-"))

    def test_revoked_does_not_count(self):
        _add_revoked_key(self.store, self.uid)
        _add_owned_unbound_key(self.store, self.uid)
        _clear_cooldown(self.store, self.uid)
        key = self.store.create_key_for_user(self.uid)
        self.assertTrue(key.startswith("DENG-"))

    def test_redeem_already_owned_does_not_consume_slot(self):
        from agent.license_store import KeyAlreadySelfOwned

        raw = _add_owned_unbound_key(self.store, self.uid)
        with self.assertRaises(KeyAlreadySelfOwned):
            self.store.redeem_key_for_user(self.uid, raw)
        self.assertEqual(self.store.get_active_key_slot_count(self.uid), 1)

    def test_assert_can_have_new_key_slot_alias(self):
        self.assertEqual(
            self.store.get_active_key_slot_count(self.uid),
            self.store.count_active_keys_for_limit(self.uid),
        )

    def test_concurrent_generate_cannot_exceed_two(self):
        errors: list[Exception] = []
        successes: list[str] = []

        def worker():
            try:
                s = _tmp_store()
                s._path = self.store._path
                s.get_or_create_user(self.uid)
                _clear_cooldown(s, self.uid)
                successes.append(s.create_key_for_user(self.uid))
            except UserLimitError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertLessEqual(len(successes), 2)
        self.assertGreaterEqual(len(errors), 1)
        self.assertLessEqual(self.store.get_active_key_slot_count(self.uid), 2)


class TestHwidResetDailyLimit(unittest.TestCase):
    def setUp(self):
        self.store = _tmp_store()
        self.uid = "900002"
        self.store.get_or_create_user(self.uid)

    def _bound_key(self) -> str:
        raw = _add_owned_bound_key(self.store, self.uid)
        db = self.store._load()
        key_hash = None
        for kh, rec in db["keys"].items():
            if rec.get("owner_discord_id") == self.uid and db["bindings"].get(kh, {}).get("is_active"):
                key_hash = kh
                break
        self.assertIsNotNone(key_hash)
        return key_hash  # type: ignore[return-value]

    def test_first_reset_allowed(self):
        key_id = self._bound_key()
        self.store.reset_hwid(self.uid, key_id)
        self.assertEqual(self.store.get_successful_hwid_resets_today(self.uid), 1)

    def test_second_reset_same_day_denied(self):
        key_id = self._bound_key()
        self.store.reset_hwid(self.uid, key_id, consume_daily_quota=False)
        self.store.record_successful_panel_reset(self.uid, 1)
        with self.assertRaises(PanelLimitError) as ctx:
            self.store.assert_can_reset_hwid(self.uid)
        self.assertIn("HWID reset for today", str(ctx.exception))

    def test_reset_message_constant(self):
        self.assertIn("00:00 WIB", HWID_RESET_LIMIT_MESSAGE)

    def test_no_binding_does_not_count(self):
        raw = _add_owned_unbound_key(self.store, self.uid)
        db = self.store._load()
        key_id = next(k for k, v in db["keys"].items() if v.get("owner_discord_id") == self.uid)
        with self.assertRaises(NoActiveBindingError):
            self.store.reset_hwid(self.uid, key_id)
        self.assertEqual(self.store.get_successful_hwid_resets_today(self.uid), 0)

    def test_batch_reset_records_once(self):
        from agent.license import generate_license_key, hash_license_key

        key_a = self._bound_key()
        raw_b = generate_license_key()
        kh_b = hash_license_key(raw_b)
        db = self.store._load()
        now = _utc_now()
        db["keys"][kh_b] = {
            "id": kh_b,
            "prefix": raw_b[:9],
            "suffix": raw_b[-4:],
            "owner_discord_id": self.uid,
            "status": "active",
            "plan": "standard",
            "expires_at": None,
            "redeemed_at": now,
            "created_at": now,
            "updated_at": now,
        }
        db["bindings"][kh_b] = {
            "key_id": kh_b,
            "install_id_hash": "hash2",
            "device_model": "Phone",
            "is_active": True,
            "created_at": now,
            "last_seen_at": now,
        }
        self.store._save(db)
        self.store.reset_hwid(self.uid, key_a, consume_daily_quota=False)
        self.store.reset_hwid(self.uid, kh_b, consume_daily_quota=False)
        count = self.store.record_successful_panel_reset(self.uid, 2)
        self.assertEqual(count, 1)
        with self.assertRaises(PanelLimitError):
            self.store.record_successful_panel_reset(self.uid, 1)


class TestCleanupScript(unittest.TestCase):
    def test_dry_run_does_not_require_confirm(self):
        from scripts.rejoin_cleanup_key_slots import _analyze
        from agent.key_stats_format import filter_active_visible_license_rows

        data = {
            "keys": [
                {
                    "id": "a" * 64,
                    "prefix": "DENG-1111",
                    "suffix": "AAAA",
                    "status": "active",
                    "owner_discord_id": "123",
                    "site_user_id": None,
                    "redeemed_at": _utc_now(),
                    "expires_at": None,
                    "created_at": _utc_now(),
                    "updated_at": _utc_now(),
                },
                {
                    "id": "b" * 64,
                    "prefix": "DENG-2222",
                    "suffix": "BBBB",
                    "status": "active",
                    "owner_discord_id": "123",
                    "site_user_id": None,
                    "redeemed_at": _utc_now(),
                    "expires_at": None,
                    "created_at": _utc_now(),
                    "updated_at": _utc_now(),
                },
                {
                    "id": "c" * 64,
                    "prefix": "DENG-3333",
                    "suffix": "CCCC",
                    "status": "active",
                    "owner_discord_id": "123",
                    "site_user_id": None,
                    "redeemed_at": _utc_now(),
                    "expires_at": None,
                    "created_at": _utc_now(),
                    "updated_at": _utc_now(),
                },
            ],
            "bindings": [],
            "site_users": [],
        }
        analysis = _analyze(data, 2)
        self.assertEqual(analysis["affected_users"], 1)
        self.assertEqual(analysis["total_extra_keys"], 1)
        rows = analysis["affected"][0]
        self.assertEqual(rows["active_before"], 3)
        self.assertEqual(rows["active_after"], 2)


if __name__ == "__main__":
    unittest.main()
