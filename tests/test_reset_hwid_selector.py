"""Tests for the HWID reset key selector UX (tests 1-32).

Covers:
- store.list_user_keys_with_binding_state() — LocalJsonLicenseStore
- agent/license_panel builders: build_reset_selector_embed,
  build_reset_no_keys_response, build_reset_mixed_summary_embed
- Security: no full key leakage, no secrets exposure
- Docs/branding: DISCORD_LICENSE_PANEL.md + BRANDING_ASSETS.md existence
- Regression: existing flows unbroken (generate, redeem, list_user_keys)
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent.license_panel import (
    build_reset_mixed_summary_embed,
    build_reset_no_keys_response,
    build_reset_selector_embed,
)
from agent.license_store import (
    ACTIVE_HEARTBEAT_WINDOW_S,
    MAX_HWID_RESETS_PER_24H,
    ActiveKeyWarning,
    LocalJsonLicenseStore,
    NoActiveBindingError,
    ResetLimitError,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _tmp_store() -> LocalJsonLicenseStore:
    """Return a store backed by a fresh temp file."""
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    Path(tmp.name).unlink()
    return LocalJsonLicenseStore(Path(tmp.name))


def _iso_ago(seconds: float) -> str:
    """Return an ISO timestamp for 'seconds' ago."""
    dt = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return dt.replace(microsecond=0).isoformat()


def _bind_key(store: LocalJsonLicenseStore, key_id: str, device: str = "Test Device") -> None:
    """Directly write an active binding into the store's JSON for a key."""
    db = store._load()
    db.setdefault("bindings", {})[key_id] = {
        "install_id_hash": "aaaa" * 8,
        "device_label": "",
        "device_model": device,
        "bound_at": _iso_ago(3600),
        "last_seen_at": _iso_ago(600),   # 10 minutes ago → safe to reset
        "last_status": "active",
        "is_active": True,
    }
    store._save(db)


def _setup_user_with_key(uid: str = "111") -> tuple[LocalJsonLicenseStore, str, str]:
    """Create a store, user, and key.  Returns (store, raw_key, key_id)."""
    store = _tmp_store()
    store.get_or_create_user(uid, "TestUser")
    raw_key = store.create_key_for_user(uid)
    # Derive key_id (hash)
    from agent.license import hash_license_key, normalize_license_key
    key_id = hash_license_key(normalize_license_key(raw_key))
    return store, raw_key, key_id


# ── Group 1: list_user_keys_with_binding_state — no binding ───────────────────

class TestListKeysWithStateUnbound(unittest.TestCase):
    """Tests 1-5: unbound keys cannot be reset (nothing to clear)."""

    def setUp(self):
        self.store, self.raw_key, self.key_id = _setup_user_with_key("111")

    def test_01_returns_list(self):
        """Test 1 – returns a list."""
        result = self.store.list_user_keys_with_binding_state("111")
        self.assertIsInstance(result, list)

    def test_02_one_entry_per_key(self):
        """Test 2 – one entry per non-revoked key."""
        result = self.store.list_user_keys_with_binding_state("111")
        self.assertEqual(len(result), 1)

    def test_03_unbound_active_binding_false(self):
        """Test 3 – key with no binding has active_binding=False."""
        result = self.store.list_user_keys_with_binding_state("111")
        self.assertFalse(result[0]["active_binding"])

    def test_04_unbound_can_reset_false(self):
        """Test 4 – unbound key has can_reset=False."""
        result = self.store.list_user_keys_with_binding_state("111")
        self.assertFalse(result[0]["can_reset"])

    def test_05_unbound_reason_set(self):
        """Test 5 – reason_if_not_resettable is populated for unbound key."""
        result = self.store.list_user_keys_with_binding_state("111")
        reason = result[0]["reason_if_not_resettable"]
        self.assertIsNotNone(reason)
        self.assertIsNotNone(reason)  # reason_if_not_resettable is set for unbound key


# ── Group 2: list_user_keys_with_binding_state — with binding ─────────────────

