"""Small, dependency-free Termux banner."""

from __future__ import annotations

import os
import sys

from .constants import PRODUCT_NAME, VERSION

RED = "\033[31m"
BRIGHT_RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

ASCII_DENG = r"""
########   ########  ##    ##   ######
##     ##  ##        ###   ##  ##    ##
##     ##  ##        ####  ##  ##
##     ##  ######    ## ## ##  ##   ####
##     ##  ##        ##  ####  ##    ##
##     ##  ##        ##   ###  ##    ##
########   ########  ##    ##   ######
""".strip("\n")

ASCII_DENG_SHADOW = r"""
  ::::::::    ::::::::   ::    ::    ::::::
  ::     ::   ::         :::   ::   ::    ::
  ::     ::   ::         ::::  ::   ::
  ::     ::   ::::::     :: :: ::   ::   ::::
  ::     ::   ::         ::  ::::   ::    ::
  ::     ::   ::         ::   :::   ::    ::
  ::::::::    ::::::::   ::    ::    ::::::
""".strip("\n")


def supports_color() -> bool:
    """Return true when ANSI color is likely useful."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "").lower() in {"dumb", ""} and not os.environ.get("TERMUX_VERSION"):
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)()) or bool(os.environ.get("TERMUX_VERSION"))


def banner_text(use_color: bool | None = None) -> str:
    """Build the DENG banner with optional ANSI red styling."""
    if use_color is None:
        use_color = supports_color()
    if use_color:
        primary = ASCII_DENG.splitlines()
        shadow = ASCII_DENG_SHADOW.splitlines()
        logo_lines = [f"{DIM}{RED}{shadow_line}{RESET}\n{BOLD}{BRIGHT_RED}{line}{RESET}" for line, shadow_line in zip(primary, shadow)]
        logo = "\n".join(logo_lines)
    else:
        logo = ASCII_DENG
    return f"{logo}\n{PRODUCT_NAME.replace('DENG Tool: ', 'Tool: ')} v{VERSION}"


def print_banner(use_color: bool | None = None) -> None:
    """Print the product banner."""
    print(banner_text(use_color=use_color))
