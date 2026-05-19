"""Safe SQLite account detection for Roblox packages.

Called ONLY from Package Setup / Refresh Account Mapping flows.
NEVER called from Start or the supervisor hot loop.

Safety rules enforced:
- DB files are copied to a DENG-owned temp path via root before reading.
- The copied DB is opened read-only (uri=True with ?mode=ro).
- The temp copy is deleted after the scan, even on error.
- Max DB file size: 8 MB (configurable).
- Max DB files scanned per package: 5.
- Max tables inspected: 20 per DB.
- Max rows read per table: 200.
- WAL/SHM companion files are ignored.
- No raw row data is logged or printed.
- Forbidden path/table/column names are skipped silently.
- Corrupt or unreadable DBs are skipped (no crash).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import root_access

_log = logging.getLogger("deng_tool_rejoin")

# ─── Safety constants ─────────────────────────────────────────────────────────

MAX_DB_BYTES: int = 8 * 1024 * 1024   # 8 MB
MAX_DB_FILES: int = 5                  # per package
MAX_TABLES: int = 20                   # per DB
MAX_ROWS: int = 200                    # per table
MIN_ROBLOX_USER_ID: int = 1
MAX_ROBLOX_USER_ID: int = 9_999_999_999

# ─── Forbidden identifiers ────────────────────────────────────────────────────

FORBIDDEN_PATH_MARKERS: frozenset[str] = frozenset({
    "cookie", "cookies", "webview", "browser",
    "token", "auth", "session", "password", "credential",
    "refresh", "bearer", "jwt", "csrf", "secret", "roblosecurity",
})

FORBIDDEN_TABLE_MARKERS: frozenset[str] = frozenset({
    "cookie", "cookies", "webview", "browser", "token",
    "auth", "session", "password", "credential", "secret",
    "roblosecurity", "refresh", "bearer", "jwt", "csrf",
    "cache",  # cookie/session cache tables
})

FORBIDDEN_COLUMN_MARKERS: frozenset[str] = frozenset({
    "cookie", "token", "password", "secret", "roblosecurity",
    "credential", "auth", "bearer", "jwt", "csrf", "refresh",
    "session", "webview", "browser",
})

# ─── Allowed columns ──────────────────────────────────────────────────────────

ALLOWED_USERID_COLUMNS: frozenset[str] = frozenset({
    "userId", "user_id", "robloxUserId", "roblox_user_id",
    "authenticatedUserId", "authenticated_user_id",
    "currentUserId", "current_user_id",
    "loggedInUserId", "logged_in_user_id",
    "activeUserId", "active_user_id",
    "accountUserId", "account_user_id",
    "playerUserId", "player_user_id",
})
# Lower-case lookup set for case-insensitive matching
_ALLOWED_USERID_LOWER: frozenset[str] = frozenset(c.lower() for c in ALLOWED_USERID_COLUMNS)

ALLOWED_USERNAME_COLUMNS: frozenset[str] = frozenset({
    "username", "user_name", "accountName", "account_name",
    "displayName", "display_name",
    "robloxUsername", "roblox_username",
    "currentUsername", "current_username",
    "loggedInUsername", "logged_in_username",
})
_ALLOWED_USERNAME_LOWER: frozenset[str] = frozenset(c.lower() for c in ALLOWED_USERNAME_COLUMNS)

# display_name columns — treated as hint only (Needs Confirm)
_DISPLAY_NAME_COLUMNS_LOWER: frozenset[str] = frozenset({
    "displayname", "display_name",
})


# ─── Result type ──────────────────────────────────────────────────────────────

@dataclass
class SQLiteAccountResult:
    """Result of scanning a package's SQLite databases."""
    user_id: int = 0
    username: str = ""
    is_display_name_only: bool = False
    source: str = "not_found"
    db_path: str = ""


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _has_forbidden_marker(name: str, markers: frozenset[str]) -> bool:
    low = name.lower()
    return any(m in low for m in markers)


def _is_safe_db_path(path: str) -> bool:
    """Return True if the path itself doesn't contain forbidden keywords."""
    basename = os.path.basename(path).lower()
    dirname = os.path.dirname(path).lower()
    for marker in FORBIDDEN_PATH_MARKERS:
        if marker in basename or marker in dirname:
            return False
    return True


