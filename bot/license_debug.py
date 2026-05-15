#!/usr/bin/env python3
"""DENG Tool: Rejoin — License diagnostic tool.

Inspect the state of a Discord user or license key in the store without
printing raw secrets.

Usage
-----
    python -m bot.license_debug --discord-user 123456789012345678
    python -m bot.license_debug --key DENG-EF95-50EA-9E36-DCD2

Output masks:
  - Full key hash: never shown
  - install_id_hash: first 8 chars only
  - Service role key: never logged
  - Full license key: never logged (masked only)

Environment variables
---------------------
  DISCORD_BOT_TOKEN   Not required for this tool (store access only).
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  Required if DENG_LICENSE_STORE=supabase.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Load .env from project root
try:
    from dotenv import load_dotenv as _load_dotenv
    _env = Path(__file__).resolve().parents[1] / ".env"
    if _env.exists():
        _load_dotenv(_env)
except ImportError:
    pass

from agent.license_store import (
    ACTIVE_HEARTBEAT_WINDOW_S,
    MAX_HWID_RESETS_PER_24H,
    get_default_store,
)


def _seconds_since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    except (ValueError, TypeError):
        return None


def _fmt_elapsed(sec: float | None) -> str:
    if sec is None:
        return "—"
    if sec < 60:
        return f"{int(sec)}s ago"
    if sec < 3600:
        return f"{int(sec // 60)}m {int(sec % 60)}s ago"
    return f"{int(sec // 3600)}h ago"


def _mask_hash(h: str | None) -> str:
    if not h:
        return "(none)"
    return h[:8] + "..."


def diag_by_discord_user(discord_user_id: str) -> None:
    store = get_default_store()
    store_type = type(store).__name__

    print(f"\n{'='*60}")
    print(f"  License Diagnostic — Discord user: {discord_user_id}")
    print(f"  Store: {store_type}")
    print(f"{'='*60}\n")

    user = store.get_user_by_discord_id(discord_user_id)
    if not user:
        print("  [!] No license user record found for this Discord ID.")
        print("      The user has never interacted with the license panel.\n")
        return

    print(f"  User found:         yes")
    print(f"  Discord username:   {user.get('discord_username') or '(not recorded)'}")
    print(f"  Max keys:           {user.get('max_keys', 1)}")
    print(f"  Is blocked:         {user.get('is_blocked', False)}")
    print()

    keys = store.list_user_keys(discord_user_id)
    print(f"  Keys owned:         {len(keys)}")

    for i, key in enumerate(keys, 1):
        key_id = key.get("id", "???")
        masked = key.get("masked_key", "???")
        status = key.get("status", "unknown")
        bound_device = key.get("bound_device") or "(unbound)"
        last_seen = key.get("last_seen_at")
        elapsed = _seconds_since(last_seen)

        print(f"\n  --- Key {i} ---")
        print(f"  Masked key:         {masked}")
        print(f"  Status:             {status}")
        print(f"  Device bound:       {'yes — ' + bound_device if bound_device != '(unbound)' else 'NO'}")
        print(f"  Last seen:          {last_seen or '—'} ({_fmt_elapsed(elapsed)})")

        resets = store.get_reset_count_24h(key_id)
        print(f"  Resets (last 24h):  {resets}/{MAX_HWID_RESETS_PER_24H}")
        print(f"  Reset allowed:      {'NO — limit reached' if resets >= MAX_HWID_RESETS_PER_24H else 'yes'}")

        if elapsed is not None and elapsed < ACTIVE_HEARTBEAT_WINDOW_S:
            remaining = int(ACTIVE_HEARTBEAT_WINDOW_S - elapsed)
            print(f"  Active guard:       KEY IS ACTIVE — wait {remaining}s before HWID reset")
        else:
            print(f"  Active guard:       ok (not active in last 5 min)")

    print()


def diag_by_key(raw_key: str) -> None:
    from agent.license import normalize_license_key, hash_license_key, mask_license_key, LicenseKeyError

    store = get_default_store()
    store_type = type(store).__name__

    try:
        normalized = normalize_license_key(raw_key)
    except LicenseKeyError as exc:
        print(f"[!] Invalid key format: {exc}")
        sys.exit(1)

    masked = mask_license_key(normalized)
    key_hash = hash_license_key(normalized)

    print(f"\n{'='*60}")
    print(f"  License Diagnostic — Key: {masked}")
    print(f"  Store: {store_type}")
    print(f"{'='*60}\n")

    # Try to get key record (store-agnostic: use list_user_keys on all users is expensive,
    # so we check via bind_or_check_device with a dummy hash)
    # Better: directly read the db if local, or query Supabase if remote
    try:
        if hasattr(store, "_load"):
            db = store._load()  # type: ignore[attr-defined]
            record = db.get("keys", {}).get(key_hash)
            if not record:
                print("  [!] Key not found in store.\n")
                return
            owner = record.get("owner_discord_id", "(unowned)")
            status = record.get("status", "unknown")
            binding = db.get("bindings", {}).get(key_hash, {})
            is_active = binding.get("is_active", False)
            device_model = binding.get("device_model") or "(unbound)"
            last_seen = binding.get("last_seen_at")
            inst_hash = binding.get("install_id_hash")
        else:
            # Supabase: use public API
            res = store._client.table("license_keys").select("*").eq("id", key_hash).execute()  # type: ignore[attr-defined]
            if not res.data:
                print("  [!] Key not found in Supabase.\n")
                return
            record = res.data[0]
            owner = record.get("owner_discord_id", "(unowned)")
            status = record.get("status", "unknown")
            b_res = store._client.table("device_bindings").select("*").eq("key_id", key_hash).execute()  # type: ignore[attr-defined]
            binding = b_res.data[0] if b_res.data else {}
            is_active = binding.get("is_active", False)
            device_model = binding.get("device_model") or "(unbound)"
            last_seen = binding.get("last_seen_at")
            inst_hash = binding.get("install_id_hash")
    except Exception as exc:  # noqa: BLE001
        print(f"  [!] Store error: {exc}\n")
        return

    elapsed = _seconds_since(last_seen)
    resets = store.get_reset_count_24h(key_hash)

    print(f"  Masked key:         {masked}")
    print(f"  Status:             {status}")
    print(f"  Owner discord ID:   {owner}")
    print()
    print(f"  Active binding:     {'yes' if is_active else 'NO'}")
    print(f"  Device model:       {device_model}")
    print(f"  Install ID hash:    {_mask_hash(inst_hash)}")
    print(f"  Last seen:          {last_seen or '—'} ({_fmt_elapsed(elapsed)})")
    print()
    print(f"  Resets (last 24h):  {resets}/{MAX_HWID_RESETS_PER_24H}")
    print(f"  Reset allowed:      {'NO — limit reached' if resets >= MAX_HWID_RESETS_PER_24H else 'yes'}")

    if elapsed is not None and elapsed < ACTIVE_HEARTBEAT_WINDOW_S:
        remaining = int(ACTIVE_HEARTBEAT_WINDOW_S - elapsed)
        print(f"  Active guard:       ACTIVE — wait {remaining}s before HWID reset")
    elif not is_active:
        print(f"  Active guard:       no binding — nothing to reset")
    else:
        print(f"  Active guard:       ok (not active in last 5 min)")

    secret = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if secret:
        print(f"\n  Store mode:         {store_type} (service role key present — server-side only)")
    else:
        print(f"\n  Store mode:         {store_type} (local/offline mode)")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="DENG Tool license diagnostic. Masks secrets. Does not print tokens."
    )
    parser.add_argument("--discord-user", metavar="DISCORD_ID", help="Inspect by Discord user ID")
    parser.add_argument("--key", metavar="DENG-XXXX-...", help="Inspect by license key (masked in output)")

    args = parser.parse_args()

    if not args.discord_user and not args.key:
        parser.error("Specify --discord-user DISCORD_ID or --key DENG-XXXX-XXXX-XXXX-XXXX")

    if args.discord_user and args.key:
        parser.error("Specify only one of --discord-user or --key")

    if args.discord_user:
        diag_by_discord_user(args.discord_user)
    elif args.key:
        diag_by_key(args.key)


if __name__ == "__main__":
    main()
