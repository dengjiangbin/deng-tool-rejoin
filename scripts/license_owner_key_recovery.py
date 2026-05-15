#!/usr/bin/env python3
"""Owner/admin maintenance: backup, inspect, backfill export ciphertext, reset stuck keys.

Examples::

    python scripts/license_owner_key_recovery.py inspect --discord-user-id 123456789012345678
    python scripts/license_owner_key_recovery.py backup --discord-user-id 123456789012345678
    python scripts/license_owner_key_recovery.py verify-export-columns
    python scripts/license_owner_key_recovery.py backfill --discord-user-id ... \\
        --full-key-env DENG_REJOIN_BACKFILL_FULL_KEY
    python scripts/license_owner_key_recovery.py reset-unrecoverable --discord-user-id ... \\
        --confirm RESET_OWNER_KEY

Environment::

    SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY — production Supabase (when DENG_LICENSE_STORE=supabase)
    LICENSE_KEY_EXPORT_SECRET — required for encrypt/backfill
    DENG_LICENSE_STORE — ``supabase`` or ``local``
    LICENSE_OWNER_DISCORD_IDS — optional; use ``--from-single-env-owner`` only when exactly one ID is listed

Safety::

    Never logs full plaintext keys. Backup JSON omits ciphertext bytes (presence/hash-prefix only).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _resolve_discord_uid(ns: argparse.Namespace) -> str:
    from agent.license_owner_recovery import OwnerRecoveryError, parse_single_owner_target_from_env

    if getattr(ns, "discord_user_id", None):
        uid = str(ns.discord_user_id).strip()
        if not uid.isdigit():
            raise OwnerRecoveryError("--discord-user-id must be numeric")
        return uid
    if getattr(ns, "from_single_env_owner", False):
        one = parse_single_owner_target_from_env()
        if not one:
            raise OwnerRecoveryError(
                "LICENSE_OWNER_DISCORD_IDS must list exactly one numeric Discord user ID "
                "(or pass --discord-user-id explicitly)."
            )
        print(f"[target] Discord user ID from LICENSE_OWNER_DISCORD_IDS (sole entry): {one}")
        return one
    raise OwnerRecoveryError("Provide --discord-user-id or --from-single-env-owner.")


def _cmd_inspect(ns: argparse.Namespace) -> int:
    from agent.license_owner_recovery import inspect_summary
    from agent.license_store import get_default_store

    uid = _resolve_discord_uid(ns)
    store = get_default_store()
    summary = inspect_summary(store, uid)
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_backup(ns: argparse.Namespace) -> int:
    from agent.license_owner_recovery import fetch_owner_snapshot, write_backup_file

    uid = _resolve_discord_uid(ns)
    from agent.license_store import get_default_store

    store = get_default_store()
    snap = fetch_owner_snapshot(store, uid)
    path = write_backup_file(PROJECT_ROOT, uid, snap)
    print(f"[backup] wrote {path}")
    return 0


def _cmd_verify_export_columns(ns: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    mode = os.environ.get("DENG_LICENSE_STORE", "local").strip().lower()
    if mode != "supabase":
        print("[verify-export-columns] DENG_LICENSE_STORE is not supabase — migration applies to Supabase only.")
        return 0
    from agent.license_store import SupabaseLicenseStore
    from agent.license_owner_recovery import verify_supabase_export_columns

    store = SupabaseLicenseStore()
    ok, detail = verify_supabase_export_columns(store._client)
    print(json.dumps({"ok": ok, "detail": detail}, indent=2))
    return 0 if ok else 2


def _cmd_backfill(ns: argparse.Namespace) -> int:
    from agent.license_owner_recovery import (
        DEFAULT_BACKFILL_ENV_VAR,
        OwnerRecoveryError,
        backfill_plaintext_for_owner_key,
    )
    from agent.license_store import get_default_store

    uid = _resolve_discord_uid(ns)
    env_name = ns.full_key_env or DEFAULT_BACKFILL_ENV_VAR
    plain = os.environ.get(env_name, "").strip()
    if not plain:
        raise OwnerRecoveryError(f"Environment variable {env_name!r} is empty or unset.")

    store = get_default_store()
    msg = backfill_plaintext_for_owner_key(
        store,
        uid,
        plain,
        key_id=getattr(ns, "key_id", None),
    )
    print(f"[backfill] OK ({msg}). Key Stats should show full copyable key.")
    return 0


def _cmd_reset(ns: argparse.Namespace) -> int:
    from agent.license_owner_recovery import reset_unrecoverable_owner_keys
    from agent.license_store import get_default_store

    uid = _resolve_discord_uid(ns)
    confirm = (ns.confirm or "").strip()
    store = get_default_store()
    backup, revoked = reset_unrecoverable_owner_keys(
        store,
        uid,
        confirm_token=confirm,
        project_root=PROJECT_ROOT,
    )
    print(f"[reset] backup: {backup}")
    if revoked:
        print(f"[reset] revoked row id prefixes (sha256): {[k[:12] + '...' for k in revoked]}")
    print("[reset] Owner may Generate Key or Redeem in Discord panel.")
    return 0


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv()

    from agent.license_owner_recovery import CONFIRM_RESET_TOKEN, OwnerRecoveryError

    parser = argparse.ArgumentParser(description="License owner maintenance / recovery")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_uid_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--discord-user-id", help="Target Discord snowflake string")
        p.add_argument(
            "--from-single-env-owner",
            action="store_true",
            help="Use sole ID from LICENSE_OWNER_DISCORD_IDS (fails unless exactly one)",
        )

    p_ins = sub.add_parser("inspect", help="JSON summary of owner's keys (safe)")
    add_uid_flags(p_ins)

    p_bak = sub.add_parser("backup", help="Write timestamped backup JSON under data/backups/")
    add_uid_flags(p_bak)

    sub.add_parser("verify-export-columns", help="Probe Supabase for migration 002 columns")

    p_bf = sub.add_parser("backfill", help="Encrypt full key from env into license_keys row")
    add_uid_flags(p_bf)
    p_bf.add_argument(
        "--full-key-env",
        default="DENG_REJOIN_BACKFILL_FULL_KEY",
        help="Env var name holding plaintext full key (default: DENG_REJOIN_BACKFILL_FULL_KEY)",
    )
    p_bf.add_argument("--key-id", help="SHA-256 key id when owner has multiple active keys")

    p_rs = sub.add_parser(
        "reset-unrecoverable",
        help=f'Soft-revoke active owned keys (requires matching --confirm token)',
    )
    add_uid_flags(p_rs)
    p_rs.add_argument(
        "--confirm",
        required=True,
        help=f'Must be "{CONFIRM_RESET_TOKEN}"',
    )

    ns = parser.parse_args()

    try:
        if ns.command == "inspect":
            return _cmd_inspect(ns)
        if ns.command == "backup":
            return _cmd_backup(ns)
        if ns.command == "verify-export-columns":
            return _cmd_verify_export_columns(ns)
        if ns.command == "backfill":
            return _cmd_backfill(ns)
        if ns.command == "reset-unrecoverable":
            return _cmd_reset(ns)
    except OwnerRecoveryError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    raise AssertionError("unknown command")


if __name__ == "__main__":
    raise SystemExit(main())
