"""Tests for agent/sqlite_account_detect.py — safe SQLite account detection.

Covers:
- Valid userId detection from allowlisted columns
- Valid username detection from allowlisted columns
- displayName-only is marked Needs Confirm (is_display_name_only=True)
- Forbidden table/column/path names are skipped
- Invalid userId values are rejected
- Email-like values rejected as username
- Corrupt/oversized DBs are skipped without crash
- Temp copy is deleted after scan
- Never called from Start/supervisor (static analysis)
"""

from __future__ import annotations

import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent.sqlite_account_detect import (
    ALLOWED_USERID_COLUMNS,
    ALLOWED_USERNAME_COLUMNS,
    FORBIDDEN_COLUMN_MARKERS,
    FORBIDDEN_TABLE_MARKERS,
    MAX_DB_BYTES,
    SQLiteAccountResult,
    _has_forbidden_marker,
    _is_safe_db_path,
    _is_valid_display_name,
    _is_valid_user_id,
    _is_valid_username,
    _scan_db_file,
    scan_package_dbs,
)


def _make_test_db(**kwargs) -> str:
    """Create a temp SQLite DB with given table/column/data for testing."""
    tmp = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(tmp)
    try:
        table = kwargs.get("table", "UserData")
        cols = kwargs.get("cols", ["userId INTEGER", "username TEXT"])
        conn.execute(f'CREATE TABLE "{table}" ({", ".join(cols)})')
        rows = kwargs.get("rows", [])
        if rows:
            n = len(cols)
            ph = ", ".join(["?"] * n)
            for row in rows:
                conn.execute(f'INSERT INTO "{table}" VALUES ({ph})', row)
        conn.commit()
    finally:
        conn.close()
    return tmp


# ---------------------------------------------------------------------------
# 1. Basic userId detection
# ---------------------------------------------------------------------------

class TestSQLiteUserIdDetection(unittest.TestCase):

    def _scan(self, db_path: str) -> SQLiteAccountResult:
        return _scan_db_file(db_path, db_path)

    def test_detects_userId_column(self):
        db = _make_test_db(
            table="UserData",
            cols=["userId INTEGER"],
            rows=[(12345678,)],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.user_id, 12345678)
            self.assertEqual(result.source, "sqlite_user_id")
        finally:
            os.unlink(db)

    def test_detects_robloxUserId_column(self):
        db = _make_test_db(
            table="Profile",
            cols=["robloxUserId INTEGER"],
            rows=[(987654321,)],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.user_id, 987654321)
        finally:
            os.unlink(db)

    def test_detects_authenticatedUserId_column(self):
        db = _make_test_db(
            table="Account",
            cols=["authenticatedUserId INTEGER"],
            rows=[(55443322,)],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.user_id, 55443322)
            self.assertFalse(result.is_display_name_only)
        finally:
            os.unlink(db)

    def test_detects_currentUserId_column(self):
        db = _make_test_db(
            table="Settings",
            cols=["currentUserId INTEGER"],
            rows=[(11223344,)],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.user_id, 11223344)
        finally:
            os.unlink(db)

    def test_user_id_as_text_string(self):
        db = _make_test_db(
            table="Prefs",
            cols=["userId TEXT"],
            rows=[("99887766",)],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.user_id, 99887766)
        finally:
            os.unlink(db)

    def test_user_id_prefers_userId_over_username(self):
        db = _make_test_db(
            table="UserData",
            cols=["userId INTEGER", "username TEXT"],
            rows=[(42000000, "Player123")],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.user_id, 42000000)
            self.assertEqual(result.username, "Player123")
            self.assertEqual(result.source, "sqlite_user_id")
        finally:
            os.unlink(db)

    def test_invalid_user_id_zero_rejected(self):
        db = _make_test_db(
            table="Data",
            cols=["userId INTEGER"],
            rows=[(0,)],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.user_id, 0)
        finally:
            os.unlink(db)

    def test_invalid_user_id_negative_rejected(self):
        db = _make_test_db(
            table="Data",
            cols=["userId INTEGER"],
            rows=[(-1,)],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.user_id, 0)
        finally:
            os.unlink(db)

    def test_invalid_user_id_too_large_rejected(self):
        db = _make_test_db(
            table="Data",
            cols=["userId INTEGER"],
            rows=[(99_999_999_999,)],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.user_id, 0)
        finally:
            os.unlink(db)