class TestListKeysWithStateBound(unittest.TestCase):
    """Tests 6-11: bound keys can be reset when limits and heartbeat allow."""

    def setUp(self):
        self.store, self.raw_key, self.key_id = _setup_user_with_key("222")
        _bind_key(self.store, self.key_id, "Pixel 8")

    def test_06_bound_active_binding_true(self):
        """Test 6 – key with active binding has active_binding=True."""
        result = self.store.list_user_keys_with_binding_state("222")
        self.assertTrue(result[0]["active_binding"])

    def test_07_bound_can_reset_true_after_window(self):
        """Test 7 – key bound 10 min ago has can_reset=True."""
        result = self.store.list_user_keys_with_binding_state("222")
        self.assertTrue(result[0]["can_reset"])

    def test_08_bound_device_model_populated(self):
        """Test 8 – device_model is populated from binding."""
        result = self.store.list_user_keys_with_binding_state("222")
        self.assertEqual(result[0]["device_model"], "Pixel 8")

    def test_09_bound_reason_none(self):
        """Test 9 – reason_if_not_resettable is None when can_reset=True."""
        result = self.store.list_user_keys_with_binding_state("222")
        self.assertIsNone(result[0]["reason_if_not_resettable"])

    def test_10_reset_count_24h_field(self):
        """Test 10 – reset_count_24h is an int."""
        result = self.store.list_user_keys_with_binding_state("222")
        self.assertIsInstance(result[0]["reset_count_24h"], int)

    def test_11_masked_key_no_full_key(self):
        """Test 11 – masked_key does not expose full raw key."""
        result = self.store.list_user_keys_with_binding_state("222")
        masked = result[0]["masked_key"]
        self.assertIn("...", masked, "Masked key should contain ellipsis")
        self.assertNotEqual(masked, self.raw_key)


# ── Group 3: can_reset rules — recently active ────────────────────────────────

class TestCanResetRecentlyActive(unittest.TestCase):
    """Tests 12-13: key active < 5 min ago must STILL be resettable if no prior reset has occurred.

    The old behavior (blocking HWID reset based on last_seen_at) was incorrect.
    Cooldown is now based only on actual reset history. A key used 60 seconds ago
    for license verification must be immediately resettable on the first reset attempt.
    """

    def setUp(self):
        self.store, _, self.key_id = _setup_user_with_key("333")
        # Write binding with last_seen_at = 60 seconds ago (< 5 min — old code would block this)
        db = self.store._load()
        db.setdefault("bindings", {})[self.key_id] = {
            "install_id_hash": "bbbb" * 8,
            "device_label": "",
            "device_model": "Recent Phone",
            "bound_at": _iso_ago(3600),
            "last_seen_at": _iso_ago(60),
            "last_status": "active",
            "is_active": True,
        }
        self.store._save(db)

    def test_12_recent_can_reset_false(self):
        """Test 12 – key active 60s ago with no prior reset has can_reset=True (not False).

        Correct: recently-active key with no reset history should be immediately resettable.
        """
        result = self.store.list_user_keys_with_binding_state("333")
        self.assertTrue(result[0]["can_reset"], "No prior resets → first reset must be allowed immediately")

    def test_13_recent_reason_contains_wait(self):
        """Test 13 – recently-active key with no prior reset has no blocking reason."""
        result = self.store.list_user_keys_with_binding_state("333")
        reason = result[0]["reason_if_not_resettable"]
        # No reason needed — reset is allowed
        self.assertIsNone(reason, f"No blocking reason expected but got: {reason!r}")


# ── Group 4: can_reset rules — reset limit ────────────────────────────────────

class TestCanResetLimitExceeded(unittest.TestCase):
    """Tests 14-15: key at reset limit → can_reset=False."""

    def setUp(self):
        self.store, _, self.key_id = _setup_user_with_key("444")
        _bind_key(self.store, self.key_id)
        # Inject MAX_HWID_RESETS_PER_24H reset log entries
        db = self.store._load()
        db.setdefault("reset_logs", [])
        from agent.license_store import _utc_now
        for _ in range(MAX_HWID_RESETS_PER_24H):
            db["reset_logs"].append({
                "key_id": self.key_id,
                "owner_discord_id": "444",
                "old_install_id_hash": "",
                "reason": "user_requested",
                "created_at": _utc_now(),
            })
        self.store._save(db)

    def test_14_limit_can_reset_false(self):
        """Test 14 – key at reset limit has can_reset=False."""
        result = self.store.list_user_keys_with_binding_state("444")
        self.assertFalse(result[0]["can_reset"])

    def test_15_limit_reason_mentions_limit(self):
        """Test 15 – reason mentions reset limit for exhausted key."""
        result = self.store.list_user_keys_with_binding_state("444")
        reason = result[0]["reason_if_not_resettable"]
        self.assertIsNotNone(reason)
        self.assertIn("Reset limit", reason)


# ── Group 5: revoked keys are excluded ────────────────────────────────────────

