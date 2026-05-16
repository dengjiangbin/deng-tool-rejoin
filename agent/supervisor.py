"""Auto rejoin supervisor loop."""

from __future__ import annotations

import signal
import threading
import time
from typing import Any

from . import db
from .backoff import calculate_backoff_seconds
from .config import load_config, validate_config
from .experience_detector import EvidenceLevel, detect_experience_state
from .launcher import perform_rejoin
from .lockfile import LockManager
from .logger import configure_logging, log_event
from .monitor import check_package_health, check_roblox_health
from .roblox_health import categorize_unhealthy


def _reapply_layout_for_package(package: str) -> None:
    """Best-effort layout re-apply for ONE package during recovery.

    Computes the package's slot in the current display layout and writes the
    XML keys + Set-enable booleans so the relaunched window uses the desired
    bounds.  Never raises.  All details go to the file logger.
    """
    try:
        from . import window_layout
        from . import window_apply
        display = window_layout.detect_display_info()
        # We don't know the full selected-package set here; compute a 1-package
        # layout fallback that uses the right-pane rules.  This keeps the
        # window landscape-shaped on its own; the next full Start cycle will
        # rebalance for multi-package layouts.
        rects = window_layout.calculate_split_layout(
            [package], display.width, display.height,
        )
        if rects:
            window_apply.apply_window_layout_silent(
                rects, force_stop_before=False, verify_after=False, retries=0,
            )
    except Exception as exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger("deng.rejoin.supervisor").debug(
            "reapply_layout_for_package(%s) error: %s", package, exc,
        )


# ─── Status constants (shown in terminal and webhook) ─────────────────────────

STATUS_ONLINE            = "Online"
STATUS_OFFLINE           = "Offline"
STATUS_LAUNCHING         = "Launching"
STATUS_CHECKING          = "Preparing"
STATUS_BACKGROUND        = "Background"
STATUS_RECONNECTING      = "Reconnecting"
STATUS_WARNING           = "Warning"
STATUS_FAILED            = "Failed"
STATUS_UNKNOWN           = "Unknown"
# Richer state constants for improved UX
STATUS_LOBBY             = "Lobby"             # App open at home/lobby, no URL join active
STATUS_IN_SERVER         = "In Server"         # Strong evidence: game experience loaded
STATUS_JOINING           = "Joining"           # Deep-link / private URL sent, waiting
STATUS_CLOSED            = "Closed"            # App cleanly not running after a session
STATUS_JOIN_UNCONFIRMED  = "Join Unconfirmed"  # App healthy but no in-game evidence yet

# All healthy states — used for state-machine guards
_HEALTHY_STATES = frozenset({
    STATUS_ONLINE, STATUS_LOBBY, STATUS_IN_SERVER, STATUS_JOIN_UNCONFIRMED,
})


