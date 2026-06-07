"""DENG Tool: Rejoin — one-time cleanup for users exceeding active key slot limits.

Usage:
  python scripts/rejoin_cleanup_key_slots.py inspect
  python scripts/rejoin_cleanup_key_slots.py dry-run --max-slots 2
  python scripts/rejoin_cleanup_key_slots.py apply --max-slots 2 --confirm DELETE_EXTRA_REJOIN_KEYS

Safety:
  - Timestamped JSON backup before apply (data/backups/rejoin_key_slot_cleanup_*.json)
  - No full keys printed to console
  - --apply requires exact confirm string
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

BACKUP_DIR = WORKSPACE_ROOT / "data" / "backups"
CONFIRM_STRING = "DELETE_EXTRA_REJOIN_KEYS"


def _mask_key_id(key_id: str | None) -> str:
    if not key_id:
        return "(none)"
    text = str(key_id).strip()
    return f"{text[:8]}..." if len(text) > 8 else text


def _mask_discord_id(discord_id: str | None) -> str:
    if not discord_id:
        return "(none)"
    text = str(discord_id).strip()
    return f"{text[:6]}..." if len(text) > 6 else text


def _mask_row_key(row: dict[str, Any]) -> str:
    prefix = row.get("prefix") or "DENG-????"
    suffix = row.get("suffix") or "????"
    return f"{prefix}-...-{suffix}"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _get_store():
    from dotenv import load_dotenv

    load_dotenv(WORKSPACE_ROOT / ".env")
    os.environ.setdefault("DENG_LICENSE_STORE", "supabase")
    from agent.license_store import SupabaseLicenseStore

    return SupabaseLicenseStore()


def _load_raw_data(client) -> dict[str, list[dict[str, Any]]]:
    def fetch(table: str, columns: str) -> list[dict[str, Any]]:
        try:
            res = client.table(table).select(columns).limit(10000).execute()
            return res.data or []
        except Exception as exc:
            print(f"  WARNING: could not fetch {table}: {exc}", file=sys.stderr)
            return []

    return {
        "keys": fetch(
            "license_keys",
            "id, prefix, suffix, status, owner_discord_id, site_user_id, "
            "redeemed_at, expires_at, created_at, updated_at, plan",
        ),
        "bindings": fetch(
            "device_bindings",
            "key_id, is_active, last_seen_at, install_id_hash",
        ),
        "site_users": fetch("site_users", "id, discord_user_id"),
    }


def _build_site_maps(site_users: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in site_users:
        site_id = str(row.get("id") or "").strip()
        discord_id = str(row.get("discord_user_id") or "").strip()
        if site_id and discord_id:
            out[site_id] = discord_id
    return out


def _resolve_discord_user_id(key: dict[str, Any], site_map: dict[str, str]) -> str | None:
    owner = str(key.get("owner_discord_id") or "").strip()
    if owner.startswith("site:"):
        return site_map.get(owner[5:], None)
    if owner and owner.isdigit():
        return owner
    site_user_id = str(key.get("site_user_id") or "").strip()
    if site_user_id:
        return site_map.get(site_user_id)
    return owner or None


def _binding_for(key_id: str, binding_by_key: dict[str, dict]) -> dict[str, Any]:
    return binding_by_key.get(key_id, {})


def _row_for_limit(key: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any]:
    active_binding = bool(binding.get("is_active"))
    device = ""
    if active_binding:
        device = (
            binding.get("device_model") or binding.get("device_label") or ""
        ).strip() or None
    return {
        "key_id": key["id"],
        "prefix": key.get("prefix"),
        "suffix": key.get("suffix"),
        "license_status": key.get("status", "active"),
        "status": key.get("status", "active"),
        "used": active_binding,
        "device_display": device,
        "last_seen_at": binding.get("last_seen_at"),
        "redeemed_at": key.get("redeemed_at"),
        "expires_at": key.get("expires_at"),
        "created_at": key.get("created_at"),
        "updated_at": key.get("updated_at"),
    }


def _sort_ts(value: str | None) -> str:
    return str(value or "")


def _keep_priority(row: dict[str, Any]) -> tuple:
    redeemed_or_bound = 1 if (row.get("redeemed_at") or row.get("used")) else 0
    return (
        redeemed_or_bound,
        _sort_ts(row.get("last_seen_at")),
        _sort_ts(row.get("redeemed_at")),
        _sort_ts(row.get("updated_at")),
        _sort_ts(row.get("created_at")),
    )


def _analyze(data: dict[str, list[dict]], max_slots: int) -> dict[str, Any]:
    from agent.key_stats_format import filter_active_visible_license_rows

    site_map = _build_site_maps(data["site_users"])
    binding_by_key = {b["key_id"]: b for b in data["bindings"] if b.get("key_id")}

    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    key_by_id: dict[str, dict[str, Any]] = {}

    for key in data["keys"]:
        discord_id = _resolve_discord_user_id(key, site_map)
        if not discord_id:
            continue
        row = _row_for_limit(key, _binding_for(key["id"], binding_by_key))
        key_by_id[key["id"]] = key
        by_user[discord_id].append(row)

    affected: list[dict[str, Any]] = []
    total_active_before = 0
    total_extra = 0

    for discord_id, rows in sorted(by_user.items()):
        active_rows = filter_active_visible_license_rows(rows)
        count = len(active_rows)
        total_active_before += count
        if count <= max_slots:
            continue
        ranked = sorted(active_rows, key=_keep_priority, reverse=True)
        keep = ranked[:max_slots]
        delete = ranked[max_slots:]
        total_extra += len(delete)
        affected.append({
            "discord_user_id": discord_id,
            "discord_user_id_masked": _mask_discord_id(discord_id),
            "active_before": count,
            "active_after": len(keep),
            "keep": [
                {
                    "key_id_masked": _mask_key_id(r.get("key_id")),
                    "masked_key": _mask_row_key(key_by_id.get(r["key_id"], {})),
                }
                for r in keep
            ],
            "delete": [
                {
                    "key_id": r["key_id"],
                    "key_id_masked": _mask_key_id(r.get("key_id")),
                    "masked_key": _mask_row_key(key_by_id.get(r["key_id"], {})),
                }
                for r in delete
            ],
        })

    return {
        "max_slots": max_slots,
        "affected_users": len(affected),
        "total_active_before": total_active_before,
        "total_extra_keys": total_extra,
        "affected": affected,
        "key_by_id": key_by_id,
    }


def _write_backup(analysis: dict[str, Any], data: dict[str, list[dict]]) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"rejoin_key_slot_cleanup_{_utc_stamp()}.json"
    delete_ids = {
        item["key_id"]
        for user in analysis["affected"]
        for item in user["delete"]
    }
    payload = {
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "max_slots": analysis["max_slots"],
        "affected_users": analysis["affected_users"],
        "deleted_key_ids": sorted(delete_ids),
        "affected": analysis["affected"],
        "keys": [
            analysis["key_by_id"][kid]
            for kid in sorted(delete_ids)
            if kid in analysis["key_by_id"]
        ],
        "bindings": [b for b in data["bindings"] if b.get("key_id") in delete_ids],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _print_summary(analysis: dict[str, Any], *, mode: str) -> None:
    print(f"\n=== Rejoin key slot cleanup ({mode}) ===")
    print(f"Max slots per user: {analysis['max_slots']}")
    print(f"Affected users: {analysis['affected_users']}")
    print(f"Total active keys (counted users): {analysis['total_active_before']}")
    print(f"Extra keys to delete: {analysis['total_extra_keys']}")
    if analysis["affected"]:
        print("\nAffected users (masked):")
        for user in analysis["affected"][:25]:
            print(
                f"  user={user['discord_user_id_masked']} "
                f"before={user['active_before']} after={user['active_after']} "
                f"delete={len(user['delete'])}"
            )
            kept = ", ".join(k["masked_key"] for k in user["keep"])
            deleted = ", ".join(k["masked_key"] for k in user["delete"])
            print(f"    keep: {kept}")
            print(f"    delete: {deleted}")
        if len(analysis["affected"]) > 25:
            print(f"  ... and {len(analysis['affected']) - 25} more")


def cmd_inspect(args: argparse.Namespace) -> int:
    store = _get_store()
    data = _load_raw_data(store._client)
    analysis = _analyze(data, args.max_slots)
    _print_summary(analysis, mode="inspect")
    max_remaining = 0
    from agent.key_stats_format import filter_active_visible_license_rows

    site_map = _build_site_maps(data["site_users"])
    binding_by_key = {b["key_id"]: b for b in data["bindings"] if b.get("key_id")}
    by_user: dict[str, list[dict]] = defaultdict(list)
    for key in data["keys"]:
        discord_id = _resolve_discord_user_id(key, site_map)
        if not discord_id:
            continue
        row = _row_for_limit(key, _binding_for(key["id"], binding_by_key))
        by_user[discord_id].append(row)
    for rows in by_user.values():
        max_remaining = max(max_remaining, len(filter_active_visible_license_rows(rows)))
    print(f"Max active keys for any user: {max_remaining}")
    return 0


def cmd_dry_run(args: argparse.Namespace) -> int:
    store = _get_store()
    data = _load_raw_data(store._client)
    analysis = _analyze(data, args.max_slots)
    _print_summary(analysis, mode="dry-run")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out_path = BACKUP_DIR / f"rejoin_key_slot_cleanup_dryrun_{_utc_stamp()}.txt"
    lines = [
        f"mode=dry-run max_slots={args.max_slots}",
        f"affected_users={analysis['affected_users']}",
        f"total_extra_keys={analysis['total_extra_keys']}",
    ]
    for user in analysis["affected"]:
        lines.append(
            f"user={user['discord_user_id_masked']} "
            f"before={user['active_before']} after={user['active_after']}"
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nDry-run output saved: {out_path}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    if args.confirm != CONFIRM_STRING:
        print(
            f"ERROR: apply requires --confirm {CONFIRM_STRING}",
            file=sys.stderr,
        )
        return 1

    store = _get_store()
    client = store._client
    data = _load_raw_data(client)
    analysis = _analyze(data, args.max_slots)
    if not analysis["total_extra_keys"]:
        print("Nothing to delete — all users are within the slot limit.")
        return 0

    backup_path = _write_backup(analysis, data)
    print(f"Backup written: {backup_path}")

    deleted = 0
    for user in analysis["affected"]:
        for item in user["delete"]:
            key_id = item["key_id"]
            try:
                client.table("device_bindings").delete().eq("key_id", key_id).execute()
            except Exception as exc:
                print(f"  WARNING: binding delete { _mask_key_id(key_id) }: {exc}", file=sys.stderr)
            try:
                client.table("license_keys").delete().eq("id", key_id).execute()
                deleted += 1
            except Exception as exc:
                print(f"  ERROR: key delete { _mask_key_id(key_id) }: {exc}", file=sys.stderr)
                return 1

    verify = _analyze(_load_raw_data(client), args.max_slots)
    _print_summary(
        {
            **verify,
            "total_active_before": analysis["total_active_before"],
            "total_extra_keys": deleted,
        },
        mode="apply",
    )
    print(f"Permanently deleted keys: {deleted}")
    if verify["affected_users"]:
        print(
            f"ERROR: {verify['affected_users']} user(s) still exceed max slots after apply.",
            file=sys.stderr,
        )
        return 1
    print("Verification passed: all users are within slot limit.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Rejoin active key slot cleanup")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_p = sub.add_parser("inspect", help="Show users exceeding the slot limit")
    inspect_p.add_argument("--max-slots", type=int, default=2)
    dry_p = sub.add_parser("dry-run", help="Show planned deletions without writing")
    dry_p.add_argument("--max-slots", type=int, default=2)
    apply_p = sub.add_parser("apply", help="Delete extra keys after backup")
    apply_p.add_argument("--max-slots", type=int, default=2)
    apply_p.add_argument("--confirm", required=True)

    args = parser.parse_args()
    if args.command == "inspect":
        return cmd_inspect(args)
    if args.command == "dry-run":
        return cmd_dry_run(args)
    if args.command == "apply":
        return cmd_apply(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
