"""SQLite storage for local events, attempts, heartbeats, and state."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS config(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  level TEXT NOT NULL,
  type TEXT NOT NULL,
  message TEXT NOT NULL,
  meta_json TEXT
);

CREATE TABLE IF NOT EXISTS rejoin_attempts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  reason TEXT NOT NULL,
  package TEXT NOT NULL,
  launch_mode TEXT NOT NULL,
  masked_launch_url TEXT,
  root_used INTEGER NOT NULL,
  success INTEGER NOT NULL,
  error TEXT
);

CREATE TABLE IF NOT EXISTS heartbeats(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  status TEXT NOT NULL,
  meta_json TEXT
);

CREATE TABLE IF NOT EXISTS agent_state(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def upsert_config(config: dict[str, Any], db_path: Path = DB_PATH) -> None:
    init_db(db_path)
    now = utc_now()
    with connect(db_path) as conn:
        for key, value in config.items():
            conn.execute(
                "INSERT INTO config(key, value, updated_at) VALUES(?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value, sort_keys=True), now),
            )
        conn.commit()


def load_config_from_db(db_path: Path = DB_PATH) -> dict[str, Any]:
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute("SELECT key, value FROM config").fetchall()
    result: dict[str, Any] = {}
    for row in rows:
        try:
            result[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            result[row["key"]] = row["value"]
    return result


def insert_event(level: str, event_type: str, message: str, meta: dict[str, Any] | None = None, db_path: Path = DB_PATH) -> None:
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO events(ts, level, type, message, meta_json) VALUES(?, ?, ?, ?, ?)",
            (utc_now(), level.upper(), event_type, message, json.dumps(meta or {}, sort_keys=True)),
        )
        conn.commit()


def insert_rejoin_attempt(
    *,
    reason: str,
    package: str,
    launch_mode: str,
    masked_launch_url: str | None,
    root_used: bool,
    success: bool,
    error: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO rejoin_attempts(ts, reason, package, launch_mode, masked_launch_url, root_used, success, error) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (utc_now(), reason, package, launch_mode, masked_launch_url, int(root_used), int(success), error),
        )
        conn.commit()


def insert_heartbeat(status: str, meta: dict[str, Any] | None = None, db_path: Path = DB_PATH) -> None:
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO heartbeats(ts, status, meta_json) VALUES(?, ?, ?)",
            (utc_now(), status, json.dumps(meta or {}, sort_keys=True)),
        )
        conn.commit()


def set_agent_state(key: str, value: Any, db_path: Path = DB_PATH) -> None:
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO agent_state(key, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value, sort_keys=True), utc_now()),
        )
        conn.commit()


def get_agent_state(key: str, db_path: Path = DB_PATH) -> Any:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT value FROM agent_state WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return row["value"]


def latest_row(table: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    if table not in {"events", "rejoin_attempts", "heartbeats"}:
        raise ValueError("unsupported latest_row table")
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None
