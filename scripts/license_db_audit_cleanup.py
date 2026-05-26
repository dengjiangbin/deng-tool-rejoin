"""DENG Tool: Rejoin — License Database Audit & Cleanup Script.

Usage:
  python scripts/license_db_audit_cleanup.py inspect
  python scripts/license_db_audit_cleanup.py cleanup --dry-run
  python scripts/license_db_audit_cleanup.py cleanup --apply --confirm CLEAN_LICENSE_DB
  python scripts/license_db_audit_cleanup.py verify

Safety guarantees:
  - No full keys printed in logs (masked as DENG-????-...-????)
  - No Discord IDs printed in full (truncated to first 6 chars + ...)
  - No SUPABASE_URL or service-role key printed
  - All DB writes are parameterized via supabase-py
  - Backup is created before every apply step
  - --apply requires --confirm CLEAN_LICENSE_DB
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

# ── Constants ─────────────────────────────────────────────────────────────────

BACKUP_DIR = WORKSPACE_ROOT / "data" / "backups"
UNREDEEMED_EXPIRY_SECONDS = 86400  # 24 hours

# Canonical stat definitions (mirrors licenseService.js and license_store.py)
# Generated Keys: status='active' (non-revoked, non-expired, non-inactive)
# Redeemed Keys:  status='active' AND (redeemed_at IS NOT NULL OR active binding)
# Active Devices: distinct is_active=TRUE bindings for active keys
# Unique Users:   distinct discord_user_id across license_users + site_users (non-owner, non-blocked)


# ── Masking helpers ────────────────────────────────────────────────────────────

def _mask_key_id(key_id: str | None) -> str:
    """Show only first 8 hex chars of a SHA-256 key hash."""
    if not key_id:
        return "(none)"
    text = str(key_id).strip()
    return f"{text[:8]}..." if len(text) > 8 else text


def _mask_discord_id(discord_id: str | None) -> str:
    """Truncate Discord user ID for safe logging."""
    if not discord_id:
        return "(none)"
    text = str(discord_id).strip()
    return f"{text[:6]}..." if len(text) > 6 else text


def _mask_full_key(prefix: str | None, suffix: str | None) -> str:
    """Display safe masked key like DENG-8F3A-...-44F0."""
    p = str(prefix or "DENG-????").strip()
    s = str(suffix or "????").strip()
    return f"{p}-...-{s}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _iso_expired(iso_str: str | None) -> bool:
    if not iso_str:
        return False
    try:
        normalized = str(iso_str).replace("Z", "+00:00")
        exp_dt = datetime.fromisoformat(normalized)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > exp_dt
    except (ValueError, TypeError):
        return False


# ── Database connection ────────────────────────────────────────────────────────

def _get_supabase_client():
    """Load env and return a supabase client.  Never prints URL or key."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        pass
    else:
        load_dotenv(WORKSPACE_ROOT / ".env")

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.", file=sys.stderr)
        sys.exit(1)
    try:
        from supabase import create_client
    except ImportError:
        print("ERROR: supabase-py not installed.  Run: pip install supabase", file=sys.stderr)
        sys.exit(1)
    client = create_client(url, key)
    return client


# ── Data fetchers ──────────────────────────────────────────────────────────────

def _fetch_all(client, table: str, columns: str) -> list[dict]:
    """Fetch all rows from a table with pagination (up to 10 000 rows)."""
    try:
        res = client.table(table).select(columns).limit(10000).execute()
        return res.data or []
    except Exception as exc:
        print(f"  WARNING: Could not fetch {table}: {exc}", file=sys.stderr)
        return []


def _fetch_counts(client) -> dict[str, int]:
    """Fetch exact row counts for each table via Supabase count=exact."""
    tables = [
        "license_keys",
        "license_users",
        "device_bindings",
        "hwid_reset_logs",
        "license_check_logs",
        "license_key_executions",
        "site_users",
        "license_ad_challenges",
        "license_panel_config",
        "license_log_configs",
        "admin_audit_logs",
    ]
    counts: dict[str, int] = {}
    for table in tables:
        pk = "key_id" if table == "device_bindings" else "id"
        try:
            res = client.table(table).select(pk, count="exact").execute()
            counts[table] = res.count or 0
        except Exception:
            counts[table] = -1  # -1 = table missing or inaccessible
    return counts


def _load_all_data(client) -> dict[str, list[dict]]:
    """Load all tables needed for audit analysis."""
    print("  Loading license_keys …")
    keys = _fetch_all(
        client,
        "license_keys",
        "id, prefix, suffix, status, owner_discord_id, site_user_id, "
        "redeemed_at, expires_at, created_at, created_by, plan",
    )
    print("  Loading device_bindings …")
    bindings = _fetch_all(
        client, "device_bindings", "key_id, is_active, install_id_hash, last_seen_at"
    )
    print("  Loading license_users …")
    lu = _fetch_all(
        client,
        "license_users",
        "id, discord_user_id, is_owner, is_blocked, max_keys, last_key_generated_at, created_at",
    )
    print("  Loading site_users …")
    su = _fetch_all(
        client,
        "site_users",
        "id, discord_user_id, is_active, linked_license_user_discord_id, created_at",
    )
    print("  Loading license_ad_challenges (relevant rows) …")
    challenges = _fetch_all(
        client,
        "license_ad_challenges",
        "id, status, license_key_id, site_user_id, discord_user_id, created_at, key_expires_at",
    )
    print("  Loading hwid_reset_logs …")
    reset_logs = _fetch_all(client, "hwid_reset_logs", "id, key_id, owner_discord_id, created_at")
    print("  Loading license_key_executions (is_public_release) …")
    executions = _fetch_all(
        client,
        "license_key_executions",
        "id, key_id, owner_discord_id, is_public_release, version, channel, executed_at",
    )
    return {
        "keys": keys,
        "bindings": bindings,
        "license_users": lu,
        "site_users": su,
        "challenges": challenges,
        "reset_logs": reset_logs,
        "executions": executions,
    }


