"""Doctor diagnostics for Termux, Android, root, config, DB, and locks."""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import android, db
from .config import ConfigError, enabled_package_entries, ensure_app_dirs, load_config
from .constants import APP_HOME, CONFIG_PATH, DB_PATH, LOG_DIR, LOG_PATH
from .launcher_file import LAUNCHER_FILENAME
from .lockfile import LockManager, read_pid
from .platform_detect import detect_public_download_dir, fallback_launcher_path, get_platform_info
from . import webhook, window_layout


@dataclass(frozen=True)
class DoctorItem:
    status: str
    name: str
    detail: str
    suggestion: str = ""


def _item(status: str, name: str, detail: str, suggestion: str = "") -> DoctorItem:
    return DoctorItem(status, name, detail, suggestion)


def _signal4_suggestion(result: android.CommandResult) -> str:
    text = f"{result.returncode} {result.stdout} {result.stderr}".lower()
    if result.returncode in {132, -4} or "illegal instruction" in text or "signal 4" in text:
        return "Termux or the Android image may be CPU/ABI incompatible. Try a current Termux build from F-Droid/GitHub or a compatible Android image."
    return ""


def run_doctor(config_data: dict[str, Any] | None = None) -> list[DoctorItem]:
    items: list[DoctorItem] = []

    py_ok = sys.version_info >= (3, 10)
    items.append(
        _item(
            "PASS" if py_ok else "FAIL",
            "Python",
            f"{platform.python_version()}",
            "Install Python 3.10+ in Termux with pkg install python." if not py_ok else "",
        )
    )

    termux = android.is_termux()
    platform_info = get_platform_info()
    items.append(
        _item(
            "PASS" if termux else "WARN",
            "Termux environment",
            "Termux detected" if termux else "not running inside a normal Termux environment",
            "Run from Termux on Android for full functionality." if not termux else "",
        )
    )

    ensure_app_dirs()
    items.append(
        _item(
            "PASS" if platform_info.android_release != "unknown" else "WARN",
            "Android release",
            platform_info.android_release,
            "Run on Android/Termux for Android version detection." if platform_info.android_release == "unknown" else "",
        )
    )
    items.append(
        _item(
            "PASS" if platform_info.android_sdk != "unknown" else "WARN",
            "Android SDK",
            platform_info.android_sdk,
            "Run on Android/Termux for SDK detection." if platform_info.android_sdk == "unknown" else "",
        )
    )
    items.append(_item("PASS", "Termux prefix", platform_info.termux_prefix or "not set", ""))
    items.append(_item("PASS", "Home directory", platform_info.home, ""))
    items.append(_item("PASS" if APP_HOME.exists() else "FAIL", "App directory", str(APP_HOME), "Run setup again."))

    download_dir = detect_public_download_dir()
    items.append(
        _item(
            "PASS" if download_dir else "WARN",
            "Public Download path",
            download_dir or "no accessible /sdcard Download folder detected",
            "Run termux-setup-storage. DENG will use the Termux-home launcher fallback if public storage is unavailable.",
        )
    )
    launcher_path = str((Path(download_dir) / LAUNCHER_FILENAME) if download_dir else fallback_launcher_path())
    items.append(_item("PASS", "Launcher path", launcher_path, ""))
    items.append(
        _item(
            "PASS" if android.has_storage_permission() else "WARN",
            "Storage permission",
            "shared storage is readable" if android.has_storage_permission() else "shared storage was not detected",
            "Run termux-setup-storage if file access is needed.",
        )
    )

    shell_ok = android.has_android_shell_access()
    items.append(
        _item(
            "PASS" if shell_ok else "FAIL",
            "Android shell access",
            "getprop works" if shell_ok else "getprop is unavailable",
            "Use a real Android/Termux environment.",
        )
    )

    pkg_result = android.package_manager_result()
    signal4 = _signal4_suggestion(pkg_result)
    items.append(
        _item(
            "PASS" if pkg_result.ok else "FAIL",
            "Package manager",
            "cmd/pm package listing works" if pkg_result.ok else (pkg_result.summary or "package manager failed"),
            signal4 or "Check Termux installation and Android shell permissions.",
        )
    )

    root_info = android.detect_root()
    items.append(
        _item(
            "PASS" if root_info.available else "WARN",
            "Root availability",
            f"available via {root_info.tool}" if root_info.available else root_info.detail,
            "Root is optional. Non-root mode can launch Roblox but cannot reliably force-stop it." if not root_info.available else "",
        )
    )

    try:
        cfg = config_data or load_config()
        items.append(_item("PASS", "Config", str(CONFIG_PATH), ""))
    except ConfigError as exc:
        cfg = {}
        items.append(_item("FAIL", "Config", str(exc), "Run setup or config and fix invalid values."))

    entries = enabled_package_entries(cfg) if cfg else []
    packages = [entry["package"] for entry in entries]
    if entries:
        missing = [package for package in packages if not android.package_installed(package)]
        items.append(
            _item(
                "PASS" if not missing else "FAIL",
                "Selected Roblox packages",
                ", ".join(packages) if not missing else f"missing: {', '.join(missing)}",
                "Install Roblox/clones or set the correct package names in config.",
            )
        )
        unlabeled = [entry["package"] for entry in entries if not entry.get("label")]
        items.append(
            _item(
                "PASS" if not unlabeled else "WARN",
                "Package labels",
                "all selected packages have labels" if not unlabeled else f"label not set: {', '.join(unlabeled)}",
                "Labels are optional, but adding Main/Alt names makes the start table easier to read.",
            )
        )
    else:
        items.append(_item("FAIL", "Selected Roblox packages", "not configured", "Run first-time setup."))

    if cfg.get("webhook_enabled"):
        try:
            webhook.validate_webhook_url(cfg.get("webhook_url"))
            items.append(_item("PASS", "Discord webhook", "URL format looks valid", ""))
        except ValueError as exc:
            items.append(_item("FAIL", "Discord webhook", str(exc), "Open config and fix or disable the webhook."))
    else:
        items.append(_item("PASS", "Discord webhook", "disabled", ""))

    if cfg.get("webhook_enabled") and cfg.get("webhook_snapshot_enabled"):
        items.append(
            _item(
                "PASS" if android.command_exists("screencap") else "WARN",
                "Snapshot command",
                "screencap available" if android.command_exists("screencap") else "screencap not found",
                "Snapshots are optional. Disable snapshot or use an Android image with screencap.",
            )
        )

    size_result = android.run_command(["wm", "size"], timeout=5)
    density_result = android.run_command(["wm", "density"], timeout=5)
    items.append(
        _item(
            "PASS" if size_result.ok else "WARN",
            "Display size",
            size_result.stdout or size_result.summary or "wm size unavailable",
            "Automatic layout uses a safe fallback if wm size is unavailable.",
        )
    )
    items.append(
        _item(
            "PASS" if density_result.ok else "WARN",
            "Display density",
            density_result.stdout or density_result.summary or "wm density unavailable",
            "Automatic layout uses a safe fallback if wm density is unavailable.",
        )
    )
    if len(packages) > 1:
        root = android.detect_root()
        if not root.available:
            items.append(
                _item(
                    "WARN",
                    "App Cloner XML access",
                    "root/file access unavailable",
                    "Automatic layout can calculate positions, but App Cloner preference writes need root/file access.",
                )
            )
        else:
            readable = [str(window_layout.app_cloner_prefs_path(package)) for package in packages if window_layout.app_cloner_prefs_path(package).exists()]
            items.append(_item("PASS" if readable else "WARN", "App Cloner XML access", ", ".join(readable) if readable else "no preference XML found", "Missing XML is OK; DENG will still launch apps."))

    launch_tools = android.command_exists("am") and android.command_exists("monkey")
    items.append(
        _item(
            "PASS" if launch_tools else "WARN",
            "Launch commands",
            "am and monkey are available" if launch_tools else "am and/or monkey not found in PATH",
            "Android launch commands are required for rejoin attempts.",
        )
    )

    try:
        db.init_db(DB_PATH)
        items.append(_item("PASS", "SQLite database", str(DB_PATH), ""))
    except Exception as exc:  # noqa: BLE001 - diagnostic boundary.
        items.append(_item("FAIL", "SQLite database", str(exc), "Check storage permissions and free space."))

    log_ok = LOG_DIR.exists() and LOG_DIR.is_dir()
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.touch(exist_ok=True)
        log_ok = True
    except OSError:
        log_ok = False
    items.append(_item("PASS" if log_ok else "FAIL", "Log directory", str(LOG_DIR), "Run setup again."))

    manager = LockManager()
    pid = read_pid()
    if manager.is_running():
        items.append(_item("WARN", "Agent lock", f"agent already running with PID {pid}", "Use stop before starting another copy."))
    elif pid:
        items.append(_item("WARN", "Agent lock", f"stale or unconfirmed PID {pid}", "Run stop to clean stale state."))
    else:
        items.append(_item("PASS", "Agent lock", "no duplicate agent detected", ""))

    return items


def summarize(items: list[DoctorItem]) -> dict[str, int]:
    return {
        "PASS": sum(1 for item in items if item.status == "PASS"),
        "WARN": sum(1 for item in items if item.status == "WARN"),
        "FAIL": sum(1 for item in items if item.status == "FAIL"),
    }


def print_doctor(items: list[DoctorItem]) -> None:
    print("Doctor checks your Termux, Android, Roblox, and DENG setup.")
    print("PASS means OK. WARN means usable but needs attention. FAIL means a feature cannot work yet.")
    print()
    for item in items:
        print(f"[{item.status}] {item.name}: {item.detail}")
        if item.suggestion and item.status != "PASS":
            print(f"       Suggestion: {item.suggestion}")
    summary = summarize(items)
    print()
    print(f"Summary: PASS={summary['PASS']} WARN={summary['WARN']} FAIL={summary['FAIL']}")