class Supervisor:
    """State-machine based local supervisor (single-package, legacy)."""

    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.failure_count = 0
        self.unhealthy_since: float | None = None

    def _handle_stop(self, signum, frame) -> None:  # noqa: ANN001 - signal handler signature.
        self.stop_event.set()

    def _sleep(self, seconds: int | float) -> None:
        deadline = time.time() + max(1, float(seconds))
        while not self.stop_event.is_set() and time.time() < deadline:
            time.sleep(min(1.0, deadline - time.time()))

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT, self._handle_stop)

        cfg = validate_config(load_config())
        logger = configure_logging(level=cfg.get("log_level", "INFO"))
        with LockManager():
            log_event(logger, "info", "agent_started")
            db.insert_event("INFO", "agent_started", "auto supervisor started")
            while not self.stop_event.is_set():
                cfg = validate_config(load_config())
                interval = int(cfg["health_check_interval_seconds"])

                if not cfg.get("auto_rejoin_enabled"):
                    db.insert_heartbeat("disabled", {"auto_rejoin_enabled": False})
                    log_event(logger, "info", "heartbeat", status="disabled")
                    self._sleep(interval)
                    continue

                db.insert_heartbeat("checking_environment", {"package": cfg["roblox_package"]})
                health = check_roblox_health(cfg)

                if health.state == "healthy":
                    self.failure_count = 0
                    self.unhealthy_since = None
                    db.insert_heartbeat("healthy", health.meta)
                    log_event(logger, "info", "heartbeat", status="healthy", package=cfg["roblox_package"])
                    self._sleep(interval)
                    continue

                if health.state in {"network_down", "roblox_not_installed"}:
                    db.insert_heartbeat(health.state, health.meta)
                    log_event(logger, "warning", health.state, message=health.message, package=cfg["roblox_package"])
                    self._sleep(interval)
                    continue

                if health.state == "roblox_not_running":
                    now = time.time()
                    if self.unhealthy_since is None:
                        self.unhealthy_since = now
                    elapsed = now - self.unhealthy_since
                    grace = int(cfg["foreground_grace_seconds"])
                    if elapsed < grace:
                        db.insert_heartbeat("roblox_not_running", {**health.meta, "grace_remaining_seconds": int(grace - elapsed)})
                        log_event(logger, "warning", "roblox_not_running", message=health.message, grace_remaining_seconds=int(grace - elapsed))
                        self._sleep(min(interval, max(5, grace - elapsed)))
                        continue

                    db.insert_heartbeat("launching", health.meta)
                    result = perform_rejoin(cfg, reason=health.state)
                    if result.success:
                        self.failure_count = 0
                        self.unhealthy_since = None
                        db.insert_heartbeat("waiting_after_launch", {"package": cfg["roblox_package"]})
                        self._sleep(max(int(cfg["reconnect_delay_seconds"]), interval))
                    else:
                        self.failure_count += 1
                        fast_failures = int(cfg["max_fast_failures"])
                        backoff_index = max(1, self.failure_count - fast_failures + 1)
                        delay = calculate_backoff_seconds(backoff_index, cfg["backoff_min_seconds"], cfg["backoff_max_seconds"])
                        db.insert_heartbeat("backoff", {"failure_count": self.failure_count, "sleep_seconds": delay})
                        log_event(logger, "error", "backoff", failure_count=self.failure_count, sleep_seconds=delay, error=result.error or "")
                        self._sleep(delay)
                    continue

                db.insert_heartbeat("error", {"state": health.state, "message": health.message})
                log_event(logger, "error", "unknown_health_state", state=health.state, message=health.message)
                self._sleep(interval)

            db.insert_heartbeat("disabled", {"stop_requested": True})
            db.insert_event("INFO", "agent_stopped", "auto supervisor stopped")
            log_event(logger, "info", "agent_stopped")


# ─── Per-package worker ────────────────────────────────────────────────────────


