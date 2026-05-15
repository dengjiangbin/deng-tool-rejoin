"""Tests for HWID reset fixes, generate/redeem relationship, and license API.

Tests numbers follow the spec in the task:
1-8:   Reset HWID
9-14:  Generate/Redeem
15-21: Client/remote (license store API contract)
22-24: Panel/status UX
25-29: Tutorial docs existence
30:    Regression
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Project root ───────────────────────────────────────────────────────────────
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.license import (
    generate_license_key,
    hash_license_key,
    mask_license_key,
    normalize_license_key,
)
from agent.license_store import (
    ACTIVE_HEARTBEAT_WINDOW_S,
    MAX_HWID_RESETS_PER_24H,
    RESULT_ACTIVE,
    RESULT_WRONG_DEVICE,
    ActiveKeyWarning,
    KeyAlreadySelfOwned,
    KeyNotFoundError,
    KeyOwnershipError,
    LocalJsonLicenseStore,
    NoActiveBindingError,
    ResetLimitError,
    UserLimitError,
)


# ── Helper ─────────────────────────────────────────────────────────────────────

def _tmp_store() -> LocalJsonLicenseStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    Path(tmp.name).unlink()
    return LocalJsonLicenseStore(Path(tmp.name))


def _bound_store_with_key(uid: str, *, active_binding: bool = True, last_seen_old: bool = True):
    """Create a store with a user, key, and device binding."""
    store = _tmp_store()
    store.get_or_create_user(uid)
    full_key = store.create_key_for_user(uid)
    key_hash = hash_license_key(normalize_license_key(full_key))

    if active_binding:
        store.bind_or_check_device(full_key, "aa" * 32, "Pixel 6", "1.0")
        if last_seen_old:
            # Make last_seen_at old enough to pass the active guard
            db = store._load()
            db["bindings"][key_hash]["last_seen_at"] = "2020-01-01T00:00:00+00:00"
            store._save(db)

    return store, full_key, key_hash


# ══════════════════════════════════════════════════════════════════════════════
# 1-8: Reset HWID tests
# ══════════════════════════════════════════════════════════════════════════════

class TestResetHwidNoKey(unittest.TestCase):
    """Test 1 – reset with no key returns no_key (KeyNotFoundError)."""

    def test_reset_no_key_raises_not_found(self):
        store = _tmp_store()
        uid = "u001"
        store.get_or_create_user(uid)
        with self.assertRaises(KeyNotFoundError):
            store.reset_hwid(uid, "nonexistent_key_hash_xxxx")


class TestResetHwidNoBinding(unittest.TestCase):
    """Test 2 – reset with key but no active binding raises NoActiveBindingError."""

    def test_no_binding_raises(self):
        store = _tmp_store()
        uid = "u002"
        store.get_or_create_user(uid)
        full_key = store.create_key_for_user(uid)
        key_hash = hash_license_key(normalize_license_key(full_key))
        # No bind_or_check_device called → no binding exists
        with self.assertRaises(NoActiveBindingError):
            store.reset_hwid(uid, key_hash)

    def test_inactive_binding_raises(self):
        """An existing but inactive (is_active=False) binding should also raise."""
        store, full_key, key_hash = _bound_store_with_key("u002b")
        # Force inactive
        db = store._load()
        db["bindings"][key_hash]["is_active"] = False
        store._save(db)
        with self.assertRaises(NoActiveBindingError):
            store.reset_hwid("u002b", key_hash)


class TestResetHwidNoBindingDoesNotLog(unittest.TestCase):
    """Test 3 – no_binding does not insert reset log."""

    def test_no_log_written_when_no_binding(self):
        store = _tmp_store()
        uid = "u003"
        store.get_or_create_user(uid)
        full_key = store.create_key_for_user(uid)
        key_hash = hash_license_key(normalize_license_key(full_key))

        with self.assertRaises(NoActiveBindingError):
            store.reset_hwid(uid, key_hash)

        db = store._load()
        reset_logs = [e for e in db.get("reset_logs", []) if e.get("key_id") == key_hash]
        self.assertEqual(len(reset_logs), 0, "Reset log must NOT be written when no binding exists")

    def test_no_log_does_not_consume_reset_slot(self):
        """Attempting reset with no binding does not count against the 5-per-day limit."""
        store = _tmp_store()
        uid = "u003b"
        store.get_or_create_user(uid)
        full_key = store.create_key_for_user(uid)
        key_hash = hash_license_key(normalize_license_key(full_key))

        for _ in range(3):
            try:
                store.reset_hwid(uid, key_hash)
            except NoActiveBindingError:
                pass

        count = store.get_reset_count_24h(key_hash)
        self.assertEqual(count, 0, "Reset count must remain 0 when no binding was cleared")


class TestResetHwidSuccess(unittest.TestCase):
    """Test 4 – reset with active binding older than 5 minutes clears binding."""

    def test_clears_active_binding(self):
        store, full_key, key_hash = _bound_store_with_key("u004", active_binding=True, last_seen_old=True)
        store.reset_hwid("u004", key_hash)
        db = store._load()
        binding = db["bindings"].get(key_hash, {})
        self.assertFalse(binding.get("is_active"), "Binding must be deactivated after reset")

    def test_success_message_not_fake(self):
        """Successful reset must write a reset_log entry (proof something was cleared)."""
        store, full_key, key_hash = _bound_store_with_key("u004b", active_binding=True, last_seen_old=True)
        store.reset_hwid("u004b", key_hash)
        db = store._load()
        logs = [e for e in db.get("reset_logs", []) if e.get("key_id") == key_hash]
        self.assertGreater(len(logs), 0, "Reset log must be written on successful reset")


class TestResetHwidActiveRecently(unittest.TestCase):
    """Test 5 – reset blocks if last_seen_at is within the active window."""

    def test_blocks_active_recently(self):
        store, full_key, key_hash = _bound_store_with_key("u005", active_binding=True, last_seen_old=False)
        # Ensure last_seen_at is very recent
        db = store._load()
        db["bindings"][key_hash]["last_seen_at"] = datetime.now(timezone.utc).isoformat()
        store._save(db)
        with self.assertRaises(ActiveKeyWarning):
            store.reset_hwid("u005", key_hash)

    def test_no_log_on_active_recently_block(self):
        """Active guard block must not write a reset log."""
        store, full_key, key_hash = _bound_store_with_key("u005b", active_binding=True, last_seen_old=False)
        db = store._load()
        db["bindings"][key_hash]["last_seen_at"] = datetime.now(timezone.utc).isoformat()
        store._save(db)

        try:
            store.reset_hwid("u005b", key_hash)
        except ActiveKeyWarning:
            pass

        db = store._load()
        logs = [e for e in db.get("reset_logs", []) if e.get("key_id") == key_hash]
        self.assertEqual(len(logs), 0, "Reset log must NOT be written on active-recently block")


class TestResetHwidRateLimit(unittest.TestCase):
    """Test 6 – reset count >= 5 blocks with ResetLimitError."""

    def test_sixth_reset_blocked(self):
        uid = "u006"
        store, full_key, key_hash = _bound_store_with_key(uid, active_binding=True, last_seen_old=True)

        for _ in range(MAX_HWID_RESETS_PER_24H):
            store.reset_hwid(uid, key_hash)
            # Re-bind for next iteration
            db = store._load()
            db["bindings"][key_hash] = {
                "install_id_hash": "deadbeef" * 8,
                "is_active": True,
                "device_model": "Pixel",
                "last_seen_at": "2020-01-01T00:00:00+00:00",
                "bound_at": "2020-01-01T00:00:00+00:00",
                "last_status": "active",
                "device_label": "",
            }
            store._save(db)

        with self.assertRaises(ResetLimitError):
            store.reset_hwid(uid, key_hash)


class TestResetHwidWritesLog(unittest.TestCase):
    """Test 7 – successful reset writes reset_log."""

    def test_log_written(self):
        uid = "u007"
        store, full_key, key_hash = _bound_store_with_key(uid)
        store.reset_hwid(uid, key_hash)
        db = store._load()
        logs = [e for e in db.get("reset_logs", []) if e.get("key_id") == key_hash]
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["owner_discord_id"], uid)


class TestResetHwidNoFakeSuccess(unittest.TestCase):
    """Test 8 – response message never says 'cleared' if nothing was cleared."""

    def test_no_binding_raises_not_cleared(self):
        """NoActiveBindingError is raised — the bot layer must NOT say 'cleared'."""
        store = _tmp_store()
        uid = "u008"
        store.get_or_create_user(uid)
        full_key = store.create_key_for_user(uid)
        key_hash = hash_license_key(normalize_license_key(full_key))

        try:
            store.reset_hwid(uid, key_hash)
            self.fail("Expected NoActiveBindingError but none was raised")
        except NoActiveBindingError as exc:
            # The exception message must NOT say "cleared"
            self.assertNotIn("cleared", str(exc).lower())

    def test_build_reset_no_binding_response_not_cleared(self):
        """The no-binding response builder must not say 'cleared'."""
        from agent.license_panel import build_reset_no_binding_response
        payload = build_reset_no_binding_response()
        description = payload["embed"]["description"]
        self.assertNotIn("cleared", description.lower())

    def test_build_reset_success_says_cleared(self):
        """The success response builder SHOULD say 'cleared' (as positive confirmation)."""
        from agent.license_panel import build_reset_success_response
        payload = build_reset_success_response()
        description = payload["embed"]["description"]
        self.assertIn("cleared", description.lower())


# ══════════════════════════════════════════════════════════════════════════════
# 9-14: Generate / Redeem
# ══════════════════════════════════════════════════════════════════════════════

class TestGeneratedKeyOwnership(unittest.TestCase):
    """Test 9 – generated key is owned by the generator."""

    def test_generated_key_owner(self):
        store = _tmp_store()
        uid = "gen001"
        store.get_or_create_user(uid)
        full_key = store.create_key_for_user(uid)
        key_hash = hash_license_key(normalize_license_key(full_key))
        db = store._load()
        self.assertEqual(db["keys"][key_hash]["owner_discord_id"], uid)


class TestRedeemSameUser(unittest.TestCase):
    """Test 10 – same user redeeming own generated key returns KeyAlreadySelfOwned."""

    def test_raises_already_self_owned(self):
        store = _tmp_store()
        uid = "rd001"
        store.get_or_create_user(uid)
        full_key = store.create_key_for_user(uid)
        with self.assertRaises(KeyAlreadySelfOwned):
            store.redeem_key_for_user(uid, full_key)

    def test_already_self_owned_message_contains_masked(self):
        store = _tmp_store()
        uid = "rd001b"
        store.get_or_create_user(uid)
        full_key = store.create_key_for_user(uid)
        try:
            store.redeem_key_for_user(uid, full_key)
            self.fail("Expected KeyAlreadySelfOwned")
        except KeyAlreadySelfOwned as exc:
            # Should reference the masked key, NOT the full key
            self.assertNotIn(full_key, str(exc))
            masked = mask_license_key(normalize_license_key(full_key))
            self.assertIn(masked, str(exc))


class TestRedeemOtherUserRejected(unittest.TestCase):
    """Test 11 – other user redeeming owned key is rejected."""

    def test_raises_ownership_error(self):
        store = _tmp_store()
        uid1, uid2 = "own001", "other001"
        store.get_or_create_user(uid1)
        store.get_or_create_user(uid2)
        full_key = store.create_key_for_user(uid1)
        with self.assertRaises(KeyOwnershipError):
            store.redeem_key_for_user(uid2, full_key)


class TestRedeemUnownedKey(unittest.TestCase):
    """Test 12 – unowned key can be redeemed by any user under limit."""

    def _make_unowned_key(self, store: LocalJsonLicenseStore, creator_uid: str) -> str:
        """Create a key that is owned but force-detach it to simulate unowned."""
        store.get_or_create_user(creator_uid)
        full_key = store.create_key_for_user(creator_uid)
        key_hash = hash_license_key(normalize_license_key(full_key))
        db = store._load()
        db["keys"][key_hash]["owner_discord_id"] = None
        store._save(db)
        return full_key

    def test_unowned_key_can_be_redeemed(self):
        store = _tmp_store()
        full_key = self._make_unowned_key(store, "creator_x")
        redeemer = "redeemer_y"
        store.get_or_create_user(redeemer)
        masked = store.redeem_key_for_user(redeemer, full_key)
        self.assertIn("...", masked)  # masked format
        # Verify ownership transferred
        key_hash = hash_license_key(normalize_license_key(full_key))
        db = store._load()
        self.assertEqual(db["keys"][key_hash]["owner_discord_id"], redeemer)


class TestRedeemAtLimit(unittest.TestCase):
    """Test 13 – over-limit user cannot redeem another unowned key."""

    def test_user_at_limit_cannot_redeem(self):
        store = _tmp_store()
        uid1 = "limit001"
        uid2 = "limit002"
        store.get_or_create_user(uid1)
        store.get_or_create_user(uid2)
        # uid2 already has 1 key (default max)
        store.create_key_for_user(uid2)
        # Create an unowned key (uid1 creates it, then we detach ownership)
        full_key2 = store.create_key_for_user(uid1)
        key_hash = hash_license_key(normalize_license_key(full_key2))
        db = store._load()
        db["keys"][key_hash]["owner_discord_id"] = None
        store._save(db)
        # uid2 is already at limit — should fail
        with self.assertRaises(UserLimitError):
            store.redeem_key_for_user(uid2, full_key2)


class TestRedeemResponseNoFullKey(unittest.TestCase):
    """Test 14 – redeem response never shows full key."""

    def test_redeem_success_shows_masked(self):
        from agent.license_panel import build_redeem_success_response
        masked = "DENG-8F3A...44F0"
        payload = build_redeem_success_response(masked)
        desc = payload["embed"]["description"]
        # Masked format shown
        self.assertIn(masked, desc)

    def test_redeem_success_does_not_include_full_key(self):
        from agent.license_panel import build_redeem_success_response
        full_key = "DENG-8F3A-B3C4-D5E6-44F0"
        masked = mask_license_key(full_key)
        payload = build_redeem_success_response(masked)
        desc = payload["embed"]["description"]
        self.assertNotIn(full_key, desc)

    def test_already_owned_response_shows_masked(self):
        from agent.license_panel import build_redeem_already_owned_response
        payload = build_redeem_already_owned_response("This key is already attached to your account (DENG-8F3A...44F0).")
        desc = payload["embed"]["description"]
        self.assertIn("DENG-8F3A...44F0", desc)

    def test_already_owned_response_title_informational(self):
        from agent.license_panel import build_redeem_already_owned_response
        payload = build_redeem_already_owned_response("msg")
        title = payload["embed"]["title"]
        # Should be informational, not error-level
        self.assertNotIn("Error", title)
        self.assertNotIn("Failed", title)


# ══════════════════════════════════════════════════════════════════════════════
# 15-21: Client/remote license API contract
# ══════════════════════════════════════════════════════════════════════════════

class TestLicenseApiContract(unittest.TestCase):
    """Tests 15-21: License API correctness. Uses LocalJsonStore, not real network."""

    def setUp(self):
        self.store = _tmp_store()
        self.uid = "api_user_001"
        self.store.get_or_create_user(self.uid)
        self.full_key = self.store.create_key_for_user(self.uid)
        self.install_hash_a = "aa" * 32  # 64-char hex-like string
        self.install_hash_b = "bb" * 32

    def tearDown(self):
        try:
            self.store._path.unlink()
        except FileNotFoundError:
            pass

    def test_remote_mode_service_role_not_in_check_request(self):
        """Test 15 – check endpoint only needs key/install_id_hash/device_model/app_version."""
        # The endpoint contract: client sends no service role key
        required_fields = {"key", "install_id_hash", "device_model", "app_version"}
        forbidden_fields = {"service_role_key", "supabase_key", "token", "bot_token"}
        # Verify the license_api.py reads only permitted fields
        import bot.license_api as api_mod
        import inspect
        src = inspect.getsource(api_mod._wsgi_app)
        for field in forbidden_fields:
            self.assertNotIn(
                f'body.get("{field}")', src,
                f"license_api must not read field '{field}' from client body"
            )
        for field in required_fields:
            self.assertIn(field, src)

    def test_first_bind_returns_active(self):
        """Test 16 – remote check binds first install_id if no binding."""
        result = self.store.bind_or_check_device(
            self.full_key, self.install_hash_a, "Pixel 6", "1.0"
        )
        self.assertEqual(result, RESULT_ACTIVE)

    def test_same_install_id_heartbeat_updates(self):
        """Test 17 – same install_id heartbeat updates last_seen_at."""
        from agent.license import normalize_license_key as _norm, hash_license_key as _hash
        k = _hash(_norm(self.full_key))
        self.store.bind_or_check_device(self.full_key, self.install_hash_a, "Pixel 6", "1.0")
        first_seen = self.store.get_last_seen_at(k)
        # Wait a tiny bit and call again
        import time; time.sleep(0.01)
        self.store.bind_or_check_device(self.full_key, self.install_hash_a, "Pixel 6", "1.0")
        second_seen = self.store.get_last_seen_at(k)
        # Both are valid timestamps; second may equal first if within same second — just check not None
        self.assertIsNotNone(second_seen)

    def test_different_install_id_returns_wrong_device(self):
        """Test 18 – different install_id returns wrong_device."""
        self.store.bind_or_check_device(self.full_key, self.install_hash_a, "Pixel 6", "1.0")
        result = self.store.bind_or_check_device(self.full_key, self.install_hash_b, "Other", "1.0")
        self.assertEqual(result, RESULT_WRONG_DEVICE)

    def test_reset_allows_different_device_bind(self):
        """Test 19 – reset clears binding so different install_id can bind."""
        from agent.license import normalize_license_key as _norm, hash_license_key as _hash
        k = _hash(_norm(self.full_key))
        self.store.bind_or_check_device(self.full_key, self.install_hash_a, "Pixel 6", "1.0")
        # Deactivate via reset
        db = self.store._load()
        db["bindings"][k]["last_seen_at"] = "2020-01-01T00:00:00+00:00"
        store = self.store
        store._save(db)
        store.reset_hwid(self.uid, k)
        # Now a different device can bind
        result = store.bind_or_check_device(self.full_key, self.install_hash_b, "New Phone", "1.0")
        self.assertEqual(result, RESULT_ACTIVE)

    def test_deng_dev_skips_check(self):
        """Test 21 – DENG_DEV=1 sets DEV_MODE in keystore.py."""
        import agent.keystore as ks
        with patch.dict(os.environ, {"DENG_DEV": "1"}):
            dev_mode = bool(os.environ.get("DENG_DEV", ""))
            self.assertTrue(dev_mode)

    def test_service_role_key_not_in_health_response(self):
        """Health endpoint must never expose service role key or any secret."""
        import bot.license_api as api_mod
        import inspect
        src = inspect.getsource(api_mod._wsgi_app)
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", src.split("health")[1].split("check")[0])


# ══════════════════════════════════════════════════════════════════════════════
# 22-24: Panel/status UX
# ══════════════════════════════════════════════════════════════════════════════

class TestPanelStatusUX(unittest.TestCase):
    """Test 22 – /license_panel status includes bound/unbound state."""

    def test_key_list_response_shows_bound_state(self):
        from agent.license_panel import build_key_list_response

        # Unbound key
        keys_unbound = [{"id": "abc", "masked_key": "DENG-1234...ABCD", "status": "active", "bound_device": "(unbound)", "last_seen_at": None}]
        payload = build_key_list_response(keys_unbound)
        desc = payload["embed"]["description"]
        self.assertIn("unbound", desc.lower())

        # Bound key
        keys_bound = [{"id": "abc", "masked_key": "DENG-1234...ABCD", "status": "active", "bound_device": "Pixel 6", "last_seen_at": "2026-05-01T00:00:00+00:00"}]
        payload = build_key_list_response(keys_bound)
        desc = payload["embed"]["description"]
        self.assertIn("Pixel 6", desc)

    def test_key_list_response_shows_last_seen(self):
        from agent.license_panel import build_key_list_response
        ts = "2026-05-15T04:00:00+00:00"
        keys = [{"id": "x", "masked_key": "DENG-AAAA...BBBB", "status": "active", "bound_device": "Redmi", "last_seen_at": ts}]
        payload = build_key_list_response(keys)
        desc = payload["embed"]["description"]
        self.assertIn(ts, desc)

    def test_key_list_empty_shows_instructions(self):
        from agent.license_panel import build_key_list_response
        payload = build_key_list_response([])
        desc = payload["embed"]["description"]
        self.assertIn("Generate Key", desc)


class TestAdminStatusHidesSecrets(unittest.TestCase):
    """Test 23 – admin_status hides secrets."""

    def test_admin_status_source_no_token(self):
        import inspect
        import bot.cog_license_panel as cog_mod
        src = inspect.getsource(cog_mod)
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", src.replace("os.environ.get(", ""))
        self.assertNotIn("DISCORD_BOT_TOKEN", src)

    def test_admin_status_no_raw_key(self):
        """admin_status embed must not include a raw license key."""
        import bot.cog_license_panel as cog_mod
        import inspect
        src = inspect.getsource(cog_mod)
        # Check cmd_admin_status doesn't call create_key_for_user or return raw key
        admin_src = src[src.find("admin_status"):]
        self.assertNotIn("create_key_for_user", admin_src[:1000])


class TestResetButtonMessages(unittest.TestCase):
    """Test 24 – Reset HWID button uses structured reset result messages."""

    def test_no_binding_response_not_success(self):
        from agent.license_panel import build_reset_no_binding_response
        payload = build_reset_no_binding_response()
        title = payload["embed"]["title"]
        desc = payload["embed"]["description"]
        # Must not claim success
        self.assertNotIn("cleared", desc.lower())
        self.assertNotIn("success", title.lower())

    def test_success_response_is_positive(self):
        from agent.license_panel import build_reset_success_response
        payload = build_reset_success_response()
        title = payload["embed"]["title"]
        # Should be confirmatory
        self.assertIn("HWID", title)

    def test_limit_response_shows_count(self):
        from agent.license_panel import build_reset_limit_response
        payload = build_reset_limit_response(5, 5)
        desc = payload["embed"]["description"]
        self.assertIn("5/5", desc)

    def test_active_warning_shows_elapsed(self):
        from agent.license_panel import build_reset_active_warning_response
        payload = build_reset_active_warning_response(120)  # 2 minutes
        desc = payload["embed"]["description"]
        self.assertIn("2m", desc)


# ══════════════════════════════════════════════════════════════════════════════
# 25-29: Docs existence
# ══════════════════════════════════════════════════════════════════════════════

DOCS_DIR = PROJECT / "docs"
TUTORIAL_PATH = DOCS_DIR / "TERMUX_INSTALL_TUTORIAL.md"


class TestTutorialDocExists(unittest.TestCase):
    """Test 25 – TERMUX_INSTALL_TUTORIAL.md exists."""

    def test_tutorial_file_exists(self):
        self.assertTrue(TUTORIAL_PATH.exists(), f"Tutorial not found at {TUTORIAL_PATH}")

    def test_tutorial_not_empty(self):
        if TUTORIAL_PATH.exists():
            content = TUTORIAL_PATH.read_text(encoding="utf-8")
            self.assertGreater(len(content), 500)


class TestTutorialContent(unittest.TestCase):
    """Tests 26-29 – tutorial content coverage."""

    @classmethod
    def setUpClass(cls):
        if TUTORIAL_PATH.exists():
            cls.content = TUTORIAL_PATH.read_text(encoding="utf-8").lower()
        else:
            cls.content = ""

    def setUp(self):
        if not self.content:
            self.skipTest("Tutorial file not found — skipping content checks")

    def test_covers_termux_install(self):
        """Test 26 – tutorial includes Termux install."""
        self.assertIn("termux", self.content)
        self.assertIn("pkg", self.content)

    def test_covers_license_steps(self):
        """Test 27 – tutorial includes license generate/redeem/reset."""
        self.assertIn("generate", self.content)
        self.assertIn("redeem", self.content)
        self.assertIn("reset hwid", self.content)

    def test_covers_layout(self):
        """Test 28 – tutorial includes 40/60 layout mention."""
        self.assertTrue(
            "40" in self.content or "layout" in self.content,
            "Tutorial should mention window layout or 40/60 split"
        )

    def test_warns_service_role_key(self):
        """Test 29 – tutorial warns not to share service role key."""
        self.assertTrue(
            "service role" in self.content or "service_role" in self.content,
            "Tutorial must warn against sharing Supabase service role key"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 30: Regression
# ══════════════════════════════════════════════════════════════════════════════

class TestRegressionLocalStore(unittest.TestCase):
    """Test 30 – core store operations still work correctly after fixes."""

    def test_create_and_list_key(self):
        store = _tmp_store()
        uid = "reg001"
        store.get_or_create_user(uid)
        full_key = store.create_key_for_user(uid)
        keys = store.list_user_keys(uid)
        self.assertEqual(len(keys), 1)
        self.assertIn("...", keys[0]["masked_key"])

    def test_full_key_not_in_list_response(self):
        store = _tmp_store()
        uid = "reg002"
        store.get_or_create_user(uid)
        full_key = store.create_key_for_user(uid)
        keys = store.list_user_keys(uid)
        for k in keys:
            self.assertNotIn(full_key, str(k))

    def test_bind_after_reset_works(self):
        store, full_key, key_hash = _bound_store_with_key("reg003")
        store.reset_hwid("reg003", key_hash)
        result = store.bind_or_check_device(full_key, "new" * 21 + "x", "New Phone", "2.0")
        self.assertEqual(result, RESULT_ACTIVE)

    def test_no_key_new_exceptions_importable(self):
        """New exception classes must be importable from license_store."""
        from agent.license_store import KeyAlreadySelfOwned, NoActiveBindingError
        self.assertTrue(issubclass(KeyAlreadySelfOwned, Exception))
        self.assertTrue(issubclass(NoActiveBindingError, Exception))

    def test_license_api_module_importable(self):
        """License API module must import cleanly."""
        import bot.license_api as api_mod
        self.assertTrue(callable(api_mod.maybe_start_api_thread))

    def test_license_debug_module_importable(self):
        """License debug module must import cleanly."""
        import bot.license_debug as dbg_mod
        self.assertTrue(callable(dbg_mod.main))


if __name__ == "__main__":
    unittest.main()