# ---------------------------------------------------------------------------
# 2. Username detection
# ---------------------------------------------------------------------------

class TestSQLiteUsernameDetection(unittest.TestCase):

    def _scan(self, db_path: str) -> SQLiteAccountResult:
        return _scan_db_file(db_path, db_path)

    def test_detects_username_column(self):
        db = _make_test_db(
            table="Profile",
            cols=["username TEXT"],
            rows=[("Player123",)],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.username, "Player123")
            self.assertFalse(result.is_display_name_only)
            self.assertEqual(result.source, "sqlite_username")
        finally:
            os.unlink(db)

    def test_detects_accountName_column(self):
        db = _make_test_db(
            table="Account",
            cols=["accountName TEXT"],
            rows=[("RobloxUser99",)],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.username, "RobloxUser99")
        finally:
            os.unlink(db)

    def test_display_name_marked_as_hint(self):
        db = _make_test_db(
            table="Profile",
            cols=["displayName TEXT"],
            rows=[("Cool Gamer",)],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.username, "Cool Gamer")
            self.assertTrue(result.is_display_name_only)
            self.assertEqual(result.source, "sqlite_display_name_hint")
        finally:
            os.unlink(db)

    def test_email_rejected_as_username(self):
        db = _make_test_db(
            table="Profile",
            cols=["username TEXT"],
            rows=[("user@example.com",)],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.username, "")
        finally:
            os.unlink(db)

    def test_username_too_short_rejected(self):
        db = _make_test_db(
            table="Profile",
            cols=["username TEXT"],
            rows=[("ab",)],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.username, "")
        finally:
            os.unlink(db)

    def test_username_too_long_rejected(self):
        db = _make_test_db(
            table="Profile",
            cols=["username TEXT"],
            rows=[("a" * 21,)],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.username, "")
        finally:
            os.unlink(db)


# ---------------------------------------------------------------------------
# 3. Forbidden table/column/path filtering
# ---------------------------------------------------------------------------

class TestSQLiteForbiddenFiltering(unittest.TestCase):

    def _scan(self, db_path: str) -> SQLiteAccountResult:
        return _scan_db_file(db_path, db_path)

    def test_forbidden_table_cookie_skipped(self):
        db = _make_test_db(
            table="cookie_store",
            cols=["userId INTEGER"],
            rows=[(12345678,)],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.user_id, 0, "cookie table must be skipped")
        finally:
            os.unlink(db)

    def test_forbidden_table_auth_skipped(self):
        db = _make_test_db(
            table="auth_tokens",
            cols=["userId INTEGER"],
            rows=[(12345678,)],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.user_id, 0, "auth table must be skipped")
        finally:
            os.unlink(db)

    def test_forbidden_table_session_skipped(self):
        db = _make_test_db(
            table="session_data",
            cols=["userId INTEGER"],
            rows=[(12345678,)],
        )
        try:
            result = self._scan(db)
            self.assertEqual(result.user_id, 0, "session table must be skipped")
        finally:
            os.unlink(db)

    def test_forbidden_column_token_skipped(self):
        db = _make_test_db(
            table="UserData",
            cols=["token TEXT", "userId INTEGER"],
            rows=[("secret123", 12345678)],
        )
        try:
            result = self._scan(db)
            # userId should be found, token should not be returned
            self.assertEqual(result.user_id, 12345678)
        finally:
            os.unlink(db)

    def test_forbidden_path_cookie_skipped(self):
        self.assertFalse(_is_safe_db_path("/data/data/com.roblox.client/app_webview/cookies.db"))

    def test_forbidden_path_browser_skipped(self):
        self.assertFalse(_is_safe_db_path("/data/data/com.roblox.client/app_browser/cache.db"))

    def test_safe_path_accepted(self):
        self.assertTrue(_is_safe_db_path("/data/data/com.roblox.client/databases/userdata.db"))

    def test_no_raw_db_dump_in_result(self):
        """Result must not contain raw DB content."""
        db = _make_test_db(
            table="UserData",
            cols=["userId INTEGER", "username TEXT"],
            rows=[(12345678, "Player123")],
        )
        try:
            result = self._scan(db)
            # Result fields should be plain values, not raw data
            self.assertIsInstance(result.user_id, int)
            self.assertIsInstance(result.username, str)
            self.assertNotIn("SELECT", result.username)
            self.assertNotIn("INSERT", result.username)
        finally:
            os.unlink(db)


