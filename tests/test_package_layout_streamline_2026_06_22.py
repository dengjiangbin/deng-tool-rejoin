"""Regression: package dashboard shows only #, package name, and username."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from agent import termux_ui
from agent.commands import build_start_table


class PackageLayoutStreamlineTests(unittest.TestCase):
    def test_build_start_table_three_columns_only(self) -> None:
        table = build_start_table(
            [(1, "com.moons.litesc", "JBDENG8", "Online", "01:02:03", "120 MB")],
            use_color=False,
        )
        header = next(line for line in table.splitlines() if "Package" in line)
        cols = [c.strip() for c in header.split("│") if c.strip()]
        self.assertEqual(cols, ["#", "Package", "Username"])
        self.assertNotIn("Online", table)
        self.assertNotIn("120 MB", table)

    def test_fit_line_clamps_package_menu_row(self) -> None:
        long_pkg = "com.moons.litesc" + ("X" * 64)
        with mock.patch("agent.safe_io.terminal_columns", return_value=50):
            line = termux_ui.fit_line(f"  [1] {long_pkg} | username: user123")
        self.assertLessEqual(termux_ui.visible_len(line), 50)
        self.assertTrue(line.endswith("..."))


if __name__ == "__main__":
    unittest.main()
