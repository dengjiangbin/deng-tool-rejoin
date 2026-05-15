"""Tests for scripts/package_release.py — the release package builder.

Loads the script as a module via importlib so it can be tested without
making it a package import.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import zipfile
from pathlib import Path

import unittest

# ── Load scripts/package_release.py as a module ───────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "package_release.py"

spec = importlib.util.spec_from_file_location("package_release", _SCRIPT_PATH)
_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(_mod)  # type: ignore[union-attr]

build_package = _mod.build_package
collect_package_files = _mod.collect_package_files
compute_sha256 = _mod.compute_sha256
_should_exclude = _mod._should_exclude
_verify_no_secrets = _mod._verify_no_secrets
VALID_CHANNELS = _mod.VALID_CHANNELS


# ── Helper ─────────────────────────────────────────────────────────────────────

def _make_fake_project(tmp: Path) -> Path:
    """Create a minimal fake project tree for testing."""
    root = tmp / "project"
    # agent/
    (root / "agent").mkdir(parents=True)
    (root / "agent" / "__init__.py").write_text("", encoding="utf-8")
    (root / "agent" / "constants.py").write_text('VERSION = "1.2.3"\n', encoding="utf-8")
    (root / "agent" / "commands.py").write_text("# commands\n", encoding="utf-8")
    # agent/__pycache__/ — should be excluded
    (root / "agent" / "__pycache__").mkdir()
    (root / "agent" / "__pycache__" / "constants.cpython-313.pyc").write_bytes(b"\x00abc")
    # examples/
    (root / "examples").mkdir()
    (root / "examples" / "config.example.json").write_text("{}", encoding="utf-8")
    # scripts/ — a few client scripts
    (root / "scripts").mkdir()
    (root / "scripts" / "start-agent.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (root / "scripts" / "stop-agent.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (root / "scripts" / "bootstrap_install.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    # bot/ — server code, should be EXCLUDED
    (root / "bot").mkdir()
    (root / "bot" / "main.py").write_text("# server\n", encoding="utf-8")
    # tests/ — should be EXCLUDED
    (root / "tests").mkdir()
    (root / "tests" / "test_foo.py").write_text("# tests\n", encoding="utf-8")
    # supabase/ — should be EXCLUDED
    (root / "supabase").mkdir()
    (root / "supabase" / "migrations").mkdir()
    (root / "supabase" / "migrations" / "001.sql").write_text("-- sql\n", encoding="utf-8")
    # .env — should be EXCLUDED
    (root / ".env").write_text("SECRET=hunter2\n", encoding="utf-8")
    (root / "agent" / "agent.env").write_text("SECRET=x\n", encoding="utf-8")
    # top-level files
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "README.md").write_text("# DENG\n", encoding="utf-8")
    (root / "SECURITY.md").write_text("# Security\n", encoding="utf-8")
    (root / "install.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    # keydb.json — should be EXCLUDED
    (root / "keydb.json").write_text("{}", encoding="utf-8")
    return root


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestShouldExclude(unittest.TestCase):

    def test_env_file_excluded(self):
        self.assertTrue(_should_exclude(".env"))
        self.assertTrue(_should_exclude("agent/agent.env"))
        self.assertTrue(_should_exclude("config.env"))

    def test_pyc_excluded(self):
        self.assertTrue(_should_exclude("agent/__pycache__/foo.pyc"))
        self.assertTrue(_should_exclude("agent/constants.cpython-313.pyc"))

    def test_bot_dir_excluded(self):
        self.assertTrue(_should_exclude("bot/main.py"))
        self.assertTrue(_should_exclude("bot/__init__.py"))

    def test_tests_dir_excluded(self):
        self.assertTrue(_should_exclude("tests/test_foo.py"))

    def test_supabase_excluded(self):
        self.assertTrue(_should_exclude("supabase/migrations/001.sql"))

    def test_keydb_excluded(self):
        self.assertTrue(_should_exclude("keydb.json"))

    def test_agent_py_included(self):
        self.assertFalse(_should_exclude("agent/constants.py"))
        self.assertFalse(_should_exclude("agent/__init__.py"))

    def test_readme_included(self):
        self.assertFalse(_should_exclude("README.md"))
        self.assertFalse(_should_exclude("VERSION"))


class TestBuildPackage(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.project = _make_fake_project(self.tmp)
        self.dist = self.tmp / "dist"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_zip_file_created(self):
        manifest = build_package(self.project, "stable", "1.2.3", self.dist)
        zip_path = self.dist / "releases" / "stable" / "1.2.3" / manifest["filename"]
        self.assertTrue(zip_path.is_file(), f"Zip not found: {zip_path}")

    def test_manifest_json_created(self):
        build_package(self.project, "stable", "1.2.3", self.dist)
        manifest_path = self.dist / "releases" / "stable" / "1.2.3" / "manifest.json"
        self.assertTrue(manifest_path.is_file())
        data = json.loads(manifest_path.read_text())
        self.assertEqual(data["version"], "1.2.3")
        self.assertEqual(data["channel"], "stable")
        self.assertIn("sha256", data)
        self.assertIn("file_count", data)

    def test_sha256sums_created(self):
        build_package(self.project, "stable", "1.2.3", self.dist)
        sums_path = self.dist / "releases" / "stable" / "1.2.3" / "SHA256SUMS.txt"
        self.assertTrue(sums_path.is_file())

    def test_sha256_matches_zip(self):
        manifest = build_package(self.project, "stable", "1.2.3", self.dist)
        zip_path = self.dist / "releases" / "stable" / "1.2.3" / manifest["filename"]
        actual = compute_sha256(zip_path)
        self.assertEqual(actual, manifest["sha256"])

    def test_env_files_excluded(self):
        manifest = build_package(self.project, "stable", "1.2.3", self.dist)
        zip_path = self.dist / "releases" / "stable" / "1.2.3" / manifest["filename"]
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
        self.assertNotIn(".env", names)
        # No file ending in .env at any depth
        for name in names:
            self.assertFalse(
                name == ".env" or name.endswith("/.env") or name.endswith(".env"),
                f"Found .env file in package: {name}",
            )

    def test_bot_directory_excluded(self):
        manifest = build_package(self.project, "stable", "1.2.3", self.dist)
        zip_path = self.dist / "releases" / "stable" / "1.2.3" / manifest["filename"]
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
        self.assertFalse(
            any(n.startswith("bot/") for n in names),
            f"bot/ found in package: {[n for n in names if n.startswith('bot/')]}",
        )

    def test_tests_directory_excluded(self):
        manifest = build_package(self.project, "stable", "1.2.3", self.dist)
        zip_path = self.dist / "releases" / "stable" / "1.2.3" / manifest["filename"]
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
        self.assertFalse(
            any(n.startswith("tests/") for n in names),
            f"tests/ found in package",
        )

    def test_agent_python_files_included(self):
        manifest = build_package(self.project, "stable", "1.2.3", self.dist)
        zip_path = self.dist / "releases" / "stable" / "1.2.3" / manifest["filename"]
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
        self.assertIn("agent/__init__.py", names)
        self.assertIn("agent/constants.py", names)

    def test_pyc_files_excluded(self):
        manifest = build_package(self.project, "stable", "1.2.3", self.dist)
        zip_path = self.dist / "releases" / "stable" / "1.2.3" / manifest["filename"]
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
        self.assertFalse(
            any(n.endswith(".pyc") for n in names),
            "Found .pyc file in package",
        )

    def test_invalid_channel_raises(self):
        with self.assertRaises(ValueError):
            build_package(self.project, "nightly", "1.2.3", self.dist)

    def test_invalid_version_raises(self):
        with self.assertRaises(ValueError):
            build_package(self.project, "stable", "not-a-version", self.dist)

    def test_force_overwrites(self):
        build_package(self.project, "stable", "1.2.3", self.dist)
        # Second call without force should raise
        with self.assertRaises(FileExistsError):
            build_package(self.project, "stable", "1.2.3", self.dist)
        # With force=True should succeed
        m2 = build_package(self.project, "stable", "1.2.3", self.dist, force=True)
        self.assertEqual(m2["version"], "1.2.3")

    def test_beta_channel(self):
        manifest = build_package(self.project, "beta", "1.2.3", self.dist)
        self.assertEqual(manifest["channel"], "beta")
        self.assertIn("beta", manifest["filename"])

    def test_dev_channel(self):
        manifest = build_package(self.project, "dev", "1.2.3", self.dist)
        self.assertEqual(manifest["channel"], "dev")

    def test_verify_no_secrets_clean_zip(self):
        """A freshly built package should pass the security gate."""
        manifest = build_package(self.project, "stable", "1.2.3", self.dist)
        zip_path = self.dist / "releases" / "stable" / "1.2.3" / manifest["filename"]
        forbidden = _verify_no_secrets(zip_path)
        self.assertEqual(forbidden, [], f"Forbidden paths found: {forbidden}")

    def test_verify_no_secrets_detects_env(self):
        """If a tampered zip contains .env, _verify_no_secrets catches it."""
        manifest = build_package(self.project, "stable", "1.2.3", self.dist)
        zip_path = self.dist / "releases" / "stable" / "1.2.3" / manifest["filename"]
        # Inject a .env into the zip
        with zipfile.ZipFile(zip_path, "a") as zf:
            zf.writestr(".env", "SECRET=injected\n")
        forbidden = _verify_no_secrets(zip_path)
        self.assertIn(".env", forbidden)

    def test_manifest_has_required_fields(self):
        manifest = build_package(self.project, "stable", "1.2.3", self.dist)
        for field in ("app", "version", "channel", "filename", "sha256",
                      "size_bytes", "created_at", "min_client_version",
                      "notes", "file_count"):
            self.assertIn(field, manifest, f"Missing field: {field}")

    def test_collect_package_files_no_secrets(self):
        files = collect_package_files(self.project)
        arcnames = [arcname for _, arcname in files]
        self.assertFalse(
            any(".env" in n for n in arcnames),
            f"Secret found in collected files: {arcnames}",
        )
        self.assertFalse(
            any(n.startswith("bot/") for n in arcnames),
            "bot/ in collected files",
        )


if __name__ == "__main__":
    unittest.main()
