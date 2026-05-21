"""Tests for agent/package_key.py — package key helper module.

Covers:
  1.  mask_package_key hides full key.
  2.  mask_package_key: FREE_ key shows prefix + last 4 chars.
  3.  mask_package_key: short suffix shows FREE_****.
  4.  mask_package_key: empty key returns empty.
  5.  mask_package_key: non-FREE_ key shows first 4 + ****.
  6.  package_key_license_path returns correct path.
  7.  package_key_license_path: Internals is case-sensitive exact match.
  8.  package_key_license_path: invalid package name raises ValueError.
  9.  is_valid_package_key: FREE_ prefix → True.
  10. is_valid_package_key: lowercase free_ → False.
  11. is_valid_package_key: empty → False.
  12. resolve_package_key: per-package overrides global.
  13. resolve_package_key: falls back to global when per-package absent.
  14. resolve_package_key: returns None when no key configured.
  15. resolve_package_key: returns None on invalid package name.
  16. write_package_key_file: valid key writes correctly via Python I/O.
  17. write_package_key_file: empty key returns error without writing.
  18. write_package_key_file: non-FREE_ key returns error without writing.
  19. ensure_package_key_for_start: no configured key → method=skipped, success=True.
  20. ensure_package_key_for_start: file missing → writes file, write_needed=True.
  21. ensure_package_key_for_start: file already correct → method=already_correct, write_needed=False.
  22. ensure_package_key_for_start: file has wrong key → rewrites, write_needed=True.
  23. ensure_package_key_for_start: never logs or returns full key.
  24. package_key does NOT import or call license.py.
  25. DENG Tool license system untouched by write_package_key_file.
"""
from __future__ import annotations

import os
import tempfile
import unittest
import unittest.mock
from typing import Any


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_config(
    global_key: str = "",
    per_package: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "package_keys": {
            "global": global_key,
            "per_package": per_package or {},
        }
    }


_PKG = "com.roblox.client"
_KEY = "FREE_TESTKEY1234"
_KEY_SHORT = "FREE_AB"


# ── 1–5: mask_package_key ─────────────────────────────────────────────────────

class MaskPackageKeyTests(unittest.TestCase):

    def setUp(self):
        from agent.package_key import mask_package_key
        self.fn = mask_package_key

    def test_hides_full_key(self):
        result = self.fn("FREE_ABCDEFGH1234")
        self.assertNotIn("ABCDEFGH", result)
        self.assertNotIn("FREE_ABCDEFGH1234", result)

    def test_free_key_shows_prefix_and_last_four(self):
        result = self.fn("FREE_ABCDEFGH1234")
        self.assertTrue(result.startswith("FREE_..."))
        self.assertIn("1234", result)
        self.assertNotIn("5", result[8:])  # suffix only last 4

    def test_short_suffix_shows_asterisks(self):
        result = self.fn("FREE_AB")
        self.assertEqual(result, "FREE_****")

    def test_empty_key_returns_empty(self):
        result = self.fn("")
        self.assertEqual(result, "")

    def test_non_free_prefix_shows_first_four_stars(self):
        result = self.fn("PAID_SECRETKEY")
        self.assertEqual(result, "PAID****")
        self.assertNotIn("SECRET", result)


# ── 6–8: package_key_license_path ────────────────────────────────────────────

class PackageKeyLicensePathTests(unittest.TestCase):

    def setUp(self):
        from agent.package_key import package_key_license_path
        self.fn = package_key_license_path

    def test_returns_correct_path(self):
        path = self.fn("com.roblox.client")
        expected = (
            "/storage/emulated/0/Android/data/com.roblox.client"
            "/files/gloop/external/Internals/license"
        )
        self.assertEqual(path, expected)

    def test_internals_is_capitalized(self):
        # 'Internals' must be capital-I (case sensitive)
        path = self.fn("com.roblox.client")
        self.assertIn("/Internals/", path)
        self.assertNotIn("/internals/", path)

    def test_invalid_package_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.fn("../evil/path")

    def test_empty_package_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.fn("")


