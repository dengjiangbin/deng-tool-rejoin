"""Protected internal main-dev tarball builder."""

from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from agent.internal_test_artifact import (
    _ALLOWED_ARTIFACT_PATHS,
    build_internal_test_tarball,
    iter_internal_test_pack_files,
    path_should_exclude,
    verify_tarball_exclusions,
)


class ExcludePathTests(unittest.TestCase):
    def test_env_and_data_excluded(self) -> None:
        self.assertTrue(path_should_exclude(".env"))
        self.assertTrue(path_should_exclude("data/foo.json"))
        self.assertTrue(path_should_exclude("data/backups/x.json"))
        self.assertTrue(path_should_exclude("tests/test_x.py"))
        self.assertTrue(path_should_exclude("agent/__pycache__/x.pyc"))

    def test_env_example_not_shipped(self) -> None:
        self.assertTrue(path_should_exclude("examples/.env.example"))

    def test_db_suffix_excluded(self) -> None:
        self.assertTrue(path_should_exclude("agent/x.db"))


class BuilderFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tarball_excludes_junk_and_has_sha256(self) -> None:
        (self.tmp / "agent").mkdir()
        (self.tmp / "agent" / "commands.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.tmp / "bot").mkdir()
        (self.tmp / "bot" / "main.py").write_text("x\n", encoding="utf-8")
        (self.tmp / "scripts").mkdir()
        (self.tmp / "scripts" / "noop.sh").write_text("#\n", encoding="utf-8")
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "a.md").write_text("doc\n", encoding="utf-8")
        (self.tmp / "examples").mkdir()
        (self.tmp / "examples" / ".env.example").write_text("X=y\n", encoding="utf-8")
        (self.tmp / "install.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
        (self.tmp / "README.md").write_text("r\n", encoding="utf-8")
        (self.tmp / "VERSION").write_text("0.0.0\n", encoding="utf-8")
        (self.tmp / "requirements-bot.txt").write_text("discord.py\n", encoding="utf-8")
        # Must not appear
        (self.tmp / "data").mkdir()
        (self.tmp / "data" / "bad.json").write_text("{}", encoding="utf-8")
        (self.tmp / "data" / "backups").mkdir()
        (self.tmp / ".env").write_text("SECRET=x\n", encoding="utf-8")
        nasty = self.tmp / "agent" / "__pycache__"
        nasty.mkdir()
        (nasty / "z.pyc").write_bytes(b"\0")
        logs = self.tmp / "logs"
        logs.mkdir()
        (logs / "x.log").write_text("!", encoding="utf-8")

        out = self.tmp / "releases" / "main-dev" / "deng-tool-rejoin-main-dev.tar.gz"
        sha = build_internal_test_tarball(self.tmp, out)
        self.assertEqual(len(sha), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in sha))

        raw = out.read_bytes()
        verify_tarball_exclusions(raw)

        names = []
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
            names = sorted(tf.getnames())
            files = {
                name: tf.extractfile(name).read()
                for name in names
                if tf.getmember(name).isfile()
            }
        joined = "\n".join(names)
        self.assertEqual(set(names), _ALLOWED_ARTIFACT_PATHS)
        self.assertIn("agent/deng_tool_rejoin.py", names)
        self.assertIn("agent/_protected_runtime.py", names)
        self.assertIn("agent/.deng_runtime.bin", names)
        self.assertIn("RELEASE-MANIFEST.json", names)
        self.assertIn("RELEASE-MANIFEST.sig", names)
        self.assertNotIn("agent/commands.py", names)
        self.assertNotIn("examples/.env.example", names)
        self.assertNotIn(".env", names)
        self.assertNotIn("data/bad.json", names)
        self.assertNotIn("agent/__pycache__/z.pyc", names)
        self.assertNotIn("logs/x.log", names)
        self.assertNotIn("node_modules/pkg/index.js", names)

        self.assertNotIn(".env", names)
        self.assertFalse(any(n.endswith("/.env") for n in names), msg=joined)
        self.assertNotIn("__pycache__", joined)
        combined = b"\n".join(files.values()).decode("utf-8", errors="ignore")
        forbidden = (
            "SUPABASE_SERVICE_ROLE",
            "service_role",
            "DISCORD_BOT_TOKEN",
            "BOT_TOKEN",
            "CLIENT_SECRET",
            "SHARED_SECRET",
            "BACKEND_SECRET",
            "DATABASE_URL",
            "POSTGRES",
            "Cloudflare token",
            "ecosystem.config",
            "license_panel",
            "reset HWID admin",
        )
        for marker in forbidden:
            self.assertNotIn(marker, combined)
        manifest = json.loads(files["RELEASE-MANIFEST.json"])
        self.assertEqual(manifest["project"], "deng-tool-rejoin")
        self.assertEqual(manifest["client_protocol"], 2)
        self.assertEqual(manifest["min_server_protocol"], 2)
        self.assertTrue(manifest["build_id"])
        mf = {item["path"]: item for item in manifest["files"]}
        self.assertEqual(set(mf), _ALLOWED_ARTIFACT_PATHS - {"RELEASE-MANIFEST.json", "RELEASE-MANIFEST.sig"})
        runtime = files["agent/.deng_runtime.bin"]
        self.assertEqual(mf["agent/.deng_runtime.bin"]["sha256"], __import__("hashlib").sha256(runtime).hexdigest())
        sig = json.loads(files["RELEASE-MANIFEST.sig"])
        self.assertEqual(sig["algorithm"], "RS256")
        self.assertTrue(sig["signature"])

    def test_stable_version_metadata_can_be_embedded(self) -> None:
        (self.tmp / "agent").mkdir()
        (self.tmp / "agent" / "commands.py").write_text("VALUE = 1\n", encoding="utf-8")
        out = self.tmp / "releases" / "v1.0.0" / "deng-tool-rejoin-v1.0.0.tar.gz"

        build_internal_test_tarball(
            self.tmp,
            out,
            channel="stable",
            version="v1.0.0",
        )

        raw = out.read_bytes()
        verify_tarball_exclusions(raw)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
            build_info = json.loads(tf.extractfile("BUILD-INFO.json").read())
            release_manifest = json.loads(tf.extractfile("RELEASE-MANIFEST.json").read())
        self.assertEqual(build_info["version"], "v1.0.0")
        self.assertEqual(build_info["channel"], "stable")
        self.assertEqual(release_manifest["version"], "v1.0.0")


