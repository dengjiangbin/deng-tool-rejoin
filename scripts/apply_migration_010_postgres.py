#!/usr/bin/env python3
"""Apply migration 010 (monitor_device_bridge_status) via direct Postgres.

Adds monitor_devices.last_bridge_status (JSONB) so the backend can store
scrubbed Termux-bridge snapshot/upload diagnostics for the APK.

Requires one of:
  SUPABASE_DB_PASSWORD      — Postgres database password (Supabase Dashboard →
                              Project Settings → Database → Database password)
  SUPABASE_SERVICE_ROLE_KEY — tried as a fallback (won't work on newer projects)

Usage:
    python scripts/apply_migration_010_postgres.py

Idempotent: the migration uses ADD COLUMN IF NOT EXISTS, so it is safe to run
multiple times. Verifies the column exists afterwards.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    import os
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    pwd = (os.environ.get("SUPABASE_DB_PASSWORD") or "").strip()
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()

    m = re.search(r"https://([^.]+)\.supabase\.co", url)
    if not m:
        print("[error] Could not parse project ref from SUPABASE_URL.", file=sys.stderr)
        return 2
    ref = m.group(1)

    sql_path = PROJECT_ROOT / "supabase" / "migrations" / "010_monitor_device_bridge_status.sql"
    stmts = sql_path.read_text(encoding="utf-8")

    try:
        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
    except ImportError:
        print("[error] pip install psycopg2-binary", file=sys.stderr)
        return 2

    attempts = [
        {"host": f"db.{ref}.supabase.co", "port": 5432, "user": "postgres", "password": pwd or key},
        {"host": "aws-0-ap-southeast-1.pooler.supabase.com", "port": 6543, "user": f"postgres.{ref}", "password": pwd or key},
        {"host": "aws-0-ap-southeast-1.pooler.supabase.com", "port": 5432, "user": f"postgres.{ref}", "password": pwd or key},
        {"host": "aws-0-us-east-1.pooler.supabase.com", "port": 6543, "user": f"postgres.{ref}", "password": pwd or key},
        {"host": "aws-0-eu-west-1.pooler.supabase.com", "port": 6543, "user": f"postgres.{ref}", "password": pwd or key},
    ]

    last_error = None
    for a in attempts:
        if not a["password"]:
            continue
        try:
            conn = psycopg2.connect(
                host=a["host"],
                port=a["port"],
                user=a["user"],
                password=a["password"],
                dbname="postgres",
                connect_timeout=10,
                sslmode="require",
            )
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            try:
                with conn.cursor() as cur:
                    cur.execute(stmts)
                    cur.execute(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'monitor_devices' "
                        "AND column_name = 'last_bridge_status'"
                    )
                    ok = cur.fetchone() is not None
                print(f"[ok] Applied {sql_path.name} via {a['host']}:{a['port']}")
                print(f"[verify] last_bridge_status column present: {ok}")
                return 0 if ok else 1
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            last_error = e
            print(f"[skip] {a['host']}:{a['port']} — {type(e).__name__}: {str(e)[:90]}")

    print(
        "\n[HELP] All connection attempts failed.\n"
        "Set SUPABASE_DB_PASSWORD in .env (Supabase Dashboard → Project Settings →\n"
        "Database → Database password), then re-run this script. Alternatively paste\n"
        "the migration SQL into the Supabase Dashboard → SQL Editor.\n"
        f"Last error: {last_error}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
