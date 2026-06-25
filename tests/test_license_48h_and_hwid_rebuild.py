"""Rebuild coverage: 48h server-side expiry, non-resetting legacy migration,
HWID 1-key=1-device binding, removed reset/redeem safety, test-license bypass,
and the enriched validation API response.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent.license import hash_license_key, normalize_license_key
from agent.license_store import (
    KEY_LIFETIME_SECONDS,
    LocalJsonLicenseStore,
    RESULT_ACTIVE,
    RESULT_EXPIRED,
    RESULT_REVOKED,
    RESULT_WRONG_DEVICE,
)

KEY = "DENG-AAAA-BBBB-CCCC-DDDD"
HWID_A = "a" * 64
HWID_B = "b" * 64


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_store(tmp_path: Path, **key_fields) -> tuple[LocalJsonLicenseStore, str]:
    store = LocalJsonLicenseStore(tmp_path / "store.json")
    db = store._load()
    kh = hash_license_key(normalize_license_key(KEY))
    record = {"status": "active", "owner_discord_id": "123", "created_at": _iso(_now())}
    record.update(key_fields)
    db["keys"][kh] = record
    store._save(db)
    return store, kh


# ── 48-hour expiry ────────────────────────────────────────────────────────────

def test_new_key_valid_immediately_and_before_48h(tmp_path):
    store, _ = _make_store(tmp_path, expires_at=_iso(_now() + timedelta(seconds=KEY_LIFETIME_SECONDS)))
    details: dict = {}
    assert store.bind_or_check_device(KEY, HWID_A, "m", "v", details=details) == RESULT_ACTIVE
    assert details["expires_at"]
    assert details["server_now"]
    assert details["hwid_bound"] is True


def test_key_invalid_after_48h(tmp_path):
    store, _ = _make_store(tmp_path, expires_at=_iso(_now() - timedelta(minutes=1)))
    assert store.bind_or_check_device(KEY, HWID_A, "m", "v") == RESULT_EXPIRED


def test_expired_blocks_even_with_matching_hwid(tmp_path):
    store, kh = _make_store(tmp_path, expires_at=_iso(_now() - timedelta(minutes=1)))
    # Seed an active binding for HWID_A — expiry must still win.
    db = store._load()
    db["bindings"][kh] = {"install_id_hash": HWID_A, "is_active": True}
    store._save(db)
    details: dict = {}
    assert store.validate_existing_binding(KEY, HWID_A, details=details) == RESULT_EXPIRED
    assert details.get("expired") is True


def test_generated_key_validates_directly_without_redeem(tmp_path):
    # Key has owner set at generation and NO redeemed_at — must bind directly.
    store, _ = _make_store(
        tmp_path,
        expires_at=_iso(_now() + timedelta(seconds=KEY_LIFETIME_SECONDS)),
        redeemed_at=None,
    )
    assert store.bind_or_check_device(KEY, HWID_A, "m", "v") == RESULT_ACTIVE


# ── Non-resetting legacy migration ────────────────────────────────────────────

def test_legacy_key_gets_one_48h_window_that_does_not_reset(tmp_path):
    store, kh = _make_store(tmp_path, expires_at=None)
    assert store.bind_or_check_device(KEY, HWID_A, "m", "v") == RESULT_ACTIVE
    db = store._load()
    first_expiry = db["keys"][kh]["expires_at"]
    assert first_expiry is not None
    assert db["keys"][kh].get("migration_started_at")

    # Re-validate later — expiry must NOT move (non-resetting).
    again = store.validate_existing_binding(KEY, HWID_A)
    assert again == RESULT_ACTIVE
    db2 = store._load()
    assert db2["keys"][kh]["expires_at"] == first_expiry


def test_revoked_legacy_key_not_revived(tmp_path):
    store, kh = _make_store(tmp_path, expires_at=None, status="revoked")
    assert store.bind_or_check_device(KEY, HWID_A, "m", "v") == RESULT_REVOKED
    db = store._load()
    # Revoked key must NOT get an expiry stamp (never revived/migrated).
    assert db["keys"][kh].get("expires_at") in (None, "")
    assert db["keys"][kh].get("migration_started_at") in (None, "")


# ── HWID binding: 1 key = 1 device ────────────────────────────────────────────

def test_hwid_binds_first_then_rejects_second_device(tmp_path):
    store, _ = _make_store(tmp_path, expires_at=_iso(_now() + timedelta(seconds=KEY_LIFETIME_SECONDS)))
    assert store.bind_or_check_device(KEY, HWID_A, "m", "v") == RESULT_ACTIVE
    # Same device may revalidate.
    assert store.validate_existing_binding(KEY, HWID_A) == RESULT_ACTIVE
    # Different device is rejected.
    details: dict = {}
    assert store.validate_existing_binding(KEY, HWID_B, details=details) == RESULT_WRONG_DEVICE
    assert details.get("hwid_match") is False


def test_bind_does_not_clear_or_extend_expiry(tmp_path):
    exp = _iso(_now() + timedelta(hours=10))
    store, kh = _make_store(tmp_path, expires_at=exp)
    store.bind_or_check_device(KEY, HWID_A, "m", "v")
    db = store._load()
    assert db["keys"][kh]["expires_at"] == exp  # unchanged by bind


# ── Test-license bypass (channel-gated) ───────────────────────────────────────

def test_test_bypass_only_active_on_dev_channel(tmp_path, monkeypatch):
    from agent import license as lic

    marker = tmp_path / ".test-license-bypass"
    monkeypatch.setattr(lic, "TEST_BYPASS_MARKER_PATH", marker)

    monkeypatch.setattr(lic, "installed_channel", lambda: "main-dev")
    assert lic.enable_test_license_bypass() is True
    assert marker.exists()
    assert lic.is_test_license_bypass_active() is True

    # Same marker on a stable build → bypass ignored.
    monkeypatch.setattr(lic, "installed_channel", lambda: "stable")
    assert lic.is_test_license_bypass_active() is False
    assert lic.enable_test_license_bypass() is False  # cannot enable on stable

    # Disable removes the marker.
    monkeypatch.setattr(lic, "installed_channel", lambda: "main-dev")
    assert lic.disable_test_license_bypass() is True
    assert not marker.exists()
    assert lic.is_test_license_bypass_active() is False


# ── Enriched validation API response ──────────────────────────────────────────

def test_build_response_includes_server_truth_fields():
    from bot.license_api import _build_response

    payload, status = _build_response(
        "active", details={"expires_at": "2026-01-01T00:00:00+00:00", "hwid_bound": True, "hwid_match": True}
    )
    data = json.loads(payload.decode())
    assert data["valid"] is True
    assert data["expired"] is False
    assert data["reason"] == "active"
    assert data["server_now"]
    assert data["expires_at"] == "2026-01-01T00:00:00+00:00"
    assert data["hwid_match"] is True

    payload2, _ = _build_response("expired")
    d2 = json.loads(payload2.decode())
    assert d2["valid"] is False
    assert d2["expired"] is True
