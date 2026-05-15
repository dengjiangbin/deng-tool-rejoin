"""Sanitized internal main-dev tarball builder."""

from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from agent.internal_test_artifact import (
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

    def test_env_example_allowed(self) -> None:
        self.assertFalse(path_should_exclude("examples/.env.example"))

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
        (self.tmp / "agent" / "deng_tool_rejoin.py").write_text("# ok\n", encoding="utf-8")
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
        joined = "\n".join(names)
        self.assertIn("agent/deng_tool_rejoin.py", names)
        self.assertIn("examples/.env.example", names)
        self.assertNotIn(".env", names)
        self.assertNotIn("data/bad.json", names)
        self.assertNotIn("agent/__pycache__/z.pyc", names)
        self.assertNotIn("logs/x.log", names)
        self.assertNotIn("node_modules/pkg/index.js", names)

        self.assertNotIn(".env", names)
        self.assertFalse(any(n.endswith("/.env") for n in names), msg=joined)
        self.assertNotIn("__pycache__", joined)


class RegistryStableGateTests(unittest.TestCase):
    def test_v100_disabled_public_stable_placeholder(self) -> None:
        root = Path(__file__).resolve().parents[1]
        rows = json.loads((root / "data" / "rejoin_versions.json").read_text(encoding="utf-8"))
        stable = next(r for r in rows if r.get("version") == "v1.0.0")
        self.assertFalse(stable.get("enabled"))
        main_dev = next(r for r in rows if r.get("version") == "main-dev")
        self.assertTrue(main_dev.get("enabled"))
        self.assertEqual(
            main_dev.get("artifact_path"),
            "releases/main-dev/deng-tool-rejoin-main-dev.tar.gz",
        )
        self.assertEqual(len(str(main_dev.get("artifact_sha256") or "")), 64)


class RepoTarballSmokeTests(unittest.TestCase):
    """Optional smoke test — repo tarball lists expected roots."""

    def test_repo_iteration_includes_agent_bot(self) -> None:
        root = Path(__file__).resolve().parents[1]
        pairs = iter_internal_test_pack_files(root)
        arcs = {a for a, _ in pairs}
        self.assertIn("install.sh", arcs)
        self.assertIn("agent/deng_tool_rejoin.py", arcs)
        self.assertTrue(any(a.startswith("bot/") for a in arcs))
        self.assertFalse(any(a.startswith("tests/") for a in arcs))
        self.assertFalse(any(a.startswith("data/") for a in arcs))


if __name__ == "__main__":
    unittest.main()
