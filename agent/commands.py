"""CLI command handlers for DENG Tool: Rejoin."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import account_detect, android, db
from .banner import print_banner
from .config import (
    ConfigError,
    default_config,
    enabled_package_entries,
    enabled_package_names,
    ensure_app_dirs,
    load_config,
    mask_license_key,
    normalize_package_detection_hint,
    package_display_name,
    package_entry,
    safe_config_view,
    save_config,
    validate_account_username,
    validate_config,
    validate_license_key,
    validate_package_detection_hints,
    validate_package_entries,
    validate_package_name,
)
from .constants import (
    CONFIG_PATH,
    DB_PATH,
    DEFAULT_ROBLOX_PACKAGE,
    DEFAULT_ROBLOX_PACKAGE_HINTS,
    GITHUB_REMOTE,
    LOCK_PATH,
    LOG_PATH,
    PID_PATH,
    PRODUCT_NAME,
    RAW_INSTALL_URL,
    TERMUX_BOOT_SCRIPT,
    VERSION,
)
from .doctor import print_doctor, run_doctor
from .launcher import RejoinResult, perform_rejoin
from .launcher_file import create_market_launchers
from .lockfile import LockManager, stop_running_agent
from .menu import run_menu
from .platform_detect import detect_public_download_dir, get_android_release, get_android_sdk, get_platform_info
from .supervisor import MultiPackageSupervisor, Supervisor
from . import keystore
from . import snapshot, webhook, window_layout
from .url_utils import UrlValidationError, detect_launch_mode_from_url, mask_urls_in_text, validate_launch_url

COMMANDS = {
    "setup",
    "first-setup",
    "doctor",
    "status",
    "once",
    "start",
    "stop",
    "reset",
    "config",
    "logs",
    "version",
    "menu",
    "enable-boot",
    "update",
}

# ─── ANSI color constants (used only when a tty is available) ─────────────────
_ANSI_GREEN   = "\033[32m"
_ANSI_YELLOW  = "\033[33m"
_ANSI_RED     = "\033[31m"
_ANSI_CYAN    = "\033[36m"
_ANSI_BOLD    = "\033[1m"
_ANSI_DIM     = "\033[2m"
_ANSI_RESET   = "\033[0m"
_ANSI_RE      = re.compile(r"\x1b\[[0-9;]*m")


def _is_interactive() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


def _prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{text}{suffix}: ").strip()
    return value or default


def _prompt_yes_no(text: str, default: bool = False) -> bool:
    marker = "Y/n" if default else "y/N"
    while True:
        value = input(f"{text} [{marker}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def _prompt_int(text: str, default: int, minimum: int) -> int:
    while True:
        value = _prompt(text, str(default))
        try:
            number = int(value)
        except ValueError:
            print("Please enter a number.")
            continue
        if number < minimum:
            print(f"Value must be at least {minimum}.")
            continue
        return number


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def _yes_no(value: bool) -> str:
    return "Enabled" if value else "Disabled"


def _launch_mode_label(value: str) -> str:
    return {
        "app": "Open Roblox app only",
        "deeplink": "Roblox deep link",
        "web_url": "Roblox web/private-server URL",
    }.get(value, value)


def _package_list_label(packages: list[Any]) -> str:
    if not packages:
        return "Not set"
    entries = validate_package_entries(packages)
    return ", ".join(package_display_name(entry) for entry in entries if entry.get("enabled", True)) or "Not set"


def _package_row_label(entry: dict[str, Any]) -> str:
    return package_display_name(entry, include_package=True)


def _account_username_value(entry: dict[str, Any]) -> str:
    return validate_account_username(entry.get("account_username", "")) or "Username not set"


def _hint_list_label(hints: list[str]) -> str:
    return ", ".join(hints) if hints else "Default"


def _safe_detection_hints(config_data: dict[str, Any] | None = None) -> list[str]:
    source = None
    if config_data:
        source = config_data.get("package_detection_hints")
    if source is None:
        try:
            source = load_config().get("package_detection_hints")
        except ConfigError:
            source = DEFAULT_ROBLOX_PACKAGE_HINTS
    try:
        return validate_package_detection_hints(source)
    except ConfigError:
        return list(DEFAULT_ROBLOX_PACKAGE_HINTS)


def _safe_url_label(value: str | None) -> str:
    if not value:
        return "Not set"
    return safe_config_view({"launch_url": value}).get("launch_url") or "Not set"


def _refresh_detected_fields(config_data: dict[str, Any]) -> dict[str, Any]:
    config_data["root_available"] = android.detect_root().available
    config_data["android_release"] = get_android_release()
    config_data["android_sdk"] = get_android_sdk()
    config_data["download_dir"] = detect_public_download_dir()
    return config_data


def _print_config_summary(config_data: dict[str, Any]) -> None:
    cfg = safe_config_view(validate_config(config_data))
    entries = validate_package_entries(cfg["roblox_packages"])
    enabled_entries = [entry for entry in entries if entry.get("enabled", True)]
    print("DENG Tool: Rejoin Settings")
    print()
    print("Roblox Packages:")
    print("  Roblox Username / Account Name")
    if enabled_entries:
        for idx, entry in enumerate(enabled_entries, start=1):
            username = _account_username_value(entry)
            print(f"  {idx}. {username:<16} {entry['package']}")
    else:
        print("  Not set")
    print(f"  Detection hints: {_hint_list_label(cfg['package_detection_hints'])}")
    print()
    print("Launch:")
    print(f"  Mode: {_launch_mode_label(cfg['launch_mode'])}")
    print(f"  URL: {_safe_url_label(cfg['launch_url'])}")
    print()
    print("License:")
    print(f"  Key: {cfg.get('license_key') or 'Not set'}")
    print()
    print("Discord Webhook:")
    print(f"  Enabled: {'Yes' if cfg['webhook_enabled'] else 'No'}")
    if cfg["webhook_enabled"]:
        print(f"  Mode: {cfg['webhook_mode']}")
        print(f"  URL: {cfg.get('webhook_url') or 'Not set'}")
        tags = cfg.get("webhook_tags") or []
        if tags:
            print(f"  Tags: {', '.join(tags)}")
    print()
    print("Snapshot:")
    print(f"  Enabled: {'Yes' if cfg['webhook_enabled'] and cfg['webhook_snapshot_enabled'] else 'No'}")
    print()
    print("Webhook Interval:")
    print(f"  {cfg['webhook_interval_seconds']} seconds" if cfg["webhook_enabled"] else "  Disabled")
    print()
    print("YesCaptcha:")
    print(f"  API key: {'Configured' if cfg.get('yescaptcha_key') else 'Not set'}")
    print()
    print("Auto Resize:")
    print("  Automatic based on selected package count and device DPI")
    if len(enabled_entries) > 1:
        print("  Multi-package: 40% left reserved for Termux log, 60% right for Roblox")


def _print_setup_menu(config_data: dict[str, Any], title: str = "DENG Tool: Rejoin Setup") -> None:
    cfg = safe_config_view(validate_config(config_data))
    print("--------------------------------")
    print(title)
    print("--------------------------------")
    print(f"1. Device Name: {cfg['device_name']}")
    print(f"2. Roblox Package: {cfg['roblox_package']}")
    print(f"3. Launch Mode: {_launch_mode_label(cfg['launch_mode'])}")
    print(f"4. Launch URL / Private Server URL: {_safe_url_label(cfg['launch_url'])}")
    print(f"5. Auto Rejoin: {_yes_no(cfg['auto_rejoin_enabled'])}")
    print(f"6. Reconnect Delay: {cfg['reconnect_delay_seconds']} seconds")
    print(f"7. Root Mode: {_yes_no(cfg['root_mode_enabled'])}")
    print(f"8. Health Check Interval: {cfg['health_check_interval_seconds']} seconds")
    print("9. Save and Finish")
    print("A. Advanced Info")
    print("0. Cancel")
    print("--------------------------------")


def _choose_package() -> str:
    return _choose_package_menu(DEFAULT_ROBLOX_PACKAGE)


def _ordered_roblox_packages(package_detection_hints: list[str] | None = None) -> list[str]:
    packages = android.find_roblox_packages(package_detection_hints or _safe_detection_hints())
    ordered: list[str] = []
    if DEFAULT_ROBLOX_PACKAGE in packages:
        ordered.append(DEFAULT_ROBLOX_PACKAGE)
    for package in packages:
        if package not in ordered:
            ordered.append(package)
    return ordered


def _prompt_manual_package(default: str = DEFAULT_ROBLOX_PACKAGE) -> str | None:
    print("\nEnter Roblox package name")
    print("Example: com.roblox.client")
    while True:
        value = _prompt("Package name", default).strip()
        if not value:
            return None
        try:
            return validate_package_name(value)
        except ConfigError:
            print("That does not look like a safe Android package name. Use a format like com.roblox.client.")


def _entry_for_package(package: str, current_entries: list[dict[str, Any]]) -> dict[str, Any]:
    for entry in current_entries:
        if entry["package"] == package:
            return dict(entry)
    return package_entry(package, "", True)


def _detect_or_prompt_account_username(entry: dict[str, Any]) -> dict[str, Any]:
    updated = dict(entry)
    if validate_account_username(updated.get("account_username", "")):
        return updated
    result = account_detect.detect_account_username_for_package(updated["package"])
    if result:
        updated["account_username"] = result.username
        updated["username_source"] = result.source
        return updated
    updated["username_source"] = "not_set"
    if _is_interactive():
        print(f"DENG could not safely detect a Roblox username/account name for {updated['package']}.")
        print("This name is only used to make the Start table easy to read.")
        manual = _prompt(f"Enter Roblox username/account name for {updated['package']}, or press Enter to skip", "").strip()
        if manual:
            updated["account_username"] = validate_account_username(manual)
            updated["username_source"] = "manual"
    return updated


def _print_package_entries(entries: list[dict[str, Any]]) -> None:
    if not entries:
        print("  No packages selected.")
        return
    for idx, entry in enumerate(entries, start=1):
        enabled = "" if entry.get("enabled", True) else " [Disabled]"
        print(f"  {idx}. {_account_username_value(entry):<20} {entry['package']}{enabled}")


def _choose_package_menu(current_package: str = DEFAULT_ROBLOX_PACKAGE, package_detection_hints: list[str] | None = None) -> str:
    entries, _hints = _choose_packages_menu([package_entry(current_package, "Main", True)], package_detection_hints)
    enabled = [entry for entry in entries if entry.get("enabled", True)]
    return enabled[0]["package"] if enabled else current_package


def _choose_packages_menu(
    current_packages: list[Any] | None = None,
    package_detection_hints: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    selected = validate_package_entries(current_packages or [package_entry(DEFAULT_ROBLOX_PACKAGE, "Main", True)])
    hints = _safe_detection_hints({"package_detection_hints": package_detection_hints})
    if not _is_interactive():
        return selected, hints

    print()
    print("--------------------------------")
    print("Roblox Package Setup")
    print("--------------------------------")
    print("1. Auto detect Roblox packages")
    print("2. Enter package name manually")
    print("--------------------------------")
    choice = input("Choose [1]: ").strip() or "1"

    if choice == "1":
        detected = _ordered_roblox_packages(hints)
        if not detected:
            print("No Roblox package was detected.")
            print("Try option 2 to enter package name manually, or install Roblox and re-run.")
            return selected, hints
        if len(detected) == 1:
            selected = [_detect_or_prompt_account_username(_entry_for_package(detected[0], selected))]
            print(f"Auto-selected: {detected[0]}")
            return selected, hints
        print()
        print("Detected Roblox Packages:")
        for idx, package in enumerate(detected, start=1):
            marker = " (Recommended)" if package == DEFAULT_ROBLOX_PACKAGE else ""
            print(f"  {idx}. {package}{marker}")
        print("  A. Select all")
        raw = input("Choose packages (e.g. 1,2 or A) [1]: ").strip().lower() or "1"
        if raw == "a":
            selected = [_detect_or_prompt_account_username(_entry_for_package(pkg, selected)) for pkg in detected]
        else:
            choices = [part.strip() for part in raw.split(",") if part.strip()]
            new_selection: list[dict[str, Any]] = []
            for part in choices:
                if part.isdigit():
                    index = int(part)
                    if 1 <= index <= len(detected):
                        pkg = detected[index - 1]
                        if pkg not in [e["package"] for e in new_selection]:
                            new_selection.append(_detect_or_prompt_account_username(_entry_for_package(pkg, selected)))
            if new_selection:
                selected = new_selection
    elif choice == "2":
        default_package = selected[0]["package"] if selected else DEFAULT_ROBLOX_PACKAGE
        manual = _prompt_manual_package(default_package)
        if manual:
            selected = [_detect_or_prompt_account_username(_entry_for_package(manual, selected))]

    return selected, hints


def _choose_launch_settings() -> tuple[str, str]:
    if not _is_interactive():
        return "app", ""
    print("Launch mode:")
    print("  1. app only")
    print("  2. roblox deeplink")
    print("  3. roblox web/private-server URL")
    while True:
        choice = _prompt("Choose launch mode", "1")
        mode = {"1": "app", "2": "deeplink", "3": "web_url"}.get(choice, choice.strip().lower())
        if mode not in {"app", "deeplink", "web_url"}:
            print("Choose 1, 2, 3, app, deeplink, or web_url.")
            continue
        if mode == "app":
            return mode, ""
        while True:
            url = _prompt("Launch URL")
            inferred = detect_launch_mode_from_url(url)
            if mode == "deeplink" and inferred != "deeplink":
                print("Deep link mode needs a roblox:// URL.")
                continue
            if mode == "web_url" and inferred != "web_url":
                print("Web URL mode needs a Roblox https:// URL.")
                continue
            try:
                result = validate_launch_url(url, mode, allow_uncertain=True)
                if result.warning:
                    print(f"Warning: {result.warning}")
                return mode, url
            except UrlValidationError as exc:
                print(f"Invalid URL: {exc}")


def _choose_launch_mode(current_mode: str) -> str:
    if not _is_interactive():
        return current_mode
    print()
    print("Launch Mode")
    print("1. Open Roblox app only")
    print("2. Roblox deep link")
    print("3. Roblox web/private-server URL")
    while True:
        choice = _prompt("Choose launch mode", {"app": "1", "deeplink": "2", "web_url": "3"}.get(current_mode, "1"))
        mode = {"1": "app", "2": "deeplink", "3": "web_url"}.get(choice, choice.strip().lower())
        if mode in {"app", "deeplink", "web_url"}:
            return mode
        print("Choose 1, 2, or 3.")


def _prompt_launch_url(current_url: str, launch_mode: str) -> str:
    if launch_mode == "app":
        print("App-only mode does not need a URL. DENG will open Roblox normally.")
        return ""
    print()
    print("Paste a Roblox link.")
    if launch_mode == "deeplink":
        print("Example: roblox://experiences/start?placeId=123")
    else:
        print("Example: https://www.roblox.com/games/123/name?privateServerLinkCode=...")
    while True:
        value = _prompt("Launch URL", current_url).strip()
        try:
            result = validate_launch_url(value, launch_mode, allow_uncertain=True)
            if result.warning:
                print(f"Note: {result.warning}")
            return value
        except UrlValidationError as exc:
            print(f"That URL cannot be used yet: {exc}")


def _setup_launch_link(draft: dict[str, Any]) -> None:
    print()
    print("Roblox Launch Link")
    print("1. App only, no link")
    print("2. Public Roblox game URL")
    print("3. Private server URL")
    print("4. Roblox deeplink")
    choice = _prompt("Choose launch link type", "1").strip()
    if choice == "1":
        draft["launch_mode"] = "app"
        draft["launch_url"] = ""
        return
    if choice in {"2", "3"}:
        draft["launch_mode"] = "web_url"
        draft["launch_url"] = _prompt_launch_url(str(draft.get("launch_url") or ""), "web_url")
        return
    if choice == "4":
        draft["launch_mode"] = "deeplink"
        draft["launch_url"] = _prompt_launch_url(str(draft.get("launch_url") or ""), "deeplink")
        return
    print("Unknown choice. Keeping the current launch link settings.")


def _setup_webhook(draft: dict[str, Any]) -> None:
    print()
    print("Discord Webhook Setup")
    print("DENG can send safe status updates to Discord. The webhook URL is stored locally and masked in screens/logs.")
    print("1. No")
    print("2. Yes, send new messages")
    print("3. Yes, edit one existing status message when possible")
    choice = _prompt("Choose webhook mode", "1").strip()
    if choice == "1":
        draft["webhook_enabled"] = False
        draft["webhook_snapshot_enabled"] = False
        draft["webhook_send_snapshot"] = False
        draft["webhook_mode"] = "new_message"
        return
    draft["webhook_enabled"] = True
    draft["webhook_mode"] = "new_message" if choice == "2" else "edit_message"
    while True:
        value = _prompt("Discord webhook URL", str(draft.get("webhook_url") or "")).strip()
        try:
            draft["webhook_url"] = webhook.validate_webhook_url(value)
            break
        except ValueError as exc:
            print(f"Webhook URL is not valid: {exc}")
    if draft["webhook_mode"] == "edit_message":
        draft["webhook_message_id"] = _prompt("Existing message ID (optional)", str(draft.get("webhook_message_id") or ""))


def _setup_snapshot(draft: dict[str, Any]) -> None:
    print()
    print("Phone Snapshot For Webhook")
    print("A snapshot may include private information visible on screen. Only enable it on your own phone/cloud phone.")
    print("1. No")
    print("2. Yes, attach snapshot")
    enabled = _prompt("Choose snapshot option", "1").strip() == "2"
    draft["webhook_snapshot_enabled"] = enabled
    draft["webhook_send_snapshot"] = enabled


def _setup_webhook_interval(draft: dict[str, Any]) -> None:
    print()
    print("Webhook Info Interval")
    print("Short intervals can spam Discord or hit rate limits. Minimum is 30 seconds.")
    choices = {"1": 30, "2": 60, "3": 300, "4": 600}
    print("1. 30 seconds")
    print("2. 1 minute")
    print("3. 5 minutes")
    print("4. 10 minutes")
    print("5. Custom")
    choice = _prompt("Choose interval", "3").strip()
    if choice in choices:
        draft["webhook_interval_seconds"] = choices[choice]
        return
    while True:
        value = _prompt_int("Webhook interval seconds", int(draft.get("webhook_interval_seconds", 300)), 30)
        try:
            draft["webhook_interval_seconds"] = webhook.validate_webhook_interval(value)
            return
        except ValueError as exc:
            print(exc)


def _setup_license_key(draft: dict[str, Any]) -> None:
    """Prompt the user to enter or clear their DENG license key."""
    print()
    print("License Key")
    print("Format: DENG-<hex> (example: DENG-38ab1234cd56ef78)")
    print("Leave blank to skip or clear the current key.")
    current = draft.get("license_key", "") or ""
    display = mask_license_key(current) if current else "Not set"
    print(f"Current: {display}")
    raw = _prompt("License key", "").strip()
    if not raw:
        return
    try:
        draft["license_key"] = validate_license_key(raw)
        print(f"License key saved: {mask_license_key(draft['license_key'])}")
    except ConfigError as exc:
        print(f"Invalid license key: {exc}")


def _setup_yescaptcha_key(draft: dict[str, Any]) -> None:
    """Prompt the user to enter or clear their YesCaptcha API key."""
    print()
    print("YesCaptcha API Key")
    print("Obtain your key from https://yescaptcha.com — used for CAPTCHA solving.")
    print("Leave blank to skip or clear.")
    current = draft.get("yescaptcha_key", "") or ""
    print(f"Current: {'Configured (hidden)' if current else 'Not set'}")
    raw = _prompt("YesCaptcha key", "").strip()
    if not raw:
        return
    draft["yescaptcha_key"] = raw[:256]
    print("YesCaptcha API key saved.")


def _write_termux_boot_script() -> None:
    TERMUX_BOOT_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
    script = """#!/data/data/com.termux/files/usr/bin/sh
