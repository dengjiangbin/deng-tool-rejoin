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
from .config import ConfigError, default_config, ensure_app_dirs, load_config, safe_config_view, save_config, validate_config
from .constants import (
    CONFIG_PATH,
    DB_PATH,
    DEFAULT_ROBLOX_PACKAGE,
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
from .launcher import perform_rejoin
from .launcher_file import create_market_launchers
from .lockfile import LockManager, stop_running_agent
from .menu import run_menu
from .platform_detect import detect_public_download_dir, get_android_release, get_android_sdk, get_platform_info
from .supervisor import Supervisor
from .url_utils import UrlValidationError, detect_launch_mode_from_url, mask_urls_in_text, validate_launch_url

COMMANDS = {
    "setup",
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


def _choose_package() -> str:
    packages = android.find_roblox_packages()
    if DEFAULT_ROBLOX_PACKAGE in packages:
        return DEFAULT_ROBLOX_PACKAGE
    if not _is_interactive():
        return packages[0] if packages else DEFAULT_ROBLOX_PACKAGE
    if packages:
        print("Detected Roblox-like packages:")
        for idx, package in enumerate(packages, start=1):
            print(f"  {idx}. {package}")
        choice = _prompt("Choose package number or enter package name", "1")
        if choice.isdigit() and 1 <= int(choice) <= len(packages):
            return packages[int(choice) - 1]
        return choice
    return _prompt("Roblox package was not auto-detected. Enter package name", DEFAULT_ROBLOX_PACKAGE)


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


def cmd_setup(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    ensure_app_dirs()
    db.init_db(DB_PATH)

    print("Preparing local Termux agent directories...")
    print(f"App directory: {CONFIG_PATH.parent}")
    print(f"Android release: {get_android_release()}")
    print(f"Android SDK: {get_android_sdk()}")
    print(f"Termux detected: {str(android.is_termux()).lower()}")
    print(f"Storage permission: {str(android.has_storage_permission()).lower()}")
    print(f"Download directory: {detect_public_download_dir() or 'not detected'}")

    root_info = android.detect_root()
    print(f"Root availability: {'available via ' + str(root_info.tool) if root_info.available else root_info.detail}")
    termux_boot_detected = android.package_installed("com.termux.boot")
    print(f"Termux:Boot detected: {str(termux_boot_detected).lower()}")

    cfg = default_config()
    cfg["roblox_package"] = _choose_package()
    cfg["root_available"] = root_info.available
    cfg["android_release"] = get_android_release()
    cfg["android_sdk"] = get_android_sdk()
    cfg["download_dir"] = detect_public_download_dir()

    launch_mode, launch_url = _choose_launch_settings()
    cfg["launch_mode"] = launch_mode
    cfg["launch_url"] = launch_url

    if _is_interactive():
        cfg["reconnect_delay_seconds"] = _prompt_int("Reconnect delay seconds", cfg["reconnect_delay_seconds"], 5)
        cfg["health_check_interval_seconds"] = _prompt_int("Health check interval seconds", cfg["health_check_interval_seconds"], 10)
        cfg["auto_rejoin_enabled"] = _prompt_yes_no("Enable auto rejoin supervisor", False)
        if root_info.available:
            cfg["root_mode_enabled"] = _prompt_yes_no("Enable root mode for safe force-stop before launch", True)
        if cfg["auto_rejoin_enabled"]:
            cfg["termux_boot_enabled"] = _prompt_yes_no("Prepare Termux:Boot startup script", termux_boot_detected)
    else:
        print("Non-interactive setup detected; using safe defaults.")

    try:
        saved = save_config(cfg)
    except ConfigError as exc:
        print(f"Setup failed: {exc}")
        return 2

    if saved.get("termux_boot_enabled"):
        try:
            _write_termux_boot_script()
            print(f"Termux:Boot script created: {TERMUX_BOOT_SCRIPT}")
            if not termux_boot_detected:
                print("Termux:Boot app was not detected. Install Termux:Boot, then Android will run this script after boot.")
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
        print(f"{label}: none")
        return
    safe = {key: mask_urls_in_text(str(value)) if value is not None else None for key, value in row.items()}
    print(f"{label}:")
    _print_json(safe)


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
    print(f"Agent running: {str(running).lower()}")
    print(f"Roblox package: {cfg['roblox_package']}")
    print(f"Auto rejoin: {'enabled' if cfg['auto_rejoin_enabled'] else 'disabled'}")
    print(f"Root available: {str(root_info.available).lower()} ({root_info.tool or 'no tool'})")
    print(f"Android release: {platform_info.android_release}")
    print(f"Android SDK: {platform_info.android_sdk}")
    print(f"Download directory: {platform_info.download_dir or 'not detected'}")
    print("\nConfig:")
    _print_json(safe)

    _print_latest("Latest heartbeat", db.latest_row("heartbeats"))
    _print_latest("Latest rejoin attempt", db.latest_row("rejoin_attempts"))
    latest_event = db.latest_row("events")
    if latest_event and latest_event.get("level") == "ERROR":
        _print_latest("Latest error", latest_event)
    else:
        print("Latest error: none")
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
    print_banner(use_color=not args.no_color)
    cfg = load_config()
    print("Current safe config:")
    _print_json(safe_config_view(cfg))
    if not _is_interactive():
        return 0

    if not _prompt_yes_no("Edit config", False):
        return 0

    editable = dict(cfg)
    editable["roblox_package"] = _prompt("Roblox package", editable["roblox_package"])
    launch_mode, launch_url = _choose_launch_settings()
    editable["launch_mode"] = launch_mode
    editable["launch_url"] = launch_url
    editable["reconnect_delay_seconds"] = _prompt_int("Reconnect delay seconds", editable["reconnect_delay_seconds"], 5)
    editable["health_check_interval_seconds"] = _prompt_int("Health check interval seconds", editable["health_check_interval_seconds"], 10)
    editable["foreground_grace_seconds"] = _prompt_int("Foreground grace seconds", editable["foreground_grace_seconds"], 10)
    editable["auto_rejoin_enabled"] = _prompt_yes_no("Enable auto rejoin", editable["auto_rejoin_enabled"])
    root_info = android.detect_root()
    editable["root_available"] = root_info.available
    editable["root_mode_enabled"] = _prompt_yes_no("Enable root mode", editable["root_mode_enabled"] and root_info.available)
    try:
        saved = save_config(editable)
    except ConfigError as exc:
        print(f"Config not saved: {exc}")
        return 2
    print("Config saved:")
    _print_json(safe_config_view(saved))
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
    print(f"{PRODUCT_NAME} v{VERSION}")
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
