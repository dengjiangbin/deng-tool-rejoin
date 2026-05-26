#!/usr/bin/env python3
"""Apply migration 007 (license_key_limits) via direct Postgres connection.

Requires one of:
  SUPABASE_DB_PASSWORD   — Postgres database password from Supabase Dashboard → Database Settings
  SUPABASE_SERVICE_ROLE_KEY — tried as fallback (won't work on newer Supabase projects)

Usage:
    python scripts/apply_migration_007_postgres.py

Safe to run multiple times (uses CREATE TABLE IF NOT EXISTS + ON CONFLICT DO NOTHING).
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

    sql_path = PROJECT_ROOT / "supabase" / "migrations" / "007_license_key_limits.sql"
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
                conn.close()
                return 0
            finally:
                conn.close()
        except Exception as e:
            last_error = e
            print(f"[skip] {a['host']}:{a['port']} — {type(e).__name__}: {str(e)[:80]}")

    print(
        "\n[HELP] All connection attempts failed.\n"
        "Set SUPABASE_DB_PASSWORD in .env (from Supabase Dashboard → Database → Database password)\n"
        f"Last error: {last_error}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
