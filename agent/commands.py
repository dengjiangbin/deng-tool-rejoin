"""CLI command handlers for DENG Tool: Rejoin."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import android, db
from .banner import print_banner
from .config import (
    ConfigError,
    default_config,
    ensure_app_dirs,
    load_config,
    normalize_package_detection_hint,
    safe_config_view,
    save_config,
    validate_config,
    validate_package_detection_hints,
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
from .launcher import launch_configured_packages, perform_rejoin
from .launcher_file import create_market_launchers
from .lockfile import LockManager, stop_running_agent
from .menu import run_menu
from .platform_detect import detect_public_download_dir, get_android_release, get_android_sdk, get_platform_info
from .supervisor import Supervisor
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


def _post_launch_action_label(value: str) -> str:
    return {
        "none": "None",
        "open_app": "Open Roblox app only",
        "open_link": "Open configured Roblox link",
        "send_webhook": "Send Discord webhook update after launch",
        "show_running_log": "Show running status table after launch",
    }.get(value, value)


def _package_list_label(packages: list[str]) -> str:
    return ", ".join(packages) if packages else "Not set"


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
    print("Device")
    print(f"  Device name: {cfg['device_name']}")
    print(f"  Android: {cfg['android_release']} (SDK {cfg['android_sdk']})")
    print(f"  Download folder: {cfg['download_dir'] or 'Not detected'}")
    print()
    print("Roblox")
    print(f"  Packages: {_package_list_label(cfg['roblox_packages'])}")
    print(f"  Detection hints: {_hint_list_label(cfg['package_detection_hints'])}")
    print(f"  Launch mode: {_launch_mode_label(cfg['launch_mode'])}")
    print(f"  Launch URL: {_safe_url_label(cfg['launch_url'])}")
    print(f"  Post-launch action: {_post_launch_action_label(cfg['post_launch_action'])}")
    print()
    print("Rejoin Settings")
    print(f"  Auto rejoin: {_yes_no(cfg['auto_rejoin_enabled'])}")
    print(f"  Reconnect delay: {cfg['reconnect_delay_seconds']} seconds")
    print(f"  Health check interval: {cfg['health_check_interval_seconds']} seconds")
    print(f"  Foreground grace: {cfg['foreground_grace_seconds']} seconds")
    print(f"  Root mode: {_yes_no(cfg['root_mode_enabled'])}")
    print(f"  Webhook: {_yes_no(cfg['webhook_enabled'])} ({cfg['webhook_mode']})")
    print(f"  Snapshot: {_yes_no(cfg['webhook_snapshot_enabled'])}")
    print(f"  Auto resize: {_yes_no(cfg['auto_resize_enabled'])} ({cfg['auto_resize_mode']})")


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


def _choose_package_menu(current_package: str = DEFAULT_ROBLOX_PACKAGE, package_detection_hints: list[str] | None = None) -> str:
    hints = _safe_detection_hints({"package_detection_hints": package_detection_hints})
    packages = _ordered_roblox_packages(hints)
    if not _is_interactive():
        if DEFAULT_ROBLOX_PACKAGE in packages:
            return DEFAULT_ROBLOX_PACKAGE
        return packages[0] if packages else current_package

    while True:
        print()
        print("--------------------------------")
        print("Roblox Package Setup")
        print("--------------------------------")
        packages = _ordered_roblox_packages(hints)
        if packages:
            print(f"Detection hints: {_hint_list_label(hints)}")
            print("Detected packages:")
            for idx, package in enumerate(packages, start=1):
                marker = " (Recommended)" if package == DEFAULT_ROBLOX_PACKAGE else ""
                selected = " [Current]" if package == current_package else ""
                print(f"{idx}. {package}{marker}{selected}")
        else:
            print("No Roblox package was detected yet.")
            print(f"Current detection hints: {_hint_list_label(hints)}")
            print("You can enter the package manually now, add a clone hint like moons, or install Roblox and rescan later.")
        print()
        print("M. Enter package name manually")
        print("R. Rescan packages")
        print("0. Back")
        choice = _prompt("Choose package", "1" if packages else "M").strip().lower()
        if choice == "0":
            return current_package
        if choice == "r":
            print("Rescanning Android packages...")
            continue
        if choice == "m":
            manual = _prompt_manual_package(current_package or DEFAULT_ROBLOX_PACKAGE)
            if manual:
                return manual
            continue
        if choice.isdigit() and packages:
            index = int(choice)
            if 1 <= index <= len(packages):
                return packages[index - 1]
        print("Please choose a package number, M, R, or 0.")


def _choose_packages_menu(
    current_packages: list[str] | None = None,
    package_detection_hints: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    selected = list(current_packages or [DEFAULT_ROBLOX_PACKAGE])
    hints = _safe_detection_hints({"package_detection_hints": package_detection_hints})
    if not _is_interactive():
        return selected, hints

    while True:
        print()
        print("--------------------------------")
        print("Roblox Package Setup")
        print("--------------------------------")
        print("1. Auto-detect Roblox packages")
        print("2. Enter package manually")
        print("3. View selected packages")
        print("H. Detection hints for cloned package names")
        print("0. Back")
        choice = _prompt("Choose option", "1").strip().lower()
        if choice == "0":
            return selected, hints
        if choice == "1":
            detected = _ordered_roblox_packages(hints)
            print()
            print(f"Detection hints: {_hint_list_label(hints)}")
            if not detected:
                print("No Roblox package was detected yet.")
                print("If your clone uses names like com.moons.*, add the detection hint moons.")
                print("You can also enter package names manually now, or install Roblox and rescan later.")
                continue
            print("Detected packages:")
            for idx, package in enumerate(detected, start=1):
                marker = " (Recommended)" if package == DEFAULT_ROBLOX_PACKAGE else ""
                already = " [Selected]" if package in selected else ""
                print(f"{idx}. {package}{marker}{already}")
            print("A. Select all detected packages")
            print("0. Back")
            raw = _prompt("Choose one or more numbers separated by commas", "1").strip().lower()
            if raw == "0":
                continue
            if raw == "a":
                selected = detected
                continue
            choices = [part.strip() for part in raw.split(",") if part.strip()]
            new_selection: list[str] = []
            for part in choices:
                if not part.isdigit():
                    continue
                index = int(part)
                if 1 <= index <= len(detected):
                    package = detected[index - 1]
                    if package not in new_selection:
                        new_selection.append(package)
            if new_selection:
                selected = new_selection
            else:
                print("No valid package numbers were selected.")
        elif choice == "2":
            while True:
                manual = _prompt_manual_package(selected[0] if selected else DEFAULT_ROBLOX_PACKAGE)
                if manual and manual not in selected:
                    selected.append(manual)
                    print(f"Added: {manual}")
                if not _prompt_yes_no("Add another package?", False):
                    break
        elif choice == "3":
            print("Selected packages:")
            for idx, package in enumerate(selected, start=1):
                print(f"  {idx}. {package}")
        elif choice == "h":
            print()
            print("Detection Hints")
            print("Hints are safe package-name fragments used only for local package scanning.")
            print(f"Current hints: {_hint_list_label(hints)}")
            print("Example for com.moons.* clones: moons")
            print("Example for a prefix: com.moons.")
            print("1. Add hint")
            print("2. Reset to defaults")
            print("0. Back")
            hint_choice = _prompt("Choose option", "1").strip().lower()
            if hint_choice == "1":
                raw_hint = _prompt("Detection hint", "moons").strip()
                try:
                    hint = normalize_package_detection_hint(raw_hint)
                    if hint not in hints:
                        hints.append(hint)
                    print(f"Detection hint saved for this setup: {hint}")
                except ConfigError as exc:
                    print(f"That hint is not safe: {exc}")
            elif hint_choice == "2":
                hints = list(DEFAULT_ROBLOX_PACKAGE_HINTS)
                print("Detection hints reset to defaults.")
        else:
            print("Please choose 1, 2, 3, H, or 0.")


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


def _setup_post_launch_action(draft: dict[str, Any]) -> None:
    print()
    print("Post-Launch Action")
    print("DENG does not run Roblox scripts, executors, anti-AFK, farming, or gameplay automation.")
    print("1. None")
    print("2. Open Roblox app only")
    print("3. Open configured Roblox link")
    print("4. Send Discord webhook update after launch")
    print("5. Show running status table after launch")
    mapping = {"1": "none", "2": "open_app", "3": "open_link", "4": "send_webhook", "5": "show_running_log"}
    draft["post_launch_action"] = mapping.get(_prompt("Choose action", "1").strip(), "none")


def _setup_auto_resize(draft: dict[str, Any]) -> None:
    print()
    print("Auto Resize / Window Layout Setup")
    print("For cloned Roblox packages, DENG can calculate a safe window grid and update App Cloner window preferences only when accessible.")
    print("1. No")
    print("2. Yes, auto layout based on package count")
    print("3. Preview layout only")
    choice = _prompt("Choose auto resize option", "1").strip()
    packages = draft.get("roblox_packages") or [draft.get("roblox_package", DEFAULT_ROBLOX_PACKAGE)]
    if choice == "1":
        draft["auto_resize_enabled"] = False
        draft["auto_resize_mode"] = "off"
        return
    draft["auto_resize_enabled"] = choice == "2"
    draft["auto_resize_mode"] = "auto" if choice == "2" else "preview"
    draft["window_gap_px"] = _prompt_int("Window gap pixels", int(draft.get("window_gap_px", 8)), 0)
    preview = window_layout.build_layout_preview(packages, gap=int(draft["window_gap_px"]))
    draft["last_layout_preview"] = preview
    print("Layout preview:")
    for line in preview:
        print(f"  {line}")


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
    """Edit config through a public-friendly menu.

    Returns `(config, saved)`; `config is None` means the user cancelled.
    """
    draft = _refresh_detected_fields(dict(config_data))
    if not _is_interactive():
        _print_setup_menu(draft, title=title)
        print("\nCurrent settings:")
        _print_config_summary(draft)
        print("\nRun this command in an interactive Termux session to edit settings.")
        return draft, False

    while True:
        print_banner(use_color=not args.no_color)
        if "Setup" in title:
            print("Welcome. This setup uses simple choices; you do not need to edit code or JSON.")
            print()
        _print_setup_menu(draft, title=title)
        try:
            choice = input("Choose an option [9]: ").strip().lower() or "9"
        except EOFError:
            print("\nNo interactive input was available. Run this command in Termux to edit settings.")
            print("\nCurrent settings:")
            _print_config_summary(draft)
            return draft, False
        if choice == "0":
            print("Setup cancelled. No changes were saved.")
            return None, False
        if choice == "1":
            print("Give this phone/cloud-phone a simple name for status screens.")
            draft["device_name"] = _prompt("Device name", str(draft.get("device_name") or "Termux Android")).strip() or "Termux Android"
        elif choice == "2":
            draft["roblox_package"] = _choose_package_menu(
                str(draft.get("roblox_package") or DEFAULT_ROBLOX_PACKAGE),
                list(draft.get("package_detection_hints") or DEFAULT_ROBLOX_PACKAGE_HINTS),
            )
            draft["roblox_packages"] = [draft["roblox_package"]]
            draft["selected_package_mode"] = "single"
        elif choice == "3":
            draft["launch_mode"] = _choose_launch_mode(str(draft.get("launch_mode") or "app"))
            if draft["launch_mode"] == "app":
                draft["launch_url"] = ""
        elif choice == "4":
            draft["launch_url"] = _prompt_launch_url(str(draft.get("launch_url") or ""), str(draft.get("launch_mode") or "app"))
        elif choice == "5":
            print("Auto rejoin lets DENG watch Roblox locally and relaunch when it appears closed or unhealthy.")
            draft["auto_rejoin_enabled"] = _prompt_yes_no("Enable auto rejoin?", bool(draft.get("auto_rejoin_enabled")))
        elif choice == "6":
            print("Reconnect delay is how long DENG waits after closing Roblox before reopening it.")
            draft["reconnect_delay_seconds"] = _prompt_int("Reconnect delay (seconds)", int(draft["reconnect_delay_seconds"]), 5)
        elif choice == "7":
            root_info = android.detect_root()
            draft["root_available"] = root_info.available
            print("Root is optional.")
            print("With root, DENG can force-close Roblox before reopening it.")
            print("Without root, DENG can still open Roblox, but restart power is limited.")
            if root_info.available:
                print(f"Root detected via {root_info.tool}.")
            else:
                print("Root was not detected or permission was not granted.")
            draft["root_mode_enabled"] = _prompt_yes_no("Use root mode if available?", bool(draft.get("root_mode_enabled")) and root_info.available)
        elif choice == "8":
            print("Health check interval controls how often the auto-rejoin supervisor checks Roblox.")
            draft["health_check_interval_seconds"] = _prompt_int("Health check interval (seconds)", int(draft["health_check_interval_seconds"]), 10)
        elif choice == "9":
            try:
                saved = save_config(draft)
            except ConfigError as exc:
                print(f"Config could not be saved: {exc}")
                input("Press Enter to continue...")
                continue
            print("\nSettings saved.")
            _print_config_summary(saved)
            return saved, True
        elif choice == "a":
            print("\nAdvanced Info")
            _print_json(safe_config_view(draft))
            input("\nPress Enter to return to setup...")
        else:
            print("Please choose 1-9, A, or 0.")
            input("Press Enter to continue...")


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
    print("Step 1 of 8: Roblox Package Setup")
    packages, hints = _choose_packages_menu(
        list(draft.get("roblox_packages") or [draft.get("roblox_package", DEFAULT_ROBLOX_PACKAGE)]),
        list(draft.get("package_detection_hints") or DEFAULT_ROBLOX_PACKAGE_HINTS),
    )
    draft["roblox_packages"] = packages
    draft["package_detection_hints"] = hints
    draft["roblox_package"] = draft["roblox_packages"][0]
    draft["selected_package_mode"] = "multiple" if len(draft["roblox_packages"]) > 1 else "single"
    print("\nStep 2 of 8: Roblox Public / Private Server Link")
    _setup_launch_link(draft)
    print("\nStep 3 of 8: Discord Webhook Setup")
    _setup_webhook(draft)
    print("\nStep 4 of 8: Phone Snapshot For Webhook")
    _setup_snapshot(draft)
    print("\nStep 5 of 8: Webhook Info Interval")
    _setup_webhook_interval(draft)
    print("\nStep 6 of 8: Post-Launch Action")
    _setup_post_launch_action(draft)
    print("\nStep 7 of 8: Auto Resize / Window Layout Setup")
    _setup_auto_resize(draft)
    print("\nStep 8 of 8: Save And Start")
    draft["first_setup_completed"] = True
    try:
        saved = save_config(draft)
    except ConfigError as exc:
        print(f"Setup could not be saved: {exc}")
        return None, False
    print("First-time setup saved.")
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
        print("1. Roblox Package Setup")
        print("2. Roblox Launch Link")
        print("3. Discord Webhook Setup")
        print("4. Phone Snapshot For Webhook")
        print("5. Webhook Info Interval")
        print("6. Post-Launch Action")
        print("7. Auto Resize / Window Layout Setup")
        print("8. Save and Finish")
        print("0. Cancel")
        print("--------------------------------")
        print("\nCurrent settings:")
        _print_config_summary(draft)
        return draft, False

    while True:
        print_banner(use_color=not args.no_color)
        print("--------------------------------")
        print("DENG Tool: Rejoin Config")
        print("--------------------------------")
        print("1. Roblox Package Setup")
        print("2. Roblox Launch Link")
        print("3. Discord Webhook Setup")
        print("4. Phone Snapshot For Webhook")
        print("5. Webhook Info Interval")
        print("6. Post-Launch Action")
        print("7. Auto Resize / Window Layout Setup")
        print("8. Save and Finish")
        print("A. Advanced Info")
        print("0. Cancel")
        print("--------------------------------")
        try:
            choice = input("Choose setting [8]: ").strip().lower() or "8"
        except EOFError:
            print("\nNo interactive input was available. Run this command in Termux to edit settings.")
            print("\nCurrent settings:")
            _print_config_summary(draft)
            return draft, False
        if choice == "0":
            print("No changes saved.")
            return None, False
        if choice == "1":
            packages, hints = _choose_packages_menu(
                list(draft.get("roblox_packages") or [draft.get("roblox_package", DEFAULT_ROBLOX_PACKAGE)]),
                list(draft.get("package_detection_hints") or DEFAULT_ROBLOX_PACKAGE_HINTS),
            )
            draft["roblox_packages"] = packages
            draft["package_detection_hints"] = hints
            draft["roblox_package"] = draft["roblox_packages"][0]
            draft["selected_package_mode"] = "multiple" if len(draft["roblox_packages"]) > 1 else "single"
        elif choice == "2":
            _setup_launch_link(draft)
        elif choice == "3":
            _setup_webhook(draft)
        elif choice == "4":
            _setup_snapshot(draft)
        elif choice == "5":
            _setup_webhook_interval(draft)
        elif choice == "6":
            _setup_post_launch_action(draft)
        elif choice == "7":
            _setup_auto_resize(draft)
        elif choice == "8":
            try:
                saved = save_config(draft)
            except ConfigError as exc:
                print(f"Config could not be saved: {exc}")
                input("Press Enter to continue...")
                continue
            print("Config saved.")
            return saved, True
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
    print(f"  Selected packages: {_package_list_label(cfg['roblox_packages'])}")
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
    print(f"  Post-launch action: {_post_launch_action_label(cfg['post_launch_action'])}")
    print()
    print("Webhook")
    print(f"  Status updates: {_yes_no(cfg['webhook_enabled'])}")
    print(f"  Mode: {cfg['webhook_mode']}")
    print(f"  Snapshot: {_yes_no(cfg['webhook_snapshot_enabled'])}")
    print(f"  Interval: {cfg['webhook_interval_seconds']} seconds")
    print(f"  URL: {safe.get('webhook_url') or 'Not set'}")
    print()
    print("Window Layout")
    print(f"  Auto resize: {_yes_no(cfg['auto_resize_enabled'])}")
    print(f"  Mode: {cfg['auto_resize_mode']}")
    print(f"  Gap: {cfg['window_gap_px']} px")
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


def cmd_start(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    try:
        cfg = load_config()
        if not cfg.get("first_setup_completed"):
            print("First-time setup is required before starting.")
            if _is_interactive():
                _run_first_time_setup_wizard(cfg, args, start_after_save=True)
                return 0
            print("Run: deng-rejoin and choose First Time Setup Config.")
            return 2

        packages = cfg.get("roblox_packages") or [cfg["roblox_package"]]
        if cfg.get("auto_resize_mode") in {"auto", "preview"}:
            write_xml = bool(cfg.get("auto_resize_enabled") and cfg.get("auto_resize_mode") == "auto")
            messages, preview = window_layout.apply_layout_to_packages(packages, gap=int(cfg.get("window_gap_px", 8)), write_xml=write_xml)
            cfg["last_layout_preview"] = preview
            save_config(cfg)
            print("Window layout:")
            for message in messages:
                print(f"  {message}")

        results = launch_configured_packages(cfg, reason="start")
        if cfg.get("post_launch_action") == "show_running_log":
            print("Roblox launch table:")
            print("Package | Result | Root Used | Error")
            for package, result in zip(packages, results):
                print(f"{package} | {'OK' if result.success else 'FAIL'} | {str(result.root_used).lower()} | {result.error or ''}")

        snapshot_path = None
        if cfg.get("webhook_snapshot_enabled"):
            snapshot.cleanup_old_snapshots(int(cfg.get("snapshot_max_age_seconds", 300)))
            snapshot_path, snap_message = snapshot.capture_snapshot()
            print(f"Snapshot: {snap_message}")
            if snapshot_path:
                cfg["snapshot_temp_path"] = str(snapshot_path)

        if cfg.get("webhook_enabled"):
            ok, message, message_id = webhook.send_webhook_update(cfg, event="start", snapshot_path=snapshot_path, force=cfg.get("post_launch_action") == "send_webhook")
            print(f"Webhook: {message}")
            if ok:
                cfg["webhook_last_sent_at"] = datetime.now(timezone.utc).timestamp()
                if message_id:
                    cfg["webhook_last_message_id"] = message_id
                save_config(cfg)

        if cfg.get("auto_rejoin_enabled"):
            print("Auto rejoin is enabled. Starting supervisor loop.")
            Supervisor().run_forever()
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
