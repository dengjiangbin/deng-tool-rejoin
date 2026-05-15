"""DENG Tool: Rejoin — Supabase connection test.

Usage:
    python -m bot.check_supabase

Verifies:
- SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are present
- Supabase client connects successfully
- All required tables exist and are readable
- A harmless test row can be inserted / read / deleted in admin_audit_logs

Security:
- Never prints secrets or credentials
- Never stores or returns full license keys
"""

from __future__ import annotations

import os
import sys
import uuid

REQUIRED_TABLES = [
    "license_users",
    "license_keys",
    "device_bindings",
    "hwid_reset_logs",
    "license_check_logs",
    "license_panel_config",
    "admin_audit_logs",
    "web_accounts",
    "web_sessions",
    "discord_identities",
]

MIGRATION_PATH = "supabase/migrations/001_license_system.sql"


def _ok(msg: str) -> None:
    print(f"  OK    {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL  {msg}")


def _warn(msg: str) -> None:
    print(f"  WARN  {msg}")


def _miss(msg: str) -> None:
    print(f"  MISS  {msg}")


def check() -> int:
    from dotenv import load_dotenv

    load_dotenv()

    print()
    print("=" * 60)
    print("  DENG Tool Rejoin — Supabase Connection Test")
    print("=" * 60)

    # ── 1. Environment variables ─────────────────────────────────────────────
    print("\n[1] Environment variables")
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    ok = True

    if url:
        _ok(f"SUPABASE_URL present  (length={len(url)})")
    else:
        _fail("SUPABASE_URL is not set")
        ok = False

    if key:
        _ok(f"SUPABASE_SERVICE_ROLE_KEY present  (length={len(key)})")
        # Warn if this looks like a publishable/anon key rather than service_role JWT
        if key.startswith("sb_publishable_") or key.startswith("sb_anon_"):
            _warn(
                "Key prefix suggests this may be an anon/publishable key, "
                "not the service_role key."
            )
            _warn(
                "Go to Supabase Dashboard → Settings → API → "
                "'service_role' (secret) key and use that value."
            )
    else:
        _fail("SUPABASE_SERVICE_ROLE_KEY is not set")
        ok = False

    if not ok:
        return 1

    # ── 2. Create client ─────────────────────────────────────────────────────
    print("\n[2] Supabase client")
    try:
        from supabase import create_client
    except ImportError:
        _fail("supabase-py not installed. Run: pip install supabase")
        return 1

    try:
        client = create_client(url, key)
        _ok("Client created")
    except Exception as exc:
        _fail(f"Could not create Supabase client: {exc}")
        return 1

    # ── 3. Table checks ──────────────────────────────────────────────────────
    print("\n[3] Required tables")
    missing: list[str] = []
    for table in REQUIRED_TABLES:
        try:
            res = client.table(table).select("*", count="exact").limit(0).execute()
            count = res.count if res.count is not None else "?"
            _ok(f"'{table}'  (rows ≈ {count})")
        except Exception as exc:
            err = str(exc)
            # PGRST205 = table not in PostgREST schema cache (i.e. doesn't exist)
            if (
                "PGRST205" in err
                or "does not exist" in err
                or "relation" in err.lower()
                or "42P01" in err
                or "undefined_table" in err.lower()
                or "schema cache" in err.lower()
            ):
                _miss(f"'{table}' MISSING — not in database schema")
                missing.append(table)
            else:
                _warn(f"'{table}' query error: {err}")

    if missing:
        print()
        _fail(f"Missing tables: {missing}")
        print(
            f"\n  → Paste the contents of  {MIGRATION_PATH}\n"
            "    into Supabase Dashboard → SQL Editor → Run"
        )
        return 1

    # ── 4. Write test ────────────────────────────────────────────────────────
    print("\n[4] Insert / read / delete (admin_audit_logs)")
    marker = f"check-{uuid.uuid4().hex[:10]}"
    inserted_id: str | None = None
    try:
        ins = client.table("admin_audit_logs").insert(
            {
                "actor_discord_id": "check-bot",
                "action": "supabase_connection_test",
                "target_type": "test",
                "target_id": marker,
                "metadata": {"automated": True},
            }
        ).execute()
        if ins.data:
            inserted_id = ins.data[0].get("id")
        _ok("Insert succeeded")
    except Exception as exc:
        _warn(f"Insert failed: {exc}")
        _warn(
            "This typically means SUPABASE_SERVICE_ROLE_KEY is not "
            "the service_role key (RLS blocks anon writes)."
        )

    if inserted_id:
        try:
            r = client.table("admin_audit_logs").select("id").eq("id", inserted_id).execute()
            if r.data:
                _ok("Read succeeded")
            else:
                _warn("Inserted row not readable — check RLS policies")
        except Exception as exc:
            _warn(f"Read failed: {exc}")

        try:
            client.table("admin_audit_logs").delete().eq("id", inserted_id).execute()
            _ok("Delete succeeded (test row cleaned up)")
        except Exception as exc:
            _warn(f"Delete failed: {exc}")

    if not missing:
        print("\n[5] Optional: license_keys export columns (002_key_export_support)")
        try:
            client.table("license_keys").select("key_ciphertext,key_export_available").limit(
                0
            ).execute()
            _ok("license_keys export columns present (DB ready for optional full-key export)")
        except Exception as exc:
            err = str(exc)
            if (
                "PGRST204" in err
                or "42703" in err
                or "column" in err.lower()
                or "undefined_column" in err.lower()
                or "schema cache" in err.lower()
            ):
                _warn(
                    "Export columns not found — apply supabase/migrations/002_key_export_support.sql "
                    "to enable full-key export for newly generated keys."
                )
                _warn("Key Stats and Download still work; older keys remain masked-only.")
            else:
                _warn(f"Could not verify export columns: {exc}")

    # ── Done ─────────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    if missing:
        print("  RESULT: FAIL — missing tables (see above)")
        return 1
    print("  RESULT: OK — Supabase connection verified")
    print("=" * 60)
    print()
    return 0


def main() -> None:
    sys.exit(check())


if __name__ == "__main__":
    main()
