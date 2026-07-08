"""Protected artifact entrypoint and plain-startup regression tests."""

from __future__ import annotations

import ast
import compileall
import os
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

    def test_repo_entrypoint_has_no_top_level_commands_import(self) -> None:
        src = (PROJECT / "agent" / "deng_tool_rejoin.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module in {"agent.commands", "commands"}:
                self.fail("top-level agent.commands import is forbidden")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in {"agent.commands", "agent._protected_runtime"}:
                        self.fail("top-level heavy agent import is forbidden")

    def test_repo_entrypoint_has_boot_trace_helper(self) -> None:
        src = (PROJECT / "agent" / "deng_tool_rejoin.py").read_text(encoding="utf-8")
        self.assertIn("def _boot_trace(", src)
        self.assertIn("sys.stderr.flush()", src)
        self.assertIn("DENG_BOOT_TRACE", src)

    def test_generated_init_does_not_auto_install_protected_runtime(self) -> None:
        from agent.internal_test_artifact import _load_or_create_signing_key

        rendered = _render_raw_runtime_files(
            _load_or_create_signing_key(PROJECT),
            repo_root=PROJECT,
            package_version="v1.3.0",
        )
        init_src = rendered["agent/__init__.py"]
        self.assertNotIn("_protected_runtime", init_src)
        self.assertNotIn("install()", init_src)

    def test_generated_protected_runtime_does_not_auto_install(self) -> None:
        from agent.internal_test_artifact import _load_or_create_signing_key

        rendered = _render_raw_runtime_files(
            _load_or_create_signing_key(PROJECT),
            repo_root=PROJECT,
            package_version="v1.3.0",
        )
        rt_src = rendered["agent/_protected_runtime.py"].rstrip()
        self.assertFalse(rt_src.endswith("install()"))

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

    def test_built_v130_plain_entrypoint_starts_without_traceback(self) -> None:
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
            env = os.environ.copy()
            env["DENG_DISABLE_TERMUX_HARD_EXIT"] = "1"
            env["DENG_REJOIN_HOME"] = str(root)
            env["PYTHONPATH"] = str(root)
            proc = subprocess.run(
                [sys.executable, str(root / "agent" / "deng_tool_rejoin.py")],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                input="0\n",
                check=False,
            )
            self.assertNotIn("Traceback", proc.stderr)
            self.assertNotIn("ImportError", proc.stderr)
            self.assertNotIn("TypeError", proc.stderr)
            self.assertIn(proc.returncode, {0, 1})

    def test_built_v130_contains_boot_probe(self) -> None:
        archive = PROJECT / "releases/v1.3.0/deng-tool-rejoin-v1.3.0.tar.gz"
        if not archive.is_file():
            self.skipTest("v1.3.0 artifact not built yet")
        with tarfile.open(archive, "r:gz") as tf:
            names = tf.getnames()
        self.assertIn("tools/boot_probe.py", names)

    def test_fresh_build_never_emits_double_brace_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "probe.tar.gz"
            build_internal_test_tarball(PROJECT, out, channel="stable", version="v1.3.0")
            with tarfile.open(out, "r:gz") as tf:
                entry = tf.extractfile("agent/deng_tool_rejoin.py").read().decode("utf-8")
                names = tf.getnames()
            self.assertNotIn('{{None, ""}}', entry)
            self.assertIn('if __package__ in (None, ""):', entry)
            self.assertIn("tools/boot_probe.py", names)


class InstallerWrapperStartupTests(unittest.TestCase):
    def test_wrapper_reports_startup_crash_diagnostics(self) -> None:
        from agent.bootstrap_installer import wrapper_body_sh

        body = wrapper_body_sh("https://rejoin.deng.my.id")
        self.assertIn("deng-rejoin crashed during startup", body)
        self.assertIn("DENG_BOOT_TRACE=1 deng-rejoin", body)
        self.assertIn("tools/boot_probe.py", body)


if __name__ == "__main__":
    unittest.main()
