"""Tests for agent/package_key.py — package key helper module.

Covers:
  1.  mask_package_key hides full key.
  2.  mask_package_key: FREE_ key shows prefix + last 4 chars.
  3.  mask_package_key: short suffix shows FREE_****.
  4.  mask_package_key: empty key returns empty.
  5.  mask_package_key: non-FREE_ key shows first 4 + ****.
  6.  package_key_license_path returns correct path (incl. Cache segment).
  7.  package_key_license_path: Internals/Cache are case-sensitive exact match.
  8.  package_key_license_path: invalid package name raises ValueError.
  8a. package_key_license_path: uses per-package substitution (probe p-52aeb6420f).
  8b. package_key_license_path: file name is exactly ``license`` (no .json).
  8c. package_key_license_dir / internals_dir / mime_type helpers.
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

import hashlib
import io
import os
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
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
        # Probe p-52aeb6420f required the ``Cache`` segment between
        # ``Internals`` and ``license``.
        path = self.fn("com.roblox.client")
        expected = (
            "/storage/emulated/0/Android/data/com.roblox.client"
            "/files/gloop/external/Internals/Cache/license"
        )
        self.assertEqual(path, expected)

    def test_path_contains_cache_segment(self):
        # ``Cache`` is required — the on-device folder name is capitalised.
        path = self.fn("com.moons.litesc")
        self.assertIn("/Internals/Cache/license", path)
        # Ensure we never use the old `Internals/license` (no Cache segment).
        self.assertNotIn("/Internals/license", path)

    def test_internals_is_capitalized(self):
        # 'Internals' must be capital-I (case sensitive)
        path = self.fn("com.roblox.client")
        self.assertIn("/Internals/", path)
        self.assertNotIn("/internals/", path)

    def test_cache_is_capitalized(self):
        path = self.fn("com.roblox.client")
        # ``Cache`` (capital C) must follow ``Internals/`` directly.
        self.assertIn("/Internals/Cache/", path)
        self.assertNotIn("/internals/cache/", path)

    def test_path_uses_actual_package_name(self):
        # The package segment must be substituted, NOT hardcoded.  The
        # screenshot example com.moons.litesc must not leak into other
        # packages.
        a = self.fn("com.roblox.client")
        b = self.fn("com.some.clone")
        self.assertIn("/com.roblox.client/", a)
        self.assertIn("/com.some.clone/", b)
        self.assertNotIn("com.moons.litesc", a)
        self.assertNotIn("com.moons.litesc", b)

    def test_file_name_is_exactly_license(self):
        path = self.fn("com.roblox.client")
        # No JSON, no extension, no Termux home, no shorthand.
        self.assertTrue(path.endswith("/license"))
        self.assertFalse(path.endswith(".json"))
        self.assertFalse(path.endswith("/license.json"))

    def test_path_is_absolute_external_storage(self):
        path = self.fn("com.roblox.client")
        self.assertTrue(path.startswith("/storage/emulated/0/Android/data/"))
        # Forbidden patterns from the spec.
        self.assertNotIn("/Termux/", path)
        self.assertNotIn("$HOME", path)
        self.assertNotIn("/Cache/license/license", path)  # no double Cache

    def test_invalid_package_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.fn("../evil/path")

    def test_empty_package_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.fn("")


# ── package_key_license_dir / mime_type ───────────────────────────────────────

class PackageKeyLicenseDirAndMimeTests(unittest.TestCase):

    def test_dir_is_cache_parent(self):
        from agent.package_key import package_key_license_dir
        d = package_key_license_dir("com.roblox.client")
        self.assertEqual(
            d,
            "/storage/emulated/0/Android/data/com.roblox.client"
            "/files/gloop/external/Internals/Cache",
        )

    def test_internals_dir_is_cache_parent(self):
        from agent.package_key import package_key_internals_dir
        d = package_key_internals_dir("com.moons.litesc")
        self.assertTrue(d.endswith("/Internals"))
        self.assertNotIn("/Cache", d)

    def test_mime_type_is_octet_stream(self):
        from agent.package_key import package_key_license_mime_type
        self.assertEqual(package_key_license_mime_type(), "application/octet-stream")


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


# ── package_key_license_info ──────────────────────────────────────────────────

class PackageKeyLicenseInfoTests(unittest.TestCase):
    """Menu 4 file-info helper.

    Covers:
      - Returns the expected ``…/Internals/Cache/license`` path.
      - ``exists=False`` when missing, with no crash and no error.
      - ``exists=True`` + size/md5/key_masked when present.
      - Full key NEVER appears in the returned dict.
      - ``mime_type`` is ``application/octet-stream``.
      - ``file_name`` is ``"license"`` (no extension).
    """

    def test_missing_file_returns_exists_false(self):
        from agent.package_key import package_key_license_info
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_path = os.path.join(tmpdir, "Cache", "license")
            with unittest.mock.patch(
                "agent.package_key.package_key_license_path", return_value=fake_path,
            ):
                info = package_key_license_info("com.roblox.client", root_enabled=False)
        self.assertFalse(info["exists"])
        self.assertEqual(info["file_name"], "license")
        self.assertEqual(info["mime_type"], "application/octet-stream")
        self.assertEqual(info["package"], "com.roblox.client")
        self.assertEqual(info["error"], "")
        self.assertEqual(info["key_masked"], "")
        self.assertEqual(info["md5"], "")
        self.assertIn("fs_type", info)

    def test_existing_file_returns_size_md5_and_masked_key(self):
        from agent.package_key import package_key_license_info
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "Cache")
            os.makedirs(cache_dir)
            fake_path = os.path.join(cache_dir, "license")
            content = "FREE_ABCDEFGH1234"
            with open(fake_path, "w", encoding="utf-8") as fh:
                fh.write(content)
            with unittest.mock.patch(
                "agent.package_key.package_key_license_path", return_value=fake_path,
            ):
                info = package_key_license_info("com.roblox.client", root_enabled=False)
        self.assertTrue(info["exists"])
        self.assertEqual(info["size_bytes"], len(content))
        self.assertEqual(info["md5"], hashlib.md5(content.encode()).hexdigest())
        self.assertEqual(info["key_masked"], "FREE_...1234")
        # Full key must NEVER appear.
        self.assertNotIn(content, info["key_masked"])
        self.assertNotIn(content, str(info))
        self.assertIn("modified_iso", info)
        self.assertIn("permissions", info)
        self.assertIn("fs_type", info)

    def test_dir_path_is_internals_cache(self):
        from agent.package_key import package_key_license_info
        # We don't need a real file — just inspect what the helper reports.
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_path = os.path.join(tmpdir, "license")
            with unittest.mock.patch(
                "agent.package_key.package_key_license_path", return_value=fake_path,
            ):
                info = package_key_license_info("com.roblox.client", root_enabled=False)
        # dir comes from package_key_license_dir which is unaffected by the
        # patched path helper.
        self.assertTrue(info["dir"].endswith("/Internals/Cache"))

    def test_invalid_package_returns_error_no_crash(self):
        from agent.package_key import package_key_license_info
        info = package_key_license_info("../evil", root_enabled=False)
        self.assertFalse(info["exists"])
        self.assertNotEqual(info["error"], "")


class Menu4PackageKeyUiTests(unittest.TestCase):
    def _cfg(self, packages: list[str]) -> dict[str, Any]:
        return {
            "roblox_package": packages[0],
            "roblox_packages": [
                {
                    "package": pkg,
                    "app_name": "",
                    "enabled": True,
                    "username_source": "not_set",
                }
                for pkg in packages
            ],
            "package_keys": {"global": "", "per_package": {}},
        }

    def test_multiple_packages_show_selector_and_all_packages(self):
        from agent import commands

        cfg = self._cfg(["com.moons.litesc", "com.moons.litesd", "com.moons.litese"])
        out = io.StringIO()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True), \
             unittest.mock.patch("agent.commands.safe_io.safe_prompt", return_value="0"), \
             redirect_stdout(out):
            result = commands._config_menu_key(cfg)

        text = out.getvalue()
        self.assertIs(result, cfg)
        self.assertIn("Select Package For Package Key", text)
        self.assertIn("1. com.moons.litesc", text)
        self.assertIn("2. com.moons.litesd", text)
        self.assertIn("3. com.moons.litese", text)
        self.assertIn("A. All Packages", text)
        self.assertIn("0. Back", text)

    def test_single_package_opens_package_key_menu_directly(self):
        from agent import commands

        cfg = self._cfg(["com.roblox.client"])
        out = io.StringIO()
        with unittest.mock.patch("agent.commands._is_interactive", return_value=True), \
             unittest.mock.patch("agent.commands.safe_io.safe_prompt", return_value="0"), \
             redirect_stdout(out):
            commands._config_menu_key(cfg)

        text = out.getvalue()
        self.assertIn("Package Key menu:", text)
        self.assertIn("1. Enter / Update Package Key", text)
        self.assertIn("2. Show Package Key File Info", text)
        self.assertIn("3. Remove Saved Package Key", text)
        self.assertNotIn("Select Package For Package Key", text)

    def test_file_info_output_contains_required_fields_and_cache_path(self):
        from agent import commands

        content = "FREE_ABCDEFGH1234"
        info = {
            "package": "com.moons.litesc",
            "file_name": "license",
            "mime_type": "application/octet-stream",
            "exists": True,
            "size_bytes": len(content),
            "modified_iso": "2026-05-23T00:00:00Z",
            "permissions": "rw-r--r--",
            "path": (
                "/storage/emulated/0/Android/data/com.moons.litesc"
                "/files/gloop/external/Internals/Cache/license"
            ),
            "dir": (
                "/storage/emulated/0/Android/data/com.moons.litesc"
                "/files/gloop/external/Internals/Cache"
            ),
            "fs_type": "fuseblk",
            "md5": hashlib.md5(content.encode()).hexdigest(),
            "error": "",
        }
        out = io.StringIO()
        with unittest.mock.patch("agent.package_key.package_key_license_info", return_value=info), \
             redirect_stdout(out):
            commands._print_package_key_file_info("com.moons.litesc")

        text = out.getvalue()
        self.assertIn("Package Key File Info", text)
        self.assertIn("Package: com.moons.litesc", text)
        self.assertIn("File name: license", text)
        self.assertIn("Type: application/octet-stream", text)
        self.assertIn("Size: 17 bytes", text)
        self.assertIn("Last modification: 2026-05-23T00:00:00Z", text)
        self.assertIn("Permissions: rw-r--r--", text)
        self.assertIn("/Internals/Cache/license", text)
        self.assertNotIn("/Internals/license", text)
        self.assertIn("FS path: /storage/emulated/0/Android/data/com.moons.litesc/files/gloop/external/Internals/Cache", text)
        self.assertIn("FS type: fuseblk", text)
        self.assertIn(f"MD5: {info['md5']}", text)
        self.assertNotIn(content, text)

    def test_missing_file_output_does_not_crash_and_shows_expected_path(self):
        from agent import commands

        path = (
            "/storage/emulated/0/Android/data/com.roblox.client"
            "/files/gloop/external/Internals/Cache/license"
        )
        info = {
            "package": "com.roblox.client",
            "mime_type": "application/octet-stream",
            "exists": False,
            "path": path,
            "dir": path.rsplit("/", 1)[0],
            "fs_type": "unknown",
            "error": "",
        }
        out = io.StringIO()
        with unittest.mock.patch("agent.package_key.package_key_license_info", return_value=info), \
             redirect_stdout(out):
            commands._print_package_key_file_info("com.roblox.client")

        text = out.getvalue()
        self.assertIn("Package key file not found.", text)
        self.assertIn(f"Full path: {path}", text)
        self.assertIn("FS path: /storage/emulated/0/Android/data/com.roblox.client/files/gloop/external/Internals/Cache", text)

    def test_all_packages_save_writes_each_package_specific_cache_path(self):
        from agent import commands

        cfg = self._cfg(["com.moons.litesc", "com.moons.litesd"])
        writes: list[tuple[str, str]] = []

        def fake_write(pkg: str, key: str):
            writes.append((pkg, key))
            return {
                "success": True,
                "path": (
                    f"/storage/emulated/0/Android/data/{pkg}"
                    "/files/gloop/external/Internals/Cache/license"
                ),
            }

        with unittest.mock.patch("agent.package_key.write_package_key_file", side_effect=fake_write), \
             unittest.mock.patch("agent.commands.save_config", side_effect=lambda c: c):
            result = commands._save_package_key_for_packages(
                cfg,
                ["com.moons.litesc", "com.moons.litesd"],
                "FREE_TESTKEY1234",
                save_global=True,
            )

        self.assertEqual(
            writes,
            [
                ("com.moons.litesc", "FREE_TESTKEY1234"),
                ("com.moons.litesd", "FREE_TESTKEY1234"),
            ],
        )
        self.assertEqual(result["package_keys"]["global"], "FREE_TESTKEY1234")


if __name__ == "__main__":
    unittest.main()
