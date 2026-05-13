"""Small, dependency-free Termux banner."""

from __future__ import annotations

import os
import sys

from .constants import PRODUCT_NAME, VERSION

PINK = "\033[95m"
RESET = "\033[0m"

ASCII_DENG = r"""
DDDDD   EEEEE  N   N   GGGG
D    D  E      NN  N  G
D    D  EEEE   N N N  G  GG
D    D  E      N  NN  G   G
DDDDD   EEEEE  N   N   GGG
""".strip("\n")


def supports_color() -> bool:
    """Return true when ANSI color is likely useful."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "").lower() in {"dumb", ""} and not os.environ.get("TERMUX_VERSION"):
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)()) or bool(os.environ.get("TERMUX_VERSION"))


def banner_text(use_color: bool | None = None) -> str:
    """Build the DENG banner with optional ANSI pink color."""
    if use_color is None:
        use_color = supports_color()
    logo = f"{PINK}{ASCII_DENG}{RESET}" if use_color else ASCII_DENG
    return f"{logo}\n{PRODUCT_NAME.replace('DENG Tool: ', 'Tool: ')} v{VERSION}"


def print_banner(use_color: bool | None = None) -> None:
    """Print the product banner."""
    print(banner_text(use_color=use_color))
