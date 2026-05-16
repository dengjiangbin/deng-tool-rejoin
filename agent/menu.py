"""Public-user Termux menu for DENG Tool: Rejoin."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from . import keystore, safe_io
from .banner import print_banner
from .config import ConfigError, enabled_package_entries, load_config

Handler = Callable[[argparse.Namespace], int]

# License entry and New User Help are removed from the normal menu.
# License is a gate before the menu (run by cmd_menu before calling run_menu).
# Users who need to change their key later can run: deng-rejoin license
MENU_ITEMS = (
    ("1", "First Time Setup Config", "first-setup"),
    ("2", "Setup / Edit Config", "config"),
    ("3", "Start", "start"),
    ("0", "Exit", "exit"),
)


def _is_interactive() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


def _menu_prelude_lines() -> list[str]:
    """Return a minimal hint if setup is incomplete; empty list when ready."""
    try:
        cfg = load_config()
    except ConfigError:
        return ["Setup required: choose First Time Setup Config to begin."]
    if cfg is None:
        return ["Setup required: choose First Time Setup Config to begin."]
    try:
        pkgs = enabled_package_entries(cfg)
    except Exception:
        pkgs = []
    if not pkgs:
        return ["Setup required: choose First Time Setup Config to begin."]
    return []


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
    """Show a simple public menu and call existing command handlers.

    Safety: all input() calls are replaced with safe_io.safe_prompt() to
    bypass the readline C extension and prevent Termux/Android segfaults.
    KeyboardInterrupt and EOF both exit cleanly.
    """
    prelude = _menu_prelude_lines()
    if not _is_interactive():
        print_menu(args, prelude)
        print("\nRun this command in an interactive Termux session to choose an option.")
        return 0

    while True:
        print_menu(args, prelude)
        choice_raw = safe_io.safe_prompt("\nChoose option: ")
        if choice_raw is None:
            print("\nNo interactive input was available. Run this command in Termux to choose an option.")
            return 0
        choice = choice_raw.strip()
        command = next((item[2] for item in MENU_ITEMS if item[0] == choice), None)
        if command is None:
            print("Please choose a valid option.")
            safe_io.press_enter()
            prelude = _menu_prelude_lines()
            continue
        if command == "exit":
            print("Goodbye.")
            return 0
        try:
            result = handlers[command](args)
        except KeyboardInterrupt:
            print("\nInterrupted — returning to menu.")
            result = 0
        except Exception:  # noqa: BLE001
            print("\nAn error occurred — returning to menu.")
            result = 1
        prelude = _menu_prelude_lines()
        if command == "start":
            return result
        ret = safe_io.safe_prompt("\nPress Enter to return to menu...")
        if ret is None:
            return result
