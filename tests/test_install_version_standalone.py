"""Install-safe version command and installer final verification regressions."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tarfile
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.bootstrap_installer import render_direct_install_bootstrap


class InstallerScriptGuards(unittest.TestCase):
    def _script(self) -> str:
        return render_direct_install_bootstrap(
            base_url="https://rejoin.deng.my.id",
            package_sha256="a" * 64,
            version_label="v1.3.0",
            channel="stable",
            token_endpoint="/install/v1.3.0/package-token",
            installer_endpoint="/install/v1.3.0",
        )

    def test_no_pythonpath_inline_agent_imports(self) -> None:
        s = self._script()
        self.assertNotIn('PYTHONPATH="$h" python3 -c', s)
        self.assertNotIn("python3 -c 'import agent", s)
        self.assertNotIn("import agent._protected_runtime", s)
        self.assertNotIn("from agent import", s)

    def test_uses_standalone_verifier_and_version_script(self) -> None:
        s = self._script()
        self.assertIn("install_verify_standalone.py", s)
        self.assertIn("version_standalone.py", s)
        self.assertIn(".deng_build.json", s)

    def test_parses_artifact_sha_equals_line(self) -> None:
        s = self._script()
        self.assertIn('grep -qE "^artifact_sha=[0-9a-f]{64}$"', s)
        self.assertIn('sed "s/^artifact_sha=//"', s)

    def test_version_failure_prints_diagnostics(self) -> None:
        s = self._script()
        self.assertIn("version check did not return artifact_sha", s)
        self.assertIn("stdout (first 40 lines)", s)
        self.assertIn("stderr (first 40 lines)", s)
        self.assertIn("version_standalone.py:", s)

    def test_wrapper_routes_version_before_entrypoint(self) -> None:
        s = self._script()
        self.assertIn('case "$1" in version|--version)', s)
        self.assertIn('exec python3 "$DENG_REJOIN_HOME/agent/version_standalone.py"', s)


class VersionStandaloneTests(unittest.TestCase):
    def test_source_has_no_agent_imports(self) -> None:
        src = (PROJECT / "agent" / "version_standalone.py").read_text(encoding="utf-8")
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            self.assertNotRegex(stripped, r"^(from|import)\s+agent\b")

    def test_prints_artifact_sha_line_from_installed_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agent").mkdir()
            sha = "b" * 64
            (root / ".installed-build.json").write_text(
                json.dumps({"artifact_sha256": sha, "version": "v1.3.0", "channel": "stable"}),
                encoding="utf-8",
            )
            script = root / "agent" / "version_standalone.py"
            script.write_text(
                (PROJECT / "agent" / "version_standalone.py").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, str(script), "version"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertRegex(proc.stdout, rf"(?m)^artifact_sha={sha}$")

    def test_version_on_built_v130_artifact(self) -> None:
        archive = PROJECT / "releases/v1.3.0/deng-tool-rejoin-v1.3.0.tar.gz"
        if not archive.is_file():
            self.skipTest("v1.3.0 artifact not built yet")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(root)
            sha = json.loads((root / ".deng_build.json").read_text(encoding="utf-8"))["artifact_sha"]
            (root / ".installed-build.json").write_text(
                json.dumps({"artifact_sha256": sha, "version": "v1.3.0", "channel": "stable"}),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, str(root / "agent" / "version_standalone.py"), "version"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertRegex(proc.stdout, rf"(?m)^artifact_sha={re.escape(sha)}$")

    @unittest.skipUnless(shutil.which("bash"), "bash required for shell wrapper test")
    def test_deng_rejoin_wrapper_version_does_not_import_agent_commands(self) -> None:
        from agent.bootstrap_installer import wrapper_body_sh

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agent").mkdir()
            sha = "c" * 64
            for name in ("version_standalone.py", "deng_tool_rejoin.py"):
                (root / "agent" / name).write_text(
                    (PROJECT / "agent" / name).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            (root / ".installed-build.json").write_text(
                json.dumps({"artifact_sha256": sha}),
                encoding="utf-8",
            )
            wrapper = root / "deng-rejoin"
            wrapper.write_text(
                wrapper_body_sh("https://rejoin.deng.my.id"),
                encoding="utf-8",
            )
            proc = subprocess.run(
                ["bash", str(wrapper), "version"],
                cwd=str(root),
                env={**dict(__import__("os").environ), "DENG_REJOIN_HOME": str(root)},
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            self.assertRegex(proc.stdout, rf"(?m)^artifact_sha={sha}$")


class DengToolRejoinEntrypointTests(unittest.TestCase):
    def test_entrypoint_dispatches_version_before_commands_import(self) -> None:
        src = (PROJECT / "agent" / "deng_tool_rejoin.py").read_text(encoding="utf-8")
        version_idx = src.find("_dispatch_install_safe_version")
        commands_idx = src.find("from agent.commands import main")
        self.assertGreater(version_idx, -1)
        self.assertGreater(commands_idx, -1)
        self.assertLess(version_idx, commands_idx)

    def test_entrypoint_version_dispatch_runs_standalone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent_dir = root / "agent"
            agent_dir.mkdir()
            sha = "d" * 64
            for name in ("version_standalone.py", "deng_tool_rejoin.py"):
                (agent_dir / name).write_text(
                    (PROJECT / "agent" / name).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            (root / ".installed-build.json").write_text(
                json.dumps({"artifact_sha256": sha}),
                encoding="utf-8",
            )

            def _boom(*_a, **_k):
                raise AssertionError("agent.commands must not import for version")

            with patch.dict(sys.modules, {"agent": type(sys)("agent")}):
                with patch("builtins.__import__", side_effect=_boom):
                    proc = subprocess.run(
                        [sys.executable, str(agent_dir / "deng_tool_rejoin.py"), "version"],
                        cwd=str(root),
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertRegex(proc.stdout, rf"(?m)^artifact_sha={sha}$")


if __name__ == "__main__":
    unittest.main()
