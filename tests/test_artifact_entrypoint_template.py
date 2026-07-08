"""Protected artifact entrypoint must not ship invalid template-escaped Python."""

from __future__ import annotations

import compileall
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.internal_test_artifact import _render_raw_runtime_files, build_internal_test_tarball


class ArtifactEntrypointTemplateTests(unittest.TestCase):
    def _rendered_entrypoint(self) -> str:
        from agent.internal_test_artifact import _load_or_create_signing_key

        rendered = _render_raw_runtime_files(
            _load_or_create_signing_key(PROJECT),
            repo_root=PROJECT,
            package_version="v1.3.0",
        )
        return rendered["agent/deng_tool_rejoin.py"]

    def test_generated_entrypoint_has_no_double_brace_package_check(self) -> None:
        src = self._rendered_entrypoint()
        self.assertNotIn('{{None, ""}}', src)
        self.assertIn('if __package__ in (None, ""):', src)

    def test_generated_entrypoint_has_no_template_double_braces(self) -> None:
        src = self._rendered_entrypoint()
        self.assertNotIn("{{", src)
        self.assertNotIn("}}", src)

    def test_repo_entrypoint_source_is_clean(self) -> None:
        src = (PROJECT / "agent" / "deng_tool_rejoin.py").read_text(encoding="utf-8")
        self.assertNotIn('{{None, ""}}', src)
        self.assertIn('if __package__ in (None, ""):', src)

    def test_built_v130_artifact_entrypoint_compiles(self) -> None:
        archive = PROJECT / "releases/v1.3.0/deng-tool-rejoin-v1.3.0.tar.gz"
        if not archive.is_file():
            self.skipTest("v1.3.0 artifact not built yet")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(root)
            ok = compileall.compile_dir(str(root), quiet=1)
            self.assertTrue(ok)
            entry = (root / "agent" / "deng_tool_rejoin.py").read_text(encoding="utf-8")
            self.assertNotIn('{{None, ""}}', entry)

    def test_built_v130_entrypoint_version_exits_zero(self) -> None:
        archive = PROJECT / "releases/v1.3.0/deng-tool-rejoin-v1.3.0.tar.gz"
        if not archive.is_file():
            self.skipTest("v1.3.0 artifact not built yet")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(root)
            import json

            sha = json.loads((root / ".deng_build.json").read_text(encoding="utf-8"))["artifact_sha"]
            (root / ".installed-build.json").write_text(
                json.dumps({"artifact_sha256": sha, "version": "v1.3.0"}),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, str(root / "agent" / "deng_tool_rejoin.py"), "version"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertRegex(proc.stdout, r"(?m)^artifact_sha=[0-9a-f]{64}$")

    def test_fresh_build_never_emits_double_brace_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "probe.tar.gz"
            build_internal_test_tarball(PROJECT, out, channel="stable", version="v1.3.0")
            with tarfile.open(out, "r:gz") as tf:
                entry = tf.extractfile("agent/deng_tool_rejoin.py").read().decode("utf-8")
            self.assertNotIn('{{None, ""}}', entry)
            self.assertIn('if __package__ in (None, ""):', entry)


if __name__ == "__main__":
    unittest.main()
