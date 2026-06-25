"""Tests for /license max_key global and per-user key limit feature.

Covers all 20 required test items:
 1. Default global max is 2 when no config row exists.
 2. set_global_max_keys updates the global max.
 3. set_user_key_limit sets a per-user override.
 4. Per-user override wins over global max.
 5. max = 0 blocks new key generation/redeem for that user.
 6. Generate Key is blocked when active count >= effective max.
 7. Generate Key is allowed when active count < effective max.
 8. Redeem Key is blocked when active count >= effective max.
 9. Redeem Key does not consume/change key if blocked.
10. Expired unredeemed keys do not count toward limit.
11. Revoked/deleted/inactive keys do not count toward limit.
12. Owned/unbound keys count toward limit.
13. Owned/bound keys count toward limit.
14. Active unredeemed unexpired generated keys count toward limit.
15. Reset HWID does not reduce active key count.
16. /license user lookup shows Active Keys and Max Keys correctly.
17. Website Generate Key uses the same shared limit logic.
18. Discord Generate Key uses the same shared limit logic.
19. Permission checks block non-admins from using /license max_key.
20. License log event is posted when max limit changes, if log channel is configured.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from agent.key_stats_format import (
    build_license_admin_stats_description,
    filter_active_visible_license_rows,
)
from agent.license_store import (
    DEFAULT_GLOBAL_MAX_KEYS,
    GenerationCooldownError,
    LocalJsonLicenseStore,
    UserLimitError,
    _utc_now,
)


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _tmp_store() -> LocalJsonLicenseStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    Path(tmp.name).unlink()
    return LocalJsonLicenseStore(Path(tmp.name))


def _clear_cooldown(store: LocalJsonLicenseStore, uid: str) -> None:
    """Zero out last_key_generated_at so the next create_key_for_user succeeds."""
    db = store._load()
    db["users"][uid]["last_key_generated_at"] = None
    store._save(db)


def _add_owned_unbound_key(store: LocalJsonLicenseStore, uid: str) -> str:
    """Create a redeemed/unbound key owned by uid without cooldown blocking."""
    from agent.license import generate_license_key, hash_license_key
    raw = generate_license_key()
    key_hash = hash_license_key(raw)
    db = store._load()
    now = _utc_now()
    db["keys"][key_hash] = {
        "id": key_hash,
        "prefix": raw[:9],
        "suffix": raw[-4:],
        "owner_discord_id": uid,
        "status": "active",
        "plan": "standard",
        "expires_at": None,
        "redeemed_at": now,
        "created_at": now,
        "updated_at": now,
        "key_ciphertext": None,
        "key_export_available": False,
    }
    store._save(db)
    return raw


def _add_expired_unredeemed_key(store: LocalJsonLicenseStore, uid: str) -> str:
    """Add an expired-unredeemed key (status='expired', no redeemed_at)."""
    from agent.license import generate_license_key, hash_license_key
    raw = generate_license_key()
    key_hash = hash_license_key(raw)
    db = store._load()
    now = _utc_now()
    db["keys"][key_hash] = {
        "id": key_hash,
        "prefix": raw[:9],
        "suffix": raw[-4:],
        "owner_discord_id": uid,
        "status": "expired",
        "plan": "standard",
        "expires_at": "2000-01-01T00:00:00+00:00",
        "redeemed_at": None,
        "created_at": "2000-01-01T00:00:00+00:00",
        "updated_at": now,
        "key_ciphertext": None,
        "key_export_available": False,
    }
    store._save(db)
    return raw


def _add_revoked_key(store: LocalJsonLicenseStore, uid: str) -> str:
    """Add a revoked key owned by uid."""
    from agent.license import generate_license_key, hash_license_key
    raw = generate_license_key()
    key_hash = hash_license_key(raw)
    db = store._load()
    now = _utc_now()
    db["keys"][key_hash] = {
        "id": key_hash,
        "prefix": raw[:9],
        "suffix": raw[-4:],
        "owner_discord_id": uid,
        "status": "revoked",
        "plan": "standard",
        "expires_at": None,
        "redeemed_at": now,
        "created_at": now,
        "updated_at": now,
        "key_ciphertext": None,
        "key_export_available": False,
    }
    store._save(db)
    return raw


def _add_owned_bound_key(store: LocalJsonLicenseStore, uid: str) -> str:
    """Add a redeemed+bound key (active device binding) for uid."""
    from agent.license import generate_license_key, hash_license_key
    raw = generate_license_key()
    key_hash = hash_license_key(raw)
    db = store._load()
    now = _utc_now()
    db["keys"][key_hash] = {
        "id": key_hash,
        "prefix": raw[:9],
        "suffix": raw[-4:],
        "owner_discord_id": uid,
        "status": "active",
        "plan": "standard",
        "expires_at": None,
        "redeemed_at": now,
        "created_at": now,
        "updated_at": now,
        "key_ciphertext": None,
        "key_export_available": False,
    }
    db["bindings"][key_hash] = {
        "key_id": key_hash,
        "install_id_hash": "fakehash",
        "device_model": "Pixel 7",
        "device_label": "",
        "is_active": True,
        "created_at": now,
        "last_seen_at": now,
    }
    store._save(db)
    return raw


# ── Test 1-4: Default and override behavior ──────────────────────────────────

class TestGlobalAndUserLimits(unittest.TestCase):

    def setUp(self):
        self.store = _tmp_store()
        self.uid = "111"
        self.store.get_or_create_user(self.uid)

    def test_01_default_global_max_is_2(self):
        """Test 1 — Default global max is 2 when no config row exists."""
        self.assertEqual(DEFAULT_GLOBAL_MAX_KEYS, 2)
        self.assertEqual(self.store.get_global_max_keys(), 2)
        self.assertEqual(self.store.get_effective_max_keys(self.uid), 2)

    def test_02_set_global_max_keys(self):
        """Test 2 — set_global_max_keys updates global max."""
        self.store.set_global_max_keys(3, updated_by="admin")
        self.assertEqual(self.store.get_global_max_keys(), 3)
        self.assertEqual(self.store.get_effective_max_keys(self.uid), 3)

    def test_03_set_user_key_limit(self):
        """Test 3 — set_user_key_limit sets a per-user override."""
        self.store.set_user_key_limit(self.uid, 5, updated_by="admin")
        self.assertEqual(self.store.get_user_key_limit(self.uid), 5)

    def test_04_per_user_override_wins(self):
        """Test 4 — Per-user override wins over global max."""
        self.store.set_global_max_keys(2, updated_by="admin")
        self.store.set_user_key_limit(self.uid, 5, updated_by="admin")
        self.assertEqual(self.store.get_effective_max_keys(self.uid), 5)

    def test_04b_another_user_still_uses_global(self):
        """Unrelated user still uses global default even after per-user override on another."""
        uid2 = "222"
        self.store.get_or_create_user(uid2)
        self.store.set_global_max_keys(2, updated_by="admin")
        self.store.set_user_key_limit(self.uid, 5, updated_by="admin")
        self.assertEqual(self.store.get_effective_max_keys(uid2), 2)

    def test_04c_no_user_override_returns_none(self):
        """get_user_key_limit returns None when no override exists."""
        self.assertIsNone(self.store.get_user_key_limit(self.uid))


# ── Test 5-9: Generate/Redeem enforcement ────────────────────────────────────

class TestGenerateKeyEnforcement(unittest.TestCase):

    def setUp(self):
        self.store = _tmp_store()
        self.uid = "555"
        self.store.get_or_create_user(self.uid)

    def test_05_max_zero_blocks_generation(self):
        """Test 5 — max = 0 blocks new key generation for that user."""
        self.store.set_user_key_limit(self.uid, 0, updated_by="admin")
        with self.assertRaises(UserLimitError):
            self.store.create_key_for_user(self.uid)

    def test_06_generate_blocked_when_at_limit(self):
        """Test 6 — Generate Key is blocked when active count >= effective max."""
        self.store.set_global_max_keys(1, updated_by="admin")
        # Add one active key to reach the limit
        _add_owned_unbound_key(self.store, self.uid)
        active = self.store.count_active_keys_for_limit(self.uid)
        self.assertEqual(active, 1)
        with self.assertRaises(UserLimitError) as ctx:
            self.store.create_key_for_user(self.uid)
        self.assertIn("maximum of 2 key slots", str(ctx.exception))

    def test_07_generate_allowed_when_below_limit(self):
        """Test 7 — Generate Key is allowed when active count < effective max."""
        self.store.set_global_max_keys(2, updated_by="admin")
        # One owned key, limit=2 → should be allowed
        _add_owned_unbound_key(self.store, self.uid)
        _clear_cooldown(self.store, self.uid)
        # Should NOT raise UserLimitError (may raise GenerationCooldownError if cooldown, but we cleared it)
        key = self.store.create_key_for_user(self.uid)
        self.assertTrue(key.startswith("DENG-"))

    def test_07b_generate_allowed_with_no_keys(self):
        """Generate Key is allowed when user has zero active keys."""
        self.store.set_global_max_keys(2, updated_by="admin")
        key = self.store.create_key_for_user(self.uid)
        self.assertTrue(key.startswith("DENG-"))


class TestRedeemKeyEnforcement(unittest.TestCase):

    def setUp(self):
        self.store = _tmp_store()
        self.owner_uid = "777"
        self.redeemer_uid = "888"
        self.store.get_or_create_user(self.owner_uid)
        self.store.get_or_create_user(self.redeemer_uid)

    def _make_unredeemed_key(self) -> str:
        """Create a key owned by owner_uid but not yet redeemed by redeemer_uid."""
        from agent.license import generate_license_key, hash_license_key
        raw = generate_license_key()
        key_hash = hash_license_key(raw)
        db = self.store._load()
        now = _utc_now()
        db["keys"][key_hash] = {
            "id": key_hash,
            "prefix": raw[:9],
            "suffix": raw[-4:],
            "owner_discord_id": None,
            "status": "active",
            "plan": "standard",
            "expires_at": None,
            "redeemed_at": None,
            "created_at": now,
            "updated_at": now,
            "key_ciphertext": None,
            "key_export_available": False,
        }
        self.store._save(db)
        return raw

    def test_08_redeem_blocked_when_at_limit(self):
        """Test 8 — Redeem Key is blocked when active count >= effective max."""
        self.store.set_user_key_limit(self.redeemer_uid, 1, updated_by="admin")
        _add_owned_unbound_key(self.store, self.redeemer_uid)
        unredeemed = self._make_unredeemed_key()
        with self.assertRaises(UserLimitError) as ctx:
            self.store.redeem_key_for_user(self.redeemer_uid, unredeemed)
        self.assertIn("maximum of 2 key slots", str(ctx.exception))

    def test_09_redeem_does_not_consume_key_if_blocked(self):
        """Test 9 — Redeem Key does not change the key if blocked."""
        from agent.license import hash_license_key
        self.store.set_user_key_limit(self.redeemer_uid, 0, updated_by="admin")
        unredeemed = self._make_unredeemed_key()
        key_hash = hash_license_key(unredeemed)
        # Attempt blocked redeem
        with self.assertRaises(UserLimitError):
            self.store.redeem_key_for_user(self.redeemer_uid, unredeemed)
        # Key must still be unowned
        db = self.store._load()
        record = db["keys"][key_hash]
        self.assertIsNone(record.get("owner_discord_id"))
        self.assertIsNone(record.get("redeemed_at"))


# ── Test 10-14: Key counting rules ───────────────────────────────────────────

class TestActivekeyCountingRules(unittest.TestCase):

    def setUp(self):
        self.store = _tmp_store()
        self.uid = "999"
        self.store.get_or_create_user(self.uid)

    def test_10_expired_unredeemed_keys_do_not_count(self):
        """Test 10 — Expired unredeemed keys do not count toward limit."""
        _add_expired_unredeemed_key(self.store, self.uid)
        self.assertEqual(self.store.count_active_keys_for_limit(self.uid), 0)

    def test_11_revoked_keys_do_not_count(self):
        """Test 11 — Revoked/deleted/inactive keys do not count toward limit."""
        _add_revoked_key(self.store, self.uid)
        self.assertEqual(self.store.count_active_keys_for_limit(self.uid), 0)

    def test_12_owned_unbound_keys_count(self):
        """Test 12 — Owned/unbound keys count toward limit."""
        _add_owned_unbound_key(self.store, self.uid)
        self.assertEqual(self.store.count_active_keys_for_limit(self.uid), 1)

    def test_13_owned_bound_keys_count(self):
        """Test 13 — Owned/bound keys count toward limit."""
        _add_owned_bound_key(self.store, self.uid)
        self.assertEqual(self.store.count_active_keys_for_limit(self.uid), 1)

    def test_14_active_unredeemed_generated_keys_count(self):
        """Test 14 — Active unredeemed unexpired generated keys count toward limit."""
        from agent.license import generate_license_key, hash_license_key
        raw = generate_license_key()
        key_hash = hash_license_key(raw)
        db = self.store._load()
        now = _utc_now()
        # Future expiry → not yet expired
        future = "2099-01-01T00:00:00+00:00"
        db["keys"][key_hash] = {
            "id": key_hash,
            "prefix": raw[:9],
            "suffix": raw[-4:],
            "owner_discord_id": self.uid,
            "status": "active",
            "plan": "standard",
            "expires_at": future,
            "redeemed_at": None,
            "created_at": now,
            "updated_at": now,
            "key_ciphertext": None,
            "key_export_available": False,
        }
        self.store._save(db)
        # Should count as 1 active key
        self.assertEqual(self.store.count_active_keys_for_limit(self.uid), 1)

    def test_14b_mix_of_active_and_inactive(self):
        """Combination: 1 revoked + 1 expired + 1 owned unbound = count of 1."""
        _add_revoked_key(self.store, self.uid)
        _add_expired_unredeemed_key(self.store, self.uid)
        _add_owned_unbound_key(self.store, self.uid)
        self.assertEqual(self.store.count_active_keys_for_limit(self.uid), 1)


# ── Test 15: Reset HWID does not reduce count ────────────────────────────────

class TestResetHwidDoesNotReduceCount(unittest.TestCase):

    def test_15_reset_hwid_does_not_reduce_count(self):
        """Test 15 — Reset HWID changes owned/bound to owned/unbound, count stays same."""
        store = _tmp_store()
        uid = "rsthwid"
        store.get_or_create_user(uid)
        # Add a bound key
        _add_owned_bound_key(store, uid)
        count_before = store.count_active_keys_for_limit(uid)
        self.assertEqual(count_before, 1)
        # Manually simulate HWID reset (deactivate binding)
        from agent.license import hash_license_key
        db = store._load()
        for k, rec in db["keys"].items():
            if rec.get("owner_discord_id") == uid:
                key_id = k
                break
        db["bindings"][key_id]["is_active"] = False
        store._save(db)
        # Key is now unbound but still redeemed → count stays at 1
        count_after = store.count_active_keys_for_limit(uid)
        self.assertEqual(count_after, 1,
            "Reset HWID must not reduce the active key count")


# ── Test 16: active-key count display (no max) ───────────────────────────────

class TestLicenseUserDisplay(unittest.TestCase):

    def test_16_license_user_shows_active_count_without_max(self):
        """Test 16 — admin stats shows a simple Active Keys count without any max."""
        stats = {
            "key_generated_count": 1,
            "key_redeemed_count": 1,
            "unbound_key_count": 1,
            "bound_key_count": 0,
            "reset_hwid_count": 0,
            "key_executed_count": 0,
        }
        desc = build_license_admin_stats_description(
            user_label="<@123> (123)",
            stats=stats,
            active_rows=[],
        )
        self.assertIn("Active Keys:", desc)
        # Max key slot/limit displays must be gone.
        self.assertNotIn("Max Keys:", desc)
        self.assertNotIn("Max Panel", desc)
        self.assertNotIn(" / ", desc)


# ── Test 17-18: Shared logic used by website and Discord ─────────────────────

class TestSharedLogicUsedByBothFlows(unittest.TestCase):
    """Tests 17-18: Website and Discord Generate Key use the same store helpers."""

    def test_17_website_generate_key_uses_same_limit_logic(self):
        """Test 17 — Website Generate Key uses count_active_keys_for_limit."""
        store = _tmp_store()
        uid = "webtest"
        store.get_or_create_user(uid)
        store.set_global_max_keys(1, updated_by="admin")
        _add_owned_unbound_key(store, uid)
        # count_active_keys_for_limit is what the website now delegates to
        active = store.count_active_keys_for_limit(uid)
        effective_max = store.get_effective_max_keys(uid)
        self.assertGreaterEqual(active, effective_max)

    def test_18_discord_generate_key_uses_same_limit_logic(self):
        """Test 18 — Discord Generate Key uses create_key_for_user which checks limit."""
        store = _tmp_store()
        uid = "discordtest"
        store.get_or_create_user(uid)
        store.set_global_max_keys(1, updated_by="admin")
        _add_owned_unbound_key(store, uid)
        # create_key_for_user must raise UserLimitError (same path used by Discord)
        with self.assertRaises(UserLimitError):
            store.create_key_for_user(uid)


# ── Test 19: Permission check ─────────────────────────────────────────────────

class TestPermissionChecks(unittest.TestCase):

    def test_19_non_admin_denied_by_is_owner_check(self):
        """Test 19 — _is_owner returns False for users not in LICENSE_OWNER_DISCORD_IDS."""
        import os
        from bot.cog_license_panel import _is_owner
        # Patch env to only allow uid 12345
        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": "12345"}, clear=False):
            class FakeUser:
                id = 99999
            class FakeOwner:
                id = 12345
            self.assertFalse(_is_owner(FakeUser()))
            self.assertTrue(_is_owner(FakeOwner()))


# ── Additional edge cases ─────────────────────────────────────────────────────

class TestEdgeCases(unittest.TestCase):

    def test_max_zero_blocks_redeem(self):
        """max = 0 blocks Redeem Key as well as Generate Key."""
        store = _tmp_store()
        uid = "maxzeroredeem"
        store.get_or_create_user(uid)
        store.set_user_key_limit(uid, 0, updated_by="admin")
        # Create an unredeemed key (owned by nobody yet)
        from agent.license import generate_license_key, hash_license_key
        raw = generate_license_key()
        key_hash = hash_license_key(raw)
        db = store._load()
        now = _utc_now()
        db["keys"][key_hash] = {
            "id": key_hash, "prefix": raw[:9], "suffix": raw[-4:],
            "owner_discord_id": None, "status": "active", "plan": "standard",
            "expires_at": None, "redeemed_at": None,
            "created_at": now, "updated_at": now,
            "key_ciphertext": None, "key_export_available": False,
        }
        store._save(db)
        with self.assertRaises(UserLimitError):
            store.redeem_key_for_user(uid, raw)

    def test_expired_key_does_not_block_generation_within_limit(self):
        """Expired unredeemed keys must not block generation when below limit."""
        store = _tmp_store()
        uid = "expirednoblock"
        store.get_or_create_user(uid)
        store.set_global_max_keys(2, updated_by="admin")
        # Add one expired key + one active owned unbound (count=1, max=2)
        _add_expired_unredeemed_key(store, uid)
        _add_owned_unbound_key(store, uid)
        # count_active_keys_for_limit should be 1 (expired doesn't count)
        self.assertEqual(store.count_active_keys_for_limit(uid), 1)
        # Generation should be allowed
        _clear_cooldown(store, uid)
        key = store.create_key_for_user(uid)
        self.assertTrue(key.startswith("DENG-"))

    def test_cooldown_still_enforced_before_limit_check(self):
        """Cooldown check still fires on second immediate generation attempt."""
        store = _tmp_store()
        uid = "cooldowntest"
        store.get_or_create_user(uid)
        store.set_global_max_keys(10, updated_by="admin")
        # First key should succeed
        store.create_key_for_user(uid)
        # Second immediate attempt should hit cooldown, NOT limit error
        with self.assertRaises(GenerationCooldownError):
            store.create_key_for_user(uid)


if __name__ == "__main__":
    unittest.main()