# ── 9–11: is_valid_package_key ───────────────────────────────────────────────

class IsValidPackageKeyTests(unittest.TestCase):

    def setUp(self):
        from agent.package_key import is_valid_package_key
        self.fn = is_valid_package_key

    def test_free_prefix_is_valid(self):
        self.assertTrue(self.fn("FREE_ANYTHING123"))

    def test_lowercase_free_prefix_is_invalid(self):
        self.assertFalse(self.fn("free_anything"))

    def test_empty_key_is_invalid(self):
        self.assertFalse(self.fn(""))

    def test_no_prefix_is_invalid(self):
        self.assertFalse(self.fn("SOMEOTHERKEY"))


# ── 12–15: resolve_package_key ───────────────────────────────────────────────

class ResolvePackageKeyTests(unittest.TestCase):

    def setUp(self):
        from agent.package_key import resolve_package_key
        self.fn = resolve_package_key

    def test_per_package_overrides_global(self):
        cfg = _make_config(
            global_key="FREE_GLOBAL",
            per_package={"com.roblox.client": "FREE_PERPACKAGE"},
        )
        result = self.fn(cfg, "com.roblox.client")
        self.assertEqual(result, "FREE_PERPACKAGE")

    def test_falls_back_to_global(self):
        cfg = _make_config(global_key="FREE_GLOBAL")
        result = self.fn(cfg, "com.roblox.client")
        self.assertEqual(result, "FREE_GLOBAL")

    def test_returns_none_when_no_key(self):
        cfg = _make_config()
        result = self.fn(cfg, "com.roblox.client")
        self.assertIsNone(result)

    def test_returns_none_on_invalid_package_name(self):
        cfg = _make_config(global_key="FREE_SOMETHING")
        result = self.fn(cfg, "../evil")
        self.assertIsNone(result)

    def test_returns_none_when_empty_string_key(self):
        cfg = _make_config(global_key="")
        result = self.fn(cfg, "com.roblox.client")
        self.assertIsNone(result)


# ── 16–18: write_package_key_file ────────────────────────────────────────────

class WritePackageKeyFileTests(unittest.TestCase):

    def setUp(self):
        from agent.package_key import write_package_key_file
        self.fn = write_package_key_file

    def test_valid_key_writes_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_path = os.path.join(tmpdir, "license")

            def _fake_license_path(pkg):
                return fake_path

            with unittest.mock.patch(
                "agent.package_key.package_key_license_path", side_effect=_fake_license_path
            ):
                result = self.fn("com.roblox.client", "FREE_TESTKEY12345", root_enabled=False)

            self.assertTrue(result["success"], result.get("error"))
            self.assertEqual(result["method"], "python")
            with open(fake_path, encoding="utf-8") as fh:
                content = fh.read()
            self.assertEqual(content, "FREE_TESTKEY12345")

    def test_empty_key_returns_error(self):
        result = self.fn("com.roblox.client", "", root_enabled=False)
        self.assertFalse(result["success"])
        self.assertIn("empty", result["error"].lower())

    def test_non_free_key_returns_error(self):
        result = self.fn("com.roblox.client", "PAID_SECRETKEY", root_enabled=False)
        self.assertFalse(result["success"])
        self.assertIn("FREE_", result["error"])


# ── 19–23: ensure_package_key_for_start ──────────────────────────────────────

