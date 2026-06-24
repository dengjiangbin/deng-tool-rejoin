"""Safe Roblox launch and rejoin orchestration."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from . import android, db
from .config import (
    effective_private_server_url,
    enabled_package_entries,
    private_url_launch_context,
    validate_config,
)
from .logger import configure_logging, log_event
from .url_utils import mask_launch_url, mask_urls_in_text, to_roblox_deep_link


def _first_log_lines(text: str, *, limit: int = 4) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return " | ".join(lines[:limit])[:500]


def _url_host_for_log(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.hostname or parsed.netloc or parsed.scheme or ""
    except Exception:  # noqa: BLE001
        return ""


def _result_used_root(result: android.CommandResult) -> bool:
    if not result.args:
        return False
    exe = os.path.basename(str(result.args[0]))
    return exe in {"su", "tsu"}


def _proc_scan_alive(package: str) -> bool:
    """Return True when process or foreground evidence exists (root-aware)."""
    if os.name == "nt":
        return True
    try:
        evidence = android.get_package_alive_evidence(package)
        return bool(
            evidence.get("running")
            or evidence.get("root_running")
            or evidence.get("foreground")
            or evidence.get("window")
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as fh:
                    cmdline = fh.read().replace(b"\x00", b" ").decode("utf-8", errors="replace")
                if package in cmdline:
                    return True
            except (OSError, PermissionError):
                continue
    except (OSError, PermissionError):
        pass
    return False


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


def _launch_wait_seconds(cfg: dict[str, Any], key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(cfg.get(key, default))
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _launch_verify_wait_seconds(cfg: dict[str, Any]) -> float:
    return _launch_wait_seconds(cfg, "launch_verify_wait_sec", 15.0, 5.0, 25.0)


def _command_for_log(args: tuple[str, ...]) -> str:
    return mask_urls_in_text(" ".join(str(a) for a in args))[:500]


def _read_launch_state(package: str) -> dict[str, Any]:
    state: dict[str, Any] = {
        "process_alive": True if os.name == "nt" else _proc_scan_alive(package),
        "activity_visible": False,
        "surface_present": False,
        "task_bounds": None,
        "task_id": None,
        "window_bounds": None,
        "resumed_activity": "",
    }
    if os.name == "nt":
        state["activity_visible"] = True
        return state
    try:
        from . import window_apply
        task = window_apply._select_task_entry(package)  # noqa: SLF001
        if task is not None:
            state["activity_visible"] = bool(task.visible or task.bounds)
            state["task_bounds"] = task.bounds
            state["task_id"] = task.task_id
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import window_apply
        win = window_apply._select_window_entry(package)  # noqa: SLF001
        if win is not None:
            state["window_bounds"] = win.bounds
            state["surface_present"] = bool(win.has_surface)
            state["activity_visible"] = bool(state["activity_visible"] or win.bounds)
    except Exception:  # noqa: BLE001
        pass
    try:
        res = android.run_command(["dumpsys", "activity", "activities"], timeout=4)
        text = res.stdout or ""
        idx = text.find("mResumedActivity")
        if idx >= 0:
            state["resumed_activity"] = text[idx: idx + 220].splitlines()[0][:220]
            if package in state["resumed_activity"]:
                state["activity_visible"] = True
    except Exception:  # noqa: BLE001
        pass
    return state


def _wait_for_launch_ready(package: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Poll launch evidence with a rigid iteration cap (never unbounded)."""
    max_polls = 15
    poll_sleep = 2.0
    last = _read_launch_state(package)
    for _ in range(max_polls):
        if last["process_alive"]:
            break
        last = _read_launch_state(package)
        time.sleep(poll_sleep)
    for _ in range(max_polls):
        if last["activity_visible"] or last["surface_present"]:
            break
        last = _read_launch_state(package)
        time.sleep(poll_sleep)
    settle = _launch_wait_seconds(cfg, "launch_settle_before_layout_sec", 1.0, 0.0, 4.0)
    if settle:
        time.sleep(min(settle, 2.0))
        last = _read_launch_state(package)
    last["black_screen_suspected"] = bool(
        last["process_alive"] and not (last["activity_visible"] or last["surface_present"])
    )
    return last


