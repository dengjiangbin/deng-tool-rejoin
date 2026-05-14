"""Small, dependency-free Termux banner."""

from __future__ import annotations

import os
import re
import sys

from .constants import PRODUCT_NAME, VERSION

RED = "\033[31m"
BOLD = "\033[1m"
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


def banner_text(use_color: bool | None = None) -> str:
    """Build the DENG banner with optional ANSI red styling."""
    if use_color is None:
        use_color = supports_color()
    logo = f"{BOLD}{RED}{ASCII_DENG}{RESET}" if use_color else ASCII_DENG
    logo_width = max(visible_width(line) for line in ASCII_DENG.splitlines())
    subtitle = f"{PRODUCT_NAME.replace('DENG Tool: ', 'Tool: ')} v{VERSION}".center(logo_width)
    return f"{logo}\n{subtitle}"


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
