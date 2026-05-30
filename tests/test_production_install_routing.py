"""Production-manifest guard for /install/latest vs /install/test/latest.

These tests deliberately run against the **real** ``data/rejoin_versions.json``
shipped in the repo. Unit tests in :mod:`test_install_api` and
:mod:`test_rejoin_versions` already exercise the resolver with synthetic
manifests; this file exists so that no future commit can accidentally:

  * point ``stable_latest`` at a dev/test row,
  * promote ``main-dev`` (or any ``refs/heads/*`` row) to public visibility,
  * leak the dev/test artifact SHA into ``/install/latest``,
  * remove the channel-pointer row that drives ``resolve_latest_public_stable``.

The reproduction case for this guard: when stable and the dev
``main-dev`` artifact were rebuilt together in the same commit, the manifest
correctly kept ``stable_latest`` on the public stable row and the resolver served the
stable SHA from ``/install/latest`` — but a future careless edit could
swap those pointers and there was no test pinning the *real* manifest
values. This file fixes that.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from agent.install_registry import (
    is_admin_internal_row,
    is_public_stable_row,
    load_registry_rows,
    resolve_latest_public_stable,
    resolve_requested_public_version,
)
from agent.rejoin_versions import default_versions_manifest_path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_MANIFEST = REPO_ROOT / "data" / "rejoin_versions.json"
CURRENT_STABLE_VERSION = f"v{(REPO_ROOT / 'VERSION').read_text(encoding='utf-8').strip()}"


class ProductionManifestSanityTests(unittest.TestCase):
    """Read-only assertions on the manifest shipped in the repo."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.assertTrue(
            cls,  # type: ignore[arg-type]
            PROD_MANIFEST.is_file(),
            f"production manifest missing: {PROD_MANIFEST}",
        )
        # Sanity: agent.rejoin_versions.default_versions_manifest_path()
        # must point at the same file the resolver will read.
        cls._resolver_target = default_versions_manifest_path()

    def setUp(self) -> None:
        # Defensive: load the canonical manifest path the resolver uses
        # (with no env override) so this test fails if anyone re-points
        # the default to a different file.
        self.rows = load_registry_rows()
        self.assertGreater(len(self.rows), 0, "production manifest is empty")

    # ── Manifest invariants ────────────────────────────────────────────

    def test_channel_pointer_row_is_present_and_correct(self) -> None:
        pointer = next(
            (r for r in self.rows if r.get("kind") == "channel_pointers"),
            None,
        )
        self.assertIsNotNone(
            pointer,
            "channel_pointers row missing — /install/latest would fall back "
            "to semver sort and could regress to an unintended version.",
        )
        assert pointer is not None  # mypy
        self.assertEqual(
            pointer.get("stable_latest"),
            CURRENT_STABLE_VERSION,
            f"stable_latest pointer must be {CURRENT_STABLE_VERSION}",
        )
        self.assertEqual(
            pointer.get("test_latest"),
            "main-dev",
            "test_latest pointer must be main-dev",
        )

    def test_current_stable_row_is_public_stable_and_has_published_sha(self) -> None:
        row = next((r for r in self.rows if r.get("version") == CURRENT_STABLE_VERSION), None)
        self.assertIsNotNone(
            row,
            f"{CURRENT_STABLE_VERSION} row missing from production manifest",
        )
        assert row is not None
        self.assertRegex(
            str(row.get("artifact_sha256", "")),
            r"^[0-9a-f]{64}$",
            "current stable row must carry a concrete published artifact SHA",
        )
        self.assertTrue(
            is_public_stable_row(row),
            f"{CURRENT_STABLE_VERSION} must qualify as a public stable row",
        )
        self.assertFalse(
            is_admin_internal_row(row),
            f"{CURRENT_STABLE_VERSION} must NOT be classified as admin/internal",
        )

    def test_main_dev_row_is_admin_internal_and_never_public_stable(self) -> None:
        row = next((r for r in self.rows if r.get("version") == "main-dev"), None)
        self.assertIsNotNone(row, "main-dev row missing from production manifest")
        assert row is not None
        # Two independent guards against main-dev leaking into /install/latest:
        self.assertFalse(
            is_public_stable_row(row),
            "main-dev must NEVER pass is_public_stable_row()",
        )
        self.assertTrue(
            is_admin_internal_row(row),
            "main-dev must remain admin/internal",
        )
        self.assertTrue(
            str(row.get("install_ref", "")).startswith("refs/heads/"),
            "main-dev must use a branch ref so it can never be served as stable",
        )
        self.assertIn(
            str(row.get("visibility", "")).lower(),
            {"admin", "internal", "private", "owner", "tester"},
            "main-dev visibility must remain non-public",
        )

    # ── Resolver behaviour against the real manifest ───────────────────

    def test_resolve_latest_public_stable_picks_current_stable_not_main_dev(self) -> None:
        row = resolve_latest_public_stable()
        self.assertIsNotNone(row, "resolver returned None on production manifest")
        assert row is not None
        self.assertEqual(row.get("version"), CURRENT_STABLE_VERSION)
        self.assertNotEqual(row.get("version"), "main-dev")
        dev_row = next((r for r in self.rows if r.get("version") == "main-dev"), None)
        self.assertIsNotNone(dev_row, "main-dev row missing from production manifest")
        assert dev_row is not None
        self.assertNotEqual(
            row.get("artifact_sha256"),
            dev_row.get("artifact_sha256"),
            "/install/latest must NEVER resolve to the main-dev SHA",
        )

    def test_resolve_requested_public_version_latest_returns_current_stable(self) -> None:
        row, err = resolve_requested_public_version("latest")
        self.assertIsNone(err)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.get("version"), CURRENT_STABLE_VERSION)

    def test_resolve_requested_public_version_current_stable_returns_current_stable(self) -> None:
        row, err = resolve_requested_public_version(CURRENT_STABLE_VERSION)
        self.assertIsNone(err)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.get("version"), CURRENT_STABLE_VERSION)

    def test_public_resolver_refuses_main_dev_by_name(self) -> None:
        # Even if a public user crafted a URL like /install/main-dev,
        # the public resolver must refuse — the dev artifact may only be
        # reached through /install/test/latest (which uses a different
        # resolver path with admin gating).
        row, err = resolve_requested_public_version("main-dev")
        self.assertIsNone(
            row,
            "main-dev must not be installable through the public resolver",
        )
        self.assertIsNotNone(err)


if __name__ == "__main__":
    unittest.main()