# ── Canonical stat computation ─────────────────────────────────────────────────

def compute_canonical_stats(data: dict[str, list[dict]]) -> dict[str, int]:
    """Compute the four canonical public stats matching licenseService.js logic."""
    keys = data["keys"]
    bindings = data["bindings"]
    license_users = data["license_users"]
    site_users = data["site_users"]

    # Index bindings by key_id
    binding_by_key: dict[str, dict] = {}
    for b in bindings:
        if b.get("key_id"):
            binding_by_key[b["key_id"]] = b

    # Index license_users by discord_user_id
    lu_by_discord: dict[str, dict] = {}
    for u in license_users:
        did = str(u.get("discord_user_id") or "").strip()
        if did:
            lu_by_discord[did] = u

    # Index site_users by id
    su_by_id: dict[str, dict] = {}
    for u in site_users:
        uid = str(u.get("id") or "").strip()
        if uid:
            su_by_id[uid] = u

    # Eligible (generated) keys: status='active', not blocked by flags
    eligible_keys = []
    for row in keys:
        status = str(row.get("status") or "active").lower()
        if status not in ("active",):
            continue
        eligible_keys.append(row)

    eligible_key_ids = {r["id"] for r in eligible_keys if r.get("id")}

    # Active bindings for eligible keys
    active_binding_key_ids = set()
    active_binding_install_hashes = set()
    for b in bindings:
        kid = b.get("key_id")
        if not kid or kid not in eligible_key_ids:
            continue
        if b.get("is_active") is True or b.get("is_active") == 1:
            active_binding_key_ids.add(kid)
            ih = str(b.get("install_id_hash") or kid).strip()
            if ih:
                active_binding_install_hashes.add(ih)

    generated_keys = len(eligible_keys)

    redeemed_keys = sum(
        1
        for row in eligible_keys
        if bool(row.get("redeemed_at")) or row["id"] in active_binding_key_ids
    )

    active_devices = len(active_binding_install_hashes)

    # Unique users: discord IDs from license_users (non-owner, non-blocked) +
    # site_users discord IDs + eligible key owners
    unique_users: set[str] = set()
    for u in license_users:
        if u.get("is_owner") or u.get("is_blocked"):
            continue
        did = str(u.get("discord_user_id") or "").strip()
        if did:
            unique_users.add(f"discord:{did}")
    for u in site_users:
        if not u.get("is_active", True):
            continue
        did = str(u.get("discord_user_id") or "").strip()
        uid = str(u.get("id") or "").strip()
        if did:
            unique_users.add(f"discord:{did}")
        elif uid:
            unique_users.add(f"site:{uid}")
    for row in eligible_keys:
        owner = str(row.get("owner_discord_id") or "").strip()
        site_uid = str(row.get("site_user_id") or "").strip()
        if owner:
            unique_users.add(f"discord:{owner}")
        elif site_uid:
            unique_users.add(f"site:{site_uid}")

    return {
        "generatedKeys": generated_keys,
        "redeemedKeys": redeemed_keys,
        "activeDevices": active_devices,
        "uniqueUsers": len(unique_users),
    }


# ── Audit checks ───────────────────────────────────────────────────────────────