_DETACHED_RECOVERY_REASONS = frozenset({
    "recovery_gate_retry",
    "dead_recovery",
    "no_heartbeat_recovery",
    "watchdog_recovery",
    "process_missing",
})


def perform_rejoin(
    config_data: dict[str, Any],
    *,
    reason: str = "manual",
    package_entry: dict[str, Any] | None = None,
    no_force_stop: bool = False,
) -> RejoinResult:
    """Perform one safe rejoin attempt and record the result locally."""
    # [DENG_REJOIN_RESURRECT_DECISION] probe_id=p-ea167faf5f
    # validate_config was previously called OUTSIDE the try/except block.
    # A ConfigError (e.g. "health_check_interval_seconds must be at least 10")
    # would propagate to the supervisor worker's outer except, get logged as
    # worker_error, and loop forever without ever actually relaunching the package.
    # Wrapping it here converts any config failure into a RejoinResult(False)
    # so the supervisor receives a clean failure and applies its backoff.
    try:
        cfg = validate_config(config_data)
    except Exception as exc:  # noqa: BLE001
        _pkg_fallback = str(config_data.get("roblox_package") or "unknown")
        _err = str(exc)
        db.insert_rejoin_attempt(
            reason=reason,
            package=_pkg_fallback,
            launch_mode=str(config_data.get("launch_mode") or "app"),
            masked_launch_url=None,
            root_used=False,
            success=False,
            error=_err,
        )
        db.insert_event("ERROR", "rejoin_failed", _err, {"reason": reason, "package": _pkg_fallback})
        return RejoinResult(False, root_used=False, error=_err)
    from . import launch_verify as _lv

    root_err = _lv.root_preflight_error()
    if root_err:
        db.insert_rejoin_attempt(
            reason=reason,
            package=str(config_data.get("roblox_package") or "unknown"),
            launch_mode=str(config_data.get("launch_mode") or "app"),
            masked_launch_url=None,
            root_used=False,
            success=False,
            error=root_err,
        )
        db.insert_event("ERROR", "rejoin_failed", root_err, {"reason": reason})
        return RejoinResult(False, root_used=False, error=root_err)
    logger = configure_logging(level=cfg.get("log_level", "INFO"))
    ents = enabled_package_entries(cfg)
    entry = package_entry
    if entry is None:
        entry = next((e for e in ents if e["package"] == cfg["roblox_package"]), ents[0])
    package = entry["package"]
    launch_mode = cfg["launch_mode"]
    launch_url = str(cfg.get("launch_url") or "").strip()
    url_context = private_url_launch_context(entry, cfg)
    url_for_launch = str(url_context.get("url") or "").strip()
    # Kaeru-equivalent fix (probe p-1239f2b5f9): Roblox's https share
    # URL is resolved by Android to the *browser*, which lands the user
    # in the Roblox app's lobby instead of the private server.  The
    # working tool (Kaeru) launches the roblox:// deep link directly.
    # ``to_roblox_deep_link`` is a no-op for already-deep-link URLs and
    # for URLs whose pattern we don't know how to translate.
    if url_for_launch:
        deep = to_roblox_deep_link(url_for_launch)
        if deep and deep != url_for_launch:
            url_for_launch = deep
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
        private_url_mode=url_context.get("private_url_mode", "global"),
        url_mode=url_context.get("url_mode", "app_only"),
        url_config_source=url_context.get("url_config_source", "blank"),
        url=masked_url or "",
    )

    try:
        if not android.package_installed(package):
            raise RuntimeError(f"Roblox package is not installed: {package}")

        if (
            not no_force_stop
            and not url_for_launch
            and reason in _DETACHED_RECOVERY_REASONS
            and cfg.get("root_mode_enabled")
        ):
            root_info = android.detect_root()
            if root_info.available and android.dispatch_detached_force_stop_relaunch(
                package,
                root_tool=root_info.tool,
                sleep_seconds=3.5,
            ):
                root_used = True
                log_event(
                    logger,
                    "info",
                    "[DENG_REJOIN_RECOVERY_DETACHED_DISPATCH]",
                    reason=reason,
                    package=package,
                    action="root_tmp_script_force_stop_activity",
                )
                db.insert_rejoin_attempt(
                    reason=reason,
                    package=package,
                    launch_mode=launch_mode,
                    masked_launch_url=None,
                    root_used=True,
                    success=True,
                    error=None,
                )
                db.insert_event(
                    "INFO",
                    "rejoin_success",
                    f"Detached recovery dispatched for {package}",
                    {"reason": reason, "package": package},
                )
                return RejoinResult(True, root_used=True, warning=warning)

        # ── ALWAYS force-stop before launch ────────────────────────────────
        # User feedback (post-probe-1239f2b5f9): "I messed up the open
        # window then restarted; the tool failed to fix it. Kaeru fixes
        # it every time."  Kaeru ALWAYS force-stops the clone before
        # launching so the new ``am start --windowingMode 5
        # --activity-launch-bounds ...`` produces a *fresh* task with
        # the requested geometry; otherwise Android brings the existing
        # task to the foreground and silently drops the bounds.
        #
        # We try unprivileged ``am force-stop`` first (works in most
        # Termux + ADB setups) and fall back to root only when needed.
        # Honors ``no_force_stop`` for callers that want the old behavior.
        if not no_force_stop:
            stopped_via = ""
            try:
                pre_stop = android.force_stop_package(package)
                if pre_stop.ok:
                    if _result_used_root(pre_stop):
                        root_used = True
                        stopped_via = f"root({os.path.basename(str(pre_stop.args[0]))})"
                    else:
                        stopped_via = "am_force_stop"
            except Exception:  # noqa: BLE001
                pass

            if not stopped_via and cfg.get("root_mode_enabled"):
                root_info = android.detect_root()
                if root_info.available:
                    root_used = True
                    log_event(
                        logger, "info", "root_command",
                        command=f"am force-stop {package}",
                        root_tool=root_info.tool or "su",
                    )
                    try:
                        root_stop = android.force_stop_package(
                            package, root_info=root_info,
                        )
                        if root_stop.ok:
                            stopped_via = f"root({root_info.tool})"
                        else:
                            warning = (
                                f"root force-stop failed: "
                                f"{mask_urls_in_text(root_stop.summary)}"
                            )
                            log_event(
                                logger, "warning", "force_stop_failed",
                                error=warning, root_used=root_used,
                            )
                    except Exception as exc:  # noqa: BLE001
                        warning = f"root force-stop error: {exc}"
                        log_event(
                            logger, "warning", "force_stop_error",
                            error=warning, root_used=root_used,
                        )
                else:
                    warning = (
                        "root mode is enabled, but root is unavailable; "
                        "launching without root"
                    )
                    log_event(logger, "warning", "root_unavailable",
                              warning=warning)

            if stopped_via:
                log_event(logger, "info", "pre_launch_stop",
                          method=stopped_via, package=package)
                # Brief grace so the WindowManager actually tears down
                # the activity before we re-create it.
                time.sleep(min(2.5, max(1.0, int(cfg.get("reconnect_delay_seconds", 2)) / 2)))
            else:
                # Even without successful stop, log so the operator can
                # correlate "bounds not honored" with "task still alive".
                log_event(logger, "warning", "pre_launch_stop_skipped",
                          package=package, root_mode=cfg.get("root_mode_enabled"))

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
            from .config import DEFAULT_SCREEN_MODE, validate_screen_mode
            _screen_mode = validate_screen_mode(cfg.get("screen_mode", DEFAULT_SCREEN_MODE))
            _rects = []
            for _source in (cfg.get("_layout_rects"), cfg.get("last_layout_preview")):
                if not isinstance(_source, list):
                    continue
                for _item in _source:
                    if not isinstance(_item, dict) or _item.get("package") != package:
                        continue
                    try:
                        _rects = [window_layout.WindowRect(
                            package=str(_item["package"]),
                            left=int(_item["left"]),
                            top=int(_item["top"]),
                            right=int(_item["right"]),
                            bottom=int(_item["bottom"]),
                        )]
                    except (KeyError, TypeError, ValueError):
                        _rects = []
                    break
                if _rects:
                    break
            if not _rects:
                _display = window_layout.detect_display_info()
                _all_pkgs = [e["package"] for e in ents] or [package]
                if package not in _all_pkgs:
                    _all_pkgs.append(package)
                _dock_frac = float(cfg.get("termux_dock_fraction", 0.0) or 0.0)
                _rects = window_layout.calculate_split_layout(
                    _all_pkgs, _display.width, _display.height,
                    termux_log_fraction=_dock_frac,
                    screen_mode=_screen_mode,
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

        # ── URL-first launch (probe p-316b3b040d fix) ───────────────────────────
        # When private_server_url is SET, the URL itself IS the join intent.
        # Pass it directly to the first (and only) am-start call so Android
        # routes the roblox:// VIEW intent to ActivityProtocolLaunch
        # immediately — exactly like the user manually tapping the link.
        # No app-first phase, no waiting, no second phase URL delivery.
        #
        # When private_server_url is BLANK, a plain MAIN/LAUNCHER intent is
        # used.  Roblox opens to home/lobby only; no join is attempted.
        log_event(
            logger, "debug", "launch_url_first",
            package=package, url_set=bool(url_for_launch),
            masked_url=masked_url or "",
        )
        launch_started = time.monotonic()
        if _bounds_rect is not None:
            result, _method = android.launch_package_with_bounds(
                package, _bounds_rect, url_for_launch or None,
            )
        else:
            result, _method = android.launch_package_with_options(
                package, url_for_launch or None,
            )
        launch_elapsed_ms = int((time.monotonic() - launch_started) * 1000)
        if _result_used_root(result) or _method.startswith("root_"):
            root_used = True

        from . import package_state as _ps

        _ps.record_launch_attempt(
            package,
            command=_command_for_log(result.args),
            rc=int(getattr(result, "returncode", -1)),
            ok=bool(result.ok),
            failure_reason="" if result.ok else mask_urls_in_text(result.summary or "launch failed"),
        )

        if url_for_launch:
            log_event(
                logger,
                "info",
                "[DENG_REJOIN_URL_LAUNCH]",
                package=package,
                private_url_mode=url_context.get("private_url_mode", "global"),
                url_mode=url_context.get("url_mode", "app_only"),
                url_config_source=url_context.get("url_config_source", "blank"),
                url_present="true",
                url_host=_url_host_for_log(url_for_launch),
                url_length=len(url_for_launch),
                command_mode=_method,
                return_code=result.returncode,
                stdout_first_lines=_first_log_lines(mask_urls_in_text(result.stdout)),
                stderr_first_lines=_first_log_lines(mask_urls_in_text(result.stderr)),
                elapsed_ms=launch_elapsed_ms,
            )

        if not result.ok:
            error = mask_urls_in_text(result.summary or "Android launch command failed")
            raise RuntimeError(error)

        from . import launch_verify

        verification = launch_verify.verify_launch(
            package,
            launch_result=result,
            launch_method=_method,
            wait_seconds=_launch_verify_wait_seconds(cfg),
        )
        readiness = _wait_for_launch_ready(package, cfg)
        if not verification.success and not readiness.get("process_alive"):
            raise RuntimeError(verification.failure_message())
        if verification.success and not readiness.get("process_alive"):
            readiness = _read_launch_state(package)
        retry_count = 0
        if readiness.get("black_screen_suspected"):
            retry_count = 1
            log_event(
                logger,
                "warning",
                "launch_black_screen_retry",
                package=package,
                first_method=_method,
                retry_method="delayed_no_bounds",
                private_url_mode=url_context.get("private_url_mode", "global"),
                url_config_source=url_context.get("url_config_source", "blank"),
            )
            time.sleep(_launch_wait_seconds(cfg, "launch_black_screen_retry_delay_sec", 1.5, 0.5, 5.0))
            retry_result, retry_method = android.launch_package_with_options(package, url_for_launch or None)
            if _result_used_root(retry_result) or retry_method.startswith("root_"):
                root_used = True
            if retry_result.ok:
                result = retry_result
                _method = retry_method
                readiness = _wait_for_launch_ready(package, cfg)
            else:
                log_event(
                    logger,
                    "warning",
                    "launch_black_screen_retry_failed",
                    package=package,
                    method=retry_method,
                    error=mask_urls_in_text(retry_result.summary),
                )
        log_event(
            logger,
            "info",
            "[DENG_REJOIN_LAUNCH_TRACE]",
            package=package,
            private_url_mode=url_context.get("private_url_mode", "global"),
            url_source=url_context.get("url_config_source", "blank"),
            launch_type=url_context.get("url_mode", "app_only"),
            command=_command_for_log(result.args),
            rc=result.returncode,
            stdout_summary=_first_log_lines(mask_urls_in_text(result.stdout), limit=2),
            stderr_summary=_first_log_lines(mask_urls_in_text(result.stderr), limit=2),
            process_alive=str(bool(readiness.get("process_alive"))).lower(),
            resumed_activity=str(readiness.get("resumed_activity") or ""),
            task_id=str(readiness.get("task_id") or ""),
            window_bounds=str(readiness.get("window_bounds") or ""),
            task_bounds=str(readiness.get("task_bounds") or ""),
            surface_present=str(bool(readiness.get("surface_present"))).lower(),
            black_screen_suspected=str(bool(readiness.get("black_screen_suspected"))).lower(),
            layout_applied_before_surface="false",
            retry_count=retry_count,
            final_state="ready" if not readiness.get("black_screen_suspected") else "suspect",
        )
        if not readiness.get("process_alive"):
            if not verification.success:
                raise RuntimeError(verification.failure_message())
            raise RuntimeError("Android launch returned success but package process was not detected")
        if readiness.get("black_screen_suspected"):
            raise RuntimeError("Android launch returned success but no visible activity or surface was detected")

        log_event(
            logger, "info", "launch_result",
            package=package, method=_method, ok=True,
            url_set=bool(url_for_launch), masked_url=masked_url or "",
            private_url_mode=url_context.get("private_url_mode", "global"),
            url_config_source=url_context.get("url_config_source", "blank"),
        )

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


def launch_package_for_current_config(
    entry: dict[str, Any],
    cfg: dict[str, Any],
    reason: str = "watchdog_recovery",
) -> "RejoinResult":
    """Canonical launcher selector for watchdog recovery and initial Start.

    Behavior:
    - If ``private_server_url`` is configured (per-entry or global):
        Calls ``perform_rejoin`` which sends the roblox:// deep-link intent.
        The private server URL is preserved including ``&type=Server`` params.
    - If ``private_server_url`` is blank:
        Calls ``perform_rejoin`` with no URL — Roblox opens to home/lobby only
        (app-only launch, no join intent sent).

    This function MUST be used by:
    - Initial Start launch (via cmd_start loop)
    - Dead recovery (watchdog detects process gone)
    - No Heartbeat recovery (watchdog force-stops then relaunches)
    - No Heartbeat recovery (private URL relaunch when configured)
    - Supervisor resurrection

    [DENG_REJOIN_CANONICAL_LAUNCHER] probe_id=p-ea167faf5f
    Private Server URL is optional. Blank URL = app-only. Configured URL = private join.
    ``perform_rejoin`` already selects the correct am-start command based on
    ``effective_private_server_url(entry, cfg)`` — this wrapper just sets the
    package and delegates.
    """
    pkg = str(entry.get("package") or "")
    pkg_cfg = dict(cfg)
    pkg_cfg["roblox_package"] = pkg
    return perform_rejoin(pkg_cfg, reason=reason, package_entry=entry)
