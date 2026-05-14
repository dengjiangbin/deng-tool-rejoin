"""Safe Roblox launch and rejoin orchestration."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from . import android, db
from .config import enabled_package_entries, validate_config
from .logger import configure_logging, log_event
from .url_utils import mask_launch_url, mask_urls_in_text


@dataclass(frozen=True)
class RejoinResult:
    success: bool
    root_used: bool
    error: str | None = None
    warning: str | None = None


def perform_rejoin(config_data: dict[str, Any], *, reason: str = "manual") -> RejoinResult:
    """Perform one safe rejoin attempt and record the result locally."""
    cfg = validate_config(config_data)
    logger = configure_logging(level=cfg.get("log_level", "INFO"))

    package = cfg["roblox_package"]
    launch_mode = cfg["launch_mode"]
    launch_url = cfg.get("launch_url", "")
    masked_url = mask_launch_url(launch_url) if launch_url else None
    root_used = False
    warning: str | None = None

    log_event(logger, "info", "rejoin_start", reason=reason, package=package, launch_mode=launch_mode, url=masked_url or "")

    try:
        if not android.package_installed(package):
            raise RuntimeError(f"Roblox package is not installed: {package}")

        if cfg.get("root_mode_enabled"):
            root_info = android.detect_root()
            if root_info.available:
                root_used = True
                log_event(logger, "info", "root_command", command=f"am force-stop {package}", root_tool=root_info.tool or "su")
                stop_result = android.force_stop_package(package, root_info=root_info)
                if not stop_result.ok:
                    warning = f"root force-stop failed: {mask_urls_in_text(stop_result.summary)}"
                    log_event(logger, "warning", "force_stop_failed", error=warning, root_used=root_used)
                time.sleep(max(5, int(cfg["reconnect_delay_seconds"])))
            else:
                warning = "root mode is enabled, but root is unavailable; launching without force-stop"
                log_event(logger, "warning", "root_unavailable", warning=warning)
        else:
            warning = "non-root mode: restart capability is limited to launching Roblox"

        if launch_mode == "app":
            result = android.launch_app(package)
        else:
            result = android.launch_url(package, launch_url, launch_mode)
            if not result.ok:
                fallback = android.launch_url_generic(launch_url, launch_mode)
                if fallback.ok:
                    warning = f"{warning + '; ' if warning else ''}package-specific URL launch failed; generic Android VIEW launch succeeded"
                    result = fallback

        if not result.ok:
            error = mask_urls_in_text(result.summary or "Android launch command failed")
            raise RuntimeError(error)

        db.insert_rejoin_attempt(
            reason=reason,
            package=package,
            launch_mode=launch_mode,
            masked_launch_url=masked_url,
            root_used=root_used,
            success=True,
            error=None,
        )
        db.insert_event(
            "INFO",
            "rejoin_success",
            f"rejoin succeeded for {package}",
            {"reason": reason, "package": package, "root_used": root_used, "launch_mode": launch_mode},
        )
        log_event(logger, "info", "rejoin_success", package=package, root_used=str(root_used).lower())
        return RejoinResult(True, root_used=root_used, warning=warning)
    except Exception as exc:  # noqa: BLE001 - CLI boundary records any operational failure.
        error = mask_urls_in_text(str(exc))
        db.insert_rejoin_attempt(
            reason=reason,
            package=package,
            launch_mode=launch_mode,
            masked_launch_url=masked_url,
            root_used=root_used,
            success=False,
            error=error,
        )
        db.insert_event("ERROR", "rejoin_failed", error, {"reason": reason, "package": package, "root_used": root_used})
        log_event(logger, "error", "rejoin_failed", error=error, package=package, root_used=str(root_used).lower())
        return RejoinResult(False, root_used=root_used, error=error, warning=warning)


def launch_configured_packages(config_data: dict[str, Any], *, reason: str = "start") -> list[RejoinResult]:
    """Launch all configured Roblox packages safely, one package at a time."""
    cfg = validate_config(config_data)
    packages = [entry["package"] for entry in enabled_package_entries(cfg)]
    results: list[RejoinResult] = []
    delay = max(5, int(cfg.get("reconnect_delay_seconds", 8)))
    for index, package in enumerate(packages):
        package_cfg = dict(cfg)
        package_cfg["roblox_package"] = package
        package_cfg["roblox_packages"] = [{"package": package, "label": "", "enabled": True}]
        result = perform_rejoin(package_cfg, reason=reason)
        results.append(result)
        if index < len(packages) - 1:
            time.sleep(delay)
    return results