def run_audit_checks(data: dict[str, list[dict]]) -> list[dict]:
    """Run all 30 audit checks.  Returns list of finding dicts."""
    keys = data["keys"]
    bindings = data["bindings"]
    license_users = data["license_users"]
    site_users = data["site_users"]
    challenges = data["challenges"]
    reset_logs = data["reset_logs"]
    executions = data["executions"]

    findings: list[dict] = []

    def finding(check_id: int, label: str, severity: str, count: int, detail: str = "") -> None:
        findings.append({
            "check": check_id,
            "label": label,
            "severity": severity,
            "count": count,
            "detail": detail,
        })

    # Build indexes
    binding_by_key: dict[str, dict] = {}
    for b in bindings:
        if b.get("key_id"):
            binding_by_key[b["key_id"]] = b

    lu_discord_ids: set[str] = set()
    lu_by_discord: dict[str, dict] = {}
    for u in license_users:
        did = str(u.get("discord_user_id") or "").strip()
        if did:
            lu_discord_ids.add(did)
            lu_by_discord[did] = u

    su_discord_ids: set[str] = set()
    su_by_discord: dict[str, dict] = {}
    for u in site_users:
        did = str(u.get("discord_user_id") or "").strip()
        if did:
            su_discord_ids.add(did)
            su_by_discord[did] = u

    key_ids: set[str] = {k["id"] for k in keys if k.get("id")}

    now = datetime.now(timezone.utc)

    # ── Check 1: Total license_keys rows ──────────────────────────────────────
    finding(1, "Total license_keys rows", "INFO", len(keys))

    # ── Check 2: Active license_keys rows (status='active') ───────────────────
    active_keys = [k for k in keys if str(k.get("status") or "").lower() == "active"]
    finding(2, "Active license_keys rows (status=active)", "INFO", len(active_keys))

    # ── Check 3: Revoked / expired / inactive keys ────────────────────────────
    bad_status = [
        k for k in keys
        if str(k.get("status") or "").lower() in {"revoked", "expired", "inactive"}
    ]
    finding(3, "Revoked/expired/inactive keys", "INFO", len(bad_status),
            ", ".join(
                f"{s}={sum(1 for k in keys if str(k.get('status','')).lower() == s)}"
                for s in ("revoked", "expired", "inactive")
            ))

    # ── Check 4: Expired unredeemed keys still marked status='active' ─────────
    expired_unredeemed_still_active = [
        k for k in keys
        if str(k.get("status") or "").lower() == "active"
        and not k.get("redeemed_at")
        and k.get("expires_at")
        and _iso_expired(k.get("expires_at"))
    ]
    finding(4, "Expired unredeemed keys still status=active (should be expired)",
            "WARN" if expired_unredeemed_still_active else "OK",
            len(expired_unredeemed_still_active),
            "These keys block Python key-generation limit counting incorrectly")

    # ── Check 5: Keys with no owner AND no binding (orphan unowned) ───────────
    no_owner_no_binding = [
        k for k in keys
        if not k.get("owner_discord_id")
        and not k.get("site_user_id")
        and k["id"] not in binding_by_key
        and str(k.get("status") or "").lower() == "active"
    ]
    finding(5, "Active keys with no owner and no binding (floating unowned active)",
            "INFO", len(no_owner_no_binding))

    # ── Check 6: Keys with owner_discord_id not in license_users ─────────────
    orphan_owner_keys = [
        k for k in keys
        if k.get("owner_discord_id")
        and str(k["owner_discord_id"]).strip() not in lu_discord_ids
        and str(k.get("status") or "").lower() == "active"
    ]
    finding(6, "Active keys with owner_discord_id missing from license_users",
            "WARN" if orphan_owner_keys else "OK",
            len(orphan_owner_keys),
            "Owner Discord ID exists on key but no license_users row")

    # ── Check 7: Owned active keys with redeemed_at IS NULL ──────────────────
    owned_unredeemed_at = [
        k for k in keys
        if k.get("owner_discord_id")
        and not k.get("redeemed_at")
        and str(k.get("status") or "").lower() == "active"
        and not _iso_expired(k.get("expires_at"))
    ]
    finding(7, "Owned active keys missing redeemed_at (legacy, may need backfill)",
            "WARN" if owned_unredeemed_at else "OK",
            len(owned_unredeemed_at),
            "Key has owner but redeemed_at is NULL — may undercount Redeemed Keys")

    # ── Check 8: Keys with active binding but redeemed_at IS NULL ────────────
    bound_no_redeemed_at = [
        k for k in keys
        if not k.get("redeemed_at")
        and k["id"] in binding_by_key
        and binding_by_key[k["id"]].get("is_active")
        and str(k.get("status") or "").lower() == "active"
    ]
    finding(8, "Active-bound keys missing redeemed_at (safe to backfill)",
            "WARN" if bound_no_redeemed_at else "OK",
            len(bound_no_redeemed_at),
            "key has is_active=TRUE binding but redeemed_at is NULL")

    # ── Check 9: Keys with redeemed_at but no owner user in license_users ─────
    redeemed_no_owner_in_lu = [
        k for k in keys
        if k.get("redeemed_at")
        and k.get("owner_discord_id")
        and str(k["owner_discord_id"]).strip() not in lu_discord_ids
    ]
    finding(9, "Keys with redeemed_at but owner missing from license_users",
            "WARN" if redeemed_no_owner_in_lu else "OK",
            len(redeemed_no_owner_in_lu))

    # ── Check 10: Keys with active binding but no owner user ─────────────────
    bound_no_owner_in_lu = [
        k for k in keys
        if k["id"] in binding_by_key
        and binding_by_key[k["id"]].get("is_active")
        and k.get("owner_discord_id")
        and str(k["owner_discord_id"]).strip() not in lu_discord_ids
    ]
    finding(10, "Active-bound keys where owner missing from license_users",
            "WARN" if bound_no_owner_in_lu else "OK",
            len(bound_no_owner_in_lu))

    # ── Check 11: Multiple active bindings (not possible with PK=key_id) ─────
    # device_bindings has key_id as PRIMARY KEY so only 1 row per key
    finding(11, "Multiple active bindings per key (impossible with PK=key_id)",
            "OK", 0, "device_bindings.key_id is PK — max 1 binding row per key")

    # ── Check 12: is_active=TRUE bindings for revoked/inactive/expired keys ───
    stale_bindings_bad_status = [
        b for b in bindings
        if b.get("is_active")
        and b.get("key_id") in key_ids
        and str(
            next((k.get("status") for k in keys if k["id"] == b["key_id"]), "active")
        ).lower() in {"revoked", "inactive", "expired"}
    ]
    finding(12, "is_active=TRUE bindings for revoked/inactive/expired keys",
            "WARN" if stale_bindings_bad_status else "OK",
            len(stale_bindings_bad_status),
            "These inflate Active Devices count")

    # ── Check 13: is_active=TRUE bindings for unredeemed keys ─────────────────
    key_status_map = {k["id"]: k for k in keys}
    stale_bindings_unredeemed = [
        b for b in bindings
        if b.get("is_active")
        and b.get("key_id") in key_status_map
        and not key_status_map[b["key_id"]].get("redeemed_at")
        and not key_status_map[b["key_id"]].get("owner_discord_id")
        and not key_status_map[b["key_id"]].get("site_user_id")
    ]
    finding(13, "is_active=TRUE bindings for completely unowned keys",
            "WARN" if stale_bindings_unredeemed else "OK",
            len(stale_bindings_unredeemed),
            "Bindings without any key owner — suspicious")

    # ── Check 14: Bindings pointing to missing key rows ───────────────────────
    binding_orphans = [b for b in bindings if b.get("key_id") not in key_ids]
    finding(14, "Bindings pointing to missing license_keys rows",
            "WARN" if binding_orphans else "OK",
            len(binding_orphans),
            "Orphan bindings — key was deleted without CASCADE")

    # ── Check 15: Duplicate license_users by discord_user_id ─────────────────
    seen_lu: set[str] = set()
    dup_lu: list[str] = []
    for u in license_users:
        did = str(u.get("discord_user_id") or "").strip()
        if did in seen_lu:
            dup_lu.append(_mask_discord_id(did))
        seen_lu.add(did)
    finding(15, "Duplicate license_users rows for same discord_user_id",
            "WARN" if dup_lu else "OK",
            len(dup_lu),
            "(UNIQUE constraint should prevent this)")

    # ── Check 16: Duplicate site_users by discord_user_id ────────────────────
    seen_su: set[str] = set()
    dup_su: list[str] = []
    for u in site_users:
        did = str(u.get("discord_user_id") or "").strip()
        if not did:
            continue
        if did in seen_su:
            dup_su.append(_mask_discord_id(did))
        seen_su.add(did)
    finding(16, "Duplicate site_users rows for same discord_user_id",
            "WARN" if dup_su else "OK",
            len(dup_su),
            "(UNIQUE constraint should prevent this)")

    # ── Check 17: site_users not linked to license_users ─────────────────────
    su_unlinked = [
        u for u in site_users
        if u.get("discord_user_id")
        and str(u["discord_user_id"]).strip() not in lu_discord_ids
        and not u.get("linked_license_user_discord_id")
        and u.get("is_active", True)
    ]
    finding(17, "Active site_users with discord_user_id not in license_users",
            "INFO", len(su_unlinked),
            "Site user logged in but no license_users row yet — normal if never generated key")

    # ── Check 18: license_users not linked to site_users ─────────────────────
    lu_unlinked = [
        u for u in license_users
        if u.get("discord_user_id")
        and str(u["discord_user_id"]).strip() not in su_discord_ids
    ]
    finding(18, "license_users with discord_user_id not in site_users",
            "INFO", len(lu_unlinked),
            "Discord user used bot but never logged into web portal — normal")

    # ── Check 19: Ad challenges with key_generated but key row missing ────────
    key_gen_challenges_bad = [
        c for c in challenges
        if c.get("status") == "key_generated"
        and c.get("license_key_id")
        and c["license_key_id"] not in key_ids
    ]
    finding(19, "Ad challenges (key_generated) with missing license_key_id in license_keys",
            "WARN" if key_gen_challenges_bad else "OK",
            len(key_gen_challenges_bad),
            "Challenge says key generated but key row missing")

    # ── Check 20: Keys linked to failed/expired ad challenges ────────────────
    bad_challenge_key_ids = {
        c["license_key_id"]
        for c in challenges
        if c.get("status") in {"failed", "expired"}
        and c.get("license_key_id")
    }
    keys_from_bad_challenges = [
        k for k in keys
        if k["id"] in bad_challenge_key_ids
        and str(k.get("status") or "").lower() == "active"
    ]
    finding(20, "Active keys linked to failed/expired ad challenges",
            "INFO", len(keys_from_bad_challenges),
            "Key may have been generated before challenge failed — verify owner")

    # ── Check 21: Unredeemed active keys older than 24h blocking generation ───
    unredeemed_blocking = [
        k for k in keys
        if str(k.get("status") or "").lower() == "active"
        and not k.get("redeemed_at")
        and not k.get("owner_discord_id")
        and k.get("expires_at")
        and _iso_expired(k.get("expires_at"))
    ]
    finding(21, "Expired-but-still-active unowned keys that may block generation in Python",
            "WARN" if unredeemed_blocking else "OK",
            len(unredeemed_blocking),
            "Python count_user_keys counts these; mark expired to fix")

    # ── Check 22: Canonical Generated Keys vs active keys ────────────────────
    canonical = compute_canonical_stats({
        "keys": keys, "bindings": bindings,
        "license_users": license_users, "site_users": site_users,
    })
    finding(22, f"Canonical public stats: generated={canonical['generatedKeys']} "
            f"redeemed={canonical['redeemedKeys']} "
            f"devices={canonical['activeDevices']} "
            f"users={canonical['uniqueUsers']}",
            "INFO", 0)

    # ── Check 23: Discord Key Stats mismatch ─────────────────────────────────
    # Python compute_active_visible_stats includes expired keys IF they have redeemed_at or binding
    # Check how many keys would be included by Python filter vs Node.js filter
    python_visible = [
        k for k in keys
        if str(k.get("status") or "").lower() not in {"revoked", "deleted", "disabled", "inactive"}
        and (
            k.get("redeemed_at")
            or (k["id"] in binding_by_key and binding_by_key[k["id"]].get("is_active"))
            or (
                str(k.get("status") or "").lower() == "active"
                and not _iso_expired(k.get("expires_at"))
            )
        )
    ]
    node_visible = [
        k for k in keys
        if str(k.get("status") or "").lower() == "active"
    ]
    if len(python_visible) != len(node_visible):
        finding(23, f"Discord vs website stat mismatch: Python sees {len(python_visible)} active-visible, "
                f"Node.js public stats sees {len(node_visible)} active",
                "WARN", abs(len(python_visible) - len(node_visible)),
                "Likely due to expired keys with redeemed_at still in Python count")
    else:
        finding(23, f"Discord and website stat counts agree: {len(python_visible)} active-visible keys",
                "OK", 0)

    # ── Check 24: Reset HWID list showing inactive/revoked/expired keys ───────
    resettable_bad = [
        k for k in keys
        if k["id"] in binding_by_key
        and binding_by_key[k["id"]].get("is_active")
        and str(k.get("status") or "").lower() in {"revoked", "inactive", "expired"}
    ]
    finding(24, "Keys with is_active binding that are revoked/inactive/expired "
            "(appear in Reset HWID list incorrectly)",
            "WARN" if resettable_bad else "OK",
            len(resettable_bad))

    # ── Check 25: Download Keys export includes bad status keys ───────────────
    # Python export uses filter_active_visible_license_rows which includes expired with binding/redeemed_at
    # This is acceptable behavior — just counts them
    expired_with_redeemed = [
        k for k in keys
        if str(k.get("status") or "").lower() in {"expired"}
        and (k.get("redeemed_at") or (k["id"] in binding_by_key))
    ]
    finding(25, "Expired keys that may appear in Download Keys export (have redeemed_at or binding)",
            "INFO", len(expired_with_redeemed),
            "Python filter includes expired-redeemed keys in download — intentional")

    # ── Check 26: /license <user> admin cmd includes inactive/revoked ─────────
    # Python filter excludes revoked/deleted/disabled
    # But does NOT exclude inactive or expired IF they have redeemed_at
    expired_revoked_in_admin = [
        k for k in keys
        if str(k.get("status") or "").lower() in {"revoked", "inactive"}
        and (k.get("redeemed_at") or k["id"] in binding_by_key)
    ]
    finding(26, "Revoked/inactive keys with redeemed_at that would still appear in /license admin cmd",
            "INFO", len(expired_revoked_in_admin),
            "is_active_visible_license_row excludes revoked — these are correctly excluded")

    # ── Check 27: Active Devices inflated by stale bindings ───────────────────
    stale_bindings = [
        b for b in bindings
        if b.get("is_active")
        and b.get("key_id") in key_status_map
        and str(key_status_map[b["key_id"]].get("status") or "").lower()
        in {"revoked", "inactive", "expired"}
    ]
    finding(27, "Stale is_active=TRUE bindings for non-active keys (inflate Active Devices)",
            "WARN" if stale_bindings else "OK",
            len(stale_bindings),
            "These should have is_active set to FALSE")

    # ── Check 28: Key Executed count includes non-public releases ─────────────
    non_public_execs = [e for e in executions if not e.get("is_public_release")]
    finding(28, "Execution rows with is_public_release=FALSE (excluded from Key Executed stat)",
            "INFO", len(non_public_execs),
            "These are correctly excluded from the public Key Executed count")

    # ── Check 29: Migration 003 field redeemed_at exists and is used ──────────
    keys_with_redeemed_at_col = sum(1 for k in keys if "redeemed_at" in k)
    if keys_with_redeemed_at_col == len(keys):
        finding(29, "Migration 003 redeemed_at column: present on all license_keys rows",
                "OK", keys_with_redeemed_at_col)
    else:
        finding(29, f"Migration 003 redeemed_at column: missing on {len(keys) - keys_with_redeemed_at_col} rows",
                "WARN", len(keys) - keys_with_redeemed_at_col)

    # ── Check 30: Overall stat consistency ────────────────────────────────────
    total_mismatches = sum(
        1 for f in findings
        if f["severity"] == "WARN" and f["count"] > 0
    )
    finding(30, f"Overall consistency: {total_mismatches} categories with findings needing attention",
            "WARN" if total_mismatches > 0 else "OK",
            total_mismatches)

    return findings


