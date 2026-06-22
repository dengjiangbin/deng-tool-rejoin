"""Kaeru-style Termux menu styling for DENG Tool: Rejoin.

Bold, bright ANSI colors and consistent [?] / [!] / [x] prefixes for readable
Termux menus. Colors are always applied for menu rendering.
"""

from __future__ import annotations

import re
import shutil
import sys
import time

from .constants import PRODUCT_NAME

# Bold bright ANSI palette (Termux-friendly).
GREEN = "\033[1;92m"
YELLOW = "\033[1;93m"
RED = "\033[1;91m"
CYAN = "\033[1;96m"
BLUE = "\033[1;94m"
PINK = "\033[38;5;205m"
COLOR_LOGO = PINK
WHITE = "\033[1;97m"
RESET = "\033[0m"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

LICENSE_SUCCESS_VERIFIED = "[!] License Key Verified Successfully."
LICENSE_SUCCESS_WELCOME = "[!] Welcome To DENG Tool: Rejoin."


from . import safe_io as _safe_io


def visible_len(text: str) -> int:
    """Return printable width after stripping ANSI escape codes."""
    return len(ANSI_RE.sub("", text))


def truncate_visible(text: str, max_width: int, *, ellipsis: str = "...") -> str:
    """Hard-truncate plain text to ``max_width`` with ellipsis — no wrap."""
    if max_width <= 0:
        return ""
    plain = ANSI_RE.sub("", text or "")
    if len(plain) <= max_width:
        return text
    if max_width <= len(ellipsis):
        return plain[:max_width]
    return plain[: max_width - len(ellipsis)] + ellipsis


def fit_line(text: str, width: int | None = None) -> str:
    """Ensure a single output line never exceeds terminal width."""
    cols = width if width is not None else _safe_io.terminal_columns()
    return truncate_visible(text, cols)


def _emit(text: str = "") -> None:
    _safe_io.write_stdout(fit_line(text), end="\n")


def separator(char: str = "=", width: int | None = None, ratio: float = 0.5) -> str:
    """Return a short visible-width separator, ignoring ANSI codes."""
    glyph = (str(char or "-"))[0]
    if width is None:
        try:
            width = shutil.get_terminal_size(fallback=(60, 24)).columns
        except Exception:  # noqa: BLE001
            width = 60
    base_width = max(1, int(width or 60))
    try:
        ratio_value = float(ratio)
    except (TypeError, ValueError):
        ratio_value = 0.5
    visible_width = round(base_width * max(0.1, min(ratio_value, 1.0)))
    visible_width = max(18, min(visible_width, 36))
    return f"{CYAN}{glyph * visible_width}{RESET}"


def header(title: str, *, width: int = 50) -> None:
    _emit()
    _emit(separator("=", width))
    _emit(f"{CYAN}{title}{RESET}")
    _emit(separator("=", width))
    _emit()


def section_title(title: str) -> None:
    _emit()
    _emit(f"{CYAN}{title}{RESET}")
    _emit(separator("-"))


def menu_number(number: str, label: str) -> str:
    return f"{YELLOW}{number}.{RESET} {WHITE}{label}{RESET}"


def prompt_prefix(text: str) -> str:
    """Format a bold cyan [?] prompt line."""
    if text.startswith("[?]"):
        body = text
    else:
        body = f"[?] {text}"
    if not body.endswith(":"):
        body = f"{body}:"
    return f"{CYAN}{body}{RESET}"


def success_line(text: str) -> str:
    if text.startswith("[!]"):
        body = text
    else:
        body = f"[!] {text}"
    if not body.endswith("."):
        body = f"{body}."
    return f"{GREEN}{body}{RESET}"


def warning_line(text: str) -> str:
    if text.startswith("[!]"):
        body = text
    else:
        body = f"[!] {text}"
    if not body.endswith("."):
        body = f"{body}."
    return f"{YELLOW}{body}{RESET}"


def error_line(text: str) -> str:
    if text.startswith("[x]"):
        body = text
    elif text.startswith("[!]"):
        body = text.replace("[!]", "[x]", 1)
    else:
        body = f"[x] {text}"
    if not body.endswith("."):
        body = f"{body}."
    return f"{RED}{body}{RESET}"


def print_success(text: str) -> None:
    _emit(success_line(text))


def print_warning(text: str) -> None:
    _emit(warning_line(text))


def print_error(text: str) -> None:
    _emit(error_line(text))


def print_prompt(text: str) -> None:
    _emit(prompt_prefix(text))


def print_license_success(*, pause_seconds: float = 0.8) -> None:
    """Print bold green license verification success lines."""
    _emit()
    _emit(success_line(LICENSE_SUCCESS_VERIFIED))
    _emit(success_line(LICENSE_SUCCESS_WELCOME))
    _emit()
    if pause_seconds > 0:
        try:
            time.sleep(pause_seconds)
        except Exception:  # noqa: BLE001
            pass


def print_top_menu(*, prelude_lines: list[str] | None = None) -> None:
    """Render the colorful Top Menu body (banner printed separately)."""
    if prelude_lines:
        _emit()
        for line in prelude_lines:
            if line.startswith("[!]"):
                _emit(warning_line(line))
            else:
                _emit(warning_line(line) if "required" in line.lower() else line)
    _emit()
    _emit(prompt_prefix("Top Menu"))
    _emit()
    _emit(menu_number("1", "First Time Setup Config"))
    _emit(menu_number("2", "Setup / Edit Config"))
    _emit(menu_number("3", "Start"))
    _emit(menu_number("0", "Exit"))
    _emit()


def print_config_menu() -> None:
    section_title("Setup / Edit Config")
    _emit(menu_number("1", "Packages"))
    _emit(menu_number("2", "Private URL"))
    _emit(menu_number("0", "Back"))
    _emit(separator("-"))


def print_submenu_header(title: str) -> None:
    section_title(title)


def print_submenu(
    title: str,
    items: list[tuple[str, str]],
    *,
    current_lines: list[str] | None = None,
) -> None:
    """Render a styled submenu with bold yellow numbers."""
    print_submenu_header(title)
    if current_lines:
        for line in current_lines:
            _emit(line)
        _emit()
    for number, label in items:
        _emit(menu_number(number, label))
    _emit(separator("-"))

def print_invalid_option() -> None:
    print_warning("Invalid Option")


def select_option_prompt(default: str | None = None) -> str:
    if default is not None:
        return prompt_prefix(f"Select Option [{default}]")
    return prompt_prefix("Select Option")


def choose_prompt(default: str | None = None) -> str:
    if default is not None:
        return prompt_prefix(f"Choose [{default}]")
    return prompt_prefix("Choose")


def config_saved_message() -> None:
    print_success("Config Saved")


def product_header_line() -> str:
    return f"{COLOR_LOGO}{PRODUCT_NAME}{RESET}"
