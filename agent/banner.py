"""Small, dependency-free Termux banner."""

from __future__ import annotations

import os
import re
import shutil
import sys

from .constants import PRODUCT_NAME, VERSION

BLUE = "\033[1;94m"
PINK = "\033[38;5;205m"
GREY = "\033[90m"
MONS_COLOR = "\033[2;38;5;240m"
COLOR_LOGO = PINK
RESET = "\033[0m"

ASCII_DENG = r"""
██████╗ ███████╗███╗   ██╗ ██████╗
██╔══██╗██╔════╝████╗  ██║██╔════╝
██║  ██║█████╗  ██╔██╗ ██║██║  ███╗
██║  ██║██╔══╝  ██║╚██╗██║██║   ██║
██████╔╝███████╗██║ ╚████║╚██████╔╝
╚═════╝ ╚══════╝╚═╝  ╚═══╝ ╚═════╝
""".strip("\n")

ASCII_MONS_COMPACT = r"""
█▄█ █▀█ █▄█ █▀▀
█ █ █ █ ███ ▀▀█
█ █ █▄█ █▀█ ▄▄█
""".strip("\n")

ASCII_MONS_WIDE = ASCII_MONS_COMPACT
ASCII_MONS_NARROW = ASCII_MONS_COMPACT

ASCII_MONS = ASCII_MONS_COMPACT

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


def mons_logo_for_width(terminal_width: int | None = None) -> str:
    """Return the approved compact MONS pixel mark for every terminal width."""
    width = _terminal_width(terminal_width)
    return ASCII_MONS_NARROW if width < 24 else ASCII_MONS_WIDE


def banner_text(use_color: bool | None = None, terminal_width: int | None = None) -> str:
    """Build the DENG banner with optional soft pink logo styling."""
    if use_color is None:
        use_color = supports_color()
    if use_color:
        colored_lines = [f"{COLOR_LOGO}{line}{RESET}" for line in ASCII_DENG.splitlines()]
        logo = "\n".join(colored_lines)
    else:
        logo = ASCII_DENG
    logo_width = max(visible_width(line) for line in ASCII_DENG.splitlines())
    subtitle_text = f"{PRODUCT_NAME.replace('DENG Tool: ', 'Tool: ')} v{VERSION}"
    mons_logo = mons_logo_for_width(terminal_width)
    if use_color:
        subtitle = f"{BLUE}{subtitle_text.center(logo_width)}{RESET}"
        mons = "\n".join(f"{MONS_COLOR}{line}{RESET}" for line in mons_logo.splitlines())
    else:
        subtitle = subtitle_text.center(logo_width)
        mons = mons_logo
    return f"{logo}\n{subtitle}\n{mons}"


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
