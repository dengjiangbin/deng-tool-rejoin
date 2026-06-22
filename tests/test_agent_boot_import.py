"""Regression: ``import agent`` must boot without eager submodule re-exports."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


class AgentBootImportTests(unittest.TestCase):
    def _run_py(self, code: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(PROJECT),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_import_agent_package(self) -> None:
        proc = self._run_py("import agent; assert agent.__version__")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)

    def test_cookie_detect_imports_from_roblox_presence(self) -> None:
        proc = self._run_py(
            "from agent.roblox_presence import detect_roblox_cookie, roblox_cookie_detect; "
            "assert callable(detect_roblox_cookie); "
            "assert roblox_cookie_detect is detect_roblox_cookie"
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)

    def test_commands_main_imports_cleanly(self) -> None:
        proc = self._run_py("from agent.commands import main; assert callable(main)")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)

    def test_init_does_not_eagerly_import_cookie_detect(self) -> None:
        proc = self._run_py(
            "import agent; "
            "assert 'roblox_cookie_detect' not in agent.__dict__"
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)

    def test_protected_bundle_includes_roblox_presence(self) -> None:
        from agent.internal_test_artifact import iter_internal_test_pack_files

        rels = {rel for rel, _path in iter_internal_test_pack_files(PROJECT)}
        self.assertIn("agent/roblox_presence.py", rels)
        self.assertNotIn("agent/roblox_cookie_detect.py", rels)

    def test_subprocess_import_agent_exit_zero(self) -> None:
        proc = self._run_py("import agent")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)


if __name__ == "__main__":
    unittest.main()
