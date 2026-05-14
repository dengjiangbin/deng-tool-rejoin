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
from .webhook import mask_webhook_url, validate_webhook_interval, validate_webhook_url
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
    DEFAULT_ROBLOX_PACKAGE_HINTS,
    LAUNCH_MODES,
    LICENSE_KEY_PATTERN,
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


SELECTED_PACKAGE_MODES = {"single", "multiple"}
WEBHOOK_MODES = {"new_message", "edit_message"}
AUTO_RESIZE_MODES = {"off", "auto", "preview"}


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
        "roblox_packages": [
            {
                "package": DEFAULT_ROBLOX_PACKAGE,
                "account_username": "Main",
                "enabled": True,
                "username_source": "manual",
            }
        ],
        "package_detection_hints": list(DEFAULT_ROBLOX_PACKAGE_HINTS),
        "selected_package_mode": "single",
        "launch_mode": "app",
        "launch_url": "",
        "webhook_enabled": False,
        "webhook_url": "",
        "webhook_mode": "new_message",
        "webhook_message_id": "",
        "webhook_interval_seconds": 300,
        "webhook_snapshot_enabled": False,
        "webhook_send_snapshot": False,
        "webhook_last_sent_at": 0,
        "webhook_last_message_id": "",
        "snapshot_max_age_seconds": 300,
        "snapshot_temp_path": "",
        "auto_resize_enabled": False,
        "auto_resize_mode": "off",
        "window_gap_px": 8,
        "last_layout_preview": [],
        "first_setup_completed": False,
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
        "license_key": "",
        "yescaptcha_key": "",
        "webhook_tags": [],
        "package_start_times": {},
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


USERNAME_SOURCES = {"manual", "detected_safe_pref", "android_app_label", "not_set"}

_LICENSE_KEY_RE = re.compile(LICENSE_KEY_PATTERN, re.IGNORECASE)
_LICENSE_MASK = "***"


def validate_license_key(key: str) -> str:
    """Validate a DENG license key. Empty string is accepted (key not set)."""
    cleaned = (key or "").strip()
    if not cleaned:
        return ""
    upper = cleaned.upper()
    if not _LICENSE_KEY_RE.match(upper):
        raise ConfigError(
            "License key must be in format DENG-<hex> with at least 8 hex characters (e.g. DENG-38ab1234cd56ef78)"
        )
    return upper


def mask_license_key(key: str) -> str:
    """Return a display-safe version of the license key (DENG-38ab...78ef)."""
    cleaned = (key or "").strip()
    if not cleaned:
        return "Not set"
    parts = cleaned.split("-", 1)
    if len(parts) != 2:
        return f"{parts[0][:4]}-{_LICENSE_MASK}"
    prefix, hex_part = parts
    if len(hex_part) > 8:
        return f"{prefix}-{hex_part[:4]}...{hex_part[-4:].lower()}"
    return f"{prefix}-{_LICENSE_MASK}"


def validate_account_username(username: Any) -> str:
    cleaned = str(username or "").strip()
    if len(cleaned) > 80:
        raise ConfigError("account username must be 80 characters or fewer")
    if any(ord(char) < 32 for char in cleaned):
        raise ConfigError("account username cannot contain control characters")
    return cleaned


def validate_username_source(source: Any, username: str = "") -> str:
    cleaned = str(source or "").strip().lower()
    if cleaned not in USERNAME_SOURCES:
        cleaned = "manual" if username else "not_set"
    if cleaned != "not_set" and not username:
        return "not_set"
    return cleaned


def package_entry(
    package: str,
    account_username: str = "",
    enabled: bool = True,
    username_source: str = "manual",
) -> dict[str, Any]:
    username = validate_account_username(account_username)
    return {
        "package": validate_package_name(package),
        "account_username": username,
        "enabled": bool(enabled),
        "username_source": validate_username_source(username_source, username),
    }


def validate_package_entries(package_entries: Any) -> list[dict[str, Any]]:
    if not isinstance(package_entries, (list, tuple)):
        raise ConfigError("roblox_packages must be a list of package entries")
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_entry in package_entries:
        if isinstance(raw_entry, str):
            entry = package_entry(raw_entry, "", True)
        elif isinstance(raw_entry, dict):
            username = raw_entry.get("account_username")
            source = raw_entry.get("username_source")
            if username is None and raw_entry.get("label") is not None:
                username = raw_entry.get("label")
                source = source or "manual"
            entry = package_entry(
                str(raw_entry.get("package") or ""),
                str(username or ""),
                _as_bool(raw_entry.get("enabled", True)),
                str(source or ""),
            )
        else:
            raise ConfigError("each Roblox package entry must be a package string or object")
        if entry["package"] in seen:
            continue
        seen.add(entry["package"])
        validated.append(entry)
    if not validated:
        raise ConfigError("at least one Roblox package must be configured")
    return validated


