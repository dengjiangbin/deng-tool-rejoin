"""Auto rejoin supervisor loop."""

from __future__ import annotations

import signal
import threading
import time
from typing import Any

from . import android, db
from .backoff import calculate_backoff_seconds
from .config import load_config, validate_config
from .launcher import perform_rejoin
from .lockfile import LockManager
from .logger import configure_logging, log_event
from .monitor import check_roblox_health


# ─── Status constants (shown in terminal and webhook) ─────────────────────────

STATUS_ONLINE    = "Online"
STATUS_OFFLINE   = "Offline"
STATUS_LAUNCHING = "Launching"
STATUS_CHECKING  = "Checking"
STATUS_REVIVING  = "Reviving"


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
    """Background thread that monitors and auto-revives one Roblox package.

    It does NOT interact with other workers and never kills packages outside
    its own scope.  Status is published to ``status_map[package]`` so the
    main thread (or a display loop) can read current states.
    """

    def __init__(
        self,
        package: str,
        cfg: dict[str, Any],
        status_map: dict[str, str],
        stop_event: threading.Event,
        on_status_change: Any = None,
    ) -> None:
        super().__init__(name=f"worker-{package}", daemon=True)
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
        self.logger = configure_logging(level=cfg.get("log_level", "INFO"))

    def _set_status(self, status: str) -> None:
        old = self.status_map.get(self.package)
        self.status_map[self.package] = status
        if old != status and callable(self.on_status_change):
            self.on_status_change(self.package, status)

    def _sleep(self, seconds: float) -> None:
        deadline = time.time() + max(0.5, float(seconds))
        while not self.stop_event.is_set() and time.time() < deadline:
            time.sleep(min(1.0, deadline - time.time()))

    def run(self) -> None:
        cfg = self.cfg
        interval = int(cfg.get("health_check_interval_seconds", 30))
        grace = int(cfg.get("foreground_grace_seconds", 30))
        grace_start: float | None = None

        while not self.stop_event.is_set():
            try:
                # Heartbeat: check if process is running
                self._set_status(STATUS_CHECKING)
                running = android.is_process_running(self.package)

                if running:
                    self.failure_count = 0
                    self.unhealthy_since = None
                    grace_start = None
                    now_ts = time.time()
                    if self.online_since is None:
                        self.online_since = now_ts
                    self.last_seen_at = now_ts
                    self._set_status(STATUS_ONLINE)
                    db.insert_heartbeat("healthy", {"package": self.package})
                    log_event(self.logger, "info", "heartbeat", status="healthy", package=self.package)
                    self._sleep(interval)
                    continue

                # Process not detected
                now = time.time()
                if grace_start is None:
                    grace_start = now
                elapsed = now - grace_start

                if elapsed < grace:
                    self._set_status(STATUS_OFFLINE)
                    log_event(self.logger, "warning", "roblox_not_running",
                              grace_remaining_seconds=int(grace - elapsed), package=self.package)
                    self._sleep(min(5, grace - elapsed))
                    continue

                # Grace period expired → auto-revive
                grace_start = None
                self._set_status(STATUS_REVIVING)
                log_event(self.logger, "info", "reviving_package", package=self.package)
                pkg_cfg = dict(cfg)
                pkg_cfg["roblox_package"] = self.package
                result = perform_rejoin(pkg_cfg, reason="heartbeat_dead")

                if result.success:
                    self.failure_count = 0
                    self.revive_count += 1
                    self.online_since = None  # reset; will be set on next healthy beat
                    self._set_status(STATUS_LAUNCHING)
                    log_event(self.logger, "info", "revive_success", package=self.package)
                    # Wait for the app to start up before re-checking
                    self._sleep(max(int(cfg.get("reconnect_delay_seconds", 8)), interval))
                else:
                    self.failure_count += 1
                    self.last_error = result.error or "revive failed"
                    self._set_status(STATUS_OFFLINE)
                    backoff = calculate_backoff_seconds(
                        max(1, self.failure_count),
                        int(cfg.get("backoff_min_seconds", 10)),
                        int(cfg.get("backoff_max_seconds", 300)),
                    )
                    log_event(self.logger, "error", "revive_failed",
                              package=self.package, failure_count=self.failure_count,
                              backoff_seconds=backoff, error=result.error or "")
                    self._sleep(backoff)

            except Exception as exc:  # noqa: BLE001 - worker must not crash
                log_event(self.logger, "error", "worker_error", package=self.package, error=str(exc))
                self._set_status(STATUS_OFFLINE)
                self._sleep(interval)