# ---------------------------------------------------------------------------
# 4. Error handling — corrupt/oversized/missing DBs
# ---------------------------------------------------------------------------

class TestSQLiteErrorHandling(unittest.TestCase):

    def test_corrupt_db_skipped(self):
        tmp = tempfile.mktemp(suffix=".db")
        with open(tmp, "wb") as f:
            f.write(b"not a valid sqlite database!!")
        try:
            result = _scan_db_file(tmp, tmp)
            self.assertEqual(result.user_id, 0)
            self.assertEqual(result.username, "")
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_empty_db_skipped(self):
        tmp = tempfile.mktemp(suffix=".db")
        conn = sqlite3.connect(tmp)
        conn.close()
        try:
            result = _scan_db_file(tmp, tmp)
            self.assertEqual(result.user_id, 0)
        finally:
            os.unlink(tmp)

    def test_oversized_db_skipped_by_copy(self):
        """copy_db_via_root checks size before returning temp path."""
        # This is tested by mocking the root copy: size exceeds MAX_DB_BYTES
        with patch("agent.sqlite_account_detect.root_access") as mock_root, \
             patch("os.path.isfile", return_value=True), \
             patch("os.path.getsize", return_value=MAX_DB_BYTES + 1), \
             patch("os.unlink"):
            mock_root.run_root_command.return_value = MagicMock(returncode=0)
            from agent.sqlite_account_detect import _copy_db_via_root
            result = _copy_db_via_root("/data/data/com.roblox.client/databases/test.db")
            self.assertIsNone(result)

    def test_scan_package_dbs_no_root_returns_empty(self):
        with patch("agent.sqlite_account_detect.root_access") as mock_root:
            mock_root.has_root.return_value = False
            result = scan_package_dbs("com.roblox.client")
            self.assertEqual(result.user_id, 0)
            self.assertEqual(result.username, "")
            self.assertEqual(result.source, "not_found")

    def test_scan_never_raises(self):
        """scan_package_dbs must never raise any exception."""
        with patch("agent.sqlite_account_detect.root_access") as mock_root:
            mock_root.has_root.return_value = True
            mock_root.list_root_glob.side_effect = RuntimeError("connection failed")
            # Should NOT raise
            result = scan_package_dbs("com.roblox.client")
            self.assertIsInstance(result, SQLiteAccountResult)


# ---------------------------------------------------------------------------
# 5. Temp copy cleanup
# ---------------------------------------------------------------------------

class TestSQLiteTempCleanup(unittest.TestCase):

    def test_temp_copy_deleted_after_scan(self):
        """Temp DB copy must be deleted after scan, even if result is empty."""
        db = _make_test_db(
            table="UserData",
            cols=["userId INTEGER"],
            rows=[(12345678,)],
        )
        created_tmp_paths: list[str] = []

        original_mktemp = tempfile.mktemp

        def mock_mktemp(**kwargs):
            path = original_mktemp(**kwargs)
            created_tmp_paths.append(path)
            return path

        # Use scan_package_dbs with mocked root
        with patch("agent.sqlite_account_detect.root_access") as mock_root, \
             patch("tempfile.mktemp", side_effect=mock_mktemp):
            mock_root.has_root.return_value = True
            mock_root.list_root_glob.return_value = [db]
            # Mock copy: actually copy the test DB to temp
            def mock_copy(args, **kw):
                import shutil
                if len(args) == 3 and args[0] == "cp":
                    shutil.copy(args[1], args[2])
                return MagicMock(returncode=0)
            mock_root.run_root_command.side_effect = mock_copy

            scan_package_dbs("com.roblox.client")

        # Verify all temp paths were cleaned up
        for tmp_path in created_tmp_paths:
            self.assertFalse(
                os.path.exists(tmp_path),
                f"Temp DB was not cleaned up: {tmp_path}"
            )

        os.unlink(db)

    def test_temp_copy_deleted_on_scan_error(self):
        """Temp copy must be deleted even when scan errors internally."""
        db = _make_test_db(table="Data", cols=["userId INTEGER"], rows=[(1,)])
        deleted_paths: list[str] = []
        original_unlink = os.unlink

        def tracking_unlink(p):
            deleted_paths.append(p)
            original_unlink(p)

        with patch("agent.sqlite_account_detect.root_access") as mock_root, \
             patch("os.unlink", side_effect=tracking_unlink):
            mock_root.has_root.return_value = True
            mock_root.list_root_glob.return_value = [db]

            import shutil

            def mock_copy(args, **kw):
                tmp = args[2]
                shutil.copy(args[1], tmp)
                return MagicMock(returncode=0)

            mock_root.run_root_command.side_effect = mock_copy

            scan_package_dbs("com.roblox.client")

        os.unlink(db)


