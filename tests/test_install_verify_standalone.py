"""Standalone install verifier — no agent imports (Termux segfault regression)."""

from __future__ import annotations

import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


class StandaloneInstallVerifyTests(unittest.TestCase):
    def test_verifier_passes_on_built_v130_artifact(self) -> None:
        archive = PROJECT / "releases/v1.3.0/deng-tool-rejoin-v1.3.0.tar.gz"
        if not archive.is_file():
            self.skipTest("v1.3.0 artifact not built yet")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(root)
            script = root / "agent" / "install_verify_standalone.py"
            self.assertTrue(script.is_file(), "artifact must ship standalone verifier")
            proc = subprocess.run(
                [sys.executable, str(script)],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
            )

    def test_verifier_does_not_import_agent_package(self) -> None:
        src = (PROJECT / "agent" / "install_verify_standalone.py").read_text(encoding="utf-8")
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            self.assertNotRegex(stripped, r"^(from|import)\s+agent\b")

    def test_artifact_includes_standalone_verifier(self) -> None:
        from agent.internal_test_artifact import _ALLOWED_ARTIFACT_PATHS

        self.assertIn("agent/install_verify_standalone.py", _ALLOWED_ARTIFACT_PATHS)


if __name__ == "__main__":
    unittest.main()