class _PackageWorker(threading.Thread):
    """Background thread that monitors and auto-revives one Roblox package."""

    def __init__(
        self,
        entry: dict[str, Any],
        cfg: dict[str, Any],
        status_map: dict[str, str],
        stop_event: threading.Event,
        on_status_change: Any = None,
    ) -> None:
        package = str(entry.get("package") or "")
        super().__init__(name=f"worker-{package}", daemon=True)
        self.entry = dict(entry)
        self.package = package
        self.cfg = cfg
        self.status_map = status_map
        self.stop_event = stop_event
        self.on_status_change = on_status_change
        self.failure_count = 0
        self.unhealthy_since: float | None = None
        self.revive_count: int = 0
        self.last_error: str | None = None
        self.online_since: float | None = None
        self.last_seen_at: float | None = None
        self.grace_start: float | None = None
        self.bg_since: float | None = None
        self._restart_times: list[float] = []
        self._last_detail: str = ""
        self.logger = configure_logging(level=cfg.get("log_level", "INFO"))
        # URL-aware state tracking
        self.has_private_url: bool = False   # set at start of run()
        self._url_launched: bool = False     # was the LAST launch done with a URL?
        self.launching_since: float | None = None  # when Launching/Joining was set

    def _sup(self) -> dict[str, Any]:
        raw = self.cfg.get("supervisor")
        return raw if isinstance(raw, dict) else {}

    def _set_status(self, status: str, detail: str = "") -> None:
        old = self.status_map.get(self.package)
        self.status_map[self.package] = status
        self._last_detail = detail or self._last_detail
        if old != status and callable(self.on_status_change):
            self.on_status_change(self.package, status)

    def _sleep(self, seconds: float) -> None:
        deadline = time.time() + max(0.5, float(seconds))
        while not self.stop_event.is_set() and time.time() < deadline:
            time.sleep(min(1.0, deadline - time.time()))

    def _restart_budget_ok(self) -> bool:
        now = time.time()
        self._restart_times = [t for t in self._restart_times if now - t < 3600]
        cap = int(self._sup().get("max_restart_attempts_per_hour", 10))
        return len(self._restart_times) < cap

    def _record_restart(self) -> None:
        self._restart_times.append(time.time())

    def _can_auto_reopen(self) -> bool:
        return bool(self.entry.get("auto_reopen_enabled", True)) and bool(self._sup().get("auto_reopen_enabled", True))

    def _can_auto_reconnect(self) -> bool:
        return bool(self.entry.get("auto_reconnect_enabled", True)) and bool(self._sup().get("auto_reconnect_enabled", True))

    def _post_launch_state(self) -> str:
        """Determine the honest display state after a health check confirms the app
        is running, when transitioning FROM Launching / Joining.

        Uses evidence-based detection to avoid falsely claiming ``In Server``
        when the app is merely healthy (process alive / foreground) without
        actual proof that the Roblox experience loaded.

        Evidence rules:
        - EXPERIENCE_LIKELY_LOADED  → In Server
        - ROBLOX_HOME_OR_LOBBY / JOIN_FAILED_OR_HOME
            + url_launched  → Join Unconfirmed  (join probably failed; be honest)
            + no URL        → Lobby
        - FOREGROUND_APP or lower
            + url_launched  → Join Unconfirmed  (app open, no game evidence)
            + no URL        → Lobby
        - PROCESS_ONLY (shouldn't reach here — health said healthy)
            + url_launched  → Joining  (still waiting for foreground)
            + no URL        → Lobby
        """
        try:
            evidence = detect_experience_state(self.package, url_launched=self._url_launched)
        except Exception:  # noqa: BLE001
            evidence = None

        if evidence is not None and evidence.is_in_game():
            log_event(
                self.logger, "info", "experience_detected",
                package=self.package,
                source=evidence.source,
                detail=evidence.detail,
            )
            return STATUS_IN_SERVER

        if evidence is not None and evidence.is_home_or_lobby():
            # Clear lobby/home evidence: if a URL was used, join probably failed.
            if self._url_launched:
                log_event(
                    self.logger, "info", "join_home_evidence",
                    package=self.package,
                    source=evidence.source,
                    detail=evidence.detail,
                )
                return STATUS_JOIN_UNCONFIRMED
            return STATUS_LOBBY

        if self._url_launched:
            # App is healthy but we have no Android evidence that an experience
            # loaded.  Be honest: do NOT claim In Server without real evidence.
            return STATUS_JOIN_UNCONFIRMED

        return STATUS_LOBBY

    def run(self) -> None:
        # All worker setup wrapped: a thrown exception here would silently
        # kill this worker thread and the package would never reconnect.
        try:
            cfg = self.cfg
            sup = self._sup()
            interval = int(sup.get("health_check_interval_seconds") or cfg.get("health_check_interval_seconds", 30))
            grace = int(sup.get("launch_grace_seconds") or cfg.get("foreground_grace_seconds", 30))
            backoff_base = int(sup.get("restart_backoff_seconds", 10))
            # Determine URL awareness once at startup
            from .config import effective_private_server_url as _epsu
            self.has_private_url = bool(str(_epsu(self.entry, cfg) or "").strip())
            # Launching/Joining timeout: how many seconds before forcing a re-check
            _launching_timeout = max(90, grace * 4)
        except Exception as exc:  # noqa: BLE001
            log_event(self.logger, "error", "worker_setup_error", package=self.package, error=str(exc))
            # Defaults so the loop still runs forever
            cfg = self.cfg
            sup = self._sup()
            interval = 30
            grace = 30
            backoff_base = 10
            self.has_private_url = False
            _launching_timeout = 120

        while not self.stop_event.is_set():
            try:
                if not cfg.get("auto_rejoin_enabled") or not sup.get("enabled", True):
                    self._set_status(STATUS_OFFLINE, "supervisor disabled")
                    db.insert_heartbeat("disabled", {"package": self.package})
                    self._sleep(interval)
                    continue

                # ── Launching/Joining timeout guard ───────────────────────────
                current_st = self.status_map.get(self.package, "")
                if self.launching_since is not None and current_st in {STATUS_LAUNCHING, STATUS_JOINING}:
                    elapsed_since_launch = time.time() - self.launching_since
                    if elapsed_since_launch > _launching_timeout:
                        # App hasn't become healthy after 4× grace — force-check now
                        timeout_health = check_package_health(cfg, self.package)
                        if timeout_health.state == "healthy":
                            # Use evidence-based detection: do not assume In Server
                            # merely because the app is responding after a URL launch.
                            target = self._post_launch_state()
                            self._set_status(target, "Launch timeout — app confirmed running")
                        else:
                            self._set_status(STATUS_FAILED, "Launch timeout — app not responding")
                        self.launching_since = None
                        # Skip normal health check this iteration — we just ran one
                        self._sleep(min(5, interval))
                        continue

                # Save state before we clobber it with STATUS_CHECKING; used to
                # correctly promote Launching/Joining after a healthy health check.
                prev_before_check = self.status_map.get(self.package, "")
                self._set_status(STATUS_CHECKING, "")
                health = check_package_health(cfg, self.package)

                if health.state == "healthy":
                    self.failure_count = 0
                    self.unhealthy_since = None
                    self.grace_start = None
                    self.bg_since = None
                    now_ts = time.time()
                    if self.online_since is None:
                        self.online_since = now_ts
                    self.last_seen_at = now_ts
                    # Promote from Launching/Joining to an appropriate healthy state.
                    # Use prev_before_check because STATUS_CHECKING is transient.
                    self.launching_since = None
                    if prev_before_check in {STATUS_LAUNCHING, STATUS_JOINING}:
                        # Evidence-based: never promote to In Server without proof.
                        target = self._post_launch_state()
                    elif prev_before_check in _HEALTHY_STATES:
                        target = prev_before_check  # stay in current healthy state
                    else:
                        target = STATUS_ONLINE
                    self._set_status(target, "Heartbeat OK")
                    db.insert_heartbeat("healthy", {"package": self.package})
                    log_event(self.logger, "info", "heartbeat", status="healthy", package=self.package)
                    self._sleep(interval)
                    continue

                if health.state in {"network_down", "roblox_not_installed"}:
                    self._set_status(STATUS_WARNING, health.message)
                    self._sleep(interval)
                    continue

                running = bool(health.meta.get("running"))
                fg = health.meta.get("foreground")
                disc = health.meta.get("disconnect_category")
                fg_wrong = running and health.state == "roblox_not_running" and (
                    fg not in (self.package, None) or bool(disc)
                )

                if fg_wrong and self._can_auto_reconnect():
                    now = time.time()
                    if self.bg_since is None:
                        self.bg_since = now
                    if now - self.bg_since < grace * 2:
                        self._set_status(STATUS_BACKGROUND, "")
                        self._sleep(min(interval, grace))
                        continue
                    if not self._restart_budget_ok():
                        self._set_status(STATUS_WARNING, "restart limit reached")
                        self._sleep(backoff_base)
                        continue
                    self.bg_since = None
                    self._set_status(STATUS_RECONNECTING, "disconnected")
                    log_event(self.logger, "info", "reconnect_stale_foreground", package=self.package)
                    # Reapply layout BEFORE relaunch so the new window has bounds.
                    _reapply_layout_for_package(self.package)
                    pkg_cfg = dict(cfg)
                    pkg_cfg["roblox_package"] = self.package
                    result = perform_rejoin(pkg_cfg, reason="disconnected", package_entry=self.entry, no_force_stop=True)
                    self._record_restart()
                    if result.success:
                        self._url_launched = self.has_private_url
                        self.launching_since = time.time()
                        new_st = STATUS_JOINING if self.has_private_url else STATUS_LAUNCHING
                        self._set_status(new_st, "Reopened — launch command sent")
                        # Reapply once more after launch (clone window may have
                        # been recreated by App Cloner with default bounds).
                        _reapply_layout_for_package(self.package)
                    else:
                        self.failure_count += 1
                        self.last_error = result.error
                        self._set_status(STATUS_FAILED, result.error or "launch failed")
                    self._sleep(max(int(cfg.get("reconnect_delay_seconds", 8)), interval))
                    continue

                self.bg_since = None

                if running:
                    self._sleep(interval)
                    continue

                now = time.time()
                if self.grace_start is None:
                    self.grace_start = now
                elapsed = now - self.grace_start

                if elapsed < grace:
                    self._set_status(STATUS_OFFLINE, f"grace {int(grace - elapsed)}s")
                    self._sleep(min(5, grace - elapsed))
                    continue

                if not self._can_auto_reopen():
                    self._set_status(STATUS_OFFLINE, "auto reopen disabled")
                    self._sleep(interval)
                    continue

                if not self._restart_budget_ok():
                    self._set_status(STATUS_WARNING, "restart limit reached")
                    self._sleep(backoff_base)
                    continue

                self.grace_start = None
                self._set_status(STATUS_RECONNECTING, categorize_unhealthy("process_missing", self.package))
                log_event(self.logger, "info", "reviving_package", package=self.package)
                # Reapply layout BEFORE relaunch so the resurrected clone window
                # uses the correct bounds from first paint.
                _reapply_layout_for_package(self.package)
                pkg_cfg = dict(cfg)
                pkg_cfg["roblox_package"] = self.package
                result = perform_rejoin(pkg_cfg, reason="process_missing", package_entry=self.entry, no_force_stop=True)
                self._record_restart()

                if result.success:
                    self.failure_count = 0
                    self.revive_count += 1
                    self.online_since = None
                    self._url_launched = self.has_private_url
                    self.launching_since = time.time()
                    new_st = STATUS_JOINING if self.has_private_url else STATUS_LAUNCHING
                    self._set_status(new_st, "Reopened — launch command sent")
                    # Once more after launch to override any default bounds.
                    _reapply_layout_for_package(self.package)
                    self._sleep(max(int(cfg.get("reconnect_delay_seconds", 8)), interval))
                else:
                    self.failure_count += 1
                    self.last_error = result.error or "revive failed"
                    self._set_status(STATUS_OFFLINE, result.error or "")
                    delay = max(
                        backoff_base,
                        calculate_backoff_seconds(
                            max(1, self.failure_count),
                            int(cfg.get("backoff_min_seconds", 10)),
                            int(cfg.get("backoff_max_seconds", 300)),
                        ),
                    )
                    log_event(
                        self.logger,
                        "error",
                        "revive_failed",
                        package=self.package,
                        failure_count=self.failure_count,
                        backoff_seconds=delay,
                        error=result.error or "",
                    )
                    self._sleep(delay)

            except Exception as exc:  # noqa: BLE001 - worker must not crash
                log_event(self.logger, "error", "worker_error", package=self.package, error=str(exc))
                self._set_status(STATUS_UNKNOWN, str(exc))
                self._sleep(interval)


