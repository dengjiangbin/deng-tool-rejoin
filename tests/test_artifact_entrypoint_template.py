"""Protected artifact entrypoint and Termux source-runtime regression tests."""

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
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.internal_test_artifact import (
    _SOURCE_RUNTIME_REQUIRED,
    _render_raw_runtime_files,
    build_internal_test_tarball,
    expected_artifact_paths,
    iter_internal_test_pack_files,
)


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

    def test_repo_entrypoint_has_runtime_mode_and_termux_detection(self) -> None:
        src = (PROJECT / "agent" / "deng_tool_rejoin.py").read_text(encoding="utf-8")
        self.assertIn("def _detect_termux(", src)
        self.assertIn("def _resolve_runtime_mode(", src)
        self.assertIn("DENG_RUNTIME_MODE", src)
        self.assertIn("using source runtime, protected runtime skipped", src)
        self.assertIn("def _boot_trace(", src)

    def test_auto_mode_chooses_source_on_termux(self) -> None:
        import agent.deng_tool_rejoin as entry

        with patch.object(entry, "_detect_termux", return_value=True):
            with patch.dict(os.environ, {"DENG_RUNTIME_MODE": "auto"}, clear=False):
                self.assertEqual(entry._resolve_runtime_mode(), "source")

    def test_auto_mode_chooses_protected_off_termux(self) -> None:
        import agent.deng_tool_rejoin as entry

        with patch.object(entry, "_detect_termux", return_value=False):
            with patch.dict(os.environ, {"DENG_RUNTIME_MODE": "auto"}, clear=False):
                self.assertEqual(entry._resolve_runtime_mode(), "protected")

    def test_forced_source_mode_skips_protected_install(self) -> None:
        import agent.deng_tool_rejoin as entry

        calls: list[str] = []

        def _trace(label: str) -> None:
            calls.append(label)

        with patch.object(entry, "_boot_trace", side_effect=_trace):
            with patch.object(entry, "_install_protected_runtime") as protected:
                mode = entry._bootstrap_runtime("source")
        self.assertEqual(mode, "source")
        protected.assert_not_called()
        self.assertIn("using source runtime, protected runtime skipped", calls)

    def test_forced_protected_mode_installs_runtime(self) -> None:
        import agent.deng_tool_rejoin as entry

        with patch.object(entry, "_install_protected_runtime", return_value=True) as protected:
            mode = entry._bootstrap_runtime("protected")
        self.assertEqual(mode, "protected")
        protected.assert_called_once()

    def test_built_v130_artifact_contains_required_source_files(self) -> None:
        archive = PROJECT / "releases/v1.3.0/deng-tool-rejoin-v1.3.0.tar.gz"
        if not archive.is_file():
            self.skipTest("v1.3.0 artifact not built yet")
        with tarfile.open(archive, "r:gz") as tf:
            names = set(tf.getnames())
        for required in _SOURCE_RUNTIME_REQUIRED:
            self.assertIn(required, names, msg=f"missing {required}")

    def test_built_v130_artifact_entrypoint_compiles(self) -> None:
        archive = PROJECT / "releases/v1.3.0/deng-tool-rejoin-v1.3.0.tar.gz"
        if not archive.is_file():
            self.skipTest("v1.3.0 artifact not built yet")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(root)
            ok = compileall.compile_dir(str(root / "agent"), quiet=1)
            self.assertTrue(ok)

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

    def test_built_v130_source_mode_plain_entrypoint_starts(self) -> None:
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
            env["DENG_RUNTIME_MODE"] = "source"
            env["DENG_BOOT_TRACE"] = "1"
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
            self.assertIn("using source runtime, protected runtime skipped", proc.stderr)
            self.assertIn("after import agent.commands", proc.stderr)
            self.assertIn(proc.returncode, {0, 1})

    def test_fresh_build_ships_source_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "probe.tar.gz"
            build_internal_test_tarball(PROJECT, out, channel="stable", version="v1.3.0")
            with tarfile.open(out, "r:gz") as tf:
                names = set(tf.getnames())
            client_rels = [rel for rel, _ in iter_internal_test_pack_files(PROJECT)]
            self.assertEqual(names, expected_artifact_paths(client_rels))
            for required in _SOURCE_RUNTIME_REQUIRED:
                self.assertIn(required, names)


class InstallerWrapperStartupTests(unittest.TestCase):
    def test_wrapper_reports_protected_crash_and_source_hint(self) -> None:
        from agent.bootstrap_installer import wrapper_body_sh

        body = wrapper_body_sh("https://rejoin.deng.my.id")
        self.assertIn("deng-rejoin crashed during startup", body)
        self.assertIn("Protected runtime module import", body)
        self.assertIn("DENG_RUNTIME_MODE=source deng-rejoin", body)
        self.assertIn("tools/boot_probe.py", body)


class BootProbeScriptTests(unittest.TestCase):
    def test_boot_probe_includes_source_and_protected_splits(self) -> None:
        src = (PROJECT / "tools" / "boot_probe.py").read_text(encoding="utf-8")
        for token in (
            "protected_import_commands_unsafe",
            "source_import_commands",
            "source_import_supervisor",
            "source_import_roblox_presence",
            "entrypoint_plain_menu_auto",
            "entrypoint_plain_menu_source",
            "entrypoint_plain_menu_protected_unsafe",
        ):
            self.assertIn(token, src)


if __name__ == "__main__":
    unittest.main()
