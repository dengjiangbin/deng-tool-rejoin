"""Tests for !id stat correctness — probe p-814a3a200f.

Covers:
- Generated count only includes active keys (not revoked/expired/dead)
- key_executed_count always 0 for local store (no public builds yet)
- Reset HWID count only increments on successful bound->unbound transitions
- HWID button press / list open / cancel does NOT increment
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.license import hash_license_key, normalize_license_key
from agent.license_store import LocalJsonLicenseStore, get_license_stats_for_discord_user


def _tmp_store() -> LocalJsonLicenseStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    Path(tmp.name).unlink()
    return LocalJsonLicenseStore(Path(tmp.name))


class TestGeneratedCountActiveOnly(unittest.TestCase):
    """Issue 5 fix: Generated count must only include active keys."""

    def test_one_active_key_counts_as_one(self):
        store = _tmp_store()
        uid = "stats_u1"
        store.get_or_create_user(uid)
        store.create_key_for_user(uid)
        stats = get_license_stats_for_discord_user(store, uid)
        self.assertEqual(stats["key_generated_count"], 1)

    def test_revoked_key_excluded_from_generated(self):
        """A revoked key must NOT count towards Generated."""
        store = _tmp_store()
        uid = "stats_u2"
        store.get_or_create_user(uid)
        full_key = store.create_key_for_user(uid)
        key_hash = hash_license_key(normalize_license_key(full_key))
        # Force revoke the key directly in the store
        db = store._load()
        db["keys"][key_hash]["status"] = "revoked"
        store._save(db)
        stats = get_license_stats_for_discord_user(store, uid)
        self.assertEqual(stats["key_generated_count"], 0, "Revoked key must not count")

    def test_one_active_one_revoked_counts_as_one(self):
        """User has 1 active + 1 revoked = Generated should be 1."""
        store = _tmp_store()
        uid = "stats_u3"
        store.get_or_create_user(uid)
        full_key1 = store.create_key_for_user(uid)
        key_hash1 = hash_license_key(normalize_license_key(full_key1))
        # Insert a second key directly (bypassing cooldown) with status=revoked
        import agent.license as _lic
        second_raw = _lic.generate_license_key()
        second_hash = hash_license_key(normalize_license_key(second_raw))
        db = store._load()
        db["keys"][second_hash] = {
            "owner_discord_id": uid,
            "status": "revoked",
            "created_by": uid,
            "created_at": "2020-01-01T00:00:00+00:00",
        }
        store._save(db)
        stats = get_license_stats_for_discord_user(store, uid)
        self.assertEqual(stats["key_generated_count"], 1)

    def test_expired_key_excluded_from_generated(self):
        """An expired key must NOT count towards Generated."""
        store = _tmp_store()
        uid = "stats_u4"
        store.get_or_create_user(uid)
        full_key = store.create_key_for_user(uid)
        key_hash = hash_license_key(normalize_license_key(full_key))
        # Force expire
        db = store._load()
        db["keys"][key_hash]["status"] = "expired"
        store._save(db)
        stats = get_license_stats_for_discord_user(store, uid)
        self.assertEqual(stats["key_generated_count"], 0, "Expired key must not count")

    def test_two_active_keys_count_as_two(self):
        store = _tmp_store()
        uid = "stats_u5"
        store.get_or_create_user(uid)
        store.create_key_for_user(uid)
        # Insert second active key directly (bypassing cooldown)
        import agent.license as _lic
        second_raw = _lic.generate_license_key()
        second_hash = hash_license_key(normalize_license_key(second_raw))
        db = store._load()
        db["keys"][second_hash] = {
            "owner_discord_id": uid,
            "status": "active",
            "created_by": uid,
            "created_at": "2020-01-01T00:00:00+00:00",
        }
        store._save(db)
        stats = get_license_stats_for_discord_user(store, uid)
        self.assertEqual(stats["key_generated_count"], 2)

    def test_no_keys_returns_zero(self):
        store = _tmp_store()
        uid = "stats_u6"
        store.get_or_create_user(uid)
        stats = get_license_stats_for_discord_user(store, uid)
        self.assertEqual(stats["key_generated_count"], 0)


class TestKeyExecutedCountLocalStore(unittest.TestCase):
    """Key executed count is always 0 for local store (no public releases)."""

    def test_key_executed_zero_for_local_store(self):
        store = _tmp_store()
        uid = "exec_u1"
        store.get_or_create_user(uid)
        stats = get_license_stats_for_discord_user(store, uid)
        self.assertIn("key_executed_count", stats)
        self.assertEqual(stats["key_executed_count"], 0)

    def test_record_key_execution_noop_for_non_public(self):
        """record_key_execution must be a no-op for non-public builds."""
        store = _tmp_store()
        # Should not raise
        store.record_key_execution(
            key_id="abc",
            owner_discord_id="u1",
            version="main-dev",
            channel="main-dev",
            is_public_release=False,
        )

    def test_stats_dict_has_all_required_keys(self):
        """Stats dict must contain all 6 required fields."""
        store = _tmp_store()
        uid = "stats_shape_u1"
        store.get_or_create_user(uid)
        stats = get_license_stats_for_discord_user(store, uid)
        required = {
            "key_generated_count",
            "key_redeemed_count",
            "unbound_key_count",
            "bound_key_count",
            "reset_hwid_count",
            "key_executed_count",
        }
        for field in required:
            self.assertIn(field, stats, f"Missing field: {field}")


class TestResetHwidCountingRule(unittest.TestCase):
    """Issue 6 fix: HWID reset count only increments on successful bound->unbound."""

    def _make_bound(self, uid: str):
        store = _tmp_store()
        store.get_or_create_user(uid)
        full_key = store.create_key_for_user(uid)
        store.bind_or_check_device(full_key, "aa" * 32, "Pixel 6", "1.0")
        key_hash = hash_license_key(normalize_license_key(full_key))
        # Age the last_seen_at to allow reset
        db = store._load()
        db["bindings"][key_hash]["last_seen_at"] = "2020-01-01T00:00:00+00:00"
        store._save(db)
        return store, full_key, key_hash

    def test_button_press_before_confirm_does_not_log(self):
        """Opening the HWID reset menu does NOT call reset_hwid, so no log entry."""
        store, full_key, key_hash = self._make_bound("hwid_c1")
        # Simulate opening the list (reading key list) — does NOT call reset_hwid
        keys = store.list_user_keys_with_binding_state("hwid_c1")
        self.assertEqual(len(keys), 1)
        db = store._load()
        logs = [e for e in db.get("reset_logs", []) if e["key_id"] == key_hash]
        self.assertEqual(len(logs), 0, "Opening key list must not write reset log")

    def test_cancel_does_not_log(self):
        """Canceling without calling reset_hwid leaves reset count at 0."""
        store, full_key, key_hash = self._make_bound("hwid_c2")
        # Simulate: user opened reset, saw the list, then cancelled — reset_hwid never called
        stats = get_license_stats_for_discord_user(store, "hwid_c2")
        self.assertEqual(stats["reset_hwid_count"], 0)

    def test_successful_reset_increments_exactly_one(self):
        """Exactly one log entry is written per successful reset."""
        store, full_key, key_hash = self._make_bound("hwid_c3")
        store.reset_hwid("hwid_c3", key_hash)
        db = store._load()
        logs = [e for e in db.get("reset_logs", []) if e["key_id"] == key_hash]
        self.assertEqual(len(logs), 1)

    def test_stats_reset_count_matches_log_entries(self):
        """get_license_stats reset_hwid_count must equal the number of log entries."""
        from agent.license_store import MAX_HWID_RESETS_PER_24H
        uid = "hwid_c4"
        store, full_key, key_hash = self._make_bound(uid)
        store.reset_hwid(uid, key_hash)
        stats = get_license_stats_for_discord_user(store, uid)
        self.assertEqual(stats["reset_hwid_count"], 1)

    def test_unbound_key_reset_does_not_increment(self):
        """Attempting reset on an unbound key raises NoActiveBindingError and logs nothing."""
        from agent.license_store import NoActiveBindingError
        store = _tmp_store()
        uid = "hwid_c5"
        store.get_or_create_user(uid)
        full_key = store.create_key_for_user(uid)
        key_hash = hash_license_key(normalize_license_key(full_key))
        with self.assertRaises(NoActiveBindingError):
            store.reset_hwid(uid, key_hash)
        db = store._load()
        logs = [e for e in db.get("reset_logs", []) if e["key_id"] == key_hash]
        self.assertEqual(len(logs), 0, "No log when key was never bound")

    def test_failed_reset_does_not_increment(self):
        """A reset that hits the rate limit leaves the count unchanged from before."""
        from agent.license_store import ResetLimitError, MAX_HWID_RESETS_PER_24H, _utc_now
        uid = "hwid_c6"
        store, full_key, key_hash = self._make_bound(uid)
        # Pre-fill reset logs to the limit
        db = store._load()
        for _ in range(MAX_HWID_RESETS_PER_24H):
            db["reset_logs"].append({
                "key_id": key_hash,
                "owner_discord_id": uid,
                "old_install_id_hash": "abc",
                "reason": "user_requested",
                "created_at": _utc_now(),
            })
        store._save(db)
        count_before = store.get_reset_count_24h(key_hash)
        with self.assertRaises(ResetLimitError):
            store.reset_hwid(uid, key_hash)
        count_after = store.get_reset_count_24h(key_hash)
        self.assertEqual(count_before, count_after, "Failed reset must not add a log entry")


class TestKeyExecutedPublicReleaseFilter(unittest.TestCase):
    """Issue 7: execution recording only happens for public builds, never main-dev/test."""

    def test_is_public_false_does_not_record(self):
        """record_key_execution with is_public_release=False is a no-op."""
        store = _tmp_store()
        uid = "exec_public_u1"
        store.get_or_create_user(uid)
        # Should not raise or write anything
        store.record_key_execution(
            key_id="test-key-id",
            owner_discord_id=uid,
            version="main-dev",
            channel="main-dev",
            is_public_release=False,
        )
        # Local store has no execution storage, so count stays 0
        stats = get_license_stats_for_discord_user(store, uid)
        self.assertEqual(stats["key_executed_count"], 0)

    def test_base_store_record_noop(self):
        """BaseLicenseStore.record_key_execution is a no-op on LocalJsonLicenseStore."""
        store = _tmp_store()
        # Should not raise regardless of parameters — local store is a no-op
        store.record_key_execution("key", "user", "v1", "stable", is_public_release=True)
        # Still zero (local store has no execution table)
        stats = get_license_stats_for_discord_user(store, "user")
        self.assertEqual(stats["key_executed_count"], 0)


if __name__ == "__main__":
    unittest.main()
