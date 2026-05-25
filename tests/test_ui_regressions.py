from __future__ import annotations

import re
import unittest

from agent import banner, termux_ui


class LogoColorRegressionTests(unittest.TestCase):
    BROKEN_MONS_LINES = (
        r"\ | /",
        "| o |",
        r"/ X \\",
        r"|\ /|",
        r"|/ \|",
        "╔╦╗ ╔╗╔╗╔═",
        "║║║ ║║║║╚╗",
        "╝╚╝ ╚╝╝╚═╝",
        "╔╦╗╔╗╔╗╔═",
        "║║║║║║║╚╗",
        "╝╚╝╚╝╝╚═╝",
    )

    @staticmethod
    def _mons_block(text: str) -> list[str]:
        lines = text.splitlines()
        subtitle_idx = next(i for i, line in enumerate(lines) if "Tool: Rejoin" in line)
        return lines[subtitle_idx + 1:]

    @staticmethod
    def _visual_mass(lines: list[str]) -> int:
        return sum(sum(1 for ch in line if not ch.isspace()) for line in lines)

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
        text = banner.banner_text(use_color=False, terminal_width=80)
        mons_block = self._mons_block(text)
        self.assertEqual(mons_block, banner.ASCII_MONS_WIDE.splitlines())
        self.assertEqual(len(mons_block), 1)
        self.assertNotIn("MONS", mons_block)
        self.assertLessEqual(max(len(line) for line in mons_block), 11)
        self.assertFalse(mons_block[0].startswith(" "))

    def test_banner_mons_uses_grey_when_colored(self):
        text = banner.banner_text(use_color=True)
        lines = text.splitlines()
        subtitle_idx = next(i for i, line in enumerate(lines) if "Tool: Rejoin" in line)
        mons_line = lines[subtitle_idx + 1]
        self.assertIn("\033[90m", mons_line)

    def test_deng_logo_remains_larger_than_mons(self):
        text = banner.banner_text(use_color=False, terminal_width=80)
        lines = text.splitlines()
        subtitle_idx = next(i for i, line in enumerate(lines) if "Tool: Rejoin" in line)
        logo_width = max(len(line) for line in lines[:subtitle_idx])
        mons_width = max(len(line) for line in lines[subtitle_idx + 1:])
        self.assertGreater(logo_width, mons_width)
        self.assertLessEqual(mons_width, logo_width // 3)

    def test_broken_mons_slash_x_art_is_removed(self):
        source = banner.banner_text(use_color=False, terminal_width=80)
        source += "\n" + banner.banner_text(use_color=False, terminal_width=40)
        module_art = "\n".join((banner.ASCII_MONS_WIDE, banner.ASCII_MONS_NARROW))
        for broken in self.BROKEN_MONS_LINES:
            self.assertNotIn(broken, source)
            self.assertNotIn(broken, module_art)
        self.assertNotIn("X", module_art)
        self.assertNotIn("╔╦╗", module_art)

    def test_banner_contains_deng_subtitle_and_block_pixel_mons(self):
        text = banner.banner_text(use_color=False, terminal_width=80)
        self.assertIn("██████╗", text)
        self.assertIn("Tool: Rejoin", text)
        mons = "\n".join(self._mons_block(text))
        self.assertNotIn("\nMONS\n", f"\n{text}\n")
        self.assertIn("MM OO NN SS", mons)
        self.assertNotIn("10 OnS", mons)
        self.assertNotIn("1ONS", mons)
        self.assertNotIn("M0NS", mons)

    def test_mons_does_not_use_red_or_error_color(self):
        text = banner.banner_text(use_color=True, terminal_width=80)
        mons = "\n".join(self._mons_block(text))
        self.assertIn(banner.GREY, mons)
        self.assertNotIn("\033[31m", mons)
        self.assertNotIn("\033[1;31m", mons)
        self.assertNotIn("\033[91m", mons)
        self.assertNotIn("\033[1;91m", mons)

    def test_mons_has_wide_and_narrow_block_rendering(self):
        self.assertEqual(banner.mons_logo_for_width(80), banner.ASCII_MONS_WIDE)
        self.assertEqual(banner.mons_logo_for_width(40), banner.ASCII_MONS_NARROW)
        for logo in (banner.ASCII_MONS_WIDE, banner.ASCII_MONS_NARROW):
            self.assertNotIn("MONS", logo.splitlines())
            self.assertNotIn("╔╦╗", logo)
            self.assertNotIn("10 OnS", logo)
            self.assertNotIn("1ONS", logo)

    def test_narrow_mons_does_not_wrap_badly(self):
        text = banner.banner_text(use_color=False, terminal_width=40)
        mons_block = self._mons_block(text)
        self.assertEqual(mons_block, banner.ASCII_MONS_NARROW.splitlines())
        self.assertEqual(mons_block, ["MM OO NN SS"])
        self.assertLessEqual(max(len(line) for line in mons_block), 11)
        self.assertTrue(all(len(line) <= 40 for line in text.splitlines()))

    def test_mons_is_tiny_companion_logo(self):
        text = banner.banner_text(use_color=False, terminal_width=80)
        lines = text.splitlines()
        subtitle_idx = next(i for i, line in enumerate(lines) if "Tool: Rejoin" in line)
        deng_mass = self._visual_mass(lines[:subtitle_idx])
        mons_mass = self._visual_mass(lines[subtitle_idx + 1:])
        ratio = mons_mass / deng_mass
        old_mons_mass = self._visual_mass([
            "█   █  ███  █  █  ███",
            "██ ██ █   █ ██ █ █",
            "█ █ █ █   █ █ ██  ██",
            "█   █ █   █ █  █    █",
            "█   █  ███  █  █ ███",
        ])
        self.assertLessEqual(mons_mass, old_mons_mass // 4 + 2)
        self.assertGreaterEqual(ratio, 0.03)
        self.assertLessEqual(ratio, 0.12)

    def test_deng_logo_and_version_line_remain_unchanged(self):
        text = banner.banner_text(use_color=False, terminal_width=80)
        lines = text.splitlines()
        deng_lines = banner.ASCII_DENG.splitlines()
        self.assertEqual(lines[:len(deng_lines)], deng_lines)
        self.assertEqual(
            lines[len(deng_lines)],
            "Tool: Rejoin v1.0.0".center(max(len(line) for line in deng_lines)),
        )

    def test_top_menu_still_renders_after_banner(self):
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch

        from agent import menu
        from agent.config import default_config, validate_config

        cfg = validate_config(default_config())
        cfg["roblox_packages"] = [{"package": "com.roblox.client", "enabled": True}]
        out = io.StringIO()
        with patch("agent.menu.load_config", return_value=cfg), redirect_stdout(out):
            menu.print_menu(type("Args", (), {"no_color": True})(), [])
        text = out.getvalue()
        self.assertIn("Tool: Rejoin", text)
        self.assertIn("Top Menu", text)
        self.assertIn("First Time Setup Config", text)


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