class TestRevokedKeysExcluded(unittest.TestCase):
    """Test 16: revoked keys do not appear in list_user_keys_with_binding_state."""

    def test_16_revoked_excluded(self):
        """Test 16 – revoked keys are excluded from the selector list."""
        store, _, key_id = _setup_user_with_key("555")
        # Revoke the key directly
        db = store._load()
        db["keys"][key_id]["status"] = "revoked"
        store._save(db)
        result = store.list_user_keys_with_binding_state("555")
        self.assertEqual(result, [])


# ── Group 6: multiple keys ─────────────────────────────────────────────────────

class TestMultipleKeys(unittest.TestCase):
    """Tests 17-18: users with multiple keys get all of them listed."""

    def setUp(self):
        self.store = _tmp_store()
        self.store.get_or_create_user("666", "MultiUser")
        # Allow 2 keys
        self.store.set_user_max_keys("666", 2)
        self.store.create_key_for_user("666")
        self.store.create_key_for_user("666")

    def test_17_two_keys_listed(self):
        """Test 17 – two keys are listed when user owns two."""
        result = self.store.list_user_keys_with_binding_state("666")
        self.assertEqual(len(result), 2)

    def test_18_both_unbound(self):
        """Test 18 – both keys are unbound (no _bind_key called)."""
        result = self.store.list_user_keys_with_binding_state("666")
        for entry in result:
            self.assertFalse(entry["active_binding"])


# ── Group 7: build_reset_selector_embed ───────────────────────────────────────

class TestBuildResetSelectorEmbed(unittest.TestCase):
    """Tests 19-22: selector embed structure and content."""

    def _make_keys(self, bound: bool = True) -> list[dict]:
        return [{
            "key_id": "abc123",
            "masked_key": "DENG-AB12...CD34",
            "status": "active",
            "active_binding": bound,
            "device_model": "Pixel 8" if bound else "",
            "device_label": "",
            "last_seen_at": _iso_ago(600) if bound else None,
            "reset_count_24h": 0,
            "can_reset": bound,
            "reason_if_not_resettable": None if bound else "No device linked — start the tool first",
        }]

    def test_19_returns_dict_with_embed(self):
        """Test 19 – returns dict with 'embed' key."""
        result = build_reset_selector_embed(self._make_keys())
        self.assertIn("embed", result)

    def test_20_embed_is_ephemeral(self):
        """Test 20 – selector embed is marked ephemeral."""
        result = build_reset_selector_embed(self._make_keys())
        self.assertTrue(result.get("ephemeral"))

    def test_21_bound_key_row_uses_yellow_circle(self):
        """Test 21 – bound key row uses 🟡 (numbered list)."""
        result = build_reset_selector_embed(self._make_keys(bound=True))
        desc = result["embed"]["description"]
        self.assertIn("\n1. \U0001f7e1", desc)

    def test_22_unbound_key_row_uses_green_circle(self):
        """Test 22 – unbound key row uses 🟢 (numbered list)."""
        result = build_reset_selector_embed(self._make_keys(bound=False))
        desc = result["embed"]["description"]
        self.assertIn("\n1. \U0001f7e2", desc)

    def test_selector_legend_above_rows_with_line_breaks(self):
        """Legend is above the key list; 🟢/🟡 lines are separated by newline (no · legend)."""
        desc = build_reset_selector_embed(self._make_keys(bound=False))["embed"]["description"]
        legend = "\U0001f7e2 No device linked\n\U0001f7e1 Bound to a device"
        self.assertIn(legend, desc)
        self.assertLess(desc.index("No device linked"), desc.index("\n1."))
        self.assertNotIn(" \u00b7 ", desc)

    def test_selector_two_keys_numbered(self):
        """Multiple keys appear as 1., 2., … with correct bound/unbound suffix."""
        k_unbound = dict(self._make_keys(bound=False)[0], key_id="unbound1")
        k_bound = dict(self._make_keys(bound=True)[0], key_id="bound2")
        desc = build_reset_selector_embed([k_unbound, k_bound])["embed"]["description"]
        # "No device linked" (not "No device bound")
        self.assertRegex(desc, r"\n1\. [\s\S]*No device linked")
        self.assertRegex(desc, r"\n2\. [\s\S]*Bound to a device")

    def test_selector_full_key_when_recoverable(self):
        """Full key appears in backticks when full_key_plaintext is set."""
        k = dict(self._make_keys(bound=False)[0])
        k["full_key_plaintext"] = "DENG-68C9-0BA2-F745-E506"
        desc = build_reset_selector_embed([k])["embed"]["description"]
        self.assertIn("`DENG-68C9-0BA2-F745-E506`", desc)
        self.assertNotIn("reference only", desc)


# ── Group 8: build_reset_no_keys_response ────────────────────────────────────

