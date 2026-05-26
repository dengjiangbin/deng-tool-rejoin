from __future__ import annotations

import json
import marshal
import tarfile
import unittest
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _bundle_modules(rel_path: str) -> dict:
    with tarfile.open(ROOT / rel_path, "r:gz") as tf:
        bundle = tf.extractfile("agent/.deng_runtime.bin").read()
    return marshal.loads(zlib.decompress(bundle))


class ReleasePatchArtifactTests(unittest.TestCase):
    def test_v100_manifest_points_to_existing_patched_artifact(self) -> None:
        rows = json.loads((ROOT / "data" / "rejoin_versions.json").read_text(encoding="utf-8"))
        row = next(r for r in rows if r.get("version") == "v1.0.0")
        self.assertEqual(row["artifact_path"], "releases/v1.0.0/deng-tool-rejoin-v1.0.0.tar.gz")
        self.assertEqual(len(row["artifact_sha256"]), 64)
        self.assertTrue((ROOT / row["artifact_path"]).is_file())

    def test_latest_and_test_pointers_target_patched_channels(self) -> None:
        rows = json.loads((ROOT / "data" / "rejoin_versions.json").read_text(encoding="utf-8"))
        pointers = next(r for r in rows if r.get("kind") == "channel_pointers")
        self.assertEqual(pointers.get("stable_latest"), "v1.0.0")
        self.assertEqual(pointers.get("test_latest"), "main-dev")

    def test_stable_and_test_artifacts_include_username_and_launch_patch_modules(self) -> None:
        rows = json.loads((ROOT / "data" / "rejoin_versions.json").read_text(encoding="utf-8"))
        for version in ("v1.0.0", "main-dev"):
            row = next(r for r in rows if r.get("version") == version)
            modules = _bundle_modules(row["artifact_path"])
            self.assertIn("agent.package_username", modules)
            self.assertIn("agent.launcher", modules)
            self.assertIn("agent.commands", modules)
            self.assertIn("agent.probe", modules)

    def test_artifact_sha_matches_manifest(self) -> None:
        import hashlib

        rows = json.loads((ROOT / "data" / "rejoin_versions.json").read_text(encoding="utf-8"))
        for version in ("v1.0.0", "main-dev"):
            row = next(r for r in rows if r.get("version") == version)
            actual = hashlib.sha256((ROOT / row["artifact_path"]).read_bytes()).hexdigest()
            self.assertEqual(actual, row["artifact_sha256"])


if __name__ == "__main__":
    unittest.main()
