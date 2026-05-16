"""Tests for agent/license.py and agent/license_store.py (tests 1-20)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.license import (
    LicenseKeyError,
    generate_license_key,
    get_or_create_install_id,
    hash_install_id,
    hash_license_key,
    mask_license_key,
    normalize_license_key,
    validate_license_key,
)
from agent.license_store import (
    RESULT_ACTIVE,
    RESULT_KEY_NOT_REDEEMED,
    RESULT_NOT_FOUND,
    RESULT_REVOKED,
    RESULT_WRONG_DEVICE,
    ActiveKeyWarning,
    KeyNotFoundError,
    KeyOwnershipError,
    LocalJsonLicenseStore,
    ResetLimitError,
    UserLimitError,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _tmp_store() -> LocalJsonLicenseStore:
    """Return a store backed by a fresh temp file."""
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    Path(tmp.name).unlink()  # allow store to create it fresh
    return LocalJsonLicenseStore(Path(tmp.name))


# ── Tests 1-7: license.py utilities ───────────────────────────────────────────

class LicenseKeyFormatTests(unittest.TestCase):
    """Test 1 – generate_license_key returns canonical DENG-XXXX-XXXX-XXXX-XXXX."""

    def test_generate_format(self):
        key = generate_license_key()
        parts = key.split("-")
        self.assertEqual(len(parts), 5, msg=f"Expected 5 parts, got: {key!r}")
        self.assertEqual(parts[0], "DENG")
        for part in parts[1:]:
            self.assertEqual(len(part), 4, msg=f"Each hex group must be 4 chars: {part!r}")
            int(part, 16)  # must be valid hex

    def test_generate_unique(self):
        """Test 2 – generates unique keys."""
        keys = {generate_license_key() for _ in range(20)}
        self.assertEqual(len(keys), 20)


class NormalizeLicenseKeyTests(unittest.TestCase):
    """Test 3 – normalize_license_key handles mixed case and optional spaces."""

    def test_lowercase_normalized(self):
        normalized = normalize_license_key("deng-8f3a-b3c4-d5e6-44f0")
        self.assertEqual(normalized, "DENG-8F3A-B3C4-D5E6-44F0")

    def test_already_canonical(self):
        key = "DENG-8F3A-B3C4-D5E6-44F0"
        self.assertEqual(normalize_license_key(key), key)


class ValidateLicenseKeyTests(unittest.TestCase):
    """Test 4 – validate_license_key accepts valid keys, raises on invalid."""

    def test_valid_key_accepted(self):
        key = generate_license_key()
        result = validate_license_key(key)
        self.assertEqual(result, normalize_license_key(key))

    def test_empty_ok(self):
        """Test 5 – empty string is always accepted."""
        self.assertEqual(validate_license_key(""), "")

    def test_invalid_raises(self):
        with self.assertRaises(LicenseKeyError):
            validate_license_key("DENG-GGG-INVALID")

    def test_wrong_prefix_raises(self):
        with self.assertRaises(LicenseKeyError):
            validate_license_key("FAKE-8F3A-B3C4-D5E6-44F0")


class MaskLicenseKeyTests(unittest.TestCase):
    """Test 6 – mask_license_key shows prefix and suffix only."""

    def test_mask_new_format(self):
        key = "DENG-8F3A-B3C4-D5E6-44F0"
        masked = mask_license_key(key)
        self.assertIn("DENG-8F3A", masked)
        self.assertIn("44F0", masked)
        self.assertNotIn("B3C4", masked)
        self.assertNotIn("D5E6", masked)

    def test_mask_empty_returns_not_set(self):
        self.assertEqual(mask_license_key(""), "Not set")


class HashLicenseKeyTests(unittest.TestCase):
    """Test 7 – hash functions are deterministic."""

    def test_hash_license_key_deterministic(self):
        key = "DENG-8F3A-B3C4-D5E6-44F0"
        self.assertEqual(hash_license_key(key), hash_license_key(key))

    def test_hash_install_id_deterministic(self):
        install_id = "aabbccddeeff00112233445566778899"
        self.assertEqual(hash_install_id(install_id), hash_install_id(install_id))


# ── Tests 8-15: LocalJsonLicenseStore user/key operations ─────────────────────

class StoreUserKeyTests(unittest.TestCase):
    def setUp(self):
        self.store = _tmp_store()
        self.uid = "111222333444555666"

    def tearDown(self):
        try:
            self.store._path.unlink()
        except FileNotFoundError:
            pass

    def test_get_or_create_user_created(self):
        """Test 8 – create user returns dict with expected fields."""
        user = self.store.get_or_create_user(self.uid, "testuser")
        self.assertEqual(user["discord_username"], "testuser")
        self.assertEqual(user["max_keys"], 1)
        self.assertFalse(user["is_blocked"])

    def test_get_or_create_user_idempotent(self):
        """Test 9 – create same user twice returns same data."""
        u1 = self.store.get_or_create_user(self.uid, "testuser")
        u2 = self.store.get_or_create_user(self.uid, "testuser")
        self.assertEqual(u1["created_at"], u2["created_at"])

    def test_create_key_for_user_returns_full_key(self):
        """Test 10 – create_key_for_user returns a valid full key."""
        self.store.get_or_create_user(self.uid)
        full_key = self.store.create_key_for_user(self.uid)
        self.assertTrue(full_key.startswith("DENG-"))
        # normalize validates it
        from agent.license import normalize_license_key
        normalize_license_key(full_key)  # should not raise

    def test_count_user_keys(self):
        """Test 11 – count_user_keys reflects created keys."""
        self.store.get_or_create_user(self.uid)
        self.assertEqual(self.store.count_user_keys(self.uid), 0)
        self.store.create_key_for_user(self.uid)
        self.assertEqual(self.store.count_user_keys(self.uid), 1)

    def test_max_keys_limit_enforced(self):
        """Test 12 – second key creation for default user raises UserLimitError."""
        self.store.get_or_create_user(self.uid)
        self.store.create_key_for_user(self.uid)
        with self.assertRaises(UserLimitError):
            self.store.create_key_for_user(self.uid)

    def test_set_user_max_keys(self):
        """Test 13 – set_user_max_keys allows more keys."""
        self.store.get_or_create_user(self.uid)
        self.store.set_user_max_keys(self.uid, 3)
        k1 = self.store.create_key_for_user(self.uid)
        k2 = self.store.create_key_for_user(self.uid)
        k3 = self.store.create_key_for_user(self.uid)
        self.assertEqual(self.store.count_user_keys(self.uid), 3)
        with self.assertRaises(UserLimitError):
            self.store.create_key_for_user(self.uid)

    def test_redeem_key_success(self):
        """Test 14 – redeem an unclaimed key succeeds and returns full normalized key."""
        admin_uid = "000000000000000001"
        target_uid = "000000000000000002"
        self.store.get_or_create_user(admin_uid)
        # Create an unowned key the admin generated but user hasn't redeemed
        store2 = self.store
        full_key = store2.create_key_for_user(admin_uid)
        # Artificially unchain owner so another user can redeem it
        db = store2._load()
        key_hash = hash_license_key(normalize_license_key(full_key))
        db["keys"][key_hash]["owner_discord_id"] = None
        store2._save(db)

        self.store.get_or_create_user(target_uid)
        ret = self.store.redeem_key_for_user(target_uid, full_key)
        self.assertEqual(ret, normalize_license_key(full_key))
        self.assertNotIn("...", ret)

    def test_redeem_owned_key_raises_ownership_error(self):
        """Test 15 – redeem a key already owned by another user raises KeyOwnershipError."""
        uid1 = "111000000000000001"
        uid2 = "111000000000000002"
        self.store.get_or_create_user(uid1)
        self.store.get_or_create_user(uid2)
        full_key = self.store.create_key_for_user(uid1)
        with self.assertRaises(KeyOwnershipError):
            self.store.redeem_key_for_user(uid2, full_key)


# ── Tests 16-20: HWID reset ────────────────────────────────────────────────────

class StoreHwidResetTests(unittest.TestCase):
    def setUp(self):
        self.store = _tmp_store()
        self.uid = "777888999000111222"
        self.store.get_or_create_user(self.uid)
        full_key = self.store.create_key_for_user(self.uid)
        from agent.license import normalize_license_key, hash_license_key
        self.key_hash = hash_license_key(normalize_license_key(full_key))
        self.full_key = full_key
        # Bind a device, then clear last_seen_at so the active-key guard doesn't fire
        self.store.bind_or_check_device(full_key, "aaaa" * 8, "Pixel 6", "1.0")
        db = self.store._load()
        db["bindings"][self.key_hash]["last_seen_at"] = None
        self.store._save(db)

    def tearDown(self):
        try:
            self.store._path.unlink()
        except FileNotFoundError:
            pass

    def test_reset_hwid_allows_up_to_limit(self):
        """Test 16 – up to 5 resets in 24h are allowed."""
        from agent.license_store import MAX_HWID_RESETS_PER_24H
        for i in range(MAX_HWID_RESETS_PER_24H):
            self.store.reset_hwid(self.uid, self.key_hash)
            # Re-bind so the next reset has something to clear; keep last_seen_at=None
            db = self.store._load()
            db["bindings"][self.key_hash] = {
                "install_id_hash": "bbbb" * 8,
                "device_label": "",
                "device_model": "Pixel",
                "bound_at": "2026-01-01T00:00:00+00:00",
                "last_seen_at": None,
                "last_status": None,
                "is_active": True,
            }
            self.store._save(db)
        self.assertEqual(self.store.get_reset_count_24h(self.key_hash), MAX_HWID_RESETS_PER_24H)

    def test_reset_hwid_blocked_at_limit(self):
        """Test 17 – 6th reset raises ResetLimitError."""
        from agent.license_store import MAX_HWID_RESETS_PER_24H
        for _ in range(MAX_HWID_RESETS_PER_24H):
            self.store.reset_hwid(self.uid, self.key_hash)
            db = self.store._load()
            db["bindings"][self.key_hash] = {
                "install_id_hash": "cccc" * 8,
                "device_label": "",
                "device_model": "Pixel",
                "bound_at": "2026-01-01T00:00:00+00:00",
                "last_seen_at": None,
                "last_status": None,
                "is_active": True,
            }
            self.store._save(db)
        with self.assertRaises(ResetLimitError):
            self.store.reset_hwid(self.uid, self.key_hash)

    def test_reset_hwid_warns_if_recently_active(self):
        """Test 18 – reset must SUCCEED when last_seen_at is recent but no prior reset exists.

        The old 'ActiveKeyWarning on recently-active key' behavior was incorrect:
        it prevented the first-ever HWID reset immediately after license verification.
        Reset cooldown is now based only on actual reset history, not heartbeat timestamps.
        """
        from datetime import datetime, timezone
        db = self.store._load()
        db["bindings"][self.key_hash]["last_seen_at"] = datetime.now(timezone.utc).isoformat()
        self.store._save(db)
        # Must NOT raise — no prior reset means first reset is always allowed
        self.store.reset_hwid(self.uid, self.key_hash)
        db2 = self.store._load()
        self.assertFalse(db2["bindings"][self.key_hash]["is_active"], "Binding should be deactivated after reset")

    def test_reset_hwid_clears_binding(self):
        """Test 19 – successful reset deactivates the binding."""
        self.store.reset_hwid(self.uid, self.key_hash)
        db = self.store._load()
        binding = db.get("bindings", {}).get(self.key_hash, {})
        self.assertFalse(binding.get("is_active", True))

    def test_reset_hwid_writes_log(self):
        """Test 20 – successful reset creates a reset_log entry."""
        self.store.reset_hwid(self.uid, self.key_hash)
        db = self.store._load()
        logs = [e for e in db.get("reset_logs", []) if e.get("key_id") == self.key_hash]
        self.assertGreaterEqual(len(logs), 1)


# ── Tests 21-25: bind_or_check_device ─────────────────────────────────────────

class StoreDeviceBindingTests(unittest.TestCase):
    def setUp(self):
        self.store = _tmp_store()
        self.uid = "123456789012345678"
        self.store.get_or_create_user(self.uid)
        self.full_key = self.store.create_key_for_user(self.uid)

    def tearDown(self):
        try:
            self.store._path.unlink()
        except FileNotFoundError:
            pass

    def test_first_bind_returns_active(self):
        """Test 21 – first bind of device returns RESULT_ACTIVE."""
        result = self.store.bind_or_check_device(self.full_key, "aabb" * 8, "Pixel", "1.0")
        self.assertEqual(result, RESULT_ACTIVE)

    def test_same_device_returns_active(self):
        """Test 22 – repeat check with same install_id_hash returns RESULT_ACTIVE."""
        self.store.bind_or_check_device(self.full_key, "aabb" * 8, "Pixel", "1.0")
        result = self.store.bind_or_check_device(self.full_key, "aabb" * 8, "Pixel", "1.0")
        self.assertEqual(result, RESULT_ACTIVE)

    def test_different_device_returns_wrong_device(self):
        """Test 23 – different install_id_hash returns RESULT_WRONG_DEVICE."""
        self.store.bind_or_check_device(self.full_key, "aabb" * 8, "Pixel", "1.0")
        result = self.store.bind_or_check_device(self.full_key, "ccdd" * 8, "Other", "1.0")
        self.assertEqual(result, RESULT_WRONG_DEVICE)

    def test_wrong_device_does_not_overwrite_binding(self) -> None:
        from agent.license import normalize_license_key, hash_license_key
        h = hash_license_key(normalize_license_key(self.full_key))
        self.store.bind_or_check_device(self.full_key, "aabb" * 8, "Pixel", "1.0")
        self.store.bind_or_check_device(self.full_key, "ccdd" * 8, "HackerPhone", "9.0")
        db = self.store._load()
        row = db["bindings"][h]
        self.assertEqual(row.get("install_id_hash"), "aabb" * 8)
        self.assertEqual(row.get("device_model"), "Pixel")

    def test_not_found_key_returns_not_found(self):
        """Test 24 – nonexistent key returns RESULT_NOT_FOUND."""
        result = self.store.bind_or_check_device("DENG-FFFF-FFFF-FFFF-FFFF", "aabb" * 8, "X", "1")
        self.assertEqual(result, RESULT_NOT_FOUND)

    def test_unowned_key_returns_key_not_redeemed_without_binding(self):
        """Pool / unredeemed keys must not bind via the tool."""
        from agent.license import normalize_license_key, hash_license_key

        creator = "999999999999999999"
        self.store.get_or_create_user(creator)
        pool_key = self.store.create_key_for_user(creator)
        kh = hash_license_key(normalize_license_key(pool_key))
        db = self.store._load()
        db["keys"][kh]["owner_discord_id"] = None
        self.store._save(db)

        result = self.store.bind_or_check_device(pool_key, "dead" * 16, "Pixel", "1.0")
        self.assertEqual(result, RESULT_KEY_NOT_REDEEMED)
        db2 = self.store._load()
        self.assertNotIn(kh, db2.get("bindings", {}))

    def test_revoked_key_returns_revoked(self):
        """Test 25 – revoked key returns RESULT_REVOKED."""
        from agent.license import normalize_license_key, hash_license_key
        key_hash = hash_license_key(normalize_license_key(self.full_key))
        db = self.store._load()
        db["keys"][key_hash]["status"] = "revoked"
        self.store._save(db)
        result = self.store.bind_or_check_device(self.full_key, "aabb" * 8, "X", "1")
        self.assertEqual(result, RESULT_REVOKED)


# ── Tests 26-27: panel config ─────────────────────────────────────────────────

class StorePanelConfigTests(unittest.TestCase):
    def setUp(self):
        self.store = _tmp_store()

    def tearDown(self):
        try:
            self.store._path.unlink()
        except FileNotFoundError:
            pass

    def test_save_and_get_panel_config(self):
        """Test 26 – panel config is saved and retrieved per guild."""
        self.store.save_panel_config("guild1", "chan1", "msg1", "admin1")
        cfg = self.store.get_panel_config("guild1")
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["channel_id"], "chan1")
        self.assertEqual(cfg["message_id"], "msg1")

    def test_clear_panel_config(self):
        """Test 27 – clear_panel_config removes the config."""
        self.store.save_panel_config("guild2", "chan2", "msg2", "admin1")
        self.store.clear_panel_config("guild2")
        self.assertIsNone(self.store.get_panel_config("guild2"))


# ── Tests 28-30: security / audit ─────────────────────────────────────────────

class StoreSecurityTests(unittest.TestCase):
    def setUp(self):
        self.store = _tmp_store()

    def tearDown(self):
        try:
            self.store._path.unlink()
        except FileNotFoundError:
            pass

    def test_full_key_not_in_store_json(self):
        """Test 28 – the plaintext key is never stored in JSON."""
        uid = "999888777666555444"
        self.store.get_or_create_user(uid)
        full_key = self.store.create_key_for_user(uid)
        raw_json = self.store._path.read_text(encoding="utf-8")
        self.assertNotIn(full_key, raw_json)

    def test_audit_log_created_on_key_creation(self):
        """Test 29 – audit log is written when key is created."""
        uid = "333444555666777888"
        self.store.get_or_create_user(uid)
        self.store.create_key_for_user(uid, created_by=uid)
        db = self.store._load()
        actions = [e["action"] for e in db.get("audit_logs", [])]
        self.assertIn("create_key", actions)

    def test_install_id_persists_across_calls(self):
        """Test 30 – get_or_create_install_id returns same value on repeat calls."""
        from agent.license import INSTALL_ID_PATH, get_or_create_install_id
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "install_id"
            with patch("agent.license.INSTALL_ID_PATH", path):
                id1 = get_or_create_install_id()
                id2 = get_or_create_install_id()
        self.assertEqual(id1, id2)
        self.assertEqual(len(id1), 32)  # install_id is 32 hex chars (16 random bytes)


if __name__ == "__main__":
    unittest.main()
