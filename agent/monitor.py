"""Freeform-aware Roblox health checks.

Key design principle:
  In Android freeform / multi-window mode, not every visible Roblox window is
  the *top foreground* app.  Only ONE app can be foreground at a time.
  Therefore, foreground-only detection is wrong for multi-client setups.

Health result states:
  "healthy"             — package is alive by at least one evidence source.
  "roblox_not_running"  — package is genuinely dead (no PID, no task, no window).
  "roblox_not_installed"— package is not installed on the device.
  "network_down"        — network is not reachable (blocking for URL joins).

Evidence hierarchy (any single TRUE = package is alive):
  1. pidof / ps process check
  2. root pidof / ps (when standard check is unavailable)
  3. dumpsys activity — task record for the package
  4. dumpsys window  — window entry for the package

Foreground status is collected as metadata for state-machine decisions
(e.g., promoting to In Server vs Lobby) but does NOT determine whether
the package is alive/dead.
"""

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
    """Freeform-aware health check for one Android package.

    A package is considered healthy (alive) if ANY of the following is true:
      - pidof / ps shows a running process
      - root pidof / ps shows a running process
      - dumpsys activity shows a task for the package
      - dumpsys window shows a window entry for the package

    Foreground status is returned as metadata but does NOT determine aliveness.
    This prevents false "Offline" states in multi-window freeform setups where
    background Roblox clients are running but not the top foreground app.
    """
    try:
        validate_config(config_data)
        package = validate_package_name(package)
    except Exception as exc:  # noqa: BLE001
        return HealthResult("roblox_not_running", f"Config/package error: {exc}", {"package": str(package)})

    try:
        if not android.network_available():
            return HealthResult("network_down", "network check failed", {"package": package})
    except Exception:  # noqa: BLE001
        pass  # network check failure is non-fatal; continue with process checks

    if not android.package_installed(package):
        return HealthResult(
            "roblox_not_installed",
            "Roblox package is not installed",
            {"package": package},
        )

    # ── Multi-source aliveness check ─────────────────────────────────────────
    evidence = android.get_package_alive_evidence(package)
    alive    = bool(evidence.get("alive"))
    running  = bool(evidence.get("running") or evidence.get("root_running"))

    # Foreground is metadata only — not a health gate.
    foreground   = android.current_foreground_package()
    is_foreground = (foreground == package)

    # ── Disconnect signal check (only when process is alive) ─────────────────
    disconnect_ev = None
    if alive:
        try:
            from .roblox_health import analyze_disconnect_signals
            disconnect_ev = analyze_disconnect_signals(package)
        except Exception:  # noqa: BLE001
            pass

    if disconnect_ev and disconnect_ev.category in (
        "disconnected",
        "server_shutdown",
        "private_server_refresh",
    ):
        # Strong disconnect signal overrides aliveness — treat as not running
        # (the supervisor will attempt a reconnect / reopen).
        return HealthResult(
            "roblox_not_running",
            f"Disconnect signal: {disconnect_ev.category} (source={disconnect_ev.source})",
            {
                "package":             package,
                "foreground":          foreground,
                "running":             running,
                "task":                evidence.get("task"),
                "window":              evidence.get("window"),
                "disconnect_category": disconnect_ev.category,
                "disconnect_source":   disconnect_ev.source,
            },
        )

    if alive:
        # Package is running / has a task / has a window — it is healthy.
        # Background (non-foreground) clients in freeform mode are still healthy.
        detail = (
            f"fg={is_foreground} proc={running} "
            f"task={evidence.get('task')} win={evidence.get('window')}"
        )
        return HealthResult(
            "healthy",
            f"Package alive ({detail})",
            {
                "package":      package,
                "foreground":   foreground,
                "running":      running,
                "task":         evidence.get("task"),
                "window":       evidence.get("window"),
                "is_foreground": is_foreground,
            },
        )

    # No evidence of the package being alive at all.
    return HealthResult(
        "roblox_not_running",
        "Package not running — no process, task, or window found",
        {
            "package":   package,
            "foreground": foreground,
            "running":   False,
            "task":      False,
            "window":    False,
        },
    )


def check_roblox_health(config_data: dict[str, Any]) -> HealthResult:
    cfg = validate_config(config_data)
    return check_package_health(config_data, cfg["roblox_package"])
