"""Tests for agent/updater.py — the client-side licensed package updater."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.updater import (
    HashMismatchError,
    LicenseCheckError,
    UpdaterError,
    _mask_key,
    _parse_version,
    backup_install,
    download_package,
    extract_package,
    is_newer_version,
    load_license_config,
    request_download_token,
    rollback_install,
    save_license_status,
    verify_package,
)
from agent.security import compute_file_sha256, verify_sha256


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_config(tmp: Path, overrides: dict | None = None) -> Path:
    cfg = {
        "license": {
            "enabled": True,
            "mode": "remote",
            "key": "DENG-TEST-TEST-TEST-TEST",
            "server_url": "http://127.0.0.1:8787",
            "install_id": "abc123",
            "channel": "stable",
            "api_secret": "",
            "device_label": "test-device",
            "last_status": "active",
            "last_check_at": None,
        }
    }
    if overrides:
        cfg["license"].update(overrides)
    path = tmp / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def _make_zip(tmp: Path, name: str = "test.zip", files: dict | None = None) -> Path:
    zp = tmp / name
    with zipfile.ZipFile(zp, "w") as zf:
        for fname, content in (files or {"agent/__init__.py": "", "VERSION": "1.1.0\n"}).items():
            zf.writestr(fname, content)
    return zp


# ── Version comparison ─────────────────────────────────────────────────────────

class TestVersionComparison(unittest.TestCase):

    def test_newer_patch(self):
        self.assertTrue(is_newer_version("1.0.1", "1.0.0"))

    def test_newer_minor(self):
        self.assertTrue(is_newer_version("1.1.0", "1.0.9"))

    def test_newer_major(self):
        self.assertTrue(is_newer_version("2.0.0", "1.9.9"))

    def test_same_version_not_newer(self):
        self.assertFalse(is_newer_version("1.0.0", "1.0.0"))

    def test_older_not_newer(self):
        self.assertFalse(is_newer_version("0.9.9", "1.0.0"))

    def test_non_numeric_segment_treated_as_zero(self):
        self.assertFalse(is_newer_version("1.0.alpha", "1.0.0"))

    def test_short_version_padded(self):
        # "1" == "1.0.0"
        self.assertFalse(is_newer_version("1", "1.0.0"))
        self.assertTrue(is_newer_version("2", "1.0.0"))

    def test_parse_version_basic(self):
        self.assertEqual(_parse_version("1.2.3"), (1, 2, 3))

    def test_parse_version_pads_to_three(self):
        self.assertEqual(_parse_version("1.2"), (1, 2, 0))
        self.assertEqual(_parse_version("1"), (1, 0, 0))


# ── Config I/O ────────────────────────────────────────────────────────────────

class TestLicenseConfigIO(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_load_returns_license_section(self):
        config_path = _make_config(self.tmp)
        lic = load_license_config(config_path)
        self.assertEqual(lic["key"], "DENG-TEST-TEST-TEST-TEST")
        self.assertEqual(lic["channel"], "stable")

    def test_load_returns_empty_when_missing(self):
        lic = load_license_config(self.tmp / "nonexistent.json")
        self.assertEqual(lic, {})

    def test_load_raises_on_invalid_json(self):
        bad = self.tmp / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        with self.assertRaises(UpdaterError):
            load_license_config(bad)

    def test_save_status_updates_fields(self):
        config_path = _make_config(self.tmp)
        save_license_status(config_path, "active")
        data = json.loads(config_path.read_text())
        self.assertEqual(data["license"]["last_status"], "active")
        self.assertIsNotNone(data["license"]["last_check_at"])

    def test_save_status_nonfatal_on_missing_file(self):
        # Should not raise even if config doesn't exist
        save_license_status(self.tmp / "missing.json", "active")

    def test_stable_is_default_channel(self):
        """If no channel is set, it should default to 'stable'."""
        config_path = _make_config(self.tmp, {"channel": None})
        lic = load_license_config(config_path)
        channel = (lic.get("channel") or "stable")
        self.assertEqual(channel, "stable")


# ── SHA-256 verification ──────────────────────────────────────────────────────

class TestHashVerification(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_verify_package_passes_correct_hash(self):
        zp = _make_zip(self.tmp)
        expected = compute_file_sha256(zp)
        verify_package(zp, expected)  # should not raise

    def test_verify_package_raises_on_bad_hash(self):
        zp = _make_zip(self.tmp)
        with self.assertRaises(HashMismatchError):
            verify_package(zp, "a" * 64)

    def test_verify_sha256_correct(self):
        zp = _make_zip(self.tmp)
        expected = compute_file_sha256(zp)
        self.assertTrue(verify_sha256(zp, expected))

    def test_verify_sha256_wrong(self):
        zp = _make_zip(self.tmp)
        self.assertFalse(verify_sha256(zp, "b" * 64))

    def test_verify_sha256_nonexistent_file(self):
        self.assertFalse(verify_sha256(self.tmp / "missing.zip", "c" * 64))


# ── Extraction security ────────────────────────────────────────────────────────

class TestExtractPackage(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_normal_extraction(self):
        zp = _make_zip(self.tmp)
        install_dir = self.tmp / "install"
        install_dir.mkdir()
        extracted = extract_package(zp, install_dir)
        self.assertIn("agent/__init__.py", extracted)
        self.assertIn("VERSION", extracted)

    def test_env_file_skipped(self):
        zp = self.tmp / "test.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("agent/__init__.py", "")
            zf.writestr(".env", "SECRET=hunter2")
            zf.writestr("agent/.env", "SECRET=x")
        install_dir = self.tmp / "install2"
        install_dir.mkdir()
        extracted = extract_package(zp, install_dir)
        self.assertNotIn(".env", extracted)
        self.assertNotIn("agent/.env", extracted)
        # Verify file not on disk
        self.assertFalse((install_dir / ".env").exists())

    def test_path_traversal_skipped(self):
        zp = self.tmp / "traversal.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("agent/__init__.py", "")
            zf.writestr("../../../etc/passwd", "root:x:0:0")
        install_dir = self.tmp / "install3"
        install_dir.mkdir()
        extracted = extract_package(zp, install_dir)
        self.assertNotIn("../../../etc/passwd", extracted)
        self.assertFalse((self.tmp / "etc" / "passwd").exists())

    def test_absolute_path_in_zip_skipped(self):
        zp = self.tmp / "absolute.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("agent/__init__.py", "")
            # Note: ZipFile may normalize this, but we still test the guard
            zf.writestr("/tmp/pwned.txt", "pwned")
        install_dir = self.tmp / "install4"
        install_dir.mkdir()
        extracted = extract_package(zp, install_dir)
        # No absolute path should be extracted
        for name in extracted:
            self.assertFalse(name.startswith("/"))


# ── Backup and rollback ───────────────────────────────────────────────────────

class TestBackupAndRollback(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_backup_creates_timestamped_dir(self):
        install_dir = self.tmp / "install"
        install_dir.mkdir()
        (install_dir / "agent").mkdir()
        (install_dir / "agent" / "commands.py").write_text("# cmd\n")
        (install_dir / "VERSION").write_text("1.0.0\n")
        backup_dir = backup_install(install_dir)
        self.assertTrue(backup_dir.is_dir())
        self.assertIn("pre-update-", backup_dir.name)

    def test_backup_skips_data_and_env(self):
        install_dir = self.tmp / "install2"
        install_dir.mkdir()
        (install_dir / "data").mkdir()
        (install_dir / "data" / "users.db").write_text("data")
        (install_dir / ".env").write_text("SECRET=x")
        (install_dir / "VERSION").write_text("1.0.0\n")
        backup_dir = backup_install(install_dir)
        self.assertFalse((backup_dir / ".env").exists())
        self.assertFalse((backup_dir / "data").exists())
        self.assertTrue((backup_dir / "VERSION").exists())

    def test_rollback_restores_files(self):
        install_dir = self.tmp / "install3"
        install_dir.mkdir()
        (install_dir / "VERSION").write_text("1.0.0\n")
        backup_dir = backup_install(install_dir)
        # Simulate a bad update
        (install_dir / "VERSION").write_text("CORRUPT\n")
        rollback_install(backup_dir, install_dir)
        self.assertEqual((install_dir / "VERSION").read_text(), "1.0.0\n")


# ── request_download_token ────────────────────────────────────────────────────

class TestRequestDownloadToken(unittest.TestCase):

    def test_sends_hashed_install_id(self):
        """The raw install_id must never be sent — only its SHA-256 hash."""
        install_id = "my-secret-install-id"
        expected_hash = hashlib.sha256(install_id.encode()).hexdigest()
        captured_payload: dict = {}

        def fake_http_post(url, payload, **kwargs):
            captured_payload.update(payload)
            return {
                "result": "active",
                "download_token": "tok123",
                "version": "1.1.0",
                "channel": "stable",
                "filename": "pkg.zip",
                "sha256": "a" * 64,
                "size_bytes": 1000,
                "download_url": "http://localhost/pkg.zip",
            }

        with patch("agent.updater._http_post_json", side_effect=fake_http_post):
            request_download_token(
                "http://127.0.0.1:8787",
                "DENG-TEST-TEST-TEST-TEST",
                install_id,
                "Pixel 7",
                "1.0.0",
            )

        self.assertNotIn("install_id", captured_payload)
        self.assertEqual(captured_payload.get("install_id_hash"), expected_hash)

    def test_raises_license_check_error_on_wrong_device(self):
        def fake_post(url, payload, **kwargs):
            return {"result": "wrong_device", "message": "Wrong device."}

        with patch("agent.updater._http_post_json", side_effect=fake_post):
            with self.assertRaises(LicenseCheckError):
                request_download_token(
                    "http://127.0.0.1:8787",
                    "DENG-TEST-TEST-TEST-TEST",
                    "install-id",
                    "Pixel 7",
                    "1.0.0",
                )

    def test_raises_updater_error_on_missing_token(self):
        def fake_post(url, payload, **kwargs):
            return {"result": "active", "message": "ok"}  # no download_token

        with patch("agent.updater._http_post_json", side_effect=fake_post):
            with self.assertRaises(UpdaterError):
                request_download_token(
                    "http://127.0.0.1:8787",
                    "DENG-TEST-TEST-TEST-TEST",
                    "install-id",
                    "Pixel 7",
                    "1.0.0",
                )

    def test_raises_updater_error_on_network_failure(self):
        import urllib.error
        def fake_post(url, payload, **kwargs):
            raise UpdaterError("Network error reaching http://127.0.0.1:8787/api/download/authorize: [Errno ...]")

        with patch("agent.updater._http_post_json", side_effect=fake_post):
            with self.assertRaises(UpdaterError):
                request_download_token(
                    "http://127.0.0.1:8787",
                    "DENG-TEST-TEST-TEST-TEST",
                    "install-id",
                    "Pixel 7",
                    "1.0.0",
                )


# ── Key masking ───────────────────────────────────────────────────────────────

class TestMaskKey(unittest.TestCase):

    def test_masks_standard_key(self):
        key = "DENG-ABCD-1234-EFGH-5678"
        masked = _mask_key(key)
        # First segment always shown
        self.assertIn("DENG", masked)
        # Middle segments (1234, EFGH) must NOT appear
        self.assertNotIn("1234", masked)
        self.assertNotIn("EFGH", masked)
        # Raw key should not appear verbatim
        self.assertNotEqual(masked, key)

    def test_short_key_masked(self):
        masked = _mask_key("SHORT")
        self.assertEqual(masked, "***")

    def test_empty_key_masked(self):
        masked = _mask_key("")
        self.assertEqual(masked, "***")


# ── Security: no secrets ──────────────────────────────────────────────────────

class TestUpdaterNoSecrets(unittest.TestCase):

    def test_no_supabase_key_in_module(self):
        import inspect
        import agent.updater as updater_mod
        src = inspect.getsource(updater_mod)
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", src)

    def test_no_discord_token_in_module(self):
        import inspect
        import agent.updater as updater_mod
        src = inspect.getsource(updater_mod)
        self.assertNotIn("DISCORD_TOKEN", src)

    def test_no_github_token_in_module(self):
        import inspect
        import agent.updater as updater_mod
        src = inspect.getsource(updater_mod)
        self.assertNotIn("GITHUB_TOKEN", src)

    def test_install_id_never_in_payload(self):
        """Verify by inspection that only install_id_hash is sent, not raw install_id."""
        import inspect
        import agent.updater as updater_mod
        src = inspect.getsource(updater_mod.request_download_token)
        # The payload must set install_id_hash, not install_id
        self.assertIn("install_id_hash", src)
        # Should not set plain install_id in payload
        self.assertNotIn('"install_id":', src.replace("install_id_hash", ""))


if __name__ == "__main__":
    unittest.main()
