"""Lightweight environment and Roblox health checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import android
from .config import validate_config


@dataclass(frozen=True)
class HealthResult:
    state: str
    message: str
    meta: dict[str, Any]


def check_roblox_health(config_data: dict[str, Any]) -> HealthResult:
    cfg = validate_config(config_data)
    package = cfg["roblox_package"]

    if not android.network_available():
        return HealthResult("network_down", "network check failed", {"package": package})

    if not android.package_installed(package):
        return HealthResult("roblox_not_installed", "Roblox package is not installed", {"package": package})

    foreground = android.current_foreground_package()
    running = android.is_process_running(package)

    if foreground == package:
        return HealthResult("healthy", "Roblox is foreground", {"package": package, "foreground": foreground, "running": running})

    if running and foreground is None:
        return HealthResult("healthy", "Roblox process is running; foreground package unavailable", {"package": package, "running": True})

    if running:
        return HealthResult(
            "roblox_not_running",
            "Roblox is running but not foreground",
            {"package": package, "foreground": foreground, "running": True},
        )

    return HealthResult(
        "roblox_not_running",
        "Roblox process was not detected",
        {"package": package, "foreground": foreground, "running": False},
    )