def _is_valid_user_id(value: Any) -> bool:
    """Return True for a plain positive integer in the Roblox user ID range."""
    if isinstance(value, int):
        return MIN_ROBLOX_USER_ID <= value <= MAX_ROBLOX_USER_ID
    if isinstance(value, str):
        s = value.strip()
        if not s.isdigit():
            return False
        return _is_valid_user_id(int(s))
    return False


def _is_email_like(value: str) -> bool:
    return "@" in value and "." in value.split("@")[-1]


def _is_valid_username(value: str) -> bool:
    """Return True for a plausible Roblox username (3-20 alphanumeric + underscore)."""
    import re
    value = value.strip()
    if not (3 <= len(value) <= 20):
        return False
    if _is_email_like(value):
        return False
    return bool(re.match(r"^[A-Za-z0-9_]+$", value))


def _is_valid_display_name(value: str) -> bool:
    """Return True for a plausible Roblox display name (1-20 printable chars, no email)."""
    value = value.strip()
    if not (1 <= len(value) <= 20):
        return False
    if _is_email_like(value):
        return False
    # Must be printable and not contain obvious non-human tokens
    return all(c.isprintable() for c in value)


def _copy_db_via_root(db_path: str, *, timeout: int = 8) -> str | None:
    """Copy a protected DB to a temp file via root. Returns temp path or None on failure."""
    try:
        tmp = tempfile.mktemp(suffix=".db", prefix="deng_sqlite_")
        result = root_access.run_root_command(
            ["cp", db_path, tmp], timeout=timeout
        )
        if result.returncode != 0 or not os.path.isfile(tmp):
            return None
        # Ensure we own the copy and it's not too large
        size = os.path.getsize(tmp)
        if size > MAX_DB_BYTES:
            _log.debug("sqlite_detect: DB too large (%d bytes), skipping %s", size, db_path)
            os.unlink(tmp)
            return None
        return tmp
    except Exception as exc:  # noqa: BLE001
        _log.debug("sqlite_detect: copy failed for %s: %s", db_path, exc)
        return None


def _scan_db_file(tmp_path: str, original_path: str) -> SQLiteAccountResult:
    """Scan a single copied DB for account identifiers. Returns best result found."""
    best = SQLiteAccountResult()
    try:
        uri = f"file:{tmp_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=3.0)
        try:
            cur = conn.cursor()
            # Get table list
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT ?", (MAX_TABLES + 5,))
            tables = [row[0] for row in cur.fetchall() if isinstance(row[0], str)]

            tables_scanned = 0
            for table_name in tables:
                if tables_scanned >= MAX_TABLES:
                    break
                if _has_forbidden_marker(table_name, FORBIDDEN_TABLE_MARKERS):
                    continue
                tables_scanned += 1

                # Get column info
                try:
                    cur.execute(f"PRAGMA table_info('{table_name}')")  # noqa: S608
                    cols_info = cur.fetchall()
                except Exception:  # noqa: BLE001
                    continue

                # Identify allowed columns
                uid_cols: list[str] = []
                uname_cols: list[str] = []
                for col in cols_info:
                    cname = str(col[1]) if len(col) > 1 else ""
                    if not cname:
                        continue
                    # Check type — skip blob columns regardless of name
                    ctype = str(col[2]).upper() if len(col) > 2 else ""
                    if "BLOB" in ctype:
                        continue
                    clow = cname.lower()
                    # Explicitly allowlisted columns bypass the forbidden marker check.
                    # This handles names like "authenticatedUserId" which contain "auth"
                    # but are defined as safe public numeric-ID columns.
                    if clow in _ALLOWED_USERID_LOWER:
                        uid_cols.append(cname)
                    elif clow in _ALLOWED_USERNAME_LOWER:
                        uname_cols.append(cname)
                    elif _has_forbidden_marker(cname, FORBIDDEN_COLUMN_MARKERS):
                        # Not in any allowlist AND contains a forbidden marker → skip
                        continue

                if not uid_cols and not uname_cols:
                    continue

                # Query only the columns we identified
                select_cols = uid_cols + uname_cols
                safe_cols = ", ".join(f'"{c}"' for c in select_cols)
                try:
                    cur.execute(
                        f'SELECT {safe_cols} FROM "{table_name}" LIMIT {MAX_ROWS}'  # noqa: S608
                    )
                    rows = cur.fetchall()
                except Exception:  # noqa: BLE001
                    continue

                for row in rows:
                    values = dict(zip(select_cols, row))
                    # Try userId columns first
                    for ucol in uid_cols:
                        val = values.get(ucol)
                        if val is not None and _is_valid_user_id(val):
                            uid = int(val) if isinstance(val, str) else val
                            best.user_id = uid
                            best.source = "sqlite_user_id"
                            best.db_path = original_path
                            # Also grab username from same row if available
                            for nc in uname_cols:
                                nv = str(values.get(nc) or "").strip()
                                nlow = nc.lower()
                                if nv and _is_valid_username(nv) and nlow not in _DISPLAY_NAME_COLUMNS_LOWER:
                                    best.username = nv
                                    break
                            return best  # userId found → done

                    # No userId — try username columns
                    for nc in uname_cols:
                        nv = str(values.get(nc) or "").strip()
                        if not nv:
                            continue
                        nlow = nc.lower()
                        if nlow in _DISPLAY_NAME_COLUMNS_LOWER:
                            # Display name is a hint only
                            if not best.username and _is_valid_display_name(nv):
                                best.username = nv
                                best.is_display_name_only = True
                                best.source = "sqlite_display_name_hint"
                                best.db_path = original_path
                        elif _is_valid_username(nv) and not best.user_id:
                            best.username = nv
                            best.is_display_name_only = False
                            best.source = "sqlite_username"
                            best.db_path = original_path
        finally:
            conn.close()
    except (sqlite3.DatabaseError, sqlite3.OperationalError) as exc:
        _log.debug("sqlite_detect: DB parse error for %s: %s", original_path, exc)
    except Exception as exc:  # noqa: BLE001
        _log.debug("sqlite_detect: unexpected error for %s: %s", original_path, exc)
    return best


