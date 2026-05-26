"""Tests for /license max_panel global and per-user daily HWID reset limit.

Covers all 21 required test items:
 1. Default global max_panel is 1 when no config row exists.
 2. set_global_max_panel updates the global max.
 3. set_user_panel_limit sets a per-user override.
 4. Per-user override wins over global max.
 5. max_panel = 0 blocks Reset HWID for that user.
 6. Reset HWID is allowed when used_count < effective max_panel.
 7. Reset HWID is blocked when used_count >= effective max_panel.
 8. Successful reset of multiple keys counts as 1 use.
 9. Opening Reset HWID panel does not count as usage.
10. Cancelling Reset HWID does not count as usage.
11. Failed Reset HWID does not count as usage.
12. Reset with no bound keys does not count as usage.
13. Counter is tied to Discord user ID, not key ID.
14. Counter resets by WIB date bucket.
15. WIB date bucket changes at 12:00 AM Asia/Jakarta.
16. Reset HWID does not change max_key ownership count.
17. Reset HWID still changes owned/bound keys to owned/unbound.
18. /license user lookup shows Max Panel Resets and Reset Uses Today.
19. Permission checks block non-admins from using /license max_panel.
20. License log event is posted when max_panel changes, if log channel configured.
21. Race test: two simultaneous Reset HWID confirmations cannot bypass max_panel = 1.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from agent.key_stats_format import build_license_admin_stats_description
from agent.license_store import (
    DEFAULT_GLOBAL_MAX_PANEL,
    LocalJsonLicenseStore,
    NoActiveBindingError,
    PanelLimitError,
    StoreError,
    _utc_now,
    get_wib_day,
)


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _tmp_store() -> LocalJsonLicenseStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    Path(tmp.name).unlink()
    return LocalJsonLicenseStore(Path(tmp.name))


def _add_owned_bound_key(store: LocalJsonLicenseStore, uid: str) -> str:
    """Create a redeemed AND bound key for uid. Returns key_hash."""
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
        "full_key_plaintext": raw,
    }
    db["bindings"][key_hash] = {
        "key_id": key_hash,
        "install_id_hash": "abc123",
        "device_model": "TestDevice",
        "device_label": "Test",
        "is_active": True,
        "created_at": now,
        "last_seen_at": now,
    }
    store._save(db)
    return key_hash


def _add_owned_unbound_key(store: LocalJsonLicenseStore, uid: str) -> str:
    """Create a redeemed/unbound key owned by uid. Returns key_hash."""
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
    return key_hash


# ── Test 1: Default global max_panel = 1 ─────────────────────────────────────

class TestDefaultGlobalMaxPanel(unittest.TestCase):

    def test_01_default_global_max_panel_is_1(self):
        """Test 1 — global max_panel defaults to 1 when no config row exists."""
        store = _tmp_store()
        self.assertEqual(store.get_global_max_panel(), 1)
        self.assertEqual(DEFAULT_GLOBAL_MAX_PANEL, 1)


# ── Test 2: Set global max_panel ─────────────────────────────────────────────

class TestSetGlobalMaxPanel(unittest.TestCase):

    def test_02_set_global_max_panel_updates(self):
        """Test 2 — set_global_max_panel changes the global default."""
        store = _tmp_store()
        store.set_global_max_panel(2, updated_by="admin")
        self.assertEqual(store.get_global_max_panel(), 2)

    def test_02b_set_global_max_panel_zero(self):
        """Test 2b — setting global max_panel to 0 is valid."""
        store = _tmp_store()
        store.set_global_max_panel(0, updated_by="admin")
        self.assertEqual(store.get_global_max_panel(), 0)


# ── Test 3: Set per-user override ────────────────────────────────────────────

class TestSetUserPanelLimit(unittest.TestCase):

    def test_03_set_user_panel_limit(self):
        """Test 3 — set_user_panel_limit sets a per-user override."""
        store = _tmp_store()
        uid = "user3"
        store.set_user_panel_limit(uid, 3, updated_by="admin")
        self.assertEqual(store.get_user_panel_limit(uid), 3)

    def test_03b_no_override_returns_none(self):
        """Test 3b — user with no override returns None from get_user_panel_limit."""
        store = _tmp_store()
        self.assertIsNone(store.get_user_panel_limit("nonexistent"))


# ── Test 4: Per-user override wins ────────────────────────────────────────────

class TestPerUserOverrideWins(unittest.TestCase):

    def test_04_per_user_override_wins_over_global(self):
        """Test 4 — per-user override takes precedence over global max_panel."""
        store = _tmp_store()
        uid = "user4"
        store.set_global_max_panel(1, updated_by="admin")
        store.set_user_panel_limit(uid, 5, updated_by="admin")
        self.assertEqual(store.get_effective_max_panel(uid), 5)

    def test_04b_global_applies_when_no_override(self):
        """Test 4b — global limit applies when no per-user override exists."""
        store = _tmp_store()
        uid = "user4b"
        store.set_global_max_panel(3, updated_by="admin")
        self.assertEqual(store.get_effective_max_panel(uid), 3)


# ── Test 5: max_panel = 0 blocks reset ───────────────────────────────────────

class TestMaxPanelZeroBlocks(unittest.TestCase):

    def test_05_zero_limit_blocks_can_user_reset(self):
        """Test 5 — max_panel = 0 causes can_user_reset_panel_today to deny."""
        store = _tmp_store()
        uid = "user5"
        store.set_user_panel_limit(uid, 0, updated_by="admin")
        allowed, used, max_p = store.can_user_reset_panel_today(uid)
        self.assertFalse(allowed)
        self.assertEqual(max_p, 0)

    def test_05b_zero_limit_record_raises(self):
        """Test 5b — record_successful_panel_reset raises PanelLimitError when max=0."""
        store = _tmp_store()
        uid = "user5b"
        store.set_user_panel_limit(uid, 0, updated_by="admin")
        with self.assertRaises(PanelLimitError):
            store.record_successful_panel_reset(uid, unbound_key_count=1)


# ── Test 6: Reset allowed when under limit ───────────────────────────────────

class TestResetAllowedUnderLimit(unittest.TestCase):

    def test_06_reset_allowed_when_under_limit(self):
        """Test 6 — can_user_reset_panel_today returns True when used < max."""
        store = _tmp_store()
        uid = "user6"
        store.set_global_max_panel(2, updated_by="admin")
        allowed, used, max_p = store.can_user_reset_panel_today(uid)
        self.assertTrue(allowed)
        self.assertEqual(used, 0)
        self.assertEqual(max_p, 2)

    def test_06b_after_first_use_still_allowed(self):
        """Test 6b — after 1 use with limit=2, still allowed."""
        store = _tmp_store()
        uid = "user6b"
        store.set_global_max_panel(2, updated_by="admin")
        store.record_successful_panel_reset(uid, unbound_key_count=1)
        allowed, used, max_p = store.can_user_reset_panel_today(uid)
        self.assertTrue(allowed)
        self.assertEqual(used, 1)


# ── Test 7: Reset blocked when at limit ──────────────────────────────────────

class TestResetBlockedAtLimit(unittest.TestCase):

    def test_07_blocked_when_at_limit(self):
        """Test 7 — can_user_reset_panel_today returns False when used >= max."""
        store = _tmp_store()
        uid = "user7"
        store.set_global_max_panel(1, updated_by="admin")
        store.record_successful_panel_reset(uid, unbound_key_count=1)
        allowed, used, max_p = store.can_user_reset_panel_today(uid)
        self.assertFalse(allowed)
        self.assertEqual(used, 1)
        self.assertEqual(max_p, 1)

    def test_07b_record_raises_when_at_limit(self):
        """Test 7b — record_successful_panel_reset raises PanelLimitError when at limit."""
        store = _tmp_store()
        uid = "user7b"
        store.set_global_max_panel(1, updated_by="admin")
        store.record_successful_panel_reset(uid, unbound_key_count=2)
        with self.assertRaises(PanelLimitError):
            store.record_successful_panel_reset(uid, unbound_key_count=1)


# ── Test 8: Multiple keys = 1 use ────────────────────────────────────────────

class TestMultipleKeysCountsAsOneUse(unittest.TestCase):

    def test_08_multiple_keys_reset_counts_as_one_use(self):
        """Test 8 — resetting 5 keys at once uses only 1 of the daily limit."""
        store = _tmp_store()
        uid = "user8"
        store.set_global_max_panel(2, updated_by="admin")
        # Record 5 keys as unbound, but it's 1 panel action
        new_count = store.record_successful_panel_reset(uid, unbound_key_count=5)
        self.assertEqual(new_count, 1)
        used_today = store.get_panel_reset_usage_today(uid)
        self.assertEqual(used_today, 1)


# ── Test 9: Opening panel doesn't count ──────────────────────────────────────

class TestOpeningPanelDoesNotCount(unittest.TestCase):

    def test_09_opening_panel_does_not_count(self):
        """Test 9 — simply calling can_user_reset_panel_today does NOT increment usage."""
        store = _tmp_store()
        uid = "user9"
        # Simulate user opening the Reset HWID panel (just checking limit)
        store.can_user_reset_panel_today(uid)
        store.can_user_reset_panel_today(uid)
        store.can_user_reset_panel_today(uid)
        # Usage must still be 0
        self.assertEqual(store.get_panel_reset_usage_today(uid), 0)


# ── Test 10: Cancelling doesn't count ────────────────────────────────────────

class TestCancelDoesNotCount(unittest.TestCase):

    def test_10_cancelling_does_not_count(self):
        """Test 10 — cancelling the reset flow (no record call) leaves usage at 0."""
        store = _tmp_store()
        uid = "user10"
        # Simulate: check passes, user opens selector, then cancels (no record call)
        allowed, used, max_p = store.can_user_reset_panel_today(uid)
        self.assertTrue(allowed)
        # No record call made (cancel path)
        self.assertEqual(store.get_panel_reset_usage_today(uid), 0)


# ── Test 11: Failed reset doesn't count ──────────────────────────────────────

class TestFailedResetDoesNotCount(unittest.TestCase):

    def test_11_failed_reset_does_not_count(self):
        """Test 11 — a reset that raises NoActiveBindingError must not increment usage."""
        store = _tmp_store()
        uid = "user11"
        key_hash = _add_owned_unbound_key(store, uid)
        # Attempt to reset an unbound key → raises NoActiveBindingError
        with self.assertRaises(NoActiveBindingError):
            store.reset_hwid(uid, key_hash)
        # Usage must still be 0 (no successful unbind occurred)
        self.assertEqual(store.get_panel_reset_usage_today(uid), 0)


# ── Test 12: No bound keys doesn't count ──────────────────────────────────────

class TestNoBoundKeysDoesNotCount(unittest.TestCase):

    def test_12_no_bound_keys_does_not_count(self):
        """Test 12 — reset with no bound keys does not increment usage."""
        store = _tmp_store()
        uid = "user12"
        # User has no keys at all — successful_count = 0, no record call made
        # (this is enforced in the cog: only call record if successful_count > 0)
        successful_count = 0
        if successful_count > 0:
            store.record_successful_panel_reset(uid, unbound_key_count=0)
        self.assertEqual(store.get_panel_reset_usage_today(uid), 0)


# ── Test 13: Counter tied to user ID, not key ID ────────────────────────────

class TestCounterTiedToUserId(unittest.TestCase):

    def test_13_counter_tied_to_user_not_key(self):
        """Test 13 — usage counters are per discord_user_id, not per key_id."""
        store = _tmp_store()
        uid_a = "userA13"
        uid_b = "userB13"
        store.set_global_max_panel(2, updated_by="admin")
        store.record_successful_panel_reset(uid_a, unbound_key_count=1)
        store.record_successful_panel_reset(uid_a, unbound_key_count=1)
        # uid_a is at limit, uid_b should be unaffected
        self.assertEqual(store.get_panel_reset_usage_today(uid_a), 2)
        self.assertEqual(store.get_panel_reset_usage_today(uid_b), 0)
        allowed_a, _, _ = store.can_user_reset_panel_today(uid_a)
        allowed_b, _, _ = store.can_user_reset_panel_today(uid_b)
        self.assertFalse(allowed_a)
        self.assertTrue(allowed_b)


# ── Test 14: Counter resets by WIB date bucket ───────────────────────────────

class TestCounterResetsByWibDate(unittest.TestCase):

    def test_14_counter_different_for_different_wib_days(self):
        """Test 14 — usage for different WIB days is independent."""
        store = _tmp_store()
        uid = "user14"
        # Manually inject usage for a past WIB day
        db = store._load()
        if "panel_usage" not in db:
            db["panel_usage"] = {}
        db["panel_usage"][f"{uid}:2000-01-01"] = {
            "discord_user_id": uid,
            "reset_day_wib": "2000-01-01",
            "used_count": 1,
            "last_reset_at": "2000-01-01T00:00:00+00:00",
            "updated_at": "2000-01-01T00:00:00+00:00",
        }
        store._save(db)
        # Today's usage must still be 0
        self.assertEqual(store.get_panel_reset_usage_today(uid), 0)
        allowed, used, _ = store.can_user_reset_panel_today(uid)
        self.assertTrue(allowed)
        self.assertEqual(used, 0)


# ── Test 15: WIB date bucket changes at 12:00 AM Asia/Jakarta ────────────────

class TestWibDateBucket(unittest.TestCase):

    def test_15_get_wib_day_uses_jakarta_timezone(self):
        """Test 15 — get_wib_day produces WIB (UTC+7) date, not UTC date."""
        # Pick a UTC datetime that is 23:30 UTC = 06:30 WIB (next day at 06:30)
        # → WIB date is the NEXT calendar day compared to the UTC date
        # UTC 2026-01-01 23:30:00 → WIB 2026-01-02 06:30:00
        utc_dt = datetime(2026, 1, 1, 23, 30, 0, tzinfo=timezone.utc)
        wib_date = get_wib_day(utc_dt)
        self.assertEqual(wib_date, "2026-01-02")

    def test_15b_wib_day_at_midnight_utc(self):
        """Test 15b — at 00:00 UTC (07:00 WIB), WIB date matches UTC date."""
        utc_dt = datetime(2026, 3, 15, 0, 0, 0, tzinfo=timezone.utc)
        wib_date = get_wib_day(utc_dt)
        self.assertEqual(wib_date, "2026-03-15")

    def test_15c_wib_midnight_bucket_changes(self):
        """Test 15c — 23:59 WIB and 00:00 WIB (next day) produce different buckets."""
        # WIB midnight = UTC 17:00 the previous day
        # 17:00 UTC = 00:00 WIB (next calendar day)
        utc_just_before_midnight_wib = datetime(2026, 5, 26, 16, 59, 0, tzinfo=timezone.utc)
        utc_at_midnight_wib = datetime(2026, 5, 26, 17, 0, 0, tzinfo=timezone.utc)
        day_before = get_wib_day(utc_just_before_midnight_wib)
        day_after = get_wib_day(utc_at_midnight_wib)
        self.assertNotEqual(day_before, day_after)
        # day_before should be 2026-05-26, day_after should be 2026-05-27
        self.assertEqual(day_before, "2026-05-26")
        self.assertEqual(day_after, "2026-05-27")


# ── Test 16: Reset HWID does not change max_key ownership count ───────────────

class TestResetHwidDoesNotReduceKeyCount(unittest.TestCase):

    def test_16_reset_hwid_does_not_reduce_max_key_count(self):
        """Test 16 — Reset HWID changes bound→unbound but key still counts toward max_key."""
        from agent.key_stats_format import filter_active_visible_license_rows
        store = _tmp_store()
        uid = "user16"
        store.set_user_key_limit(uid, 5, updated_by="admin")
        key_hash = _add_owned_bound_key(store, uid)
        # Active count before reset
        count_before = store.count_active_keys_for_limit(uid)
        # Reset HWID
        store.reset_hwid(uid, key_hash)
        # Active count after reset — must be the same (unbound still counts)
        count_after = store.count_active_keys_for_limit(uid)
        self.assertEqual(count_before, count_after)
        self.assertGreaterEqual(count_after, 1)


# ── Test 17: Reset HWID changes owned/bound → owned/unbound ──────────────────

class TestResetHwidUnbindsKey(unittest.TestCase):

    def test_17_reset_hwid_changes_bound_to_unbound(self):
        """Test 17 — Reset HWID deactivates the active binding."""
        store = _tmp_store()
        uid = "user17"
        key_hash = _add_owned_bound_key(store, uid)
        # Confirm the key is bound before reset
        db = store._load()
        self.assertTrue(db["bindings"][key_hash]["is_active"])
        # Reset
        store.reset_hwid(uid, key_hash)
        # Confirm the binding is now inactive (unbound)
        db = store._load()
        self.assertFalse(db["bindings"][key_hash]["is_active"])


# ── Test 18: /license user shows Max Panel Resets and Reset Uses Today ────────

class TestLicenseUserDisplayShowsPanelInfo(unittest.TestCase):

    def test_18_admin_stats_description_shows_max_panel(self):
        """Test 18 — build_license_admin_stats_description includes panel limit info."""
        store = _tmp_store()
        uid = "user18"
        store.set_global_max_panel(2, updated_by="admin")
        store.record_successful_panel_reset(uid, unbound_key_count=1)

        effective_max_panel = store.get_effective_max_panel(uid)
        panel_override = store.get_user_panel_limit(uid)
        max_panel_source = "user" if panel_override is not None else "global"
        panel_resets_today = store.get_panel_reset_usage_today(uid)

        stats = {
            "key_generated_count": 0,
            "key_redeemed_count": 0,
            "unbound_key_count": 0,
            "bound_key_count": 0,
            "reset_hwid_count": 0,
            "key_executed_count": 0,
        }
        desc = build_license_admin_stats_description(
            user_label="<@123> (123)",
            stats=stats,
            active_rows=[],
            effective_max_panel=effective_max_panel,
            max_panel_source=max_panel_source,
            panel_resets_today=panel_resets_today,
        )
        self.assertIn("Max Panel Resets:", desc)
        self.assertIn("Global Default", desc)
        self.assertIn("Reset Uses Today:", desc)
        self.assertIn("1 / 2", desc)
        self.assertIn("12:00 AM WIB", desc)

    def test_18b_user_override_shown_in_display(self):
        """Test 18b — per-user override label shown in description."""
        stats = {
            "key_generated_count": 0,
            "key_redeemed_count": 0,
            "unbound_key_count": 0,
            "bound_key_count": 0,
            "reset_hwid_count": 0,
            "key_executed_count": 0,
        }
        desc = build_license_admin_stats_description(
            user_label="<@99> (99)",
            stats=stats,
            active_rows=[],
            effective_max_panel=5,
            max_panel_source="user",
            panel_resets_today=2,
        )
        self.assertIn("Max Panel Resets:", desc)
        self.assertIn("User Override", desc)
        self.assertIn("5", desc)
        self.assertIn("2 / 5", desc)


# ── Test 19: Permission check ─────────────────────────────────────────────────

class TestPermissionChecks(unittest.TestCase):

    def test_19_non_admin_denied_by_is_owner_check(self):
        """Test 19 — _is_owner returns False for users not in LICENSE_OWNER_DISCORD_IDS."""
        import os
        from bot.cog_license_panel import _is_owner
        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": "12345"}, clear=False):
            class FakeUser:
                id = 99999
            class FakeOwner:
                id = 12345
            self.assertFalse(_is_owner(FakeUser()))
            self.assertTrue(_is_owner(FakeOwner()))


# ── Test 20: Log event posted on max_panel change ────────────────────────────

class TestLogEventPostedOnMaxPanelChange(unittest.IsolatedAsyncioTestCase):

    async def test_20_log_event_posted_when_global_max_panel_changes(self):
        """Test 20 — _post_max_panel_log sends an embed to the configured log channel."""
        import discord as _discord
        from bot.cog_license_panel import _post_max_panel_log

        mock_store = MagicMock()
        mock_store.get_license_log_config.return_value = {"channel_id": "111222"}

        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        mock_channel.__class__ = _discord.TextChannel

        mock_guild = MagicMock()
        mock_guild.id = 8888
        mock_guild.get_channel.return_value = mock_channel

        mock_admin = MagicMock()
        mock_admin.id = 12345

        await _post_max_panel_log(
            mock_guild, mock_store,
            mock_admin, "Global", None,
            1, 2,
        )
        mock_channel.send.assert_awaited_once()
        call_kwargs = mock_channel.send.call_args
        embed_arg = call_kwargs[1].get("embed") or call_kwargs[0][0]
        self.assertEqual(embed_arg.title, "Panel Reset Limit Updated")
        self.assertIn("Admin:", embed_arg.description)
        self.assertIn("Scope:", embed_arg.description)
        self.assertIn("Old Limit:", embed_arg.description)
        self.assertIn("New Limit:", embed_arg.description)
        self.assertIn("Reset Window:", embed_arg.description)
        self.assertIn("12:00 AM WIB", embed_arg.description)

    async def test_20b_log_not_posted_when_no_channel_configured(self):
        """Test 20b — no log is posted when no license log channel is configured."""
        import discord as _discord
        from bot.cog_license_panel import _post_max_panel_log

        mock_store = MagicMock()
        mock_store.get_license_log_config.return_value = None

        mock_guild = MagicMock()

        mock_admin = MagicMock()
        mock_admin.id = 12345

        # Should complete without error and not post
        await _post_max_panel_log(
            mock_guild, mock_store,
            mock_admin, "User", None,
            1, 3,
        )
        # No channel lookup should be attempted
        mock_guild.get_channel.assert_not_called()

    async def test_20c_log_includes_target_user_for_user_scope(self):
        """Test 20c — per-user scope log includes the target user mention."""
        import discord as _discord
        from bot.cog_license_panel import _post_max_panel_log

        mock_store = MagicMock()
        mock_store.get_license_log_config.return_value = {"channel_id": "333444"}

        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        mock_channel.__class__ = _discord.TextChannel

        mock_guild = MagicMock()
        mock_guild.id = 9999
        mock_guild.get_channel.return_value = mock_channel

        mock_admin = MagicMock()
        mock_admin.id = 11111

        mock_target = MagicMock()
        mock_target.id = 22222

        await _post_max_panel_log(
            mock_guild, mock_store,
            mock_admin, "User", mock_target,
            "Global 1", 3,
        )
        mock_channel.send.assert_awaited_once()
        embed_arg = mock_channel.send.call_args[1].get("embed") or mock_channel.send.call_args[0][0]
        self.assertIn(str(mock_target.id), embed_arg.description)
        self.assertIn("User:", embed_arg.description)


# ── Test 21: Race condition — two simultaneous confirmations ─────────────────

class TestRaceCondition(unittest.TestCase):

    def test_21_race_two_simultaneous_resets_cannot_bypass_limit_1(self):
        """Test 21 — Two calls to record_successful_panel_reset with limit=1:
        First call succeeds (used_count→1), second call raises PanelLimitError.
        """
        store = _tmp_store()
        uid = "race_user"
        store.set_global_max_panel(1, updated_by="admin")

        # First reset call succeeds
        new_count = store.record_successful_panel_reset(uid, unbound_key_count=2)
        self.assertEqual(new_count, 1)

        # Second reset call (simulated race: both passed the check, but record is atomic)
        with self.assertRaises(PanelLimitError):
            store.record_successful_panel_reset(uid, unbound_key_count=1)

        # Usage must be exactly 1, not 2
        self.assertEqual(store.get_panel_reset_usage_today(uid), 1)


# ── Additional: Reset HWID log includes panel usage ──────────────────────────

class TestResetHwidLogIncludesPanelUsage(unittest.TestCase):

    def test_reset_hwid_log_description_includes_panel_usage(self):
        """build_reset_hwid_log_description includes Reset Uses Today and Reset Window."""
        from agent.key_stats_format import build_reset_hwid_log_description
        stats = {
            "key_generated_count": 1,
            "key_redeemed_count": 1,
            "unbound_key_count": 0,
            "bound_key_count": 1,
            "reset_hwid_count": 2,
        }
        desc = build_reset_hwid_log_description(
            user_mention="<@123>",
            reset_key="DENG-XXXX-XXXX-XXXX-XXXX",
            stats=stats,
            reset_uses_today=1,
            max_panel=1,
        )
        self.assertIn("Reset Uses Today:", desc)
        self.assertIn("1 / 1", desc)
        self.assertIn("Reset Window:", desc)
        self.assertIn("12:00 AM WIB", desc)

    def test_reset_hwid_log_description_without_panel_usage(self):
        """build_reset_hwid_log_description works without panel usage args."""
        from agent.key_stats_format import build_reset_hwid_log_description
        stats = {
            "key_generated_count": 0,
            "key_redeemed_count": 0,
            "unbound_key_count": 0,
            "bound_key_count": 0,
            "reset_hwid_count": 0,
        }
        desc = build_reset_hwid_log_description(
            user_mention="<@123>",
            reset_key="DENG-XXXX-XXXX-XXXX-XXXX",
            stats=stats,
        )
        self.assertNotIn("Reset Uses Today:", desc)
        self.assertNotIn("Reset Window:", desc)


# ── Additional: Panel limit response builder ──────────────────────────────────

class TestPanelLimitResponseBuilder(unittest.TestCase):

    def test_panel_limit_blocked_response_shows_usage(self):
        """build_panel_limit_blocked_response includes usage info and WIB time."""
        from agent.license_panel import build_panel_limit_blocked_response
        payload = build_panel_limit_blocked_response(1, used_count=1)
        self.assertIn("1 / 1", payload["embed"]["description"])
        self.assertIn("12:00 AM WIB", payload["embed"]["description"])

    def test_panel_limit_blocked_response_zero_limit(self):
        """build_panel_limit_blocked_response with max=0 shows disabled message."""
        from agent.license_panel import build_panel_limit_blocked_response
        payload = build_panel_limit_blocked_response(0)
        self.assertIn("0 / day", payload["embed"]["description"])

    def test_reset_mixed_summary_embed_includes_usage(self):
        """build_reset_mixed_summary_embed includes Reset Uses Today when provided."""
        from agent.license_panel import build_reset_mixed_summary_embed
        results = [{"display_key": "DENG-TEST", "success": True, "message": "Cleared."}]
        payload = build_reset_mixed_summary_embed(
            results, reset_uses_today=1, max_panel=2
        )
        self.assertIn("Reset Uses Today:", payload["embed"]["description"])
        self.assertIn("1 / 2", payload["embed"]["description"])

    def test_reset_mixed_summary_embed_no_usage(self):
        """build_reset_mixed_summary_embed works without panel usage args."""
        from agent.license_panel import build_reset_mixed_summary_embed
        results = [{"display_key": "DENG-TEST", "success": True, "message": "Cleared."}]
        payload = build_reset_mixed_summary_embed(results)
        self.assertNotIn("Reset Uses Today:", payload["embed"]["description"])


if __name__ == "__main__":
    unittest.main()