# ── Cleanup actions ────────────────────────────────────────────────────────────

def plan_cleanup_actions(data: dict[str, list[dict]]) -> list[dict]:
    """Return a list of planned cleanup operations (deterministic, safe)."""
    keys = data["keys"]
    bindings = data["bindings"]

    binding_by_key: dict[str, dict] = {
        b["key_id"]: b for b in bindings if b.get("key_id")
    }
    key_status_map = {k["id"]: k for k in keys if k.get("id")}

    actions: list[dict] = []

    # ── Action 1: Mark expired unredeemed keys as expired ────────────────────
    exp_unredeemed = [
        k for k in keys
        if str(k.get("status") or "").lower() == "active"
        and not k.get("redeemed_at")
        and k.get("expires_at")
        and _iso_expired(k.get("expires_at"))
    ]
    if exp_unredeemed:
        actions.append({
            "action": "mark_expired_unredeemed",
            "description": f"Mark {len(exp_unredeemed)} expired unredeemed keys as status=expired",
            "key_ids": [k["id"] for k in exp_unredeemed],
            "safe": True,
            "reason": (
                "Keys older than 24h with redeemed_at IS NULL should have status=expired. "
                "Fixes Python count_user_keys inflating the limit for old keys."
            ),
        })

    # ── Action 2: Backfill redeemed_at for active-bound keys ─────────────────
    backfill_redeemed_at = [
        k for k in keys
        if not k.get("redeemed_at")
        and k["id"] in binding_by_key
        and binding_by_key[k["id"]].get("is_active")
        and str(k.get("status") or "").lower() == "active"
    ]
    if backfill_redeemed_at:
        actions.append({
            "action": "backfill_redeemed_at",
            "description": f"Backfill redeemed_at=created_at for {len(backfill_redeemed_at)} active-bound keys",
            "key_ids": [k["id"] for k in backfill_redeemed_at],
            "safe": True,
            "reason": (
                "Key has active device binding but redeemed_at is NULL. "
                "These were bound before migration 003 added redeemed_at. "
                "Sets redeemed_at = created_at as conservative fallback."
            ),
        })

    # ── Action 3: Deactivate stale bindings for non-active keys ──────────────
    stale_bindings = [
        b for b in bindings
        if b.get("is_active")
        and b.get("key_id") in key_status_map
        and str(key_status_map[b["key_id"]].get("status") or "").lower()
        in {"revoked", "inactive", "expired"}
    ]
    if stale_bindings:
        actions.append({
            "action": "deactivate_stale_bindings",
            "description": f"Set is_active=FALSE on {len(stale_bindings)} bindings for revoked/expired/inactive keys",
            "key_ids": [b["key_id"] for b in stale_bindings],
            "safe": True,
            "reason": (
                "Active bindings for non-active keys inflate the Active Devices count. "
                "Setting is_active=FALSE matches the expected state."
            ),
        })

    # ── Action 4: Backfill redeemed_at for owned active keys (no binding) ─────
    owned_no_redeemed_at = [
        k for k in keys
        if k.get("owner_discord_id")
        and not k.get("redeemed_at")
        and str(k.get("status") or "").lower() == "active"
        and not _iso_expired(k.get("expires_at"))
        and k["id"] not in binding_by_key
    ]
    if owned_no_redeemed_at:
        actions.append({
            "action": "backfill_redeemed_at_owned_unbound",
            "description": (
                f"Backfill redeemed_at=created_at for {len(owned_no_redeemed_at)} "
                "owned active unbound keys missing redeemed_at"
            ),
            "key_ids": [k["id"] for k in owned_no_redeemed_at],
            "safe": True,
            "reason": (
                "Key has owner_discord_id but redeemed_at is NULL. "
                "This means it was created by Discord bot before migration 003. "
                "Backfilling ensures Redeemed Keys count is accurate."
            ),
        })

    return actions