def scan_package_dbs(
    package: str,
    *,
    max_bytes: int = MAX_DB_BYTES,
    max_files: int = MAX_DB_FILES,
    scan_timeout: int = 10,
) -> SQLiteAccountResult:
    """Scan SQLite databases under a package's data directory for account info.

    Safety:
    - Only runs if root is available.
    - Copies each DB to temp before reading.
    - Deletes temp copies after scan.
    - Never called from Start/supervisor hot loop.

    Returns the best :class:`SQLiteAccountResult` found (user_id preferred
    over username, which is preferred over display_name hint).
    """
    if not root_access.has_root():
        _log.debug("sqlite_detect: root not available, skipping for %s", package)
        return SQLiteAccountResult()

    data_dir = f"/data/data/{package}"
    # List .db files only (exclude .db-wal, .db-shm)
    try:
        paths = root_access.list_root_glob(
            f"{data_dir}/**/*.db",
            timeout=scan_timeout,
            max_results=max_files * 4,  # over-fetch then filter
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("sqlite_detect: glob failed for %s: %s", package, exc)
        return SQLiteAccountResult()

    # Filter: only plain .db files, no WAL/SHM, no forbidden paths
    safe_paths: list[str] = []
    for p in paths:
        if not p.endswith(".db"):
            continue
        if not _is_safe_db_path(p):
            continue
        safe_paths.append(p)
        if len(safe_paths) >= max_files:
            break

    best = SQLiteAccountResult()

    for db_path in safe_paths:
        tmp_path: str | None = None
        try:
            tmp_path = _copy_db_via_root(db_path, timeout=scan_timeout)
            if not tmp_path:
                continue
            result = _scan_db_file(tmp_path, db_path)
            # Rank: user_id > username > display_name_hint > nothing
            if result.user_id > 0:
                return result  # immediate best
            if result.username and not result.is_display_name_only and not best.username:
                best = result
            elif result.username and result.is_display_name_only and not best.username:
                best = result
        except Exception as exc:  # noqa: BLE001
            _log.debug("sqlite_detect: error processing %s: %s", db_path, exc)
        finally:
            if tmp_path and os.path.isfile(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    return best
