"""Config creation, persistence, and validation."""

from __future__ import annotations

import json
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db
from .platform_detect import detect_public_download_dir, get_android_release, get_android_sdk
from .constants import (
    APP_DIRS,
    CONFIG_PATH,
    DEFAULT_BACKOFF_MAX_SECONDS,
    DEFAULT_BACKOFF_MIN_SECONDS,
    DEFAULT_DEVICE_NAME,
    DEFAULT_FOREGROUND_GRACE_SECONDS,
    DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS,
    DEFAULT_MAX_FAST_FAILURES,
    DEFAULT_RECONNECT_DELAY_SECONDS,
    DEFAULT_ROBLOX_PACKAGE,
    LAUNCH_MODES,
    MAX_BACKOFF_SECONDS,
    MIN_BACKOFF_SECONDS,
    MIN_FOREGROUND_GRACE_SECONDS,
    MIN_HEALTH_CHECK_INTERVAL_SECONDS,
    MIN_RECONNECT_DELAY_SECONDS,
    PACKAGE_NAME_REGEX,
    VERSION,
)
from .url_utils import UrlValidationError, mask_launch_url, normalize_launch_url, validate_launch_url


class ConfigError(ValueError):
    """Raised when config input is invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_app_dirs() -> None:
    for directory in APP_DIRS:
        directory.mkdir(parents=True, exist_ok=True)


def default_config() -> dict[str, Any]:
    now = utc_now()
    try:
        device_name = socket.gethostname() or DEFAULT_DEVICE_NAME
    except OSError:
        device_name = DEFAULT_DEVICE_NAME
    return {
        "device_name": device_name,
        "agent_version": VERSION,
        "roblox_package": DEFAULT_ROBLOX_PACKAGE,
        "launch_mode": "app",
        "launch_url": "",
        "auto_rejoin_enabled": False,
        "reconnect_delay_seconds": DEFAULT_RECONNECT_DELAY_SECONDS,
        "health_check_interval_seconds": DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS,
        "foreground_grace_seconds": DEFAULT_FOREGROUND_GRACE_SECONDS,
        "max_fast_failures": DEFAULT_MAX_FAST_FAILURES,
        "backoff_min_seconds": DEFAULT_BACKOFF_MIN_SECONDS,
        "backoff_max_seconds": DEFAULT_BACKOFF_MAX_SECONDS,
        "root_mode_enabled": False,
        "root_available": False,
        "termux_boot_enabled": False,
        "log_level": "INFO",
        "android_release": get_android_release(),
        "android_sdk": get_android_sdk(),
        "download_dir": detect_public_download_dir(),
        "created_at": now,
        "updated_at": now,
    }


def is_valid_package_name(package_name: str) -> bool:
    if not isinstance(package_name, str):
        return False
    if len(package_name) > 255:
        return False
    return re.fullmatch(PACKAGE_NAME_REGEX, package_name.strip()) is not None


def validate_package_name(package_name: str) -> str:
    cleaned = (package_name or "").strip()
    if not is_valid_package_name(cleaned):
        raise ConfigError("Android package name is invalid")
    return cleaned


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _as_int(name: str, value: Any, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if minimum is not None and number < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise ConfigError(f"{name} must be at most {maximum}")
    return number


def validate_config(input_config: dict[str, Any], *, allow_uncertain_url: bool = True) -> dict[str, Any]:
    merged = default_config()
    merged.update(input_config or {})

    merged["agent_version"] = VERSION
    merged["android_release"] = str(merged.get("android_release") or get_android_release())
    merged["android_sdk"] = str(merged.get("android_sdk") or get_android_sdk())
    merged["download_dir"] = str(merged.get("download_dir") or detect_public_download_dir())
    merged["roblox_package"] = validate_package_name(str(merged.get("roblox_package", "")))

    launch_mode = str(merged.get("launch_mode", "app")).strip().lower()
    if launch_mode not in LAUNCH_MODES:
        raise ConfigError("launch_mode must be one of: app, deeplink, web_url")
    merged["launch_mode"] = launch_mode

    launch_url = str(merged.get("launch_url", "") or "").strip()
    if launch_mode == "app":
        launch_url = ""
    else:
        try:
            launch_url, warning = normalize_launch_url(launch_url)
            validate_launch_url(launch_url, launch_mode, allow_uncertain=allow_uncertain_url)
            if warning:
                merged["url_warning"] = warning
        except UrlValidationError as exc:
            raise ConfigError(str(exc)) from exc
    merged["launch_url"] = launch_url

    merged["auto_rejoin_enabled"] = _as_bool(merged.get("auto_rejoin_enabled"))
    merged["root_mode_enabled"] = _as_bool(merged.get("root_mode_enabled"))
    merged["root_available"] = _as_bool(merged.get("root_available"))
    merged["termux_boot_enabled"] = _as_bool(merged.get("termux_boot_enabled"))

    merged["reconnect_delay_seconds"] = _as_int(
        "reconnect_delay_seconds", merged.get("reconnect_delay_seconds"), MIN_RECONNECT_DELAY_SECONDS
    )
    merged["health_check_interval_seconds"] = _as_int(
        "health_check_interval_seconds", merged.get("health_check_interval_seconds"), MIN_HEALTH_CHECK_INTERVAL_SECONDS
    )
    merged["foreground_grace_seconds"] = _as_int(
        "foreground_grace_seconds", merged.get("foreground_grace_seconds"), MIN_FOREGROUND_GRACE_SECONDS
    )
    merged["max_fast_failures"] = _as_int("max_fast_failures", merged.get("max_fast_failures"), 1)
    merged["backoff_min_seconds"] = _as_int("backoff_min_seconds", merged.get("backoff_min_seconds"), MIN_BACKOFF_SECONDS)
    merged["backoff_max_seconds"] = _as_int("backoff_max_seconds", merged.get("backoff_max_seconds"), MIN_BACKOFF_SECONDS, MAX_BACKOFF_SECONDS)
    if merged["backoff_max_seconds"] < merged["backoff_min_seconds"]:
        raise ConfigError("backoff_max_seconds must be greater than or equal to backoff_min_seconds")

    log_level = str(merged.get("log_level", "INFO")).upper()
    if log_level not in {"DEBUG", "INFO", "WARN", "WARNING", "ERROR"}:
        raise ConfigError("log_level must be DEBUG, INFO, WARN, WARNING, or ERROR")
    merged["log_level"] = "WARNING" if log_level == "WARN" else log_level

    if not merged.get("created_at"):
        merged["created_at"] = utc_now()
    merged["updated_at"] = utc_now()
    return {key: merged[key] for key in default_config().keys()}


def load_config(config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    ensure_app_dirs()
    if not config_path.exists():
        cfg = validate_config(default_config())
        save_config(cfg, config_path=config_path)
        return cfg
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"failed to read config: {exc}") from exc
    return validate_config(loaded)


def save_config(config_data: dict[str, Any], config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    ensure_app_dirs()
    validated = validate_config(config_data)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(validated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    db.upsert_config(validated)
    return validated


def safe_config_view(config_data: dict[str, Any]) -> dict[str, Any]:
    view = dict(config_data)
    view["launch_url"] = mask_launch_url(view.get("launch_url")) or ""
    return view
