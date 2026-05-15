"""Owner/admin maintenance for license key export backfill and unrecoverable-key reset.

Safe helpers used by ``scripts/license_owner_key_recovery.py``. No Discord imports.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .license import (
    LicenseKeyError,
    hash_license_key,
    normalize_license_key,
)
from .license_store import (
    BaseLicenseStore,
    LocalJsonLicenseStore,
    SupabaseLicenseStore,
    _utc_now,
)

CONFIRM_RESET_TOKEN = "RESET_OWNER_KEY"
DEFAULT_BACKFILL_ENV_VAR = "DENG_REJOIN_BACKFILL_FULL_KEY"

_MASKISH_KEY_RE = re.compile(r"\.\.\.|…")


class OwnerRecoveryError(RuntimeError):
    """User-fixable validation error for recovery tooling."""


def visible_license_rows_for_panel(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hide revoked keys from Key Stats / Download UI (rows remain in the database)."""
    out: list[dict[str, Any]] = []
    for r in rows:
        if str(r.get("license_status") or "").lower() == "revoked":
            continue
        out.append(r)
    return out


def parse_single_owner_target_from_env() -> str | None:
    """Return the sole Discord ID from LICENSE_OWNER_DISCORD_IDS, or None if not exactly one."""
    raw = os.environ.get("LICENSE_OWNER_DISCORD_IDS", "")
    ids: list[str] = []
    for part in raw.split(","):
        p = part.strip()
        if p.isdigit():
            ids.append(p)
    return ids[0] if len(ids) == 1 else None


def backup_filename_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def verify_supabase_export_columns(client: Any) -> tuple[bool, str]:
    """Return (ok, detail) after probing ``key_ciphertext`` / ``key_export_available``."""
    try:
        client.table("license_keys").select("key_ciphertext,key_export_available").limit(0).execute()
        return True, "license_keys export columns present"
    except Exception as exc:
        err = str(exc)
        if (
            "PGRST204" in err
            or "42703" in err
            or "column" in err.lower()
            or "undefined_column" in err.lower()
            or "schema cache" in err.lower()
        ):
            return False, "missing columns — apply supabase/migrations/002_key_export_support.sql"
        return False, f"probe failed: {err}"


def _sanitize_key_row(rec: dict[str, Any]) -> dict[str, Any]:
    out = dict(rec)
    ct = (out.get("key_ciphertext") or "").strip()
    if ct:
        out["key_ciphertext"] = None
        out["_key_ciphertext_present"] = True
        out["_key_ciphertext_sha256_prefix"] = hashlib.sha256(ct.encode()).hexdigest()[:16]
    else:
        out["_key_ciphertext_present"] = False
    return out


def fetch_owner_snapshot(store: BaseLicenseStore, discord_user_id: str) -> dict[str, Any]:
    """Return serializable snapshot for backup (no plaintext keys)."""
    if isinstance(store, LocalJsonLicenseStore):
        db = store._load()
        keys_out: list[dict[str, Any]] = []
        bindings_out: list[dict[str, Any]] = []
        for kid, rec in db.get("keys", {}).items():
            if rec.get("owner_discord_id") != discord_user_id:
                continue
            keys_out.append({"key_id": kid, **_sanitize_key_row(dict(rec))})
            bind = dict(db.get("bindings", {}).get(kid, {}))
            if bind:
                bindings_out.append({"key_id": kid, **bind})
        user_row = dict(db.get("users", {}).get(discord_user_id, {}))
        users_blob = [user_row] if discord_user_id in db.get("users", {}) else []
        audits = [
            dict(e)
            for e in db.get("audit_logs", [])
            if str(e.get("actor_discord_id")) == discord_user_id
            or str(e.get("metadata", {}).get("owner")) == discord_user_id
        ][-50:]
        return {
            "license_users": users_blob,
            "license_keys": keys_out,
            "device_bindings": bindings_out,
            "audit_tail": audits,
        }

    if isinstance(store, SupabaseLicenseStore):
        client = store._client
        lu = (
            client.table("license_users")
            .select("*")
            .eq("discord_user_id", discord_user_id)
            .execute()
        )
        lk = (
            client.table("license_keys")
            .select("*")
            .eq("owner_discord_id", discord_user_id)
            .execute()
        )
        keys_out = []
        bindings_out = []
        for rec in lk.data or []:
            kid = rec["id"]
            keys_out.append({"key_id": kid, **_sanitize_key_row(dict(rec))})
            b = (
                client.table("device_bindings")
                .select("*")
                .eq("key_id", kid)
                .execute()
            )
            if b.data:
                bindings_out.append({"key_id": kid, **dict(b.data[0])})
        aa = (
            client.table("admin_audit_logs")
            .select("*")
            .eq("actor_discord_id", discord_user_id)
            .limit(50)
            .execute()
        )
        return {
            "license_users": lu.data or [],
            "license_keys": keys_out,
            "device_bindings": bindings_out,
            "audit_tail": aa.data or [],
        }

    raise OwnerRecoveryError("Unsupported license store implementation")