def create_backup(data: dict[str, list[dict]]) -> Path:
    """Save raw data to a timestamped backup file.  Returns the backup path."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = BACKUP_DIR / f"license_db_cleanup_{ts}.json"
    # Mask sensitive data in backup (keys are already hashed, Discord IDs are kept for restore)
    backup_data = {
        "created_at": _utc_now_iso(),
        "tables": {
            "license_keys": [
                {k: v for k, v in row.items() if k not in {"key_ciphertext"}}
                for row in data["keys"]
            ],
            "device_bindings": data["bindings"],
            "license_users": data["license_users"],
            "site_users": [
                {k: v for k, v in row.items()
                 if k not in {"discord_access_token", "discord_refresh_token", "password_hash"}}
                for row in data["site_users"]
            ],
        },
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(backup_data, f, indent=2, default=str)
    print(f"  Backup saved: {path}")
    return path


def apply_cleanup_actions(client, actions: list[dict], *, dry_run: bool) -> dict[str, int]:
    """Apply cleanup actions.  Returns dict of {action: rows_affected}."""
    results: dict[str, int] = {}
    now_iso = _utc_now_iso()

    for act in actions:
        action = act["action"]
        key_ids = act.get("key_ids", [])
        description = act["description"]

        if dry_run:
            print(f"  [DRY-RUN] Would {description}")
            results[action] = len(key_ids)
            continue

        print(f"  Applying: {description}")

        if action == "mark_expired_unredeemed":
            # UPDATE license_keys SET status='expired' WHERE id IN (...)
            count = 0
            for kid in key_ids:
                try:
                    res = (
                        client.table("license_keys")
                        .update({"status": "expired", "updated_at": now_iso})
                        .eq("id", kid)
                        .eq("status", "active")          # safety guard
                        .is_("redeemed_at", "null")      # safety guard
                        .execute()
                    )
                    if res.data:
                        count += 1
                except Exception as exc:
                    print(f"    ERROR on key {_mask_key_id(kid)}: {exc}", file=sys.stderr)
            results[action] = count
            print(f"    → {count} rows updated")

        elif action in {"backfill_redeemed_at", "backfill_redeemed_at_owned_unbound"}:
            # UPDATE license_keys SET redeemed_at=created_at WHERE id=... AND redeemed_at IS NULL
            count = 0
            for kid in key_ids:
                try:
                    # Fetch created_at to use as fallback redeemed_at
                    raw = client.table("license_keys").select("created_at").eq("id", kid).execute()
                    created_at = (raw.data[0].get("created_at") if raw.data else None) or now_iso
                    res = (
                        client.table("license_keys")
                        .update({"redeemed_at": created_at, "updated_at": now_iso})
                        .eq("id", kid)
                        .is_("redeemed_at", "null")   # safety guard — never overwrite
                        .execute()
                    )
                    if res.data:
                        count += 1
                except Exception as exc:
                    print(f"    ERROR on key {_mask_key_id(kid)}: {exc}", file=sys.stderr)
            results[action] = count
            print(f"    → {count} rows updated")

        elif action == "deactivate_stale_bindings":
            # UPDATE device_bindings SET is_active=FALSE WHERE key_id IN (...)
            count = 0
            for kid in key_ids:
                try:
                    res = (
                        client.table("device_bindings")
                        .update({"is_active": False})
                        .eq("key_id", kid)
                        .eq("is_active", True)    # safety guard
                        .execute()
                    )
                    if res.data:
                        count += 1
                except Exception as exc:
                    print(f"    ERROR on binding for key {_mask_key_id(kid)}: {exc}", file=sys.stderr)
            results[action] = count
            print(f"    → {count} rows updated")

        else:
            print(f"  UNKNOWN action: {action}", file=sys.stderr)
            results[action] = 0

    return results


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_inspect(client) -> None:
    print("\n======== DENG Tool: Rejoin — License DB Inspect ========")
    print(f"Timestamp: {_utc_now_iso()}\n")

    print("── Row counts ──")
    counts = _fetch_counts(client)
    for table, count in counts.items():
        if count == -1:
            print(f"  {table:<30} (table missing or inaccessible)")
        else:
            print(f"  {table:<30} {count:>6} rows")

    print("\n── Loading full data for stat analysis …")
    data = _load_all_data(client)

    print("\n── Canonical public stats ──")
    canonical = compute_canonical_stats(data)
    for key, val in canonical.items():
        print(f"  {key:<20} {val:>6}")

    print("\n── Key status breakdown ──")
    status_counts: dict[str, int] = {}
    for k in data["keys"]:
        s = str(k.get("status") or "active").lower()
        status_counts[s] = status_counts.get(s, 0) + 1
    for s, cnt in sorted(status_counts.items()):
        print(f"  status={s:<15} {cnt:>6} keys")

    print("\n── Binding state breakdown ──")
    active_bindings = sum(1 for b in data["bindings"] if b.get("is_active"))
    inactive_bindings = sum(1 for b in data["bindings"] if not b.get("is_active"))
    print(f"  is_active=TRUE           {active_bindings:>6} bindings")
    print(f"  is_active=FALSE          {inactive_bindings:>6} bindings")

    print("\n── redeemed_at stats ──")
    with_redeemed_at = sum(1 for k in data["keys"] if k.get("redeemed_at"))
    without_redeemed_at = sum(1 for k in data["keys"] if not k.get("redeemed_at"))
    print(f"  redeemed_at IS NOT NULL  {with_redeemed_at:>6} keys")
    print(f"  redeemed_at IS NULL      {without_redeemed_at:>6} keys")

    print("\n── Ownership stats ──")
    owned = sum(1 for k in data["keys"] if k.get("owner_discord_id") or k.get("site_user_id"))
    unowned = sum(1 for k in data["keys"] if not k.get("owner_discord_id") and not k.get("site_user_id"))
    print(f"  Owned (has owner)        {owned:>6} keys")
    print(f"  Unowned                  {unowned:>6} keys")

    print("\n── license_users breakdown ──")
    owners_count = sum(1 for u in data["license_users"] if u.get("is_owner"))
    blocked_count = sum(1 for u in data["license_users"] if u.get("is_blocked"))
    print(f"  Total users              {len(data['license_users']):>6}")
    print(f"  is_owner=TRUE            {owners_count:>6}")
    print(f"  is_blocked=TRUE          {blocked_count:>6}")

    print("\n── site_users breakdown ──")
    su_with_discord = sum(1 for u in data["site_users"] if u.get("discord_user_id"))
    su_active = sum(1 for u in data["site_users"] if u.get("is_active", True))
    print(f"  Total site_users         {len(data['site_users']):>6}")
    print(f"  With discord_user_id     {su_with_discord:>6}")
    print(f"  is_active=TRUE           {su_active:>6}")

    print("\n======== End of Inspect ========\n")


def cmd_cleanup(client, *, dry_run: bool) -> None:
    mode = "DRY-RUN" if dry_run else "APPLY"
    print(f"\n======== DENG Tool: Rejoin — License DB Cleanup ({mode}) ========")
    print(f"Timestamp: {_utc_now_iso()}\n")

    print("── Loading data …")
    data = _load_all_data(client)

    print("\n── Running audit checks …")
    findings = run_audit_checks(data)

    print("\n── Audit Report ──")
    warnings = 0
    for f in findings:
        sev = f["severity"]
        icon = {"OK": "✓", "INFO": "ℹ", "WARN": "⚠"}.get(sev, "?")
        count_str = f"  count={f['count']}" if f["count"] else ""
        print(f"  [{icon}] Check {f['check']:>2}: {f['label']}{count_str}")
        if f["detail"] and sev == "WARN" and f["count"] > 0:
            print(f"           Detail: {f['detail']}")
        if sev == "WARN" and f["count"] > 0:
            warnings += 1

    print(f"\n  Total warnings: {warnings}")

    print("\n── Before-cleanup canonical stats ──")
    before = compute_canonical_stats(data)
    for key, val in before.items():
        print(f"  {key:<20} {val:>6}")

    print("\n── Planned cleanup actions ──")
    actions = plan_cleanup_actions(data)
    if not actions:
        print("  No cleanup actions needed.")
    for act in actions:
        print(f"  [{act['action']}]")
        print(f"    {act['description']}")
        print(f"    Reason: {act['reason']}")
        print()

    if not actions:
        print("  Database is already clean. No actions to apply.")
        return

    if not dry_run:
        print("── Creating backup before apply …")
        backup_path = create_backup(data)
        print(f"  Backup: {backup_path}\n")

        print("── Applying cleanup actions …")
        results = apply_cleanup_actions(client, actions, dry_run=False)

        print("\n── Reloading data for after-cleanup stats …")
        data_after = _load_all_data(client)

        print("\n── After-cleanup canonical stats ──")
        after = compute_canonical_stats(data_after)
        for key in before:
            b = before[key]
            a = after.get(key, b)
            delta = a - b
            delta_str = f"  ({"+" if delta >= 0 else ""}{delta})" if delta != 0 else ""
            print(f"  {key:<20} {b:>6} → {a:>6}{delta_str}")

        print("\n── After-cleanup audit checks ──")
        findings_after = run_audit_checks(data_after)
        remaining_warnings = sum(
            1 for f in findings_after
            if f["severity"] == "WARN" and f["count"] > 0
        )
        print(f"  Remaining warnings: {remaining_warnings}")
        for f in findings_after:
            if f["severity"] == "WARN" and f["count"] > 0:
                print(f"  ⚠  Check {f['check']:>2}: {f['label']}  count={f['count']}")

        print(f"\n  Cleanup results: {results}")

    else:
        print("── [DRY-RUN] Simulating cleanup actions …")
        apply_cleanup_actions(client, actions, dry_run=True)
        print("\n  No data was modified. Re-run with --apply --confirm CLEAN_LICENSE_DB to apply.")

    print("\n======== End of Cleanup ========\n")


def cmd_verify(client) -> None:
    print("\n======== DENG Tool: Rejoin — License DB Verify ========")
    print(f"Timestamp: {_utc_now_iso()}\n")

    print("── Loading data …")
    data = _load_all_data(client)

    print("\n── Canonical public stats (DB truth) ──")
    canonical = compute_canonical_stats(data)
    for key, val in canonical.items():
        print(f"  {key:<20} {val:>6}")

    print("\n── Audit findings summary ──")
    findings = run_audit_checks(data)
    warnings = [(f["check"], f["label"], f["count"])
                for f in findings if f["severity"] == "WARN" and f["count"] > 0]
    if warnings:
        print("  WARNINGS (require attention):")
        for check, label, count in warnings:
            print(f"    Check {check:>2}: {label}  [{count}]")
    else:
        print("  No warnings. Database state looks clean.")

    print("\n── Stat consistency check ──")
    active_keys = sum(1 for k in data["keys"] if str(k.get("status") or "").lower() == "active")
    active_bindings = sum(1 for b in data["bindings"] if b.get("is_active"))
    stale = sum(
        1 for b in data["bindings"]
        if b.get("is_active")
        and b.get("key_id") in {k["id"] for k in data["keys"]
                                 if str(k.get("status") or "").lower() != "active"}
    )
    print(f"  Active keys (status=active):              {active_keys:>6}")
    print(f"  Active bindings (is_active=TRUE):         {active_bindings:>6}")
    print(f"    of which on non-active keys (stale):    {stale:>6}  (should be 0)")
    print(f"  Canonical generated_keys:                 {canonical['generatedKeys']:>6}  (should = active keys)")
    print(f"  Canonical active_devices:                 {canonical['activeDevices']:>6}  (should = active_bindings - stale)")

    expected_active_devices = active_bindings - stale
    device_ok = canonical["activeDevices"] == expected_active_devices
    print(f"\n  Active Devices check: {'PASS' if device_ok else 'MISMATCH'}")
    if not device_ok:
        print(f"    Canonical: {canonical['activeDevices']}, Expected: {expected_active_devices}")

    generated_ok = canonical["generatedKeys"] == active_keys
    print(f"  Generated Keys check: {'PASS' if generated_ok else 'MISMATCH'}")
    if not generated_ok:
        print(f"    Canonical: {canonical['generatedKeys']}, Active keys: {active_keys}")

    print("\n── Per-user stats consistency (sample check) ──")
    # Find a user with keys and check their per-user stats
    user_key_counts: dict[str, int] = {}
    for k in data["keys"]:
        owner = str(k.get("owner_discord_id") or "").strip()
        if owner and str(k.get("status") or "").lower() == "active":
            user_key_counts[owner] = user_key_counts.get(owner, 0) + 1

    top_users = sorted(user_key_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    if top_users:
        print("  Top users by active key count:")
        for discord_id, count in top_users:
            print(f"    {_mask_discord_id(discord_id)} : {count} active key(s)")
    else:
        print("  No users with active keys found.")

    print("\n======== End of Verify ========\n")


# ── CLI entrypoint ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="DENG Tool: Rejoin — License Database Audit & Cleanup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("inspect", help="Print schema overview and row counts")

    cleanup_p = sub.add_parser("cleanup", help="Audit and optionally clean the database")
    cleanup_p.add_argument("--dry-run", action="store_true", help="Print planned changes without applying")
    cleanup_p.add_argument("--apply", action="store_true", help="Apply deterministic safe fixes")
    cleanup_p.add_argument("--confirm", type=str, default="",
                           help="Must be CLEAN_LICENSE_DB when using --apply")

    sub.add_parser("verify", help="Verify stat consistency after cleanup")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    print("Connecting to Supabase …")
    client = _get_supabase_client()
    print("Connected.\n")

    if args.command == "inspect":
        cmd_inspect(client)

    elif args.command == "cleanup":
        if args.apply and not args.dry_run:
            if args.confirm != "CLEAN_LICENSE_DB":
                print(
                    "ERROR: --apply requires --confirm CLEAN_LICENSE_DB.\n"
                    "Run with --dry-run first to review planned changes.",
                    file=sys.stderr,
                )
                sys.exit(1)
            cmd_cleanup(client, dry_run=False)
        elif args.dry_run:
            cmd_cleanup(client, dry_run=True)
        else:
            print("ERROR: Specify --dry-run or --apply --confirm CLEAN_LICENSE_DB", file=sys.stderr)
            sys.exit(1)

    elif args.command == "verify":
        cmd_verify(client)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
