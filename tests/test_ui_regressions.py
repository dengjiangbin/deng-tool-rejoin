from __future__ import annotations

import re
import unittest

from agent import banner, termux_ui


class LogoColorRegressionTests(unittest.TestCase):
    def test_logo_color_constant_is_soft_pink_not_bright_magenta_or_cyan(self):
        self.assertIn("38;5;205", banner.COLOR_LOGO)
        self.assertNotIn("95", banner.COLOR_LOGO)
        self.assertNotIn("96", banner.COLOR_LOGO)
        self.assertEqual(termux_ui.COLOR_LOGO, termux_ui.PINK)

    def test_banner_uses_pink_logo(self):
        text = banner.banner_text(use_color=True)
        first_line = text.splitlines()[0]
        self.assertTrue(first_line.startswith(banner.COLOR_LOGO))
        self.assertNotIn("\033[1;96m", first_line)
        self.assertNotIn("\033[95m", first_line)
        self.assertNotIn("\033[1;95m", first_line)

    def test_banner_contains_small_mons_after_subtitle(self):
        text = banner.banner_text(use_color=False)
        lines = text.splitlines()
        subtitle_idx = next(i for i, line in enumerate(lines) if "Tool: Rejoin" in line)
        self.assertIn("MONS", lines[subtitle_idx + 1])
        self.assertLessEqual(len(lines[subtitle_idx + 1].strip()), 4)

    def test_banner_mons_uses_grey_when_colored(self):
        text = banner.banner_text(use_color=True)
        lines = text.splitlines()
        subtitle_idx = next(i for i, line in enumerate(lines) if "Tool: Rejoin" in line)
        mons_line = lines[subtitle_idx + 1]
        self.assertIn("MONS", mons_line)
        self.assertIn("\033[90m", mons_line)


class SeparatorRegressionTests(unittest.TestCase):
    def test_separator_visible_width_not_half_length(self):
        sep = termux_ui.separator("-", width=60)
        plain = termux_ui.ANSI_RE.sub("", sep)
        self.assertEqual(len(plain), 30)
        self.assertGreaterEqual(len(plain), 18)

    def test_separator_ignores_ansi_codes_for_visible_length(self):
        sep = termux_ui.separator("-", width=50)
        self.assertEqual(termux_ui.visible_len(sep), 25)
        self.assertEqual(len(re.sub(r"\x1b\[[0-9;]*m", "", sep)), 25)

    def test_separator_clamps_to_termux_safe_width(self):
        self.assertEqual(termux_ui.visible_len(termux_ui.separator("-", width=10)), 18)
        self.assertEqual(termux_ui.visible_len(termux_ui.separator("-", width=200)), 36)


if __name__ == "__main__":
    unittest.main()
