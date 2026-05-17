"""Safe Roblox launch and rejoin orchestration."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from . import android, db
from .config import effective_private_server_url, enabled_package_entries, validate_config
from .logger import configure_logging, log_event
from .url_utils import mask_launch_url, mask_urls_in_text


@dataclass(frozen=True)
class RejoinResult:
    success: bool
    root_used: bool
    error: str | None = None
    warning: str | None = None


@dataclass(frozen=True)
class LaunchAttemptResult:
    package: str
    attempted: bool
    success: bool
    method: str
    reason: str
    stdout: str
    stderr: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "attempted": self.attempted,
            "success": self.success,
            "method": self.method,
            "reason": self.reason,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def perform_rejoin(
    config_data: dict[str, Any],
    *,
    reason: str = "manual",
    package_entry: dict[str, Any] | None = None,
    no_force_stop: bool = False,
) -> RejoinResult:
    """Perform one safe rejoin attempt and record the result locally."""
    cfg = validate_config(config_data)
    logger = configure_logging(level=cfg.get("log_level", "INFO"))
    ents = enabled_package_entries(cfg)
    entry = package_entry
    if entry is None:
        entry = next((e for e in ents if e["package"] == cfg["roblox_package"]), ents[0])
    package = entry["package"]
    launch_mode = cfg["launch_mode"]
    launch_url = str(cfg.get("launch_url") or "").strip()
    effective_url = str(effective_private_server_url(entry, cfg) or "").strip()
    legacy_url_mode = launch_mode in {"deeplink", "web_url"} and bool(launch_url)
    url_for_launch = effective_url or (launch_url if legacy_url_mode else "")
    masked_url = mask_launch_url(url_for_launch) if url_for_launch else None
    root_used = False
    warning: str | None = None

    log_event(
        logger,
        "info",
        "rejoin_start",
        reason=reason,
        package=package,
        launch_mode=launch_mode,
        url=masked_url or "",
    )

    try:
        if not android.package_installed(package):
            raise RuntimeError(f"Roblox package is not installed: {package}")

        if cfg.get("root_mode_enabled") and not no_force_stop:
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
        elif not cfg.get("root_mode_enabled"):
            warning = "non-root mode: restart capability is limited to launching Roblox"

        # Layer 2: launch with explicit windowing-mode + launch-bounds so the
        # window is placed correctly from the first frame.  Falls back to
        # bounds-less launch automatically if `--activity-launch-bounds`
        # isn't supported on this build.
        #
        # Real-device evidence (probe ``p-47fa33562a``): the old call passed
        # ``[package]`` (ONE package) to ``calculate_split_layout``, so every
        # clone was launched into the *same* single-package rect and they all
        # overlapped.  Build the layout for the FULL list of enabled clones
        # and pick the rect that matches ``package``.
        _bounds_rect: tuple[int, int, int, int] | None = None
        try:
            from . import window_layout
            _display = window_layout.detect_display_info()
            _all_pkgs = [e["package"] for e in ents] or [package]
            if package not in _all_pkgs:
                _all_pkgs.append(package)
            _dock_frac = float(cfg.get("termux_dock_fraction", 0.50))
            _rects = window_layout.calculate_split_layout(
                _all_pkgs, _display.width, _display.height,
                termux_log_fraction=_dock_frac,
            )
            _r_for_pkg = next(
                (r for r in (_rects or []) if getattr(r, "package", None) == package),
                None,
            )
            if _r_for_pkg is not None:
                _bounds_rect = (
                    _r_for_pkg.left, _r_for_pkg.top,
                    _r_for_pkg.right, _r_for_pkg.bottom,
                )
        except Exception:  # noqa: BLE001
            _bounds_rect = None

        if _bounds_rect is not None:
            result, _method = android.launch_package_with_bounds(
                package, _bounds_rect, url_for_launch or None,
            )
        else:
            result, _method = android.launch_package_with_options(
                package,
                url_for_launch or None,
            )

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


def launch_package_structured(config_data: dict[str, Any], entry: dict[str, Any]) -> LaunchAttemptResult:
    """Launch a single package with URL → am fallback; never raises."""
    cfg = validate_config(config_data)
    package = entry["package"]
    if not android.package_installed(package):
        return LaunchAttemptResult(
            package=package,
            attempted=False,
            success=False,
            method="none",
            reason="not_installed",
            stdout="",
            stderr="",
        )
    url = str(effective_private_server_url(entry, cfg) or "").strip()
    result, method = android.launch_package_with_options(package, url or None)
    reason = "" if result.ok else (result.stderr or result.stdout or "launch_failed")
    return LaunchAttemptResult(
        package=package,
        attempted=True,
        success=result.ok,
        method=method,
        reason=reason,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def launch_configured_packages(config_data: dict[str, Any], *, reason: str = "start") -> list[RejoinResult]:
    """Launch all configured Roblox packages safely, one package at a time."""
    cfg = validate_config(config_data)
    packages = [entry["package"] for entry in enabled_package_entries(cfg)]
    results: list[RejoinResult] = []
    delay = max(5, int(cfg.get("reconnect_delay_seconds", 8)))
    for index, package in enumerate(packages):
        pkg_entry = next(e for e in enabled_package_entries(cfg) if e["package"] == package)
        package_cfg = dict(cfg)
        package_cfg["roblox_package"] = package
        result = perform_rejoin(package_cfg, reason=reason, package_entry=pkg_entry)
        results.append(result)
        if index < len(packages) - 1:
            time.sleep(delay)
    return results
