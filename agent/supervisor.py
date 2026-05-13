"""Auto rejoin supervisor loop."""

from __future__ import annotations

import signal
import threading
import time
from typing import Any

from . import db
from .backoff import calculate_backoff_seconds
from .config import load_config, validate_config
from .launcher import perform_rejoin
from .lockfile import LockManager
from .logger import configure_logging, log_event
from .monitor import check_roblox_health


class Supervisor:
    """State-machine based local supervisor."""

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