def write_backup_file(root: Path, discord_user_id: str, snapshot: dict[str, Any]) -> Path:
    """Write timestamped JSON under ``data/backups/``."""
    backup_dir = root / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = backup_filename_timestamp()
    path = backup_dir / f"license_owner_key_backup_{ts}.json"
    payload = {
        "backup_created_at": datetime.now(timezone.utc).isoformat(),
        "discord_user_id": discord_user_id,
        **snapshot,
    }
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def list_active_owned_key_targets(store: BaseLicenseStore, discord_user_id: str) -> list[dict[str, Any]]:
    """Rows for keys owned by user with status != revoked."""
    snap = fetch_owner_snapshot(store, discord_user_id)
    rows = []
    for kr in snap.get("license_keys", []):
        st = str(kr.get("status") or "active").lower()
        if st == "revoked":
            continue
        rows.append(kr)
    return rows


def backfill_plaintext_for_owner_key(
    store: BaseLicenseStore,
    discord_user_id: str,
    plaintext_full_key: str,
    *,
    key_id: str | None,
    actor_label: str = "license_owner_recovery",
) -> str:
    """Encrypt and store ciphertext for one owned key after validating hash match.

    Returns a short status message for logs (never includes full key).
    """
    from . import license_key_export as lke

    raw = (plaintext_full_key or "").strip()
    if _MASKISH_KEY_RE.search(raw):
        raise OwnerRecoveryError(
            "Provided key looks masked (contains … or ...). Full plaintext key required."
        )
    try:
        normalized = normalize_license_key(raw)
    except LicenseKeyError as exc:
        raise OwnerRecoveryError(f"Invalid license key format: {exc}") from exc

    expect_hash = hash_license_key(normalized)
    targets = list_active_owned_key_targets(store, discord_user_id)
    if not targets:
        raise OwnerRecoveryError("No active owned keys found for this Discord user.")
    if len(targets) > 1 and not key_id:
        raise OwnerRecoveryError(
            "Multiple active keys owned — pass explicit --key-id <sha256> "
            "(see inspect output)."
        )
    tid = key_id or targets[0]["key_id"]
    if tid != expect_hash:
        raise OwnerRecoveryError(
            "Provided full key does not match the stored key fingerprint for this owner."
        )
    owned_ids = {t["key_id"] for t in targets}
    if tid not in owned_ids:
        raise OwnerRecoveryError("key_id does not match an active owned key.")

    if not lke.is_export_secret_configured():
        raise OwnerRecoveryError(
            "LICENSE_KEY_EXPORT_SECRET is not set or cryptography is unavailable."
        )

    ct = lke.encrypt_license_key_plaintext(normalized)
    if not ct:
        raise OwnerRecoveryError("Encryption returned empty ciphertext.")

    plain_roundtrip = lke.decrypt_license_key_ciphertext(ct)
    if plain_roundtrip != normalized:
        raise OwnerRecoveryError("Encrypt/decrypt round-trip failed.")

    if isinstance(store, LocalJsonLicenseStore):
        db = store._load()
        rec = db.get("keys", {}).get(tid)
        if not rec or rec.get("owner_discord_id") != discord_user_id:
            raise OwnerRecoveryError("Key row missing or wrong owner.")
        db["keys"][tid]["key_ciphertext"] = ct
        db["keys"][tid]["key_export_available"] = True
        db["keys"][tid]["updated_at"] = _utc_now()
        store._save(db)
        store.audit_admin_action(
            actor_label,
            "owner_recovery_backfill",
            target_type="key",
            target_id=tid[:8],
            metadata={"discord_user_id": discord_user_id},
        )
        return "backfilled_local_store"

    if isinstance(store, SupabaseLicenseStore):
        ok, msg = verify_supabase_export_columns(store._client)
        if not ok:
            raise OwnerRecoveryError(msg)
        store._client.table("license_keys").update({
            "key_ciphertext": ct,
            "key_export_available": True,
        }).eq("id", tid).eq("owner_discord_id", discord_user_id).execute()
        store.audit_admin_action(
            actor_label,
            "owner_recovery_backfill",
            target_type="key",
            target_id=tid[:8],
            metadata={"discord_user_id": discord_user_id},
        )
        return "backfilled_supabase"

    raise OwnerRecoveryError("Unsupported store")