class MultiPackageSupervisor:
    """Supervisor that monitors multiple Roblox packages independently."""

    def __init__(
        self,
        entries: list[dict[str, Any]],
        cfg: dict[str, Any],
        *,
        initial_status: dict[str, str] | None = None,
        on_status_change: Any = None,
    ) -> None:
        self.entries = list(entries)
        self.packages = [str(e["package"]) for e in self.entries]
        self.cfg = cfg
        self.stop_event = threading.Event()
        self.status_map: dict[str, str] = {pkg: STATUS_LAUNCHING for pkg in self.packages}
        if initial_status:
            self.status_map.update(initial_status)
        self.on_status_change = on_status_change
        self._workers: list[_PackageWorker] = []

    def _handle_stop(self, signum, frame) -> None:  # noqa: ANN001
        # Silent stop — no public print.  All shutdown details go to the log.
        try:
            import logging as _logging
            _logging.getLogger("deng.rejoin.supervisor").debug(
                "Supervisor received signal %s, stopping cleanly.", signum,
            )
        except Exception:  # noqa: BLE001
            pass
        self.stop_event.set()

    def run_forever(self, *, display_interval: float = 10.0, render_callback=None) -> None:
        """Run until stopped.

        Args:
            display_interval: seconds between live status refreshes.
            render_callback: optional callable invoked every ``display_interval``
                seconds instead of the default ``_print_live_status`` one-liner.
                Use this to inject a full clear+banner+table dashboard renderer.
        """
        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT, self._handle_stop)

        logger = configure_logging(level=self.cfg.get("log_level", "INFO"))
        log_event(logger, "info", "multi_supervisor_started", packages=self.packages)
        db.insert_event(
            "INFO",
            "multi_supervisor_started",
            f"monitoring {len(self.packages)} packages: {', '.join(self.packages)}",
        )

        for entry in self.entries:
            worker = _PackageWorker(
                entry,
                self.cfg,
                self.status_map,
                self.stop_event,
                self.on_status_change,
            )
            worker.start()
            self._workers.append(worker)

        try:
            while not self.stop_event.is_set():
                self.stop_event.wait(timeout=display_interval)
                if not self.stop_event.is_set():
                    if render_callback is not None:
                        try:
                            render_callback()
                        except Exception:  # noqa: BLE001
                            self._print_live_status()
                    else:
                        self._print_live_status()
        except Exception:  # noqa: BLE001
            pass

        self.stop_event.set()
        for worker in self._workers:
            if worker.is_alive():
                worker.join(timeout=5)

        db.insert_event("INFO", "multi_supervisor_stopped", "session ended by user")
        log_event(logger, "info", "multi_supervisor_stopped")

    def get_status_snapshot(self, entries: list[dict] | None = None) -> list[dict]:
        entry_map: dict[str, str] = {}
        use = entries or self.entries
        for e in use:
            pkg = str(e.get("package") or "")
            username = str(e.get("account_username") or "").strip()
            entry_map[pkg] = username if username else "Unknown"

        snapshot: list[dict] = []
        worker_map = {w.package: w for w in self._workers}
        for pkg in self.packages:
            status = self.status_map.get(pkg, STATUS_OFFLINE)
            worker = worker_map.get(pkg)
            snapshot.append(
                {
                    "package": pkg,
                    "username": entry_map.get(pkg, ""),
                    "status": status,
                    "revive_count": worker.revive_count if worker else 0,
                    "failure_count": worker.failure_count if worker else 0,
                    "last_error": worker.last_error if worker else None,
                    "online_since": worker.online_since if worker else None,
                    "last_seen_at": worker.last_seen_at if worker else None,
                }
            )
        return snapshot

    def _print_live_status(self) -> None:
        parts = []
        for pkg in self.packages:
            status = self.status_map.get(pkg, STATUS_OFFLINE)
            short_pkg = pkg.split(".")[-1][:12]
            parts.append(f"{short_pkg}:{status}")
        print("  [Monitor] " + "  ".join(parts), flush=True)
