"""Public-user Termux menu for DENG Tool: Rejoin."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from . import keystore, safe_io, termux_ui
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

# Top menu accepts only these numeric choices — no aliases for removed options.
_TOP_MENU_CHOICES = frozenset({"0", "1", "2", "3"})


def _is_interactive() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


def _menu_prelude_lines() -> list[str]:
    """Return a minimal hint if setup is incomplete; empty list when ready."""
    try:
        cfg = load_config()
    except ConfigError:
        return ["Setup Required: Choose First Time Setup Config To Begin."]
    if cfg is None:
        return ["Setup Required: Choose First Time Setup Config To Begin."]
    try:
        pkgs = enabled_package_entries(cfg)
    except Exception:
        pkgs = []
    if not pkgs:
        return ["Setup Required: Choose First Time Setup Config To Begin."]
    return []


def print_menu(args: argparse.Namespace, prelude_lines: list[str] | None = None) -> None:
    print_banner(use_color=True)
    termux_ui.print_top_menu(prelude_lines=prelude_lines)


def run_menu(args: argparse.Namespace, handlers: dict[str, Handler]) -> int:
    """Show the public top menu and dispatch to command handlers.

    Top menu is strictly: 1 First Time Setup, 2 Config, 3 Start, 0 Exit.
    All input uses safe_io.safe_prompt() to avoid readline segfaults on Termux.
    """
    prelude = _menu_prelude_lines()
    if not _is_interactive():
        print_menu(args, prelude)
        print("\nRun this command in an interactive Termux session to choose an option.")
        return 0

    while True:
        try:
            print_menu(args, prelude)
            choice_raw = safe_io.safe_prompt(f"\n{termux_ui.select_option_prompt()}: ")
            if choice_raw is None:
                print("\nGoodbye.")
                return 0
            choice = choice_raw.strip()
            if choice not in _TOP_MENU_CHOICES:
                termux_ui.print_invalid_option()
                safe_io.press_enter()
                prelude = _menu_prelude_lines()
                continue

            command = next((item[2] for item in MENU_ITEMS if item[0] == choice), None)
            if command is None:
                termux_ui.print_invalid_option()
                safe_io.press_enter()
                prelude = _menu_prelude_lines()
                continue

            if command == "exit":
                print("Goodbye.")
                return 0

            try:
                result = handlers[command](args)
            except KeyboardInterrupt:
                print("\nGoodbye.")
                return 0
            except EOFError:
                print("\nGoodbye.")
                return 0
            except Exception:  # noqa: BLE001
                print("\nAn error occurred — returning to menu.")
                result = 1

            prelude = _menu_prelude_lines()
            if command == "start":
                return result

            ret = safe_io.safe_prompt("\nPress Enter to return to menu...")
            if ret is None:
                print("\nGoodbye.")
                return 0
            continue
        except KeyboardInterrupt:
            print("\nGoodbye.")
            return 0
        except EOFError:
            print("\nGoodbye.")
            return 0
