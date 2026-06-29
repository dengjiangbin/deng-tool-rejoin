from __future__ import annotations

import re
import unittest

from agent import banner, termux_ui


class LogoColorRegressionTests(unittest.TestCase):
    OLD_MONS_ART = "\n".join([
        "█   █  ███  █  █  ███",
        "██ ██ █   █ ██ █ █",
        "█ █ █ █   █ █ ██  ██",
        "█   █ █   █ █  █    █",
        "█   █  ███  █  █ ███",
    ])
    BROKEN_MONS_LINES = (
        "\\" + " | /",
        "| " + "o" + " |",
        "/ " + "X" + " " + "\\",
        "|" + "\\ /" + "|",
        "|" + "/ \\" + "|",
        "╔" + "╦╗ ╔╗╔╗╔═",
        "║" + "║║ ║║║║╚╗",
        "╝" + "╚╝ ╚╝╝╚═╝",
        "╔" + "╦╗╔╗╔╗╔═",
        "║" + "║║║║║║╚╗",
        "╝" + "╚╝╚╝╝╚═╝",
        " ".join(("MM", "OO", "NN", "SS")),
        " ".join("MONS"),
    )

    @staticmethod
    def _mons_block(text: str) -> list[str]:
        lines = text.splitlines()
        subtitle_idx = next(i for i, line in enumerate(lines) if "Tool: Rejoin" in line)
        return lines[subtitle_idx + 1:]

    def test_logo_color_constant_is_soft_pink_with_neon_blue_outline(self):
        self.assertIn("38;5;205", banner.COLOR_LOGO)
        self.assertNotIn("95", banner.COLOR_LOGO)
        self.assertNotIn("96", banner.COLOR_LOGO)
        self.assertEqual(banner.COLOR_LOGO_OUTLINE, banner.NEON_BLUE)
        self.assertIn("96", banner.COLOR_LOGO_OUTLINE)
        self.assertEqual(termux_ui.COLOR_LOGO, termux_ui.PINK)

    def test_banner_uses_pink_logo_and_neon_blue_outline(self):
        text = banner.banner_text(use_color=True)
        first_line = text.splitlines()[0]
        self.assertTrue(first_line.startswith(banner.COLOR_LOGO))
        self.assertIn(banner.COLOR_LOGO_OUTLINE, first_line)
        self.assertNotIn("\033[30m", first_line)
        self.assertNotIn("\033[90m", first_line)
        self.assertNotIn("\033[95m", first_line)
        self.assertNotIn("\033[1;95m", first_line)

    def test_banner_contains_bold_mons_on_tool_line(self):
        text = banner.banner_text(use_color=False, terminal_width=80, version="v1.2.0")
        lines = text.splitlines()
        tool_lines = [line for line in lines if "Tool: Rejoin v1.2.0" in line]
        self.assertEqual(tool_lines, ["MONS        Tool: Rejoin v1.2.0"])
        self.assertEqual(self._mons_block(text), [])

    def test_colored_banner_mons_uses_bold_text(self):
        text = banner.banner_text(use_color=True)
        lines = text.splitlines()
        tool_line = next(line for line in lines if "Tool: Rejoin" in line)
        self.assertIn(f"{banner.BOLD}MONS{banner.RESET}", tool_line)
        self.assertIn(banner.BLUE, tool_line)
        self.assertEqual(self._mons_block(text), [])

    def test_deng_logo_remains_larger_than_mons(self):
        text = banner.banner_text(use_color=False, terminal_width=80)
        lines = text.splitlines()
        self.assertEqual(len(lines), len(banner.ASCII_DENG.splitlines()) + 1)

    def test_broken_mons_slash_x_art_is_removed(self):
        source = banner.banner_text(use_color=False, terminal_width=80)
        source += "\n" + banner.banner_text(use_color=False, terminal_width=40)
        for broken in self.BROKEN_MONS_LINES:
            self.assertNotIn(broken, source)
        self.assertNotIn(self.OLD_MONS_ART, source)
        self.assertFalse(hasattr(banner, "ASCII_MONS"))
        self.assertFalse(hasattr(banner, "ASCII_MONS_WIDE"))
        self.assertFalse(hasattr(banner, "ASCII_MONS_NARROW"))

    def test_banner_contains_deng_tool_line_and_no_block_pixel_mons(self):
        text = banner.banner_text(use_color=False, terminal_width=80)
        self.assertIn("██████╗", text)
        self.assertIn("Tool: Rejoin", text)
        self.assertNotIn("█▄█ █▀█ █▄█ █▀▀", text)
        self.assertNotIn("MM OO NN SS", text)
        self.assertNotIn("M O N S", text)

    def test_deng_logo_and_version_line_uses_runtime_version(self):
        text = banner.banner_text(use_color=False, terminal_width=80, version="v1.1.0")
        lines = text.splitlines()
        deng_lines = banner.ASCII_DENG.splitlines()
        self.assertEqual(lines[:len(deng_lines)], deng_lines)
        self.assertEqual(
            lines[len(deng_lines)],
            "MONS        Tool: Rejoin v1.1.0",
        )

    def test_banner_display_version_is_not_hardcoded_to_v1_0_0(self):
        self.assertIn("Tool: Rejoin v1.2.0", banner.banner_text(use_color=False, version="1.2.0"))
        self.assertIn("Tool: Rejoin main-dev", banner.banner_text(use_color=False, version="main-dev"))
        self.assertNotIn("Tool: Rejoin v1.0.0", banner.banner_text(use_color=False, version="1.2.0"))

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
        # "Top Menu" header removed per user request (p-1bc476d931).
        self.assertNotIn("Top Menu", text)
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