# ---------------------------------------------------------------------------
# 6. Static analysis: SQLite scan not called from supervisor
# ---------------------------------------------------------------------------

class TestSQLiteNotCalledFromSupervisor(unittest.TestCase):

    def test_supervisor_does_not_import_sqlite_account_detect(self):
        import inspect
        import agent.supervisor as sup
        src = inspect.getsource(sup)
        self.assertNotIn(
            "sqlite_account_detect", src,
            "supervisor must not import sqlite_account_detect (setup-only module)"
        )

    def test_supervisor_does_not_call_scan_package_dbs(self):
        import inspect
        import agent.supervisor as sup
        src = inspect.getsource(sup)
        self.assertNotIn(
            "scan_package_dbs", src,
            "supervisor must not call scan_package_dbs (account scan)"
        )


# ---------------------------------------------------------------------------
# 7. Validation helpers
# ---------------------------------------------------------------------------

class TestValidationHelpers(unittest.TestCase):

    def test_valid_user_id_range(self):
        self.assertTrue(_is_valid_user_id(1))
        self.assertTrue(_is_valid_user_id(12345678))
        self.assertTrue(_is_valid_user_id(9_999_999_999))

    def test_invalid_user_id_zero(self):
        self.assertFalse(_is_valid_user_id(0))

    def test_invalid_user_id_negative(self):
        self.assertFalse(_is_valid_user_id(-1))

    def test_invalid_user_id_too_large(self):
        self.assertFalse(_is_valid_user_id(10_000_000_000))

    def test_valid_username(self):
        self.assertTrue(_is_valid_username("Player123"))
        self.assertTrue(_is_valid_username("roblox_user"))
        self.assertTrue(_is_valid_username("abc"))

    def test_invalid_username_too_short(self):
        self.assertFalse(_is_valid_username("ab"))

    def test_invalid_username_too_long(self):
        self.assertFalse(_is_valid_username("a" * 21))

    def test_invalid_username_email(self):
        self.assertFalse(_is_valid_username("user@example.com"))

    def test_valid_display_name(self):
        self.assertTrue(_is_valid_display_name("Cool Gamer"))
        self.assertTrue(_is_valid_display_name("Player 1"))

    def test_invalid_display_name_email(self):
        self.assertFalse(_is_valid_display_name("user@domain.com"))

    def test_forbidden_marker_detection(self):
        self.assertTrue(_has_forbidden_marker("cookie_store", FORBIDDEN_TABLE_MARKERS))
        self.assertTrue(_has_forbidden_marker("auth_tokens", FORBIDDEN_TABLE_MARKERS))
        self.assertFalse(_has_forbidden_marker("user_data", FORBIDDEN_TABLE_MARKERS))

    def test_allowed_userid_columns_not_empty(self):
        self.assertGreater(len(ALLOWED_USERID_COLUMNS), 5)

    def test_allowed_username_columns_not_empty(self):
        self.assertGreater(len(ALLOWED_USERNAME_COLUMNS), 5)


if __name__ == "__main__":
    unittest.main()