class MultiPackageSupervisor:
    """Supervisor that monitors multiple Roblox packages independently.

    Each package runs in its own _PackageWorker thread.  Dead packages are
    revived automatically without touching the other running instances.
    The session stays alive indefinitely (blocking the calling thread).

    Usage::

        status = {pkg: STATUS_LAUNCHING for pkg in packages}
        sup = MultiPackageSupervisor(packages, cfg, initial_status=status)
        sup.run_forever()          # blocks; press Ctrl+C to stop
    """

    def __init__(
        self,
        packages: list[str],
        cfg: dict[str, Any],
        *,
        initial_status: dict[str, str] | None = None,
        on_status_change: Any = None,
    ) -> None:
        self.packages = list(packages)
        self.cfg = cfg
        self.stop_event = threading.Event()
        self.status_map: dict[str, str] = {pkg: STATUS_LAUNCHING for pkg in self.packages}
        if initial_status:
            self.status_map.update(initial_status)
        self.on_status_change = on_status_change
        self._workers: list[_PackageWorker] = []

    def _handle_stop(self, signum, frame) -> None:  # noqa: ANN001
        print("\n  Supervisor stopping — Ctrl+C received.")
        self.stop_event.set()

    def run_forever(self, *, display_interval: float = 10.0) -> None:
        """Start all workers and block until Ctrl+C or SIGTERM."""
        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT, self._handle_stop)

        logger = configure_logging(level=self.cfg.get("log_level", "INFO"))
        log_event(logger, "info", "multi_supervisor_started", packages=self.packages)
        db.insert_event("INFO", "multi_supervisor_started",
                        f"monitoring {len(self.packages)} packages: {', '.join(self.packages)}")

        for pkg in self.packages:
            worker = _PackageWorker(
                pkg, self.cfg, self.status_map, self.stop_event, self.on_status_change
            )
            worker.start()
            self._workers.append(worker)

        # Keep main thread alive; print a quick status summary periodically
        try:
            while not self.stop_event.is_set():
                self.stop_event.wait(timeout=display_interval)
                if not self.stop_event.is_set():
                    self._print_live_status()
        except Exception:  # noqa: BLE001
            pass

        self.stop_event.set()
        for worker in self._workers:
            worker.join(timeout=5)

        db.insert_event("INFO", "multi_supervisor_stopped", "session ended by user")
        log_event(logger, "info", "multi_supervisor_stopped")

    def get_status_snapshot(self, entries: list[dict] | None = None) -> list[dict]:
        """Return a per-package status snapshot list.

        Each dict contains:
            package, username, status, revive_count, failure_count,
            last_error, online_since, last_seen_at

        ``entries`` is the list of package entry dicts (each has 'package' and
        optionally 'account_username').  Pass it from ``enabled_package_entries()``
        to include username info.  If omitted, only package names are used.
        """
        entry_map: dict[str, str] = {}
        if entries:
            for e in entries:
                pkg = str(e.get("package") or "")
                username = str(e.get("account_username") or "").strip()
                entry_map[pkg] = username if username else "Unknown"

        snapshot: list[dict] = []
        worker_map = {w.package: w for w in self._workers}
        for pkg in self.packages:
            status = self.status_map.get(pkg, STATUS_OFFLINE)
            worker = worker_map.get(pkg)
            snapshot.append({
                "package": pkg,
                "username": entry_map.get(pkg, ""),
                "status": status,
                "revive_count": worker.revive_count if worker else 0,
                "failure_count": worker.failure_count if worker else 0,
                "last_error": worker.last_error if worker else None,
                "online_since": worker.online_since if worker else None,
                "last_seen_at": worker.last_seen_at if worker else None,
            })
        return snapshot

    def _print_live_status(self) -> None:
        """Print a compact per-package status line to stdout."""
        parts = []
        for pkg in self.packages:
            status = self.status_map.get(pkg, STATUS_OFFLINE)
            short_pkg = pkg.split(".")[-1][:12]
            parts.append(f"{short_pkg}:{status}")
        print("  [Monitor] " + "  ".join(parts), flush=True)
