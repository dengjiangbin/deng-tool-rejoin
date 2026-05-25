"""Small, dependency-free Termux banner."""

from __future__ import annotations

import os
import re
import shutil
import sys

from .constants import PRODUCT_NAME, VERSION

BLUE = "\033[1;94m"
NEON_BLUE = "\033[1;96m"
PINK = "\033[38;5;205m"
GREY = "\033[90m"
BOLD = "\033[1m"
COLOR_LOGO = PINK
COLOR_LOGO_OUTLINE = NEON_BLUE
RESET = "\033[0m"

ASCII_DENG = r"""
██████╗ ███████╗███╗   ██╗ ██████╗
██╔══██╗██╔════╝████╗  ██║██╔════╝
██║  ██║█████╗  ██╔██╗ ██║██║  ███╗
██║  ██║██╔══╝  ██║╚██╗██║██║   ██║
██████╔╝███████╗██║ ╚████║╚██████╔╝
╚═════╝ ╚══════╝╚═╝  ╚═══╝ ╚═════╝
""".strip("\n")

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def supports_color() -> bool:
    """Return true when ANSI color is likely useful."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "").lower() in {"dumb", ""} and not os.environ.get("TERMUX_VERSION"):
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)()) or bool(os.environ.get("TERMUX_VERSION"))


def visible_width(text: str) -> int:
    """Return printable width after removing ANSI sequences."""
    return len(ANSI_RE.sub("", text))


def visible_footprint(text: str) -> dict[str, int]:
    """Measure the actual terminal row/column footprint of rendered text."""
    plain_lines = [ANSI_RE.sub("", line) for line in text.splitlines()]
    lines = [line for line in plain_lines if line.strip()]
    height = len(lines)
    width = max((len(line) for line in lines), default=0)
    return {
        "height": height,
        "width": width,
        "area": height * width,
        "occupied": sum(1 for line in lines for ch in line if ch != " "),
    }


def _terminal_width(terminal_width: int | None = None) -> int:
    if terminal_width is not None:
        return max(1, int(terminal_width))
    return shutil.get_terminal_size((80, 24)).columns


def _color_deng_line(line: str) -> str:
    """Color DENG fill pink and box/outline glyphs neon blue."""
    out: list[str] = []
    active = ""
    for ch in line:
        color = COLOR_LOGO_OUTLINE if ch in "╗╔║╝╚═" else COLOR_LOGO
        if color != active:
            out.append(color)
            active = color
        out.append(ch)
    if active:
        out.append(RESET)
    return "".join(out)


def banner_text(use_color: bool | None = None, terminal_width: int | None = None) -> str:
    """Build the DENG banner with optional soft pink logo styling."""
    if use_color is None:
        use_color = supports_color()
    if use_color:
        colored_lines = [_color_deng_line(line) for line in ASCII_DENG.splitlines()]
        logo = "\n".join(colored_lines)
    else:
        logo = ASCII_DENG
    logo_width = max(visible_width(line) for line in ASCII_DENG.splitlines())
    subtitle_text = f"{PRODUCT_NAME.replace('DENG Tool: ', 'Tool: ')} v{VERSION}"
    gap = " " * 8
    if use_color:
        line = f"{BOLD}MONS{RESET}{gap}{BLUE}{subtitle_text}{RESET}"
    else:
        line = f"MONS{gap}{subtitle_text}"
    return f"{logo}\n{line}"


def print_banner(use_color: bool | None = None) -> None:
    """Print the product banner."""
    text = banner_text(use_color=use_color)
    try:
        print(text)
    except UnicodeEncodeError:
        if hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write(text.encode("utf-8") + b"\n")
            sys.stdout.buffer.flush()
        else:
            print(text.encode("ascii", errors="replace").decode("ascii"))