sleep 15
APP_HOME="${DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}"
cd "$APP_HOME" || exit 0
sh scripts/start-agent.sh >> "$APP_HOME/logs/agent.log" 2>&1
"""
    TERMUX_BOOT_SCRIPT.write_text(script, encoding="utf-8")
    TERMUX_BOOT_SCRIPT.chmod(0o755)


def _run_guided_config_menu(config_data: dict[str, Any], args: argparse.Namespace, *, title: str) -> tuple[dict[str, Any] | None, bool]:
    """Backward-compatible entrypoint for older setup callers."""
    return _run_edit_config_menu(config_data, args)


def _run_first_time_setup_wizard(config_data: dict[str, Any], args: argparse.Namespace, *, start_after_save: bool = False) -> tuple[dict[str, Any] | None, bool]:
    draft = _refresh_detected_fields(dict(config_data))
    if not _is_interactive():
        print_banner(use_color=not args.no_color)
        print("First Time Setup Config")
        print("Run this command in interactive Termux to complete setup.")
        print()
        _print_config_summary(draft)
        return draft, False

    print_banner(use_color=not args.no_color)
    print("First Time Setup Config")
    print("This wizard sets the important first-run options in a safe order.")
    print()
    print("Step 1 of 6: Roblox Package Setup")
    packages, hints = _choose_packages_menu(
        list(draft.get("roblox_packages") or [package_entry(draft.get("roblox_package", DEFAULT_ROBLOX_PACKAGE), "Main", True)]),
        list(draft.get("package_detection_hints") or DEFAULT_ROBLOX_PACKAGE_HINTS),
    )
    draft["roblox_packages"] = packages
    draft["package_detection_hints"] = hints
    active_entries = enabled_package_entries(draft)
    draft["roblox_package"] = active_entries[0]["package"]
    draft["selected_package_mode"] = "multiple" if len(active_entries) > 1 else "single"
    print("\nStep 2 of 6: Roblox Public / Private Server Link")
    _setup_launch_link(draft)
    print("\nStep 3 of 6: Discord Webhook Setup")
    _setup_webhook(draft)
    if draft.get("webhook_enabled"):
        print("\nStep 4 of 6: Phone Snapshot For Webhook")
        _setup_snapshot(draft)
        print("\nStep 5 of 6: Webhook Info Interval")
        _setup_webhook_interval(draft)
    else:
        print("\nDiscord webhook is off, so snapshot and webhook interval setup were skipped.")
    print("\nStep 6 of 6: Save And Start")
    draft["first_setup_completed"] = True
    try:
        saved = save_config(draft)
    except ConfigError as exc:
        print(f"Setup could not be saved: {exc}")
        return None, False
    print("First-time setup complete.")
    _print_config_summary(saved)
    if start_after_save or _prompt_yes_no("Start DENG now?", True):
        cmd_start(args)
    return saved, True


def _run_edit_config_menu(config_data: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, Any] | None, bool]:
    draft = _refresh_detected_fields(dict(config_data))
    if not _is_interactive():
        print_banner(use_color=not args.no_color)
        print("--------------------------------")
        print("DENG Tool: Rejoin Config")
        print("--------------------------------")
        print("1. Roblox Packages / Account Names")
        print("2. Roblox Launch Link")
        print("3. Discord Webhook")
        if draft.get("webhook_enabled"):
            print("4. Snapshot")
            print("5. Webhook Interval")
        else:
            print("4. Snapshot: Disabled because Discord webhook is off")
            print("5. Webhook Interval: Disabled because Discord webhook is off")
        print("6. View Current Settings")
        print("0. Back")
        print("--------------------------------")
        print("\nCurrent settings:")
        _print_config_summary(draft)
        return draft, False

    while True:
        print_banner(use_color=not args.no_color)
        print("--------------------------------")
        print("DENG Tool: Rejoin Config")
        print("--------------------------------")
        print("1. Roblox Packages / Account Names")
        print("2. Roblox Launch Link")
        print("3. Discord Webhook")
        if draft.get("webhook_enabled"):
            print("4. Snapshot")
            print("5. Webhook Interval")
        else:
            print("4. Snapshot: Disabled because Discord webhook is off")
            print("5. Webhook Interval: Disabled because Discord webhook is off")
        print("6. License Key")
        print("7. YesCaptcha API Key")
        print("8. View Current Settings")
        print("A. Advanced Info")
        print("0. Back")
        print("--------------------------------")
        try:
            choice = input("Choose setting [8]: ").strip().lower() or "8"
        except EOFError:
            print("\nNo interactive input was available. Run this command in Termux to edit settings.")
            print("\nCurrent settings:")
            _print_config_summary(draft)
            return draft, False
        if choice == "0":
            return draft, True
        if choice == "1":
            packages, hints = _choose_packages_menu(
                list(draft.get("roblox_packages") or [package_entry(draft.get("roblox_package", DEFAULT_ROBLOX_PACKAGE), "Main", True)]),
                list(draft.get("package_detection_hints") or DEFAULT_ROBLOX_PACKAGE_HINTS),
            )
            draft["roblox_packages"] = packages
            draft["package_detection_hints"] = hints
            active_entries = enabled_package_entries(draft)
            draft["roblox_package"] = active_entries[0]["package"]
            draft["selected_package_mode"] = "multiple" if len(active_entries) > 1 else "single"
            draft = save_config(draft)
            print("Package settings saved.")
        elif choice == "2":
            _setup_launch_link(draft)
            draft = save_config(draft)
            print("Launch link saved.")
        elif choice == "3":
            _setup_webhook(draft)
            draft = save_config(draft)
            print("Webhook settings saved.")
        elif choice == "4":
            if draft.get("webhook_enabled"):
                _setup_snapshot(draft)
                draft = save_config(draft)
                print("Snapshot setting saved.")
            else:
                print("Snapshot is disabled because Discord webhook is off.")
        elif choice == "5":
            if draft.get("webhook_enabled"):
                _setup_webhook_interval(draft)
                draft = save_config(draft)
                print("Webhook interval saved.")
            else:
                print("Webhook interval is disabled because Discord webhook is off.")
        elif choice == "6":
            _setup_license_key(draft)
            draft = save_config(draft)
            print("License key saved.")
        elif choice == "7":
            _setup_yescaptcha_key(draft)
            draft = save_config(draft)
            print("YesCaptcha key saved.")
        elif choice == "8":
            print()
            _print_config_summary(draft)
            input("\nPress Enter to continue...")
        elif choice == "a":
            print("\nAdvanced Info")
            _print_json(safe_config_view(draft))
            input("\nPress Enter to continue...")
        else:
            print("Please choose 1-8, A, or 0.")
            input("Press Enter to continue...")


def cmd_setup(args: argparse.Namespace) -> int:
    ensure_app_dirs()
    db.init_db(DB_PATH)

    try:
        cfg = load_config()
    except ConfigError:
        cfg = default_config()
    saved, did_save = _run_first_time_setup_wizard(cfg, args)
    if saved is None:
        return 0
    if not did_save:
        return 0

    if saved.get("termux_boot_enabled"):
        try:
            _write_termux_boot_script()
            print(f"Termux:Boot script created: {TERMUX_BOOT_SCRIPT}")
            if not android.package_installed("com.termux.boot"):
                print("Termux:Boot app was not detected. Install Termux:Boot, open it once, then Android will run this script after boot.")
        except OSError as exc:
            print(f"Warning: could not create Termux:Boot script: {exc}")

    created_launchers = create_market_launchers()
    if created_launchers:
        print("Launcher files:")
        for path in created_launchers:
            print(f"  {path}")

    print("\nDoctor report:")
    print_doctor(run_doctor(saved))
    print("\nNext commands:")
    print("  python agent/deng_tool_rejoin.py --once")
    print("  python agent/deng_tool_rejoin.py --start")
    print("  python agent/deng_tool_rejoin.py --status")
    print("  deng-rejoin")
    return 0


def cmd_first_setup(args: argparse.Namespace) -> int:
    ensure_app_dirs()
    db.init_db(DB_PATH)
    try:
        cfg = load_config()
    except ConfigError:
        cfg = default_config()
    _run_first_time_setup_wizard(cfg, args)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    cfg = None
    try:
        cfg = load_config()
        root_info = android.detect_root()
        cfg["root_available"] = root_info.available
        cfg["android_release"] = get_android_release()
        cfg["android_sdk"] = get_android_sdk()
        cfg["download_dir"] = detect_public_download_dir()
        save_config(cfg)
    except ConfigError:
        pass
    items = run_doctor(cfg)
    print_doctor(items)
    return 1 if any(item.status == "FAIL" for item in items) else 0


def _print_latest(label: str, row: dict[str, Any] | None) -> None:
    if not row:
        print(f"  {label}: none")
        return
    ts = row.get("ts", "unknown time")
    if "status" in row:
        print(f"  {label}: {row.get('status')} at {ts}")
        return
    if "success" in row:
        result = "success" if row.get("success") else "failed"
        package = row.get("package", "unknown package")
        mode = _launch_mode_label(str(row.get("launch_mode", "")))
        url = _safe_url_label(str(row.get("masked_launch_url") or ""))
        print(f"  {label}: {result} at {ts}")
        print(f"    Package: {package}")
        print(f"    Launch mode: {mode}")
        if url != "Not set":
            print(f"    URL: {url}")
        if row.get("error"):
            print(f"    Error: {mask_urls_in_text(str(row.get('error')))}")
        return
    message = row.get("message") or row.get("type") or "event"
    print(f"  {label}: {mask_urls_in_text(str(message))} at {ts}")


def cmd_status(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    cfg = load_config()
    root_info = android.detect_root()
    cfg["root_available"] = root_info.available
    cfg["android_release"] = get_android_release()
    cfg["android_sdk"] = get_android_sdk()
    cfg["download_dir"] = detect_public_download_dir()
    save_config(cfg)
    safe = safe_config_view(cfg)
    platform_info = get_platform_info()

    manager = LockManager()
    running = manager.is_running()
    print("Device")
    print(f"  Name: {safe['device_name']}")
    print(f"  Install path: {CONFIG_PATH.parent}")
    print()
    print("Android")
    print(f"  Release: {platform_info.android_release}")
    print(f"  SDK: {platform_info.android_sdk}")
    print(f"  Download folder: {platform_info.download_dir or 'Not detected'}")
    print(f"  Root available: {'Yes' if root_info.available else 'No'} ({root_info.tool or 'no tool'})")
    print()
    print("Roblox")
    entries = enabled_package_entries(cfg)
    print("  Selected packages:")
    for idx, entry in enumerate(entries, start=1):
        print(f"    {idx}. {_account_username_value(entry):<16} {entry['package']}")
    print(f"  Detection hints: {_hint_list_label(cfg['package_detection_hints'])}")
    print(f"  Launch mode: {_launch_mode_label(cfg['launch_mode'])}")
    print(f"  Launch URL: {_safe_url_label(cfg.get('launch_url'))}")
    print()
    print("Rejoin Settings")
    print(f"  First setup completed: {'Yes' if cfg['first_setup_completed'] else 'No'}")
    print(f"  Auto rejoin: {_yes_no(cfg['auto_rejoin_enabled'])}")
    print(f"  Reconnect delay: {cfg['reconnect_delay_seconds']} seconds")
    print(f"  Health check interval: {cfg['health_check_interval_seconds']} seconds")
    print(f"  Root mode: {_yes_no(cfg['root_mode_enabled'])}")
    print()
    print("Webhook")
    print(f"  Status updates: {_yes_no(cfg['webhook_enabled'])}")
    if cfg["webhook_enabled"]:
        print(f"  Mode: {cfg['webhook_mode']}")
        print(f"  Snapshot: {_yes_no(cfg['webhook_snapshot_enabled'])}")
        print(f"  Interval: {cfg['webhook_interval_seconds']} seconds")
        print(f"  URL: {safe.get('webhook_url') or 'Not set'}")
        tags = cfg.get("webhook_tags") or []
        if tags:
            print(f"  Tags: {', '.join(tags)}")
    else:
        print("  Snapshot: Disabled")
        print("  Interval: Disabled")
    print()
    print("License")
    print(f"  Key: {mask_license_key(cfg.get('license_key', ''))}")
    print(f"  YesCaptcha: {'Configured' if cfg.get('yescaptcha_key') else 'Not set'}")
    print()
    print("Window Layout")
    print("  Auto resize: Automatic")
    display = window_layout.detect_display_info()
    print(f"  Detected display: {display.width}x{display.height} density={display.density}")
    print()
    print("Runtime State")
    print(f"  Agent running: {'Yes' if running else 'No'}")
    _print_latest("Latest heartbeat", db.latest_row("heartbeats"))
    _print_latest("Latest rejoin attempt", db.latest_row("rejoin_attempts"))
    latest_event = db.latest_row("events")
    if latest_event and latest_event.get("level") == "ERROR":
        _print_latest("Latest error", latest_event)
    else:
        print("  Latest error: none")
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    cfg = load_config()
    result = perform_rejoin(cfg, reason="manual")
    if result.warning:
        print(f"Warning: {result.warning}")
    if result.success:
        print(f"Rejoin attempt succeeded. root_used={str(result.root_used).lower()}")
        return 0
    print(f"Rejoin attempt failed: {result.error}")
    return 1


def _account_username_for_table(entry: dict[str, Any]) -> str:
    """Return the display username for a Start table row — shows 'Unknown' if not set."""
    return validate_account_username(entry.get("account_username", "")) or "Unknown"


def _visible_len(s: str) -> int:
    """Return the printable width of a string, stripping ANSI escape codes."""
    return len(_ANSI_RE.sub("", s))


def _colorize_status(status: str, *, use_color: bool = True) -> str:
    """Wrap a status string in the appropriate ANSI color code."""
    if not use_color:
        return status
    color = {
        "Started":   _ANSI_GREEN,
        "Online":    _ANSI_GREEN,
        "Ready":     _ANSI_YELLOW,
        "Starting":  _ANSI_YELLOW,
        "Preparing": _ANSI_CYAN,
        "Failed":    _ANSI_RED,
        "Offline":   _ANSI_RED,
    }.get(status, "")
    return f"{color}{status}{_ANSI_RESET}" if color else status


def build_start_table(rows: list[tuple], *, use_color: bool = False) -> str:
    """Build a single clean launch status table.

    Each row must be a 4-tuple: (index, package, username, status).
    Returns a Unicode box-drawing table string.
    When ``use_color`` is True, the Status column is colorised with ANSI codes.
    """
    headers = ("#", "Package", "Username", "Status")
    str_rows = [(str(r[0]), str(r[1]), str(r[2]), str(r[3])) for r in rows]

    # Column widths are computed from raw (non-ANSI) strings
    widths = [
        max(len(headers[i]), max((_visible_len(r[i]) for r in str_rows), default=0))
        for i in range(4)
    ]

    # Build colored versions of the Status column (index 3)
    colored_rows = [
        (r[0], r[1], r[2], _colorize_status(r[3], use_color=use_color))
        for r in str_rows
    ]

    def _cell(s: str, w: int, raw: str | None = None) -> str:
        pad = w - _visible_len(raw if raw is not None else s)
        return f" {s}{' ' * max(0, pad)} "

    def _hline(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (w + 2) for w in widths) + right

    def _header_row(cells: tuple) -> str:
        return "│" + "│".join(_cell(str(cells[i]), widths[i]) for i in range(len(widths))) + "│"

    def _data_row(colored: tuple, raw: tuple) -> str:
        parts = [_cell(str(colored[i]), widths[i], str(raw[i])) for i in range(len(widths))]
        return "│" + "│".join(parts) + "│"

    lines = [
        _hline("┌", "┬", "┐"),
        _header_row(headers),
        _hline("├", "┼", "┤"),
        *(_data_row(colored_rows[i], str_rows[i]) for i in range(len(colored_rows))),
        _hline("└", "┴", "┘"),
    ]
    return "\n".join(lines)


def build_final_summary(entries: list[Any], results: dict[str, str]) -> str:
    """Return a one-line final launch summary string."""
    success_labels = {"Launched", "Started", "Success"}
    success = sum(
        1 for e in entries
        if results.get(e["package"] if isinstance(e, dict) else str(e), "") in success_labels
    )
    total = len(entries)
    if success == 0:
        return "Final: 0 packages launched."
    if success == total:
        return f"Final: {success} package{'s' if success != 1 else ''} launched successfully."
    return f"Final: {success} of {total} packages launched."


def _progress_line(index: int, total: int, entry: dict[str, Any], message: str) -> str:
    username = validate_account_username(entry.get("account_username", "")) or entry["package"]
    return f"[{index}/{total}] {username}: {message}"


def _prepare_automatic_layout(cfg: dict[str, Any], entries: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    if len(entries) <= 1:
        return cfg, "Layout skipped: only one package selected."
    packages = [entry["package"] for entry in entries]
    root_info = android.detect_root()
    # Use 40/60 split layout when multiple packages are active
    messages, preview = window_layout.apply_layout_to_packages(
        packages,
        gap=int(cfg.get("window_gap_px", 8)),
        write_xml=root_info.available,
        use_split_layout=True,
    )
    cfg["last_layout_preview"] = preview
    save_config(cfg)
    if not root_info.available:
        return cfg, "Layout calculated (40/60 split). Layout skipped, root/XML unavailable."
    if any("Updated App Cloner window preferences" in message for message in messages):
        return cfg, "Layout calculated (40/60 split). Layout applied."
    if messages:
        return cfg, "Layout calculated (40/60 split). Layout skipped, root/XML unavailable."
    return cfg, "Layout failed, launch continues."


def _run_preparation_phase(
    entries: list[dict[str, Any]],
    cfg: dict[str, Any],
    *,
    use_color: bool = True,
    verbose: bool = True,
) -> None:
    """Preparation phase: stop background Roblox apps, clear cache.

    Set ``verbose=False`` to run silently (no terminal output).
    Steps:
    1. Force-stop all Roblox packages NOT in the selected set (close background APKs).
    2. Clear the cache directory for each selected package (root required).
    """
    G   = _ANSI_GREEN  if use_color else ""
    Y   = _ANSI_YELLOW if use_color else ""
    C   = _ANSI_CYAN   if use_color else ""
    RST = _ANSI_RESET  if use_color else ""

    packages = [e["package"] for e in entries]
    hints = cfg.get("package_detection_hints")

    # Step 1 — close background Roblox processes
    if verbose:
        print(f"  {C}⏳ Stopping background Roblox processes...{RST}")
    stopped = android.force_stop_packages_except(packages, hints)
    if verbose:
        if stopped:
            for pkg in stopped:
                print(f"  {G}✓{RST} Stopped: {pkg}")
        else:
            print(f"  {Y}ℹ{RST}  No background Roblox processes found.")

    # Step 2 — clear cache for each selected package
    if verbose:
        print(f"  {C}⏳ Clearing Roblox cache...{RST}")
    any_cleared = False
    for entry in entries:
        pkg = entry["package"]
        username = _account_username_for_table(entry)
        cleared = android.clear_package_cache(pkg)
        if cleared:
            if verbose:
                print(f"  {G}✓{RST} Cache cleared: {username} ({pkg})")
            any_cleared = True
        elif verbose:
            print(f"  {Y}ℹ{RST}  Cache clear skipped (root needed): {pkg}")
    if verbose:
        if not any_cleared:
            print(f"  {Y}ℹ{RST}  Root access is required to clear Roblox cache.")
        print(f"  {G}✓{RST} Preparation complete.")
        print()


def cmd_start(args: argparse.Namespace) -> int:
    use_color = not args.no_color
    print_banner(use_color=use_color)
    try:
        cfg = load_config()
        entries = enabled_package_entries(cfg)
        if not cfg.get("first_setup_completed") or not entries:
            print("First-time setup is required before starting.")
            if _is_interactive():
                _run_first_time_setup_wizard(cfg, args, start_after_save=True)
                return 0
            print("Run: deng-rejoin and choose First Time Setup Config.")
            return 2

        # ── Key verification ────────────────────────────────────────────────
        stored_key = cfg.get("license_key", "")
        if stored_key:
            ok, msg = keystore.verify_key(stored_key)
            if not ok:
                print(f"License key error: {msg}")
                return 1
        elif keystore.DEV_MODE:
            pass  # dev mode: no key required
        else:
            if not keystore.prompt_and_verify_key():
                print("No valid key — session cancelled.")
                return 0

        n = len(entries)
        G   = _ANSI_GREEN  if use_color else ""
        Y   = _ANSI_YELLOW if use_color else ""
        RST = _ANSI_RESET  if use_color else ""

        # ── Preparation phase (silent) ──────────────────────────────────────
        _run_preparation_phase(entries, cfg, use_color=use_color, verbose=False)

        # ── Window layout (multi-package only, uses 40/60 split) ────────────
        cfg, _layout_note = _prepare_automatic_layout(cfg, entries)

        # ── Record launch start times ───────────────────────────────────────
        now_iso = datetime.now(timezone.utc).isoformat()
        start_times: dict[str, str] = dict(cfg.get("package_start_times") or {})
        for entry in entries:
            start_times[entry["package"]] = now_iso
        cfg["package_start_times"] = start_times

        # ── Launch each package silently ────────────────────────────────────
        launch_ok: dict[str, bool] = {}
        launch_err: dict[str, str] = {}
        for index, entry in enumerate(entries, start=1):
            package = entry["package"]
            package_cfg = dict(cfg)
            package_cfg["roblox_package"] = package
            package_cfg["roblox_packages"] = [entry]
            result = perform_rejoin(package_cfg, reason="start")
            launch_ok[package] = result.success
            launch_err[package] = result.error or ""

        # ── Heartbeat check — wait for apps to start ────────────────────────
        import time as _time
        _time.sleep(5)
        initial_status: dict[str, str] = {}
        for entry in entries:
            pkg = entry["package"]
            if not launch_ok[pkg]:
                err = launch_err[pkg]
                if "not installed" in err.lower():
                    initial_status[pkg] = "Not installed"
                elif "disabled" in err.lower():
                    initial_status[pkg] = "Disabled"
                else:
                    initial_status[pkg] = "Failed"
            else:
                initial_status[pkg] = "Online" if android.is_process_running(pkg) else "Starting"

        # ── Status table (4 columns: #, Package, Username, Status) ─────────
        table_rows: list[tuple] = []
        for index, entry in enumerate(entries, start=1):
            pkg = entry["package"]
            username = _account_username_for_table(entry)
            status = initial_status.get(pkg, "Unknown")
            table_rows.append((index, pkg, username, status))

        print(build_start_table(table_rows, use_color=use_color))
        print()

        # ── Webhook ─────────────────────────────────────────────────────────
        snapshot_path = None
        if cfg.get("webhook_enabled") and cfg.get("webhook_snapshot_enabled"):
            from . import snapshot as _snapshot
            _snapshot.cleanup_old_snapshots(int(cfg.get("snapshot_max_age_seconds", 300)))
            snapshot_path, _snap_message = _snapshot.capture_snapshot()
            if snapshot_path:
                cfg["snapshot_temp_path"] = str(snapshot_path)

        if cfg.get("webhook_enabled"):
            mem_info = android.get_memory_info()
            cpu_pct = android.get_cpu_usage()
            temp_c = android.get_temperature()
            cfg["_mem_info"] = mem_info
            cfg["_cpu_pct"] = cpu_pct
            cfg["_temp_c"] = temp_c

            app_stats: dict[str, Any] = {}
            for entry in entries:
                pkg = entry["package"]
                is_online = initial_status.get(pkg) == "Online"
                mem_mb = android.get_app_memory_mb(pkg) if is_online else None
                app_stats[pkg] = {
                    "online": is_online,
                    "memory_mb": mem_mb,
                    "cpu_pct": None,
                    "uptime_start": start_times.get(pkg),
                }

            ok_wh, message_wh, message_id = webhook.send_webhook_update(
                cfg,
                event="start",
                snapshot_path=snapshot_path,
                force=True,
                app_stats=app_stats,
            )
            if message_id:
                cfg["webhook_last_message_id"] = message_id

        cfg["package_start_times"] = start_times
        save_config(cfg)

        # ── Check if any package launched ───────────────────────────────────
        success_count = sum(1 for v in launch_ok.values() if v)
        if success_count == 0:
            reasons = [v for v in launch_err.values() if v]
            best_reason = reasons[0][:80] if reasons else "all launch attempts failed"
            print(f"0 packages launched. Reason: {best_reason}")
            return 1

        # ── Session keepalive — supervisor loop (always-on) ─────────────────
        packages = [entry["package"] for entry in entries]
        print(f"{G}Session active — monitoring {len(packages)} package(s). Press Ctrl+C to stop.{RST}")
        MultiPackageSupervisor(packages, cfg, initial_status=initial_status).run_forever()
        return 0
    except Exception as exc:  # noqa: BLE001 - command boundary.
        print(f"Agent start failed: {exc}")
        return 1


def cmd_stop(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    stopped, message = stop_running_agent(PID_PATH, LOCK_PATH)
    print(message)
    return 0 if stopped or "not running" in message or "stale" in message else 1


def cmd_reset(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    if not _is_interactive():
        print("Reset requires interactive confirmation.")
        return 2
    if not _prompt_yes_no("Reset config to defaults", False):
        print("Reset cancelled.")
        return 0
    cfg = default_config()
    save_config(cfg)
    print("Config reset. Logs were kept.")
    if _prompt_yes_no("Wipe SQLite database", False):
        try:
            DB_PATH.unlink()
            db.init_db(DB_PATH)
            print("Database wiped and recreated.")
        except FileNotFoundError:
            db.init_db(DB_PATH)
    if _prompt_yes_no("Wipe logs", False):
        try:
            LOG_PATH.unlink()
            print("Logs wiped.")
        except FileNotFoundError:
            pass
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = load_config()
    saved, _did_save = _run_edit_config_menu(cfg, args)
    if saved is None:
        return 0
    return 0


def _tail_lines(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    lines = max(1, min(int(lines), 1000))
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        content = handle.readlines()
    return content[-lines:]


def cmd_logs(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    lines = _tail_lines(LOG_PATH, args.lines)
    if not lines:
        print(f"No logs yet at {LOG_PATH}")
        return 0
    for line in lines:
        print(mask_urls_in_text(line.rstrip("\n")))
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    return 0


def cmd_enable_boot(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    try:
        _write_termux_boot_script()
        cfg = load_config()
        cfg["termux_boot_enabled"] = True
        save_config(cfg)
    except Exception as exc:  # noqa: BLE001 - command boundary.
        print(f"Could not enable Termux:Boot: {exc}")
        return 1
    print(f"Termux:Boot script created: {TERMUX_BOOT_SCRIPT}")
    print("Next:")
    print("  1. Install the Termux:Boot app if it is not already installed.")
    print("  2. Open Termux:Boot once.")
    print("  3. Disable battery optimization for Termux and Termux:Boot if possible.")
    print("  4. Reboot, then run deng-rejoin-status.")
    return 0


def _backup_user_data() -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = CONFIG_PATH.parent / "backups" / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    for source in (CONFIG_PATH, DB_PATH, LOG_PATH):
        if source.exists():
            target = backup_dir / source.name
            shutil.copy2(source, target)
    return backup_dir


def _copy_update_source(source: Path, destination: Path) -> None:
    skip_names = {".git", "__pycache__", "data", "logs", "run", "backups", "config.json"}
    for item in source.iterdir():
        if item.name in skip_names:
            continue
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(item, target)


def cmd_update(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    print("Updating DENG Tool: Rejoin from GitHub...")
    if not shutil.which("git"):
        print("Git is not available in this Termux session.")
        print("Fallback update command:")
        print(f"  curl -fsSL {RAW_INSTALL_URL} -o install.sh && bash install.sh")
        return 1

    backup_dir = _backup_user_data()
    print(f"User config/database/log backup: {backup_dir}")
    app_dir = CONFIG_PATH.parent

    try:
        if (app_dir / ".git").exists():
            result = subprocess.run(
                ["git", "-C", str(app_dir), "pull", "--ff-only", "origin", "main"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
                shell=False,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git pull failed")
        else:
            with tempfile.TemporaryDirectory(prefix="deng-rejoin-update-") as tmp:
                source = Path(tmp) / "repo"
                result = subprocess.run(
                    ["git", "clone", "--depth", "1", GITHUB_REMOTE, str(source)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=180,
                    shell=False,
                )
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git clone failed")
                _copy_update_source(source, app_dir)
        created = create_market_launchers()
        print(f"Launcher files refreshed: {len(created)}")
        print("\nDoctor report:")
        print_doctor(run_doctor(load_config()))
        return 0
    except Exception as exc:  # noqa: BLE001 - update boundary.
        print(f"Update failed: {exc}")
        print("Fallback update command:")
        print(f"  curl -fsSL {RAW_INSTALL_URL} -o install.sh && bash install.sh")
        return 1


def cmd_menu(args: argparse.Namespace) -> int:
    return run_menu(args, _handlers())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="deng_tool_rejoin.py", description=f"{PRODUCT_NAME} local Termux reconnect helper")
    parser.add_argument("command", nargs="?", choices=sorted(COMMANDS), help="command to run")
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--first-setup", action="store_true")
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--config", dest="config_flag", action="store_true")
    parser.add_argument("--logs", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--menu", action="store_true")
    parser.add_argument("--enable-boot", action="store_true")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--lines", type=int, default=50, help="number of log lines for logs command")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI banner color")
    ns = parser.parse_args(argv)

    flag_to_command = {
        "setup": ns.setup,
        "first-setup": ns.first_setup,
        "doctor": ns.doctor,
        "status": ns.status,
        "once": ns.once,
        "start": ns.start,
        "stop": ns.stop,
        "reset": ns.reset,
        "config": ns.config_flag,
        "logs": ns.logs,
        "version": ns.version,
        "menu": ns.menu,
        "enable-boot": ns.enable_boot,
        "update": ns.update,
    }
    selected = [command for command, enabled in flag_to_command.items() if enabled]
    if ns.command and selected:
        parser.error("use either positional command or --command flag, not both")
    if len(selected) > 1:
        parser.error("choose only one command")
    ns.resolved_command = ns.command or (selected[0] if selected else None)
    if ns.resolved_command is None:
        parser.print_help()
        parser.exit(2)
    return ns


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return _handlers()[args.resolved_command](args)


def _handlers() -> dict[str, Any]:
    return {
        "setup": cmd_setup,
        "first-setup": cmd_first_setup,
        "doctor": cmd_doctor,
        "status": cmd_status,
        "once": cmd_once,
        "start": cmd_start,
        "stop": cmd_stop,
        "reset": cmd_reset,
        "config": cmd_config,
        "logs": cmd_logs,
        "version": cmd_version,
        "menu": cmd_menu,
        "enable-boot": cmd_enable_boot,
        "update": cmd_update,
    }
