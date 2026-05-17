"""Built artifact must embed BUILD-INFO.json proof."""

from __future__ import annotations

import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.internal_test_artifact import (
    _make_build_info_bytes,
    build_internal_test_tarball,
    verify_tarball_exclusions,
)


class BuildInfoBytesTests(unittest.TestCase):
    def test_payload_has_required_keys(self) -> None:
        raw = _make_build_info_bytes(PROJECT)
        obj = json.loads(raw)
        for key in ("channel", "git_commit", "built_at_iso", "built_at_unix", "product"):
            self.assertIn(key, obj)
        self.assertEqual(obj["channel"], "main-dev")
        self.assertEqual(obj["product"], "DENG Tool: Rejoin")

    def test_payload_is_valid_json(self) -> None:
        raw = _make_build_info_bytes(PROJECT)
        # Round-trip without error.
        json.loads(raw)


class TarballEmbedsBuildInfoTests(unittest.TestCase):
    def test_built_tarball_contains_build_info_at_top_level(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "pkg.tar.gz"
            sha = build_internal_test_tarball(PROJECT, out)
            self.assertEqual(len(sha), 64)
            with tarfile.open(out, "r:gz") as tf:
                names = tf.getnames()
            self.assertIn("BUILD-INFO.json", names)

    def test_build_info_is_parseable_inside_tarball(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "pkg.tar.gz"
            build_internal_test_tarball(PROJECT, out)
            with tarfile.open(out, "r:gz") as tf:
                member = tf.getmember("BUILD-INFO.json")
                data = tf.extractfile(member).read()
            obj = json.loads(data)
            self.assertIn("git_commit", obj)
            self.assertIn("built_at_iso", obj)


class VerifyExclusionsTests(unittest.TestCase):
    def test_rejects_tarball_without_build_info(self) -> None:
        # Build a stub tarball missing BUILD-INFO.json and verify the helper
        # rejects it loudly.
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            data = b"# stub\n"
            ti = tarfile.TarInfo(name="agent/deng_tool_rejoin.py")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        with self.assertRaisesRegex(AssertionError, "BUILD-INFO.json"):
            verify_tarball_exclusions(buf.getvalue())

    def test_accepts_tarball_with_build_info(self) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            data = b"# stub\n"
            ti = tarfile.TarInfo(name="agent/deng_tool_rejoin.py")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
            bi = b'{"channel":"main-dev"}'
            ti2 = tarfile.TarInfo(name="BUILD-INFO.json")
            ti2.size = len(bi)
            tf.addfile(ti2, io.BytesIO(bi))
        # Should not raise.
        verify_tarball_exclusions(buf.getvalue())


if __name__ == "__main__":
    unittest.main()
