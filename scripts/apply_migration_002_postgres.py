#!/usr/bin/env python3
"""Apply migration 002 via direct Postgres when database password is available.

Requires:
  SUPABASE_URL           — https://<project-ref>.supabase.co
  SUPABASE_DB_PASSWORD   — Postgres password from Supabase Dashboard → Database Settings

This cannot run with only the service_role JWT (that key is not the DB password).

Safe to run multiple times (ADD COLUMN IF NOT EXISTS).
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
    if not pwd:
        print(
            "[error] SUPABASE_DB_PASSWORD is not set. "
            "Copy the database password from Supabase Dashboard → Database → Database password, "
            "add SUPABASE_DB_PASSWORD to .env temporarily, run this script, then remove it.",
            file=sys.stderr,
        )
        return 2

    m = re.search(r"https://([^.]+)\.supabase\.co", url)
    if not m:
        print("[error] Could not parse project ref from SUPABASE_URL.", file=sys.stderr)
        return 2

    ref = m.group(1)
    host = f"db.{ref}.supabase.co"

    sql_path = PROJECT_ROOT / "supabase" / "migrations" / "002_key_export_support.sql"
    stmts = sql_path.read_text(encoding="utf-8")

    try:
        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
    except ImportError:
        print("[error] pip install psycopg2-binary", file=sys.stderr)
        return 2

    conn = psycopg2.connect(
        host=host,
        port=5432,
        user="postgres",
        password=pwd,
        dbname="postgres",
        connect_timeout=15,
        sslmode="require",
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with conn.cursor() as cur:
            cur.execute(stmts)
        print(f"[ok] Applied SQL from {sql_path.relative_to(PROJECT_ROOT)} on {host}")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