def validate_package_names(package_names: list[str] | tuple[str, ...]) -> list[str]:
    return [entry["package"] for entry in validate_package_entries(package_names)]


def enabled_package_entries(config_data: dict[str, Any]) -> list[dict[str, Any]]:
    entries = validate_package_entries(config_data.get("roblox_packages") or [config_data.get("roblox_package", DEFAULT_ROBLOX_PACKAGE)])
    return [entry for entry in entries if entry.get("enabled", True)]


def enabled_package_names(config_data: dict[str, Any]) -> list[str]:
    return [entry["package"] for entry in enabled_package_entries(config_data)]


def package_display_name(entry: dict[str, Any], *, include_package: bool = True) -> str:
    username = validate_account_username(entry.get("account_username", ""))
    package = validate_package_name(str(entry.get("package") or ""))
    if username and include_package:
        return f"{username} ({package})"
    if username:
        return username
    return f"Username not set ({package})" if include_package else "Username not set"


def normalize_package_detection_hint(value: str) -> str:
    """Normalize a safe package-name fragment used for clone detection."""
    cleaned = str(value or "").strip().lower()
    if cleaned.startswith("package:"):
        cleaned = cleaned[len("package:") :].strip()
    cleaned = cleaned.rstrip("*").strip()
    cleaned = cleaned.strip()
    if not re.fullmatch(r"[a-z0-9_.]{2,64}", cleaned or ""):
        raise ConfigError("package detection hints may only use letters, numbers, underscores, and dots")
    if len(cleaned.replace(".", "").replace("_", "")) < 2:
        raise ConfigError("package detection hints must include at least two letters or numbers")
    return cleaned


