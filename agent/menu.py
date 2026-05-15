"""Public-user Termux menu for DENG Tool: Rejoin."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from . import keystore
from .banner import print_banner
from .config import ConfigError, load_config
from .constants import PRODUCT_NAME, VERSION
from .onboarding import build_onboarding_lines

Handler = Callable[[argparse.Namespace], int]

MENU_ITEMS = (
    ("1", "Enter / Update License Key", "license"),
    ("2", "First Time Setup Config", "first-setup"),
    ("3", "Setup / Edit Config", "config"),
    ("4", "Start", "start"),
    ("5", "New User Help", "new-user-help"),
    ("0", "Exit", "exit"),
)


def _is_interactive() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


def _menu_prelude_lines() -> list[str]:
    try:
        cfg = load_config()
    except ConfigError:
        cfg = None
    return build_onboarding_lines(
        cfg,
        dev_mode=keystore.DEV_MODE,
        version=VERSION,
        product=PRODUCT_NAME,
    )


def print_menu(args: argparse.Namespace, prelude_lines: list[str] | None = None) -> None:
    print_banner(use_color=not args.no_color)
    if prelude_lines:
        print()
        for line in prelude_lines:
            print(line)
    print()
    print("Menu:")
    print("--------------------------------")
    for number, label, _command in MENU_ITEMS:
        print(f"{number}. {label}")


def run_menu(args: argparse.Namespace, handlers: dict[str, Handler]) -> int:
    """Show a simple public menu and call existing command handlers."""
    prelude = _menu_prelude_lines()
    if not _is_interactive():
        print_menu(args, prelude)
        print("\nRun this command in an interactive Termux session to choose an option.")
        return 0

    while True:
        print_menu(args, prelude)
        try:
            choice = input("\nChoose option: ").strip()
        except EOFError:
            print("\nNo interactive input was available. Run this command in Termux to choose an option.")
            return 0
        command = next((item[2] for item in MENU_ITEMS if item[0] == choice), None)
        if command is None:
            print("Please choose a valid option.")
            try:
                input("Press Enter to continue...")
            except EOFError:
                return 0
            prelude = _menu_prelude_lines()
            continue
        if command == "exit":
            print("Goodbye.")
            return 0
        result = handlers[command](args)
        prelude = _menu_prelude_lines()
        if command == "start":
            return result
        try:
            input("\nPress Enter to return to menu...")
        except EOFError:
            return result

