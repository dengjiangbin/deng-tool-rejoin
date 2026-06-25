#!/usr/bin/env python3
"""Apply migration 012 (48h key expiry + legacy window) via direct Postgres.

Requires:
  SUPABASE_DB_PASSWORD   — Postgres password (Supabase Dashboard → Database)

Usage:
    python scripts/apply_migration_012_postgres.py

Idempotent + non-destructive: uses ADD COLUMN IF NOT EXISTS, a backup snapshot
table populated with ON CONFLICT DO NOTHING, and CREATE INDEX IF NOT EXISTS.
Safe to run multiple times. It NEVER deletes keys or resets legacy timers.
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

    sql_path = PROJECT_ROOT / "supabase" / "migrations" / "012_license_48h_expiry.sql"
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
                print(f"[ok] Applied {sql_path.name} via {a['host']}:{a['port']}")
                return 0
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            last_error = e
            print(f"[skip] {a['host']}:{a['port']} — {type(e).__name__}: {str(e)[:80]}")

    print(
        "\n[HELP] All connection attempts failed.\n"
        "Set SUPABASE_DB_PASSWORD in .env (Supabase Dashboard → Database → Database password)\n"
        f"Last error: {last_error}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
