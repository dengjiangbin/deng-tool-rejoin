"""Production-manifest guard for /install/latest vs /install/test/latest.

These tests deliberately run against the **real** ``data/rejoin_versions.json``
shipped in the repo. Unit tests in :mod:`test_install_api` and
:mod:`test_rejoin_versions` already exercise the resolver with synthetic
manifests; this file exists so that no future commit can accidentally:

  * point ``stable_latest`` at a dev/test row,
  * promote ``main-dev`` (or any ``refs/heads/*`` row) to public visibility,
  * leak the dev/test artifact SHA into ``/install/latest``,
  * remove the channel-pointer row that drives ``resolve_latest_public_stable``.

The reproduction case for this guard: when stable v1.0.0 and the dev
``main-dev`` artifact were rebuilt together in the same commit, the manifest
correctly kept ``stable_latest = "v1.0.0"`` and the resolver served the
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
            "v1.0.0",
            "stable_latest pointer must be v1.0.0 hotfix",
        )
        self.assertEqual(
            pointer.get("test_latest"),
            "main-dev",
            "test_latest pointer must be main-dev",
        )

    def test_v100_row_is_public_stable_and_matches_published_sha(self) -> None:
        # Locked to the SHA produced by the v1.0.3 release artifact rebuild.
        # If the artifact is ever rebuilt the test must be updated in lockstep
        # so /install/v1.0.0 cannot accidentally start serving a different
        # tarball than what was QA'd.
        EXPECTED_V100_SHA = (
            "f0522c5882c642e2208705196ab904e312d580e445ab2bf2995e834351030ab1"
        )
        row = next((r for r in self.rows if r.get("version") == "v1.0.0"), None)
        self.assertIsNotNone(row, "v1.0.0 row missing from production manifest")
        assert row is not None
        self.assertEqual(row.get("artifact_sha256"), EXPECTED_V100_SHA)
        self.assertTrue(
            is_public_stable_row(row),
            "v1.0.0 must qualify as a public stable row",
        )
        self.assertFalse(
            is_admin_internal_row(row),
            "v1.0.0 must NOT be classified as admin/internal",
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

    def test_resolve_latest_public_stable_picks_v100_not_main_dev(self) -> None:
        row = resolve_latest_public_stable()
        self.assertIsNotNone(row, "resolver returned None on production manifest")
        assert row is not None
        self.assertEqual(row.get("version"), "v1.0.0")
        self.assertNotEqual(row.get("version"), "main-dev")
        self.assertNotEqual(
            row.get("artifact_sha256"),
            "aa6e9a6aa9b439daeecedc5ff868ae57a8b1f4f7fedb6d9592ee68e9a366b3c5",
            "/install/latest must NEVER resolve to the main-dev SHA",
        )

    def test_resolve_requested_public_version_latest_returns_v100(self) -> None:
        row, err = resolve_requested_public_version("latest")
        self.assertIsNone(err)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.get("version"), "v1.0.0")

    def test_resolve_requested_public_version_v100_returns_v100(self) -> None:
        row, err = resolve_requested_public_version("v1.0.0")
        self.assertIsNone(err)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.get("version"), "v1.0.0")

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
