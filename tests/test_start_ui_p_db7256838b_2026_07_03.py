"""Regression tests for probe p-db7256838b Start UI issues."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


class StartPrepImportOrderTests(unittest.TestCase):
    def test_start_prep_deadline_imported_before_use(self) -> None:
        source = (PROJECT / "agent" / "commands.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        cmd_start = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "cmd_start":
                cmd_start = node
                break
        self.assertIsNotNone(cmd_start)
        assert cmd_start is not None
        prep_use_line = None
        prep_import_line = None
        for node in ast.walk(cmd_start):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "run_callable_with_deadline":
                    for arg in node.args:
                        if isinstance(arg, ast.Name) and arg.id == "START_PREP_DEADLINE_S":
                            prep_use_line = node.lineno
            if isinstance(node, ast.ImportFrom):
                if node.module == "cache_clear_phases":
                    for alias in node.names:
                        if alias.name == "START_PREP_DEADLINE_S":
                            prep_import_line = node.lineno
        self.assertIsNotNone(prep_use_line, "START_PREP_DEADLINE_S use not found in cmd_start")
        self.assertIsNotNone(prep_import_line, "START_PREP_DEADLINE_S import not found in cmd_start")
        assert prep_use_line is not None and prep_import_line is not None
        self.assertLess(
            prep_import_line,
            prep_use_line,
            "START_PREP_DEADLINE_S must be imported before run_callable_with_deadline uses it",
        )

    def test_cmd_start_initializes_ram_cache(self) -> None:
        source = (PROJECT / "agent" / "commands.py").read_text(encoding="utf-8")
        self.assertIn('_ram_cache: dict[str, Any] = {"info": None, "next_update": 0.0}', source)


class PhaseTableStateTests(unittest.TestCase):
    def test_header_only_skipped_after_cache_clear_closed(self) -> None:
        source = (PROJECT / "agent" / "commands.py").read_text(encoding="utf-8")
        self.assertIn("is_cache_clear_closed()", source)
        self.assertIn(
            "if hdr in _HEADER_ONLY_PHASES and not _sl_row.is_cache_clear_closed():",
            source,
        )


if __name__ == "__main__":
    unittest.main()