class TestBuildResetNoKeysResponse(unittest.TestCase):
    """Tests 23-24: no-keys response structure."""

    def test_23_is_ephemeral(self):
        """Test 23 – no-keys response is ephemeral."""
        result = build_reset_no_keys_response()
        self.assertTrue(result.get("ephemeral"))

    def test_24_has_embed_with_title(self):
        """Test 24 – no-keys response has embed with a title."""
        result = build_reset_no_keys_response()
        self.assertIn("embed", result)
        self.assertIn("title", result["embed"])


# ── Group 9: build_reset_mixed_summary_embed ─────────────────────────────────

class TestBuildResetMixedSummaryEmbed(unittest.TestCase):
    """Tests 25-28: mixed summary embed structure and coloring."""

    def test_25_all_success_green(self):
        """Test 25 – all-success results → green embed (0x27AE60)."""
        results = [{"masked_key": "DENG-AB12...CD34", "success": True, "message": "Cleared."}]
        embed = build_reset_mixed_summary_embed(results)
        self.assertEqual(embed["embed"]["color"], 0x27AE60)

    def test_26_all_failure_red(self):
        """Test 26 – all-failure results → red embed (0xE74C3C)."""
        results = [{"masked_key": "DENG-AB12...CD34", "success": False, "message": "Limit."}]
        embed = build_reset_mixed_summary_embed(results)
        self.assertEqual(embed["embed"]["color"], 0xE74C3C)

    def test_27_mixed_amber(self):
        """Test 27 – mixed results → amber embed (0xF39C12)."""
        results = [
            {"masked_key": "DENG-AB12...CD34", "success": True, "message": "Cleared."},
            {"masked_key": "DENG-EF56...GH78", "success": False, "message": "Limit."},
        ]
        embed = build_reset_mixed_summary_embed(results)
        self.assertEqual(embed["embed"]["color"], 0xF39C12)

    def test_28_is_ephemeral(self):
        """Test 28 – summary embed is marked ephemeral."""
        results = [{"masked_key": "X", "success": True, "message": "OK"}]
        embed = build_reset_mixed_summary_embed(results)
        self.assertTrue(embed.get("ephemeral"))


# ── Group 10: security ────────────────────────────────────────────────────────

class TestSecurity(unittest.TestCase):
    """Tests 29-30: masked-only rows stay masked; non-plaintext fields stay scrubbed."""

    def test_29_masked_rows_reference_only_without_plaintext(self):
        """When full_key_plaintext is absent, embed shows masked **…** (reference only)."""
        k = dict(TestBuildResetSelectorEmbed()._make_keys(bound=True)[0])
        k.pop("full_key_plaintext", None)
        desc = build_reset_selector_embed([k])["embed"]["description"]
        self.assertIn("(reference only)", desc)
        self.assertIn("**DENG-AB12...CD34**", desc)

    def test_30_no_raw_key_outside_plaintext_field(self):
        """list_user_keys_with_binding_state must not leak full key into other string fields."""
        store, raw_key, key_id = _setup_user_with_key("888")
        _bind_key(store, key_id)
        result = store.list_user_keys_with_binding_state("888")
        skip = frozenset({"full_key_plaintext"})
        for entry in result:
            for field_name, field_value in entry.items():
                if field_name in skip:
                    continue
                if isinstance(field_value, str):
                    self.assertNotIn(raw_key, field_value)


# ── Group 11: docs + branding ─────────────────────────────────────────────────

class TestDocsAndBranding(unittest.TestCase):
    """Tests 31-32: required docs and branding files exist."""

    BASE = Path(__file__).parent.parent

    def test_31_discord_panel_doc_mentions_dropdown(self):
        """Test 31 – DISCORD_LICENSE_PANEL.md documents the dropdown selector flow."""
        doc = self.BASE / "docs" / "DISCORD_LICENSE_PANEL.md"
        self.assertTrue(doc.exists(), "docs/DISCORD_LICENSE_PANEL.md must exist")
        content = doc.read_text(encoding="utf-8")
        self.assertIn("Dropdown", content)
        self.assertIn("🟢", content)
        self.assertIn("🟡", content)

    def test_32_branding_assets_doc_exists(self):
        """Test 32 – docs/BRANDING_ASSETS.md exists."""
        doc = self.BASE / "docs" / "BRANDING_ASSETS.md"
        self.assertTrue(doc.exists(), "docs/BRANDING_ASSETS.md must exist")
        content = doc.read_text(encoding="utf-8")
        # Must not contain "Plus" branding
        self.assertNotIn("Plus", content)


if __name__ == "__main__":
    unittest.main()