class EnsurePackageKeyForStartTests(unittest.TestCase):

    def setUp(self):
        from agent.package_key import ensure_package_key_for_start
        self.fn = ensure_package_key_for_start

    def test_no_configured_key_returns_skipped(self):
        cfg = _make_config()
        result = self.fn("com.roblox.client", cfg, root_enabled=False)
        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "skipped")
        self.assertFalse(result["write_needed"])

    def test_file_missing_writes_and_sets_write_needed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_path = os.path.join(tmpdir, "license")
            cfg = _make_config(global_key="FREE_TESTKEY12345")

            with unittest.mock.patch(
                "agent.package_key.package_key_license_path", return_value=fake_path
            ):
                result = self.fn("com.roblox.client", cfg, root_enabled=False)

            self.assertTrue(result["success"], result.get("error"))
            self.assertTrue(result["write_needed"])
            self.assertTrue(result["write_attempted"])
            self.assertIn(result["method"], {"python", "root_su"})
            self.assertTrue(os.path.exists(fake_path))

    def test_file_already_correct_returns_already_correct(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_path = os.path.join(tmpdir, "license")
            key = "FREE_TESTKEY12345"
            # Pre-write correct content.
            with open(fake_path, "w", encoding="utf-8") as fh:
                fh.write(key)
            cfg = _make_config(global_key=key)

            with unittest.mock.patch(
                "agent.package_key.package_key_license_path", return_value=fake_path
            ):
                result = self.fn("com.roblox.client", cfg, root_enabled=False)

            self.assertTrue(result["success"])
            self.assertEqual(result["method"], "already_correct")
            self.assertFalse(result["write_needed"])
            self.assertFalse(result["write_attempted"])

    def test_file_wrong_key_rewrites(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_path = os.path.join(tmpdir, "license")
            # Pre-write wrong key.
            with open(fake_path, "w", encoding="utf-8") as fh:
                fh.write("FREE_OLDKEY99999")
            cfg = _make_config(global_key="FREE_NEWKEY12345")

            with unittest.mock.patch(
                "agent.package_key.package_key_license_path", return_value=fake_path
            ):
                result = self.fn("com.roblox.client", cfg, root_enabled=False)

            self.assertTrue(result["success"], result.get("error"))
            self.assertTrue(result["write_needed"])
            with open(fake_path, encoding="utf-8") as fh:
                content = fh.read()
            self.assertEqual(content, "FREE_NEWKEY12345")

    def test_result_never_contains_full_key(self):
        key = "FREE_SUPERSECRETKEY9876"
        cfg = _make_config(global_key=key)
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_path = os.path.join(tmpdir, "license")
            with unittest.mock.patch(
                "agent.package_key.package_key_license_path", return_value=fake_path
            ):
                result = self.fn("com.roblox.client", cfg, root_enabled=False)

        result_str = str(result)
        self.assertNotIn(key, result_str,
                         "Full key must never appear in ensure_package_key_for_start result")


# ── 24–25: Isolation from DENG Tool license system ───────────────────────────

class LicenseIsolationTests(unittest.TestCase):

    def test_package_key_does_not_import_license_module(self):
        import agent.package_key as pkm
        import sys
        # Ensure license.py (the DENG Tool license) is not directly imported by package_key.
        pkg_key_source_file = pkm.__file__ or ""
        # Read the source and check for forbidden imports.
        with open(pkg_key_source_file, encoding="utf-8") as fh:
            src = fh.read()
        # Must not import the DENG Tool license module
        self.assertNotIn("from .license import", src)
        self.assertNotIn("import agent.license", src)
        self.assertNotIn("from agent.license", src)

    def test_write_does_not_call_license_validate(self):
        from agent.package_key import write_package_key_file
        with unittest.mock.patch("agent.license") as mock_lic:
            with tempfile.TemporaryDirectory() as tmpdir:
                fake_path = os.path.join(tmpdir, "license")
                with unittest.mock.patch(
                    "agent.package_key.package_key_license_path", return_value=fake_path
                ):
                    write_package_key_file(
                        "com.roblox.client", "FREE_KEY12345", root_enabled=False
                    )
            # No calls to agent.license should have occurred.
            mock_lic.assert_not_called()


if __name__ == "__main__":
    unittest.main()