def reset_unrecoverable_owner_keys(
    store: BaseLicenseStore,
    discord_user_id: str,
    *,
    confirm_token: str,
    actor_label: str = "license_owner_recovery",
    project_root: Path | None = None,
) -> tuple[Path, list[str]]:
    """Soft-revoke active owned keys, clear export blobs, deactivate bindings.

    Always writes backup first when *project_root* is provided.
    Returns ``(backup_path | pathlib sentinel, key_ids_revoked)``.
    """
    if confirm_token != CONFIRM_RESET_TOKEN:
        raise OwnerRecoveryError(
            f'Confirmation must be exactly "{CONFIRM_RESET_TOKEN}".'
        )

    backup_path: Path | None = None
    snapshot = fetch_owner_snapshot(store, discord_user_id)
    if project_root is not None:
        backup_path = write_backup_file(project_root, discord_user_id, snapshot)

    targets = list_active_owned_key_targets(store, discord_user_id)
    if not targets:
        raise OwnerRecoveryError("No active owned keys to reset.")

    revoked: list[str] = []
    if isinstance(store, LocalJsonLicenseStore):
        db = store._load()
        for t in targets:
            tid = t["key_id"]
            rec = db["keys"].get(tid)
            if not rec or rec.get("owner_discord_id") != discord_user_id:
                continue
            rec["status"] = "revoked"
            rec["key_ciphertext"] = ""
            rec["key_export_available"] = False
            rec["updated_at"] = _utc_now()
            db["keys"][tid] = rec
            bind = db.setdefault("bindings", {}).get(tid)
            if bind:
                bind["is_active"] = False
                db["bindings"][tid] = bind
            revoked.append(tid)
        store._save(db)
        store.audit_admin_action(
            actor_label,
            "owner_recovery_reset_unrecoverable",
            target_type="user",
            target_id=discord_user_id[:12],
            metadata={"keys_revoked": [x[:8] for x in revoked]},
        )
        return backup_path or Path("."), revoked

    if isinstance(store, SupabaseLicenseStore):
        client = store._client
        mig_ok, _ = verify_supabase_export_columns(client)
        for t in targets:
            tid = t["key_id"]
            upd: dict[str, Any] = {"status": "revoked"}
            if mig_ok:
                upd["key_ciphertext"] = None
                upd["key_export_available"] = False
            client.table("license_keys").update(upd).eq("id", tid).eq(
                "owner_discord_id", discord_user_id
            ).execute()
            client.table("device_bindings").update({"is_active": False}).eq(
                "key_id", tid
            ).execute()
            revoked.append(tid)
        store.audit_admin_action(
            actor_label,
            "owner_recovery_reset_unrecoverable",
            target_type="user",
            target_id=discord_user_id[:12],
            metadata={"keys_revoked": [x[:8] for x in revoked]},
        )
        return backup_path or Path("."), revoked

    raise OwnerRecoveryError("Unsupported store")


def inspect_summary(store: BaseLicenseStore, discord_user_id: str) -> dict[str, Any]:
    """Structured inspect payload for CLI output."""
    from . import license_key_export as lke

    targets = list_active_owned_key_targets(store, discord_user_id)
    snap = fetch_owner_snapshot(store, discord_user_id)
    export_secret_ok = lke.is_export_secret_configured()
    mig_ok = True
    mig_detail = "n/a (local store)"
    if isinstance(store, SupabaseLicenseStore):
        mig_ok, mig_detail = verify_supabase_export_columns(store._client)

    summaries = []
    for t in targets:
        tid = t["key_id"]
        masked = f"{t.get('prefix', '?')}...{t.get('suffix', '?')}"
        raw_ct = ""
        bind_active = False
        if isinstance(store, LocalJsonLicenseStore):
            db = store._load()
            raw_ct = (db.get("keys", {}).get(tid, {}).get("key_ciphertext") or "").strip()
            bind_active = bool(db.get("bindings", {}).get(tid, {}).get("is_active"))
        elif isinstance(store, SupabaseLicenseStore):
            if mig_ok:
                try:
                    res = (
                        store._client.table("license_keys")
                        .select("key_ciphertext")
                        .eq("id", tid)
                        .limit(1)
                        .execute()
                    )
                    if res.data:
                        raw_ct = (res.data[0].get("key_ciphertext") or "").strip()
                except Exception:
                    raw_ct = ""
            else:
                raw_ct = ""
            for b in snap.get("device_bindings", []):
                if b.get("key_id") == tid:
                    bind_active = bool(b.get("is_active"))
                    break

        ct_present = bool(raw_ct)
        recoverable = bool(raw_ct and lke.decrypt_license_key_ciphertext(raw_ct))

        summaries.append({
            "key_id_prefix": tid[:16],
            "masked_reference": masked,
            "status": t.get("status"),
            "active_binding": bind_active,
            "ciphertext_present": ct_present,
            "full_key_recoverable_with_current_secret": recoverable,
        })

    return {
        "discord_user_id": discord_user_id,
        "active_owned_key_count": len(targets),
        "export_secret_configured": export_secret_ok,
        "migration_002_export_columns_ok": mig_ok,
        "migration_002_detail": mig_detail,
        "keys": summaries,
    }
