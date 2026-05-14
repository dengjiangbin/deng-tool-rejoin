"""Public-user Termux menu for DENG Tool: Rejoin."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from .banner import print_banner

Handler = Callable[[argparse.Namespace], int]

MENU_ITEMS = (
    ("1", "First Time Setup Config", "first-setup"),
    ("2", "Setup / Edit Config", "config"),
    ("3", "Start", "start"),
    ("0", "Exit", "exit"),
)


def _is_interactive() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


def print_menu(use_color: bool = True) -> None:
    print_banner(use_color=use_color)
    print()
    print("Local Roblox reconnect helper")
    print("--------------------------------")
    for number, label, _command in MENU_ITEMS:
        print(f"{number}. {label}")


def run_menu(args: argparse.Namespace, handlers: dict[str, Handler]) -> int:
    """Show a simple public menu and call existing command handlers."""
    if not _is_interactive():
        print_menu(use_color=not args.no_color)
        print("\nRun this command in an interactive Termux session to choose an option.")
        return 0

    while True:
        print_menu(use_color=not args.no_color)
        try:
            choice = input("\nChoose option: ").strip()
        except EOFError:
            print("\nNo interactive input was available. Run this command in Termux to choose an option.")
            return 0
        command = next((item[2] for item in MENU_ITEMS if item[0] == choice), None)
        if command is None:
            print("Please choose a valid option.")
            input("Press Enter to continue...")
            continue
        if command == "exit":
            print("Goodbye.")
            return 0
        result = handlers[command](args)
        if command == "start":
            return result
        try:
            input("\nPress Enter to return to menu...")
        except EOFError:
            return result
