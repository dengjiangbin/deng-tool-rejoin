"""Lightweight environment and Roblox health checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import android
from .config import validate_config, validate_package_name


@dataclass(frozen=True)
class HealthResult:
    state: str
    message: str
    meta: dict[str, Any]


def check_package_health(config_data: dict[str, Any], package: str) -> HealthResult:
    """Environment + process checks for one Android package (Roblox or clone)."""
    validate_config(config_data)
    package = validate_package_name(package)

    if not android.network_available():
        return HealthResult("network_down", "network check failed", {"package": package})

    if not android.package_installed(package):
        return HealthResult("roblox_not_installed", "Roblox package is not installed", {"package": package})

    foreground = android.current_foreground_package()
    running = android.is_process_running(package)

    ev = None
    if running:
        from .roblox_health import analyze_disconnect_signals

        ev = analyze_disconnect_signals(package)

    if foreground == package:
        if ev and ev.category in ("disconnected", "server_shutdown", "private_server_refresh"):
            return HealthResult(
                "roblox_not_running",
                "Roblox connectivity or server signals detected while app is foreground",
                {
                    "package": package,
                    "foreground": foreground,
                    "running": running,
                    "disconnect_category": ev.category,
                    "disconnect_source": ev.source,
                },
            )
        return HealthResult("healthy", "Roblox is foreground", {"package": package, "foreground": foreground, "running": running})

    if running and foreground is None:
        if ev and ev.category in ("disconnected", "server_shutdown", "private_server_refresh"):
            return HealthResult(
                "roblox_not_running",
                "Disconnect indicators while Roblox process is running",
                {"package": package, "foreground": foreground, "running": True, "disconnect_category": ev.category, "disconnect_source": ev.source},
            )
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


def check_roblox_health(config_data: dict[str, Any]) -> HealthResult:
    cfg = validate_config(config_data)
    return check_package_health(config_data, cfg["roblox_package"])