class RegistryStableGateTests(unittest.TestCase):
    def test_v100_public_stable_is_frozen(self) -> None:
        """v1.0.0 is the immutable public stable release."""
        root = Path(__file__).resolve().parents[1]
        rows = json.loads((root / "data" / "rejoin_versions.json").read_text(encoding="utf-8"))
        stable = next(r for r in rows if r.get("version") == "v1.0.0")
        self.assertTrue(stable.get("enabled"))
        self.assertTrue(stable.get("frozen"))
        self.assertEqual(stable.get("visibility"), "public")
        self.assertEqual(stable.get("channel"), "stable")
        self.assertEqual(stable.get("install_ref"), "refs/tags/v1.0.0")
        self.assertEqual(stable.get("git_ref"), "refs/tags/v1.0.0")
        self.assertEqual(stable.get("artifact_path"), "releases/v1.0.0/deng-tool-rejoin-v1.0.0.tar.gz")
        self.assertEqual(len(str(stable.get("artifact_sha256") or "")), 64)
        self.assertNotIn("refs/heads", json.dumps(stable))
        self.assertNotIn("test/latest", json.dumps(stable))

    def test_stable_latest_pointer_targets_current_version(self) -> None:
        """The public latest channel moves only by changing this pointer."""
        root = Path(__file__).resolve().parents[1]
        rows = json.loads((root / "data" / "rejoin_versions.json").read_text(encoding="utf-8"))
        current_stable = f"v{(root / 'VERSION').read_text(encoding='utf-8').strip()}"
        pointers = next(r for r in rows if r.get("kind") == "channel_pointers")
        self.assertEqual(pointers.get("stable_latest"), current_stable)
        self.assertEqual(pointers.get("test_latest"), "main-dev")
        stable = next(r for r in rows if r.get("version") == pointers.get("stable_latest"))
        self.assertEqual(stable.get("channel"), "stable")
        self.assertEqual(stable.get("visibility"), "public")
        self.assertTrue(stable.get("frozen"))
        self.assertNotEqual(pointers.get("stable_latest"), pointers.get("test_latest"))

    def test_main_dev_hidden_from_discord_via_visibility(self) -> None:
        """main-dev is hidden from Discord by visibility=admin, NOT by enabled flag.

        The internal test install flow requires main-dev to be enabled so the
        authorize endpoint can find it.  Discord filtering is done by
        list_public_rejoin_versions(include_internal_channels=False) which
        excludes admin/internal visibility and refs/heads/ refs regardless of
        the enabled flag.
        """
        root = Path(__file__).resolve().parents[1]
        rows = json.loads((root / "data" / "rejoin_versions.json").read_text(encoding="utf-8"))
        main_dev = next(r for r in rows if r.get("version") == "main-dev")
        # Must be admin-only visibility → Discord Select Version never shows it
        self.assertEqual(main_dev.get("visibility"), "admin")
        # install_ref is a branch → also excluded from public list
        self.assertTrue(
            str(main_dev.get("install_ref") or "").startswith("refs/heads/"),
            msg="install_ref must be a branch ref so public install routes skip it",
        )
        # Enabled for the backend authorize endpoint
        self.assertTrue(main_dev.get("enabled"))
        # Artifact path and SHA256 must be set (build_internal_test_artifact.py populates these)
        self.assertEqual(
            main_dev.get("artifact_path"),
            "releases/main-dev/deng-tool-rejoin-main-dev.tar.gz",
        )
        self.assertEqual(len(str(main_dev.get("artifact_sha256") or "")), 64)


class RepoTarballSmokeTests(unittest.TestCase):
    """Protected artifact source selection is client-only."""

    def test_repo_iteration_excludes_server_and_entrypoint_sources(self) -> None:
        root = Path(__file__).resolve().parents[1]
        pairs = iter_internal_test_pack_files(root)
        arcs = {a for a, _ in pairs}
        self.assertIn("agent/commands.py", arcs)
        self.assertNotIn("agent/deng_tool_rejoin.py", arcs)
        self.assertFalse(any(a.startswith("bot/") for a in arcs))
        self.assertFalse(any(a.startswith("tests/") for a in arcs))
        self.assertFalse(any(a.startswith("data/") for a in arcs))


if __name__ == "__main__":
    unittest.main()