def validate_package_detection_hints(hints: Any) -> list[str]:
    if hints is None or hints == "":
        hints = DEFAULT_ROBLOX_PACKAGE_HINTS
    if not isinstance(hints, (list, tuple)):
        raise ConfigError("package_detection_hints must be a list of safe package fragments")
    validated: list[str] = []
    for hint in hints:
        cleaned = normalize_package_detection_hint(str(hint))
        if cleaned not in validated:
            validated.append(cleaned)
    if not validated:
        validated = list(DEFAULT_ROBLOX_PACKAGE_HINTS)
    return validated


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
    input_config = input_config or {}
    source_has_packages = bool(input_config.get("roblox_packages"))
    merged = default_config()
    merged.update(input_config)

    # ── Legacy migration ────────────────────────────────────────────────────
    # Migrate old-style launcher dict with intent_type (from older versions).
    _old_launcher = merged.get("launcher")
    if isinstance(_old_launcher, dict):
        _intent_type = str(_old_launcher.get("intent_type", "")).strip().lower()
        if _intent_type == "deeplink":
            merged.setdefault("launch_mode", "deeplink")
        elif _intent_type in ("web_url", "web", "url"):
            merged.setdefault("launch_mode", "web_url")
        else:
            # "disabled", "none", or unknown → enable launch with default mode
            merged.setdefault("launch_mode", "app")

    # Migrate launch_mode "disabled" or legacy values to "app" (enable launching).
    _raw_launch_mode = str(merged.get("launch_mode", "app")).strip().lower()
    if _raw_launch_mode in ("disabled", "none", "auto"):
        merged["launch_mode"] = "app"
    # ────────────────────────────────────────────────────────────────────────

    merged["agent_version"] = VERSION
    merged["android_release"] = str(merged.get("android_release") or get_android_release())
    merged["android_sdk"] = str(merged.get("android_sdk") or get_android_sdk())
    merged["download_dir"] = str(merged.get("download_dir") or detect_public_download_dir())
    migrated_package = str(merged.get("roblox_package") or DEFAULT_ROBLOX_PACKAGE)
    if not source_has_packages:
        migrated_label = str(merged.get("roblox_package_label") or "Main")
        merged["roblox_packages"] = [package_entry(migrated_package, migrated_label, True)]
    merged["roblox_packages"] = validate_package_entries(merged["roblox_packages"])
    active_entries = [entry for entry in merged["roblox_packages"] if entry["enabled"]]
    if not active_entries:
        raise ConfigError("at least one Roblox package must be enabled")
    merged["roblox_package"] = validate_package_name(str(active_entries[0]["package"]))
    selected_package_mode = str(merged.get("selected_package_mode", "single")).strip().lower()
    if selected_package_mode not in SELECTED_PACKAGE_MODES:
        raise ConfigError("selected_package_mode must be single or multiple")
    if len(active_entries) > 1:
        selected_package_mode = "multiple"
    merged["selected_package_mode"] = selected_package_mode
    merged["package_detection_hints"] = validate_package_detection_hints(merged.get("package_detection_hints"))

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
    merged["webhook_enabled"] = _as_bool(merged.get("webhook_enabled"))
    merged["webhook_snapshot_enabled"] = _as_bool(merged.get("webhook_snapshot_enabled"))
    merged["webhook_send_snapshot"] = _as_bool(merged.get("webhook_send_snapshot") or merged.get("webhook_snapshot_enabled"))
    merged["auto_resize_enabled"] = _as_bool(merged.get("auto_resize_enabled"))
    merged["first_setup_completed"] = _as_bool(merged.get("first_setup_completed"))
    if not merged["webhook_enabled"]:
        merged["webhook_snapshot_enabled"] = False
        merged["webhook_send_snapshot"] = False

    webhook_mode = str(merged.get("webhook_mode", "new_message")).strip().lower()
    if webhook_mode not in WEBHOOK_MODES:
        raise ConfigError("webhook_mode must be new_message or edit_message")
    merged["webhook_mode"] = webhook_mode
    merged["webhook_url"] = str(merged.get("webhook_url") or "").strip()
    if merged["webhook_enabled"]:
        try:
            merged["webhook_url"] = validate_webhook_url(merged["webhook_url"])
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
    merged["webhook_message_id"] = str(merged.get("webhook_message_id") or "").strip()
    merged["webhook_last_message_id"] = str(merged.get("webhook_last_message_id") or "").strip()
    merged["webhook_last_sent_at"] = merged.get("webhook_last_sent_at") or 0
    if merged["webhook_enabled"]:
        try:
            merged["webhook_interval_seconds"] = validate_webhook_interval(merged.get("webhook_interval_seconds", 300))
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
    else:
        try:
            merged["webhook_interval_seconds"] = int(merged.get("webhook_interval_seconds", 300))
        except (TypeError, ValueError):
            merged["webhook_interval_seconds"] = 300

    auto_resize_mode = str(merged.get("auto_resize_mode", "off")).strip().lower()
    if auto_resize_mode not in AUTO_RESIZE_MODES:
        raise ConfigError("auto_resize_mode must be off, auto, or preview")
    merged["auto_resize_mode"] = auto_resize_mode

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
    merged["window_gap_px"] = _as_int("window_gap_px", merged.get("window_gap_px"), 0)
    merged["snapshot_max_age_seconds"] = _as_int("snapshot_max_age_seconds", merged.get("snapshot_max_age_seconds"), 30)
    if merged["backoff_max_seconds"] < merged["backoff_min_seconds"]:
        raise ConfigError("backoff_max_seconds must be greater than or equal to backoff_min_seconds")

    log_level = str(merged.get("log_level", "INFO")).upper()
    if log_level not in {"DEBUG", "INFO", "WARN", "WARNING", "ERROR"}:
        raise ConfigError("log_level must be DEBUG, INFO, WARN, WARNING, or ERROR")
    merged["log_level"] = "WARNING" if log_level == "WARN" else log_level

    if not merged.get("created_at"):
        merged["created_at"] = utc_now()
    merged["updated_at"] = utc_now()
    if not isinstance(merged.get("last_layout_preview"), list):
        merged["last_layout_preview"] = []
    merged["snapshot_temp_path"] = str(merged.get("snapshot_temp_path") or "")

    # License key (optional; empty = not configured)
    raw_license = str(merged.get("license_key") or "").strip()
    try:
        merged["license_key"] = validate_license_key(raw_license)
    except ConfigError:
        merged["license_key"] = ""

    # YesCaptcha API key (stored verbatim; not validated beyond length)
    merged["yescaptcha_key"] = str(merged.get("yescaptcha_key") or "").strip()[:256]

    # Webhook tags (user-defined labels shown in webhook embeds)
    raw_tags = merged.get("webhook_tags")
    if not isinstance(raw_tags, list):
        raw_tags = []
    merged["webhook_tags"] = [str(t).strip()[:80] for t in raw_tags if str(t).strip()][:20]

    # Package start times (ISO timestamps of last launch per package)
    raw_start_times = merged.get("package_start_times")
    if not isinstance(raw_start_times, dict):
        raw_start_times = {}
    merged["package_start_times"] = {
        k: str(v)
        for k, v in raw_start_times.items()
        if is_valid_package_name(str(k))
    }

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
    view["webhook_url"] = mask_webhook_url(view.get("webhook_url"))
    view["license_key"] = mask_license_key(view.get("license_key", ""))
    view["yescaptcha_key"] = "***" if view.get("yescaptcha_key") else ""
    return view
