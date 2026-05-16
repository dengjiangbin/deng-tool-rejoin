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
    effective_private_server_url,
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
    validate_username_source,
)
from .constants import (
    CONFIG_PATH,
    DB_PATH,
    DEFAULT_LICENSE_SERVER_URL,
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
from .onboarding import (
    NEW_USER_HELP_TEXT,
    print_beginner_license_gate_help,
    print_beginner_menu_license_prompt,
)
from .platform_detect import detect_public_download_dir, get_android_release, get_android_sdk, get_platform_info
from .supervisor import MultiPackageSupervisor, Supervisor
from . import keystore
from .license import (
    WRONG_DEVICE_USER_MESSAGE,
    check_remote_license_status,
    get_device_summary,
    sync_install_id_with_config,
)
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
    "license",
    "new-user-help",
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


def _print_dev_license_skipped(use_color: bool) -> None:
    msg = "Dev Mode: License Check Skipped"
    if use_color:
        print(f"{_ANSI_YELLOW}{msg}{_ANSI_RESET}")
    else:
        print(msg)


def _print_license_ok(use_color: bool) -> None:
    if use_color:
        print(f"{_ANSI_GREEN}License OK{_ANSI_RESET}")
    else:
        print("OK: License Verified")


def _print_license_err(message: str, use_color: bool) -> None:
    if use_color:
        print(f"{_ANSI_RED}{message}{_ANSI_RESET}")
    else:
        print(message if message.upper().startswith("ERROR:") else f"ERROR: {message}")


def _persist_license_status(cfg: dict[str, Any], status: str) -> dict[str, Any]:
    from .config import utc_now

    lic = cfg.setdefault("license", {})
    lic["last_status"] = status
    lic["last_check_at"] = utc_now()
    return save_config(cfg)


def _ensure_install_id_saved(cfg: dict[str, Any]) -> dict[str, Any]:
    lic = cfg.setdefault("license", {})
    before = lic.get("install_id")
    sync_install_id_with_config(lic)
    if lic.get("install_id") != before:
        return save_config(cfg)
    return cfg


def _remote_license_run_check(cfg: dict[str, Any]) -> tuple[str, str]:
    lic = cfg.setdefault("license", {})
    key = (lic.get("key") or "").strip()
    if not key:
        return "missing_key", "No license key configured."
    install_id = sync_install_id_with_config(lic)
    device = get_device_summary()
    srv = (lic.get("server_url") or "").strip() or DEFAULT_LICENSE_SERVER_URL
    return check_remote_license_status(
        srv,
        license_key=key,
        install_id=install_id,
        device_model=device.get("model") or "unknown",
        app_version=VERSION,
        device_label=str(lic.get("device_label") or ""),
    )


def verify_remote_license_noninteractive(cfg: dict[str, Any], *, use_color: bool) -> bool:
    """Return True when remote license check is ``active`` (updates ``last_status``)."""
    result, msg = _remote_license_run_check(cfg)
    if result == "active":
        _print_license_ok(use_color)
        _persist_license_status(cfg, "active")
        return True
    _persist_license_status(cfg, result)
    if result == "wrong_device":
        _print_license_err(WRONG_DEVICE_USER_MESSAGE, use_color)
    elif result == "key_not_redeemed":
        _print_license_err(msg, use_color)
    elif result == "missing_key":
        _print_license_err("No License Key Found", use_color)
    else:
        _print_license_err(f"License Invalid: {msg}", use_color)
    print_beginner_license_gate_help(
        show_hwid_footer=(result not in ("wrong_device", "key_not_redeemed"))
    )
    return False


def _ensure_local_license_menu_loop(cfg: dict[str, Any], args: argparse.Namespace, use_color: bool) -> bool:
    while True:
        try:
            cfg = load_config()
        except ConfigError as exc:
            _print_license_err(str(exc), use_color)
            return False
        lic = cfg.setdefault("license", {})
        key = (lic.get("key") or "").strip() or (cfg.get("license_key") or "").strip()
        if not key:
            if not _is_interactive():
                _print_license_err("No License Key Found", use_color)
                print_beginner_license_gate_help()
                return False
            print_beginner_menu_license_prompt()
            if not keystore.prompt_and_verify_key():
                return False
            _print_license_ok(use_color)
            return True
        ok, msg = keystore.verify_key(key)
        if ok:
            _print_license_ok(use_color)
            return True
        _print_license_err(msg, use_color)
        if not _is_interactive():
            return False
        print("\n1. Enter Different Key\n2. Exit")
        try:
            choice = input("Choose [2]: ").strip() or "2"
        except EOFError:
            return False
        if choice != "1":
            return False
        lic["key"] = ""
        cfg["license_key"] = ""
        cfg = save_config(cfg)


def _ensure_remote_license_menu_loop(cfg: dict[str, Any], args: argparse.Namespace, use_color: bool) -> bool:
    while True:
        try:
            cfg = load_config()
        except ConfigError as exc:
            _print_license_err(str(exc), use_color)
            return False
        cfg = _ensure_install_id_saved(cfg)
        lic = cfg.setdefault("license", {})
        key = (lic.get("key") or "").strip()
        if not key:
            if not _is_interactive():
                _print_license_err("No License Key Found", use_color)
                print_beginner_license_gate_help()
                return False
            print_beginner_menu_license_prompt()
            try:
                raw = input("Paste your license key: ").strip()
            except EOFError:
                return False
            if not raw:
                continue
            try:
                norm = validate_license_key(raw)
            except ConfigError as exc:
                _print_license_err(str(exc), use_color)
                continue
            lic["key"] = norm
            cfg["license_key"] = norm
            cfg = save_config(cfg)
            key = norm

        result, msg = _remote_license_run_check(cfg)
        if result == "active":
            _print_license_ok(use_color)
            _persist_license_status(cfg, "active")
            return True

        cfg = _persist_license_status(cfg, result)
        if result == "wrong_device":
            _print_license_err(WRONG_DEVICE_USER_MESSAGE, use_color)
        elif result == "key_not_redeemed":
            _print_license_err(msg, use_color)
        else:
            _print_license_err(f"License Invalid: {msg}", use_color)

        if not _is_interactive():
            return False

        if result == "wrong_device":
            print("\n1. Enter Different Key\n2. Exit")
        else:
            print("\n1. Try Another Key\n2. Exit")
        try:
            choice = input("Choose [2]: ").strip() or "2"
        except EOFError:
            return False
        if choice != "1":
            return False
        lic["key"] = ""
        cfg["license_key"] = ""
        cfg = save_config(cfg)


def ensure_menu_can_open(_args: argparse.Namespace) -> bool:
    """Gate checking is performed inside cmd_menu(); this function is kept for compatibility."""
    return True


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
    return validate_account_username(entry.get("account_username", "")) or "Unknown"


def _package_username_display(entry: dict[str, Any]) -> str:
    """Username for package menus / tables — empty becomes Unknown."""
    u = validate_account_username(entry.get("account_username", ""))
    return u if u else "Unknown"


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


def _package_detection_options(config_data: dict[str, Any]) -> tuple[list[str], bool, bool]:
    pd = config_data.get("package_detection")
    if not isinstance(pd, dict):
        pd = {}
    hints_src = pd.get("hints")
    if hints_src in (None, "", []):
        hints_src = config_data.get("package_detection_hints")
    try:
        hints = validate_package_detection_hints(hints_src)
    except ConfigError:
        hints = list(DEFAULT_ROBLOX_PACKAGE_HINTS)
    return hints, bool(pd.get("include_launchable_only", True)), bool(pd.get("enabled", True))


def _gather_roblox_candidates_for_ui(config_data: dict[str, Any]) -> list[android.RobloxPackageCandidate]:
    hints2, inc_launch, det_en = _package_detection_options(config_data)
    candidates = android.discover_roblox_package_candidates(
        hints2,
        include_launchable_only=inc_launch,
        detection_enabled=det_en,
    )
    if not candidates:
        for pkg in android.find_roblox_packages(hints2):
            candidates.append(
                android.RobloxPackageCandidate(
                    package=pkg,
                    app_name=android.get_application_label(pkg) or pkg.rsplit(".", 1)[-1],
                    launchable=android.is_launchable_package(pkg),
                )
            )
    return candidates


def _print_full_discovery_table(candidates: list[android.RobloxPackageCandidate]) -> None:
    print()
    print("Detected Roblox Packages")
    print(f"{'#':<4} {'Package':<40} {'App Name':<22} {'Launchable':<10}")
    print(f"{'-'*4} {'-'*40} {'-'*22} {'-'*10}")
    for idx, c in enumerate(candidates, start=1):
        launch_cell = "Yes" if c.launchable else "No"
        print(f"{idx:<4} {c.package:<40} {c.app_name[:20]:<22} {launch_cell:<10}")


def _interactive_discover_package_entries(
    config_data: dict[str, Any],
    existing_entries: list[dict[str, Any]],
    *,
    exclude_packages: set[str] | frozenset[str] | None = None,
    config_for_detect: dict[str, Any] | None = None,
    candidates: list[android.RobloxPackageCandidate] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Full discovery table + multiselect (same path as first-time Roblox package step).

    Returns ``(entries, reason)`` where reason is ``ok``, ``no_candidates``, or ``empty_choice``.

    When ``candidates`` is passed (e.g. Add Package after pre-filter), discovery is not re-run.
    """
    if candidates is None:
        exclude = frozenset(exclude_packages or ())
        candidates = _gather_roblox_candidates_for_ui(config_data)
        candidates = [c for c in candidates if c.package not in exclude]
    if not candidates:
        return [], "no_candidates"
    if len(candidates) == 1:
        c0 = candidates[0]
        print(f"Auto-selected: {c0.package}")
        return [
            _detect_or_prompt_account_username(
                _entry_for_package(c0.package, existing_entries, app_name=c0.app_name),
                config_for_detect,
            )
        ], "ok"
    _print_full_discovery_table(candidates)
    print("  A. Select all")
    raw = input("Choose packages (e.g. 1,2 or A) [1]: ").strip().lower() or "1"
    picked: list[android.RobloxPackageCandidate] = []
    if raw == "a":
        picked = list(candidates)
    else:
        seen: set[str] = set()
        for part in [p.strip() for p in raw.split(",") if p.strip()]:
            if part.isdigit():
                index = int(part)
                if 1 <= index <= len(candidates):
                    c = candidates[index - 1]
                    if c.package not in seen:
                        seen.add(c.package)
                        picked.append(c)
    if not picked:
        return [], "empty_choice"
    return [
        _detect_or_prompt_account_username(
            _entry_for_package(c.package, existing_entries, app_name=c.app_name),
            config_for_detect,
        )
        for c in picked
    ], "ok"


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


def _entry_for_package(package: str, current_entries: list[dict[str, Any]], *, app_name: str = "") -> dict[str, Any]:
    for entry in current_entries:
        if entry["package"] == package:
            return dict(entry)
    return package_entry(package, "", True, "not_set", app_name=str(app_name or "")[:120])


def _detect_or_prompt_account_username(entry: dict[str, Any], config_data: dict[str, Any] | None = None) -> dict[str, Any]:
    updated = dict(entry)
    if validate_account_username(updated.get("account_username", "")):
        return updated
    cfg_valid = validate_config(config_data) if config_data is not None else None
    try:
        result = account_detect.detect_account_username(
            updated["package"],
            entry=updated,
            config=cfg_valid,
            use_root=True,
            respect_config_manual=False,
        )
    except PermissionError as exc:
        import logging as _logging
        _logging.getLogger("deng_tool_rejoin").debug(
            "Permission denied during username auto-detect for %s: %s",
            updated.get("package", "?"),
            exc,
        )
        result = None
    except Exception as exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger("deng_tool_rejoin").debug(
            "Username auto-detect error for %s: %s",
            updated.get("package", "?"),
            exc,
        )
        result = None
    if result:
        updated["account_username"] = validate_account_username(result.username)
        updated["username_source"] = validate_username_source(result.source, result.username)
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
        print(f"  {idx}. {_package_username_display(entry):<20} {entry['package']}{enabled}")


def _choose_package_menu(current_package: str = DEFAULT_ROBLOX_PACKAGE, package_detection_hints: list[str] | None = None) -> str:
    try:
        cfg_ctx = load_config()
    except ConfigError:
        cfg_ctx = {}
    entries, _hints = _choose_packages_menu(
        [package_entry(current_package, "", True, "not_set")],
        package_detection_hints,
        cfg_ctx,
    )
    enabled = [entry for entry in entries if entry.get("enabled", True)]
    return enabled[0]["package"] if enabled else current_package


def _choose_packages_menu(
    current_packages: list[Any] | None = None,
    package_detection_hints: list[str] | None = None,
    config_data: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    cfg_ctx = config_data if isinstance(config_data, dict) else {}
    merged_hints_ctx = dict(cfg_ctx)
    if package_detection_hints is not None:
        merged_hints_ctx["package_detection_hints"] = package_detection_hints
    selected = validate_package_entries(current_packages or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")])
    hints = _safe_detection_hints(merged_hints_ctx)
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
        new_sel, reason = _interactive_discover_package_entries(
            cfg_ctx,
            selected,
            exclude_packages=None,
            config_for_detect=None,
        )
        if reason == "no_candidates":
            print()
            print("No Roblox Package Detected")
            print()
            print("Try:")
            print("  1. Install Roblox or your Roblox clone APK.")
            print("  2. Open Roblox once manually.")
            print("  3. Return to Termux.")
            print("  4. Run package detection again.")
            print("  5. Use manual package entry if needed.")
            print()
            return selected, hints
        if reason == "ok":
            selected = new_sel
        return selected, hints
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
    print("Paste a Roblox link, or leave blank to skip.")
    if launch_mode == "deeplink":
        print("Example: roblox://experiences/start?placeId=123")
    else:
        print("Example: https://www.roblox.com/games/123/name?privateServerLinkCode=...")
    while True:
        value = _prompt("Launch URL (blank to skip)", current_url).strip()
        if not value:
            print("Skipped. No launch URL set.")
            return ""
        try:
            result = validate_launch_url(value, launch_mode, allow_uncertain=True)
            if result.warning:
                print(f"Note: {result.warning}")
            return value
        except UrlValidationError as exc:
            print(f"That URL cannot be used yet: {exc}")
            print("Enter a valid URL or leave blank to skip.")


def _setup_launch_link(draft: dict[str, Any]) -> None:
    print()
    print("Roblox Launch Link")
    print("Roblox Launch Link Is Optional. Leave Blank To Skip.")
    print("1. App Only, No Link")
    print("2. Public Roblox Game URL")
    print("3. Private Server URL")
    print("4. Roblox Deeplink")
    choice = _prompt("Choose launch link type", "1").strip()
    if choice == "1" or not choice:
        draft["launch_mode"] = "app"
        draft["launch_url"] = ""
        return
    if choice in {"2", "3"}:
        url = _prompt_launch_url(str(draft.get("launch_url") or ""), "web_url")
        if url:
            draft["launch_mode"] = "web_url"
            draft["launch_url"] = url
        else:
            draft["launch_mode"] = "app"
            draft["launch_url"] = ""
        return
    if choice == "4":
        url = _prompt_launch_url(str(draft.get("launch_url") or ""), "deeplink")
        if url:
            draft["launch_mode"] = "deeplink"
            draft["launch_url"] = url
        else:
            draft["launch_mode"] = "app"
            draft["launch_url"] = ""
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


# ─── Config Menu Submenus ─────────────────────────────────────────────────────

def _config_menu_package(draft: dict[str, Any]) -> dict[str, Any]:
    """Package submenu: Add / Remove / Auto Detect / Detect Usernames; current packages shown at top."""
    if not _is_interactive():
        return draft
    while True:
        print()
        print("--------------------------------")
        print("Package")
        print("--------------------------------")
        entries = validate_package_entries(
            draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
        )
        enabled_entries = [e for e in entries if e.get("enabled", True)]
        print("Current Packages:")
        if enabled_entries:
            for idx, entry in enumerate(enabled_entries, start=1):
                username = _package_username_display(entry)
                print(f"  {idx}. {entry['package']} — {username}")
        else:
            print("  No Packages Configured.")
        print()
        print("1. Add Package")
        print("2. Remove Package")
        print("3. Auto Detect Packages")
        print("4. Detect / Refresh Usernames")
        print("0. Back")
        print("--------------------------------")
        try:
            choice = input("Choose [0]: ").strip() or "0"
        except EOFError:
            break
        if choice == "0":
            break
        elif choice == "1":
            draft = _package_menu_add(draft)
        elif choice == "2":
            draft = _package_menu_remove(draft)
        elif choice == "3":
            draft = _package_menu_auto_detect(draft)
        elif choice == "4":
            draft = _package_menu_detect_refresh(draft)
        else:
            print("Please choose 1-4 or 0.")
    return draft


def _package_menu_detect_refresh(draft: dict[str, Any]) -> dict[str, Any]:
    """Re-run username detection for each package; optionally persist results."""
    print()
    print("Detect / Refresh Usernames")
    entries = validate_package_entries(
        draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
    )
    cfg = validate_config(draft)
    settings = cfg.get("account_detection") or {}
    if not settings.get("enabled", True):
        print("Account detection is disabled.")
        input("\nPress Enter to continue...")
        return draft

    pairs = account_detect.detect_account_usernames_for_packages(
        [dict(e) for e in entries],
        config=cfg,
        use_root=bool(settings.get("use_root", True)),
        respect_config_manual=False,
    )
    new_entries: list[dict[str, Any]] = []
    print()
    print(f"  {'Package':<40} {'Old':<16} {'New':<16} {'Source':<18} {'Status':<12}")
    print(f"  {'-'*40} {'-'*16} {'-'*16} {'-'*18} {'-'*12}")
    changed = False
    for idx, prior in enumerate(entries):
        base_entry, res = pairs[idx]
        pkg = str(base_entry.get("package") or "")
        old_u = validate_account_username(prior.get("account_username", "")) or "Unknown"
        if res:
            new_u = validate_account_username(res.username) or "Unknown"
            src = res.source
            merged = dict(prior)
            merged["account_username"] = validate_account_username(res.username)
            merged["username_source"] = validate_username_source(res.source, res.username)
            if old_u != new_u:
                status = "updated"
                changed = True
            else:
                status = "unchanged"
            new_entries.append(merged)
        else:
            new_u = old_u
            src = "—"
            status = "not found"
            new_entries.append(dict(prior))
        print(f"  {pkg:<40} {str(old_u)[:16]:<16} {str(new_u)[:16]:<16} {str(src)[:18]:<18} {status:<12}")

    if new_entries:
        draft["roblox_packages"] = new_entries
        active = enabled_package_entries(draft)
        if active:
            draft["roblox_package"] = active[0]["package"]
        draft["selected_package_mode"] = "multiple" if len(active) > 1 else "single"
        if changed:
            draft = save_config(draft)
            print("\nSaved updated usernames to config.")
        else:
            print("\nNo username changes to save.")
    input("\nPress Enter to continue...")
    return draft


def _package_menu_set_username(draft: dict[str, Any]) -> dict[str, Any]:
    """Manually set account_username for one package.

    Not offered in the Package submenu (detection covers typical cases). Kept for tests/tools.
    """
    print()
    print("Set / Edit Username")
    entries = validate_package_entries(
        draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
    )
    for idx, entry in enumerate(entries, start=1):
        print(f"  {idx}. {entry['package']} — {_package_username_display(entry)}")
    print("  0. Back")
    choice = input("Choose package [0]: ").strip() or "0"
    if choice == "0" or not choice.isdigit():
        return draft
    i = int(choice) - 1
    if not (0 <= i < len(entries)):
        print("Invalid choice.")
        return draft
    target = dict(entries[i])
    current = validate_account_username(target.get("account_username", "")) or ""
    hint = f" [{current}]" if current else ""
    raw = input(f"Roblox username / display name for {target['package']}{hint}: ").strip()
    if not raw:
        print("Skipped.")
        return draft
    try:
        target["account_username"] = validate_account_username(raw)
        target["username_source"] = validate_username_source("manual", target["account_username"])
    except ConfigError as exc:
        print(f"Invalid username: {exc}")
        input("Press Enter to continue...")
        return draft
    entries = [target if e["package"] == target["package"] else dict(e) for e in entries]
    draft["roblox_packages"] = entries
    draft = save_config(draft)
    print(f"Username saved for {target['package']}.")
    input("Press Enter to continue...")
    return draft


def _package_menu_add(draft: dict[str, Any]) -> dict[str, Any]:
    """Add package(s) via the same full discovery table as first-time wizard, or manual entry."""
    current_entries = validate_package_entries(
        draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
    )
    current_pkgs = {e["package"] for e in current_entries}
    print()
    print("Add Package")
    print("1. Auto Detect")
    print("2. Enter Manually")
    print("0. Back")
    choice = input("Choose [1]: ").strip() or "1"
    if choice == "0":
        return draft
    new_entries_to_append: list[dict[str, Any]] = []
    if choice == "1":
        all_detected = _gather_roblox_candidates_for_ui(draft)
        fresh_candidates = [c for c in all_detected if c.package not in current_pkgs]
        if not all_detected:
            print("No Roblox-compatible packages were detected. Try manual entry.")
            return draft
        if not fresh_candidates:
            print("All Detected Packages Are Already Added.")
            return draft
        new_sel, reason = _interactive_discover_package_entries(
            draft,
            current_entries,
            config_for_detect=draft,
            candidates=fresh_candidates,
        )
        if reason == "empty_choice":
            return draft
        new_entries_to_append = new_sel
    elif choice == "2":
        default_pkg = current_entries[0]["package"] if current_entries else DEFAULT_ROBLOX_PACKAGE
        manual = _prompt_manual_package(default_pkg)
        if manual:
            if manual in current_pkgs:
                print(f"Package Already Added: {manual}")
                return draft
            new_entries_to_append = [_detect_or_prompt_account_username(_entry_for_package(manual, current_entries), draft)]
    if not new_entries_to_append:
        return draft
    added_any = False
    for entry in new_entries_to_append:
        if entry["package"] in current_pkgs:
            print(f"Package Already Added: {entry['package']}")
            continue
        current_entries.append(entry)
        current_pkgs.add(entry["package"])
        added_any = True
        print(f"Package Added: {entry['package']}")
    if added_any:
        draft["roblox_packages"] = current_entries
        active = enabled_package_entries(draft)
        draft["roblox_package"] = active[0]["package"]
        draft["selected_package_mode"] = "multiple" if len(active) > 1 else "single"
        draft = save_config(draft)
    return draft


def _package_menu_remove(draft: dict[str, Any]) -> dict[str, Any]:
    """Remove a configured package by number. Confirms before removing."""
    entries = validate_package_entries(
        draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
    )
    enabled = [e for e in entries if e.get("enabled", True)]
    if not enabled:
        print("No Packages Configured.")
        return draft
    print()
    print("Remove Package")
    for idx, entry in enumerate(enabled, start=1):
        username = _package_username_display(entry)
        print(f"  {idx}. {entry['package']} — {username}")
    print("  0. Back")
    choice = input("Choose package to remove [0]: ").strip() or "0"
    if choice == "0" or not choice.isdigit():
        return draft
    i = int(choice) - 1
    if not (0 <= i < len(enabled)):
        print("Invalid choice.")
        return draft
    target = enabled[i]
    confirm = input(f"Remove {target['package']}? [y/N]: ").strip().lower()
    if confirm not in {"y", "yes"}:
        print("Cancelled.")
        return draft
    remaining = [e for e in entries if e["package"] != target["package"]]
    if not remaining:
        print("Cannot Remove The Last Package.")
        return draft
    draft["roblox_packages"] = remaining
    active = [e for e in remaining if e.get("enabled", True)]
    if active:
        draft["roblox_package"] = active[0]["package"]
    draft["selected_package_mode"] = "multiple" if len(active) > 1 else "single"
    draft = save_config(draft)
    print(f"Package Removed: {target['package']}")
    return draft


def _package_menu_auto_detect(draft: dict[str, Any]) -> dict[str, Any]:
    """Auto-detect Roblox packages and offer to add those not already added."""
    print()
    print("Auto Detect Packages")
    hints = _safe_detection_hints(draft)
    detected = _ordered_roblox_packages(hints)
    if not detected:
        print("No Roblox Packages Detected.")
        input("Press Enter to continue...")
        return draft
    current_entries = validate_package_entries(
        draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
    )
    current_pkgs = {e["package"] for e in current_entries}
    new_pkgs = [p for p in detected if p not in current_pkgs]
    print(f"Detected {len(detected)} package(s):")
    for idx, pkg in enumerate(detected, start=1):
        status = "Already Added" if pkg in current_pkgs else "New"
        marker = " (Recommended)" if pkg == DEFAULT_ROBLOX_PACKAGE else ""
        print(f"  {idx}. [{status}] {pkg}{marker}")
    if not new_pkgs:
        print("All Detected Packages Are Already Added.")
        input("Press Enter to continue...")
        return draft
    print()
    raw = input("Select packages to add (e.g. 1,2 or A for all new) [A]: ").strip().lower() or "a"
    to_add: list[str] = []
    if raw == "a":
        to_add = list(new_pkgs)
    else:
        for part in [p.strip() for p in raw.split(",") if p.strip()]:
            if part.isdigit():
                i = int(part) - 1
                if 0 <= i < len(detected):
                    p = detected[i]
                    if p not in current_pkgs and p not in to_add:
                        to_add.append(p)
    for pkg in to_add:
        entry = _detect_or_prompt_account_username(_entry_for_package(pkg, current_entries), draft)
        current_entries.append(entry)
        current_pkgs.add(pkg)
    if to_add:
        draft["roblox_packages"] = current_entries
        active = enabled_package_entries(draft)
        draft["roblox_package"] = active[0]["package"]
        draft["selected_package_mode"] = "multiple" if len(active) > 1 else "single"
        draft = save_config(draft)
        print(f"Added {len(to_add)} package(s).")
    return draft


def _package_menu_list(draft: dict[str, Any]) -> None:
    """Display all configured packages (tests / internal; not a submenu action)."""
    print()
    print("List Packages")
    entries = validate_package_entries(
        draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
    )
    if not entries:
        print("  No Packages Configured.")
    else:
        print(f"  {'#':<3} {'Username':<20} Package")
        print(f"  {'-'*3} {'-'*20} {'-'*40}")
        for idx, entry in enumerate(entries, start=1):
            username = _package_username_display(entry)
            status = "" if entry.get("enabled", True) else " [Disabled]"
            print(f"  {idx:<3} {username:<20} {entry['package']}{status}")
    input("\nPress Enter to continue...")


def _config_menu_launch_link(draft: dict[str, Any]) -> dict[str, Any]:
    """Roblox Launch Link submenu — optional, blank input skips safely."""
    if not _is_interactive():
        return draft
    while True:
        print()
        print("--------------------------------")
        print("Roblox Launch Link")
        print("--------------------------------")
        print("Roblox Launch Link Is Optional.")
        current_mode = draft.get("launch_mode", "app")
        current_url = draft.get("launch_url", "") or ""
        if current_url:
            print(f"Current: {_safe_url_label(current_url)}  (Mode: {_launch_mode_label(current_mode)})")
        else:
            print("Current: No Roblox Launch Link Set. The Tool Will Launch The App Normally.")
        print()
        print("1. Set Roblox Launch Link")
        print("2. Clear Roblox Launch Link")
        print("3. Show Current Roblox Launch Link")
        print("0. Back")
        print("--------------------------------")
        try:
            choice = input("Choose [0]: ").strip() or "0"
        except EOFError:
            break
        if choice == "0":
            break
        elif choice == "1":
            _setup_launch_link(draft)
            draft = save_config(draft)
            print("Launch Link Saved.")
        elif choice == "2":
            draft["launch_mode"] = "app"
            draft["launch_url"] = ""
            draft = save_config(draft)
            print("Roblox Launch Link Cleared.")
        elif choice == "3":
            url = draft.get("launch_url") or ""
            if url:
                print(f"  Roblox Launch Link: {_safe_url_label(url)}")
                print(f"  Mode: {_launch_mode_label(draft.get('launch_mode', 'app'))}")
            else:
                print("  No Roblox Launch Link Set. The Tool Will Launch The App Normally.")
            input("Press Enter to continue...")
        else:
            print("Please choose 1-3 or 0.")
    return draft


def _config_menu_webhook(draft: dict[str, Any]) -> dict[str, Any]:
    """Webhook submenu: URL / Interval / Mode / Snapshot / Test Webhook."""
    if not _is_interactive():
        return draft
    while True:
        print()
        print("--------------------------------")
        print("Webhook")
        print("--------------------------------")
        url = draft.get("webhook_url", "") or ""
        masked_url = webhook.mask_webhook_url(url) if url else "Not Set"
        interval = draft.get("webhook_interval_seconds", 300)
        mode = draft.get("webhook_mode", "new_message")
        mode_label = "Edit Message" if mode == "edit_message" else "New Message"
        snap = draft.get("webhook_snapshot_enabled", False)
        snap_enabled = bool(url) and snap
        print("Current Webhook:")
        print(f"  URL: {masked_url}")
        print(f"  Interval: {interval} Seconds")
        print(f"  Mode: {mode_label}")
        print(f"  Snapshot: {'Enabled' if snap_enabled else 'Disabled'}")
        print()
        print("1. Webhook URL")
        print("2. Webhook Interval")
        print("3. Webhook Mode")
        print("4. Snapshot")
        print("5. Test Webhook")
        print("0. Back")
        print("--------------------------------")
        try:
            choice = input("Choose [0]: ").strip() or "0"
        except EOFError:
            break
        if choice == "0":
            break
        elif choice == "1":
            _config_webhook_url(draft)
            draft = save_config(draft)
        elif choice == "2":
            _setup_webhook_interval(draft)
            draft = save_config(draft)
            print("Webhook Interval Saved.")
        elif choice == "3":
            _config_webhook_mode(draft)
            draft = save_config(draft)
            print("Webhook Mode Saved.")
        elif choice == "4":
            if draft.get("webhook_url"):
                _setup_snapshot(draft)
                draft = save_config(draft)
                print("Snapshot Setting Saved.")
            else:
                print("Set Webhook URL First.")
                input("Press Enter to continue...")
        elif choice == "5":
            _test_webhook(draft)
        else:
            print("Please choose 1-5 or 0.")
    return draft


def _config_webhook_url(draft: dict[str, Any]) -> None:
    """Set or update the webhook URL. The full URL is never printed."""
    print()
    print("Webhook URL")
    print("Leave blank to skip.")
    current = draft.get("webhook_url", "") or ""
    if current:
        print(f"Current: {webhook.mask_webhook_url(current)}")
    else:
        print("Current: Not Set")
    value = _prompt("Discord Webhook URL (blank to skip)", "").strip()
    if not value:
        print("Skipped.")
        return
    try:
        draft["webhook_url"] = webhook.validate_webhook_url(value)
        draft["webhook_enabled"] = True
        print("Webhook URL Saved.")
    except ValueError as exc:
        print(f"Webhook URL Is Not Valid: {exc}")


def _config_webhook_mode(draft: dict[str, Any]) -> None:
    """Set the webhook operating mode."""
    print()
    print("Webhook Mode")
    print("1. Off")
    print("2. Status Monitor (New Messages)")
    print("3. Alert Only (New Messages)")
    print("4. Status + Alerts (Edit Message)")
    current_enabled = draft.get("webhook_enabled", False)
    current_mode = draft.get("webhook_mode", "new_message")
    default = "1" if not current_enabled else ("4" if current_mode == "edit_message" else "2")
    choice = input(f"Choose [{default}]: ").strip() or default
    if choice == "1":
        draft["webhook_enabled"] = False
        draft["webhook_snapshot_enabled"] = False
        draft["webhook_send_snapshot"] = False
    elif choice in {"2", "3"}:
        draft["webhook_enabled"] = True
        draft["webhook_mode"] = "new_message"
    elif choice == "4":
        draft["webhook_enabled"] = True
        draft["webhook_mode"] = "edit_message"
    else:
        print("Unknown choice. Keeping current mode.")


def _test_webhook(draft: dict[str, Any]) -> None:
    """Send a test message to the configured webhook. URL is masked in all output."""
    url = draft.get("webhook_url", "") or ""
    if not url:
        print("No Webhook URL Is Set. Set One First.")
        input("Press Enter to continue...")
        return
    import json as _json
    import urllib.request as _urllib_req
    masked = webhook.mask_webhook_url(url)
    print(f"Sending Test Webhook To {masked}...")
    try:
        payload = _json.dumps({"content": "DENG Tool: Rejoin — Test Webhook"}).encode("utf-8")
        req = _urllib_req.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _urllib_req.urlopen(req, timeout=10) as resp:  # noqa: S310
            code = resp.getcode()
        if code in (200, 204):
            print("Test Webhook Sent Successfully.")
        else:
            print(f"Webhook Returned Status {code}.")
    except Exception as exc:  # noqa: BLE001
        print(f"Test Webhook Failed: {exc}")
    input("Press Enter to continue...")


def _config_menu_yescaptcha(draft: dict[str, Any]) -> dict[str, Any]:
    """YesCaptcha submenu: Set / Clear API key, Check Balance / Points."""
    if not _is_interactive():
        return draft
    while True:
        print()
        print("--------------------------------")
        print("YesCaptcha")
        print("--------------------------------")
        current_key = draft.get("yescaptcha_key", "") or ""
        if current_key:
            masked = current_key[:4] + "..." if len(current_key) > 4 else "****"
            print(f"API Key: Configured ({masked})")
        else:
            print("YesCaptcha API Key Not Set.")
        print()
        print("1. Set YesCaptcha API Key")
        print("2. Clear YesCaptcha API Key")
        print("3. Check Balance / Points")
        print("0. Back")
        print("--------------------------------")
        try:
            choice = input("Choose [0]: ").strip() or "0"
        except EOFError:
            break
        if choice == "0":
            break
        elif choice == "1":
            _config_yescaptcha_set(draft)
            draft = save_config(draft)
        elif choice == "2":
            draft["yescaptcha_key"] = ""
            draft = save_config(draft)
            print("YesCaptcha API Key Cleared.")
        elif choice == "3":
            _config_yescaptcha_balance(draft)
        else:
            print("Please choose 1-3 or 0.")
    return draft


def _config_yescaptcha_set(draft: dict[str, Any]) -> None:
    """Prompt for YesCaptcha API key. The key is never printed in full."""
    print()
    print("Set YesCaptcha API Key")
    print("Leave blank to skip.")
    raw = _prompt("YesCaptcha API Key (blank to skip)", "").strip()
    if not raw:
        print("Skipped.")
        return
    draft["yescaptcha_key"] = raw[:256]
    print("YesCaptcha API Key Saved.")


def _config_yescaptcha_balance(draft: dict[str, Any]) -> None:
    """Check YesCaptcha account balance / points. The API key is not exposed in output."""
    key = draft.get("yescaptcha_key", "") or ""
    if not key:
        print("YesCaptcha API Key Not Set.")
        input("Press Enter to continue...")
        return
    print("Checking Balance...")
    try:
        from . import captcha as _captcha
        balance = _captcha.get_balance(key)
        print(f"Balance / Points: {balance}")
    except Exception as exc:  # noqa: BLE001
        print(f"Balance Check Failed: {exc}")
    input("Press Enter to continue...")


# ─── Setup / Config wizards ───────────────────────────────────────────────────

def _run_guided_config_menu(config_data: dict[str, Any], args: argparse.Namespace, *, title: str) -> tuple[dict[str, Any] | None, bool]:
    """Backward-compatible entrypoint for older setup callers."""
    return _run_edit_config_menu(config_data, args)


def _run_first_time_setup_wizard(config_data: dict[str, Any], args: argparse.Namespace, *, start_after_save: bool = False) -> tuple[dict[str, Any] | None, bool]:
    draft = _refresh_detected_fields(dict(config_data))
    if not _is_interactive():
        print_banner(use_color=not args.no_color)
        print("First Time Setup Config")
        print()
        print("This will prepare your device for DENG Tool: Rejoin.")
        print("You will set Roblox packages (scan or manual), username, optional private URL,")
        print("optional webhook, then save. Package detection scans installed apps; manual entry is fallback.")
        print("Usernames are display-only in the Start table — Unknown is OK.")
        print()
        print("Run this command in interactive Termux to complete setup.")
        print()
        _print_config_summary(draft)
        return draft, False

    print_banner(use_color=not args.no_color)
    print("First Time Setup Config")
    print()
    print("This will prepare your device for DENG Tool: Rejoin.")
    print()
    print("You will set:")
    print("  1. Roblox package / clone app (pick from detection, or manual fallback)")
    print("  2. Username / account name (display only in the Start table — Unknown is OK)")
    print("  3. Private server URL (optional — not printed after saving)")
    print("  4. Discord webhook (optional, if you turn it on)")
    print("  5. Save config")
    print()
    print("Package detection:")
    print("  The tool scans installed Roblox apps against safe hints. Pick from the table.")
    print("  Manual package entry is only a fallback if nothing is found.")
    print()
    print("Step 1 of 6: Roblox Package Setup")
    packages, hints = _choose_packages_menu(
        list(draft.get("roblox_packages") or [package_entry(draft.get("roblox_package", DEFAULT_ROBLOX_PACKAGE), "", True, "not_set")]),
        list(draft.get("package_detection_hints") or DEFAULT_ROBLOX_PACKAGE_HINTS),
        draft,
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
        print("1. Package")
        print("2. Roblox Launch Link")
        print("3. Webhook")
        print("4. YesCaptcha")
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
        print("1. Package")
        print("2. Roblox Launch Link")
        print("3. Webhook")
        print("4. YesCaptcha")
        print("0. Back")
        print("--------------------------------")
        try:
            choice = input("Choose [0]: ").strip() or "0"
        except EOFError:
            print("\nNo interactive input was available. Run this command in Termux to edit settings.")
            print("\nCurrent settings:")
            _print_config_summary(draft)
            return draft, False
        if choice == "0":
            return draft, True
        if choice == "1":
            draft = _config_menu_package(draft)
        elif choice == "2":
            draft = _config_menu_launch_link(draft)
        elif choice == "3":
            draft = _config_menu_webhook(draft)
        elif choice == "4":
            draft = _config_menu_yescaptcha(draft)
        else:
            print("Please choose 1-4 or 0.")
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
        "Started": _ANSI_GREEN,
        "Online": _ANSI_GREEN,
        "Ready": _ANSI_YELLOW,
        "Starting": _ANSI_YELLOW,
        "Launching": _ANSI_YELLOW,
        "Preparing": _ANSI_CYAN,
        "Optimizing": _ANSI_CYAN,
        "Reconnecting": _ANSI_CYAN,
        "Cleared": _ANSI_GREEN,
        "Low Applied": _ANSI_GREEN,
        "Skipped": _ANSI_YELLOW,
        "Partial": _ANSI_YELLOW,
        "Failed": _ANSI_RED,
        "Offline": _ANSI_RED,
        "Background": _ANSI_YELLOW,
        "Warning": _ANSI_YELLOW,
        "Unknown": _ANSI_DIM,
        "Heartbeat OK": _ANSI_GREEN,
        "Launch command sent": _ANSI_GREEN,
    }.get(status, "")
    return f"{color}{status}{_ANSI_RESET}" if color else status


def build_start_table(rows: list[tuple], *, use_color: bool = False) -> str:
    """Build the public start summary table: #, Package, Username, State only."""
    headers = ("#", "Package", "Username", "State")
    str_rows = [(str(r[0]), str(r[1]), str(r[2]), str(r[3])) for r in rows]

    widths = [
        max(len(headers[i]), max((_visible_len(r[i]) for r in str_rows), default=0))
        for i in range(4)
    ]

    colored_rows = [
        (
            r[0],
            r[1],
            r[2],
            _colorize_status(r[3], use_color=use_color),
        )
        for r in str_rows
    ]

    def _cell(s: str, w: int, raw: str | None = None) -> str:
        pad = w - _visible_len(raw if raw is not None else s)
        return f" {s}{' ' * max(0, pad)} "

    def _hline(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (w + 2) for w in widths) + right

    def _header_row(cells: tuple[str, ...]) -> str:
        return "│" + "│".join(_cell(str(cells[i]), widths[i]) for i in range(len(widths))) + "│"

    def _data_row(colored: tuple[str, ...], raw: tuple[str, ...]) -> str:
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


_FINAL_SUMMARY_ORDER: tuple[tuple[str, str], ...] = (
    ("online", "online."),
    ("reconnecting", "reconnecting."),
    ("launching", "launching."),
    ("preparing", "preparing."),
    ("optimizing", "optimizing."),
    ("in background", "in background."),
    ("warning", "with warnings."),
    ("failed", "failed."),
    ("offline", "offline."),
    ("unknown", "unknown."),
)

_STATE_TO_SUMMARY: dict[str, str] = {
    "Online": "online",
    "Reconnecting": "reconnecting",
    "Launching": "launching",
    "Failed": "failed",
    "Offline": "offline",
    "Warning": "warning",
    "Background": "in background",
    "Unknown": "unknown",
    "Preparing": "preparing",
    "Optimizing": "optimizing",
}


def _final_summary_line(count: int, tail: str) -> str:
    word = "package" if count == 1 else "packages"
    return f"{count} {word} {tail}"


def build_final_summary(entries: list[Any], results: dict[str, str]) -> str:
    """Short multi-line summary tallying packages by public state."""
    tallies: dict[str, int] = {}
    for e in entries:
        pkg = e["package"] if isinstance(e, dict) else str(e)
        raw = str(results.get(pkg, "Unknown"))
        bucket = _STATE_TO_SUMMARY.get(raw, "unknown")
        tallies[bucket] = tallies.get(bucket, 0) + 1
    lines = ["Final:"]
    any_line = False
    for key, tail in _FINAL_SUMMARY_ORDER:
        n = tallies.get(key, 0)
        if n:
            lines.append(_final_summary_line(n, tail))
            any_line = True
    if not any_line:
        lines.append("0 packages started.")
    return "\n".join(lines)


def build_start_verbose_details(rows: list[dict[str, str]], *, use_color: bool = False) -> str:
    """Per-package cache / graphics / launch detail for ``--verbose`` / ``--debug`` / log DEBUG only."""
    if not rows:
        return ""
    lines = ["Details (verbose / debug):"]
    for row in rows:
        pkg = row.get("package", "")
        lines.append(
            f"  {pkg}: cache={row.get('cache', '')}; graphics={row.get('graphics', '')}; "
            f"launch={row.get('launch_detail', '')}"
        )
    block = "\n".join(lines)
    if use_color:
        return f"{_ANSI_DIM}{block}{_ANSI_RESET}"
    return block


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

    # Step 2 — safe cache dirs for each selected package (mandatory when Start calls prep)
    if verbose:
        print(f"  {C}⏳ Clearing safe cache (cache, code_cache, files/tmp)...{RST}")
    any_cleared = False
    for entry in entries:
        pkg = entry["package"]
        username = _account_username_for_table(entry)
        label = android.clear_safe_package_cache(pkg)
        if verbose:
            print(f"  {G if label == 'Cleared' else Y}•{RST} {pkg}: {label} ({username})")
        if label == "Cleared":
            any_cleared = True
    if verbose:
        if not any_cleared:
            print(f"  {Y}ℹ{RST}  Cache cleanup: mostly skipped (root/offline) or no cache dirs.")
        print(f"  {G}✓{RST} Preparation complete.")
        print()


def cmd_start(args: argparse.Namespace) -> int:
    use_color = not args.no_color
    print_banner(use_color=use_color)
    try:
        cfg = load_config()
        cfg = _ensure_install_id_saved(cfg)

        # ── License (required for public remote installs before any start work) ──
        if not keystore.DEV_MODE:
            license_cfg = cfg.get("license") or {}
            if not license_cfg.get("disabled_by_user") and license_cfg.get("enabled", True):
                if str(license_cfg.get("mode") or "remote").strip().lower() == "local":
                    _key = (license_cfg.get("key") or "").strip() or (cfg.get("license_key") or "").strip()
                    if _key:
                        ok, msg = keystore.verify_key(_key)
                        if not ok:
                            _print_license_err(f"License key error: {msg}", use_color)
                            print_beginner_license_gate_help()
                            return 1
                    else:
                        _print_license_err("No License Key Found", use_color)
                        print_beginner_license_gate_help()
                        return 1
                else:
                    if not verify_remote_license_noninteractive(cfg, use_color=use_color):
                        return 1

        entries = enabled_package_entries(cfg)
        if not cfg.get("first_setup_completed"):
            print("First-time setup is required before starting.")
            if _is_interactive():
                _run_first_time_setup_wizard(cfg, args, start_after_save=True)
                return 0
            print("Run: deng-rejoin and choose First Time Setup Config.")
            return 2

        if not entries:
            print("No Roblox Package Selected")
            print()
            print("Run Setup / Edit Config, then choose Roblox Package Setup.")
            return 2

        hints2, inc_launch, det_en = _package_detection_options(cfg)
        detected_n = len(
            android.discover_roblox_package_candidates(
                hints2,
                include_launchable_only=inc_launch,
                detection_enabled=det_en,
            )
        )
        if detected_n == 0:
            print("No Roblox Package Detected")
            print()
            print("Try:")
            print("  1. Install Roblox or your Roblox clone APK.")
            print("  2. Open Roblox once manually.")
            print("  3. Return to Termux.")
            print("  4. Run package detection again.")
            print("  5. Use manual package entry if needed.")
            print()

        if cfg.get("root_mode_enabled"):
            root_info = android.detect_root()
            if not root_info.available:
                print("Root Access Not Available")
                print()
                print("Try:")
                print("  su -c id")
                print()
                print("If this fails, your cloud phone or root environment may not have root enabled.")
                print()

        n = len(entries)
        G = _ANSI_GREEN if use_color else ""
        Y = _ANSI_YELLOW if use_color else ""
        RST = _ANSI_RESET if use_color else ""

        packages_sl = [e["package"] for e in entries]
        android.force_stop_packages_except(packages_sl, cfg.get("package_detection_hints"))
        prep_cache: dict[str, str] = {}
        prep_gfx: dict[str, str] = {}
        opt = cfg.get("optimization") if isinstance(cfg.get("optimization"), dict) else {}
        for entry in entries:
            pkg = entry["package"]
            prep_cache[pkg] = android.clear_safe_package_cache(pkg)
            low = bool(opt.get("low_graphics_enabled", True)) and bool(entry.get("low_graphics_enabled", True))
            prep_gfx[pkg] = android.apply_low_graphics_optimization(pkg, enabled=low)

        cfg, _layout_note = _prepare_automatic_layout(cfg, entries)

        now_iso = datetime.now(timezone.utc).isoformat()
        start_times: dict[str, str] = dict(cfg.get("package_start_times") or {})
        for entry in entries:
            start_times[entry["package"]] = now_iso
        cfg["package_start_times"] = start_times

        launch_ok: dict[str, bool] = {}
        launch_err: dict[str, str] = {}
        for index, entry in enumerate(entries, start=1):
            package = entry["package"]
            package_cfg = dict(cfg)
            package_cfg["roblox_package"] = package
            result = perform_rejoin(package_cfg, reason="start", package_entry=entry)
            launch_ok[package] = result.success
            launch_err[package] = result.error or ""

        sup = cfg.get("supervisor") if isinstance(cfg.get("supervisor"), dict) else {}
        grace_wait = int(sup.get("launch_grace_seconds", 15))
        import time as _time

        _time.sleep(max(5, grace_wait))

        initial_status: dict[str, str] = {}
        table_rows: list[tuple] = []
        detail_rows: list[dict[str, str]] = []
        for index, entry in enumerate(entries, start=1):
            pkg = entry["package"]
            username = _account_username_for_table(entry)
            cstat = prep_cache.get(pkg, "Skipped")
            gstat = prep_gfx.get(pkg, "Skipped")
            if not launch_ok[pkg]:
                err = launch_err[pkg]
                if "not installed" in err.lower():
                    state = "Failed"
                    stat_internal = "not installed"
                else:
                    state = "Failed"
                    safe_err = mask_urls_in_text(err) or "Launch failed"
                    stat_internal = (safe_err[:120] + "...") if len(safe_err) > 123 else safe_err
            elif android.is_process_running(pkg):
                state = "Online"
                stat_internal = "process running"
            else:
                state = "Launching"
                stat_internal = "launch command sent"
            initial_status[pkg] = state
            table_rows.append((index, pkg, username, state))
            detail_rows.append(
                {"package": pkg, "cache": cstat, "graphics": gstat, "launch_detail": stat_internal}
            )

        any_url = any(bool(effective_private_server_url(e, cfg)) for e in entries) or bool(
            (str(cfg.get("private_server_url") or "") + str(cfg.get("launch_url") or "")).strip()
        )
        mode_disp = "Private URL / Auto" if any_url else _launch_mode_label(str(cfg.get("launch_mode", "app")))

        print(f"{PRODUCT_NAME} v{VERSION}")
        print()
        print("Start Summary")
        print(f"Packages selected: {n}")
        print(f"Detected packages: {detected_n}")
        print(f"Launch mode: {mode_disp}")
        print(f"Supervisor: {'Enabled' if sup.get('enabled', True) else 'Disabled'}")
        print()
        print(build_start_table(table_rows, use_color=use_color))
        show_detail = (
            bool(getattr(args, "verbose", False))
            or bool(getattr(args, "debug", False))
            or str(cfg.get("log_level", "")).upper() == "DEBUG"
        )
        if show_detail:
            print()
            print(build_start_verbose_details(detail_rows, use_color=use_color))
        print()
        print(build_final_summary(entries, {entry["package"]: table_rows[i][3] for i, entry in enumerate(entries)}))
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
            print()
            print("Launch Failed")
            print()
            print("The package was selected, but Android did not launch it.")
            print()
            print("Try:")
            print("  1. Open Roblox manually once.")
            print("  2. Check the selected package in Setup / Edit Config.")
            print("  3. Run status (deng-rejoin-status).")
            print("  4. Send support the package name and Start table screenshot.")
            print()
            print(f"Detail: {best_reason}")
            return 1

        print(f"{G}Session active — monitoring {n} package(s). Press Ctrl+C to stop.{RST}")
        MultiPackageSupervisor(entries, cfg, initial_status=initial_status).run_forever()
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


def cmd_license(args: argparse.Namespace) -> int:
    use_color = not args.no_color
    print_banner(use_color=use_color)
    if keystore.DEV_MODE:
        _print_dev_license_skipped(use_color)
        return 0
    try:
        cfg = load_config()
    except ConfigError as exc:
        _print_license_err(str(exc), use_color)
        return 1
    lic = cfg.setdefault("license", {})
    if lic.get("disabled_by_user") or not lic.get("enabled", True):
        print("License checks are turned off in your config.")
        return 0
    cfg = _ensure_install_id_saved(cfg)
    mode = str(lic.get("mode") or "remote").strip().lower()
    if mode == "local":
        ok = _ensure_local_license_menu_loop(cfg, args, use_color)
    else:
        ok = _ensure_remote_license_menu_loop(cfg, args, use_color)
    return 0 if ok else 1


def cmd_new_user_help(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    print()
    print(NEW_USER_HELP_TEXT)
    return 0


def cmd_menu(args: argparse.Namespace) -> int:
    """Open the main menu, gated by a license check on first run."""
    ensure_app_dirs()
    use_color = not args.no_color

    # Dev mode: skip license gate entirely
    if keystore.DEV_MODE:
        _print_dev_license_skipped(use_color)
        return run_menu(args, _handlers())

    # Load config (use defaults if not yet created)
    try:
        cfg = load_config()
    except ConfigError:
        cfg = default_config()

    lic = cfg.setdefault("license", {})

    # Skip gate when license checking is disabled in config
    if lic.get("disabled_by_user") or not lic.get("enabled", True):
        return run_menu(args, _handlers())

    cfg = _ensure_install_id_saved(cfg)
    mode = str(lic.get("mode") or "remote").strip().lower()
    if mode == "local":
        ok = _ensure_local_license_menu_loop(cfg, args, use_color)
    else:
        ok = _ensure_remote_license_menu_loop(cfg, args, use_color)

    if not ok:
        return 1

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
    parser.add_argument("--license", action="store_true", help="enter or update your license key")
    parser.add_argument("--new-user-help", dest="new_user_help", action="store_true", help="print the built-in tutorial for beginners")
    parser.add_argument("--enable-boot", action="store_true")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--verbose", action="store_true", help="show extra Start diagnostics (cache/graphics/launch)")
    parser.add_argument("--debug", action="store_true", help="same as --verbose for Start diagnostics")
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
        "license": ns.license,
        "new-user-help": ns.new_user_help,
        "enable-boot": ns.enable_boot,
        "update": ns.update,
    }
    selected = [command for command, enabled in flag_to_command.items() if enabled]
    if ns.command and selected:
        parser.error("use either positional command or --command flag, not both")
    if len(selected) > 1:
        parser.error("choose only one command")
    ns.resolved_command = ns.command or (selected[0] if selected else "menu")
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
        "license": cmd_license,
        "new-user-help": cmd_new_user_help,
        "enable-boot": cmd_enable_boot,
        "update": cmd_update,
    }


if __name__ == "__main__":
    raise SystemExit(main())
