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
from .monitor import check_package_health, check_roblox_health
from .roblox_health import categorize_unhealthy


def _reapply_layout_for_package(package: str) -> None:
    """Best-effort layout re-apply for ONE package during recovery.

    Computes the package's slot in the current display layout, writes the
    XML keys + Set-enable booleans, AND triggers a direct (post-launch)
    resize via root so the relaunched window really lands at the desired
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
            # Layer 3: direct resize via root, with freeform mode flip.
            # This is what actually moves the visible window without
            # waiting for the next force-stop / relaunch cycle.
            try:
                ok, detail = window_apply.force_resize_package(package, rects[0])
                import logging as _logging
                _logging.getLogger("deng.rejoin.supervisor").debug(
                    "force_resize_package(%s) ok=%s detail=%s",
                    package, ok, detail,
                )
            except Exception as exc:  # noqa: BLE001
                import logging as _logging
                _logging.getLogger("deng.rejoin.supervisor").debug(
                    "force_resize_package(%s) error: %s", package, exc,
                )
    except Exception as exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger("deng.rejoin.supervisor").debug(
            "reapply_layout_for_package(%s) error: %s", package, exc,
        )


# ─── Status constants (shown in terminal and webhook) ─────────────────────────

STATUS_ONLINE            = "Online"
STATUS_OFFLINE           = "Offline"
STATUS_DEAD              = "Dead"              # Process confirmed gone (force-closed / crashed)
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
# State vocabulary aligned to user-facing terminology:
#   Launched     = Roblox process is up but no URL / game evidence yet.
#   Disconnected = Roblox error code or "connection lost" signal detected.
STATUS_LAUNCHED          = "Launched"
STATUS_DISCONNECTED      = "Disconnected"

# All healthy states — used for state-machine guards.
# Public live states: Online, Launching, Reopening, Failed, Layout.
# Legacy aliases (Lobby, In Server, Join Unconfirmed) are kept for display-map
# compatibility but should never be set by the live supervisor path.
_HEALTHY_STATES = frozenset({
    STATUS_ONLINE, STATUS_LAUNCHED,
    # Legacy aliases still accepted so _STATE_DISPLAY_MAP in commands.py can
    # map them to Online; they must not be produced by the live supervisor.
    STATUS_LOBBY, STATUS_IN_SERVER, STATUS_JOIN_UNCONFIRMED,
})

# Join Unconfirmed re-launch threshold.  With the Kaeru-style _post_launch_state
# returning Online immediately, this state is no longer entered on normal launches.
# The large value is a safety net for any legacy path that might still set it.
_JOIN_UNCONFIRMED_RELAUNCH_SECONDS = 3600  # 1 hour (effectively disabled)

# ─── Presence-based controlled relaunch constants ─────────────────────────────
# Relaunch from Presence signal requires ALL conditions to be met:
# - process is alive (running=True)
# - mapped userId exists
# - Presence API is reachable (not returning unknown/error)
# - Presence repeatedly reports offline/not-in-experience within the window
# - cooldown has passed since last presence-triggered relaunch
# - hourly cap not exceeded
# - only the affected package is relaunched (no cross-package cascade)
PRESENCE_SUSPICIOUS_CONFIRMATIONS: int = 3       # repeated "offline" hits needed
PRESENCE_SUSPICIOUS_WINDOW_SECONDS: int = 180    # within this time window
PRESENCE_RELAUNCH_COOLDOWN_SECONDS: int = 600    # min gap between presence relaunches
PRESENCE_RELAUNCH_MAX_PER_HOUR: int = 2          # hard cap per package per hour


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
        self.revive_count: int = 0       # total per-package relaunches (Kaeru: restart_count)
        self.last_error: str | None = None
        self.online_since: float | None = None
        self.last_seen_at: float | None = None       # Kaeru: last_seen_alive_at
        self.last_launch_at: float | None = None     # Kaeru: last_launch_at
        self.last_foreground_at: float | None = None # Kaeru: last_foreground_at
        self.desired_url: str = ""                   # Kaeru: desired_url (canonical launch URL)
        self.grace_start: float | None = None
        self.bg_since: float | None = None
        self._restart_times: list[float] = []
        self._last_detail: str = ""
        self.logger = configure_logging(level=cfg.get("log_level", "INFO"))
        # URL-aware state tracking
        self.has_private_url: bool = False   # set at start of run()
        self._url_launched: bool = False     # was the LAST launch done with a URL?
        self.launching_since: float | None = None  # when Launching/Joining was set
        self.join_unconfirmed_since: float | None = None  # for URL re-launch timeout
        # Heartbeat-based playing-state tracker.  Lazily imported so the
        # supervisor still works in test environments that don't have the
        # full agent package installed.
        try:
            from .playing_state import StateTracker
            self._state_tracker = StateTracker(
                offline_grace_s=float(cfg.get("offline_grace_seconds", 60.0)),
                stale_task_grace_s=20.0,
                join_unconfirmed_grace_s=25.0,
            )
        except Exception:  # noqa: BLE001
            self._state_tracker = None
        self._last_evidence: dict[str, Any] = {}
        self._consecutive_offline_checks: int = 0
        # Roblox presence — ground truth via the public Roblox API.
        self._roblox_user_id: int | None = None
        self._roblox_username: str = ""
        self._roblox_cookie: str | None = None
        self._presence_resolved: bool = False  # username → id resolved yet?
        self._last_presence_label: str = ""    # for diagnostic logging
        # Kaeru-style extended tracking (stable rebuild probe p-9e3f2a8d1c)
        self.last_presence_check_at: float | None = None  # when presence was last fetched
        self.last_presence_state: str = "unknown"         # classify_presence_result() output
        self.hourly_restart_count: int = 0                # restarts in last 60 minutes
        self.failed_reason: str = ""                      # set when Failed state is entered
        # Presence-based controlled relaunch tracking
        self._presence_suspicious_count: int = 0          # consecutive offline results
        self._presence_suspicious_window_start: float = 0 # when window opened
        self._last_presence_relaunch_at: float = 0        # last presence-triggered relaunch
        self._presence_relaunch_times: list[float] = []   # for hourly cap

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
        now = time.time()
        self._restart_times.append(now)
        self.hourly_restart_count = len([t for t in self._restart_times if now - t < 3600])

    def _can_auto_reopen(self) -> bool:
        return bool(self.entry.get("auto_reopen_enabled", True)) and bool(self._sup().get("auto_reopen_enabled", True))

    def _can_auto_reconnect(self) -> bool:
        return bool(self.entry.get("auto_reconnect_enabled", True)) and bool(self._sup().get("auto_reconnect_enabled", True))

    def _presence_relaunch_cooldown_ok(self) -> bool:
        """Return True if enough time has passed since the last presence-triggered relaunch."""
        if not self._last_presence_relaunch_at:
            return True
        return (time.time() - self._last_presence_relaunch_at) >= PRESENCE_RELAUNCH_COOLDOWN_SECONDS

    def _presence_relaunch_budget_ok(self) -> bool:
        """Return True if the per-hour presence-relaunch cap has not been reached."""
        now = time.time()
        self._presence_relaunch_times = [
            t for t in self._presence_relaunch_times if now - t < 3600
        ]
        return len(self._presence_relaunch_times) < PRESENCE_RELAUNCH_MAX_PER_HOUR

    def _should_relaunch_from_presence(self, running: bool) -> bool:
        """Return True only if all conditions for a presence-based relaunch are met.

        Conditions (all required):
        - process is alive (running=True)
        - userId is configured for this package
        - presence suspicious count reached threshold within window
        - cooldown since last presence relaunch
        - hourly cap not exceeded
        - normal restart budget also has room
        """
        if not running:
            return False
        if not self._roblox_user_id:
            return False
        now = time.time()
        # Window expired — reset counter
        if (now - self._presence_suspicious_window_start) > PRESENCE_SUSPICIOUS_WINDOW_SECONDS:
            return False
        if self._presence_suspicious_count < PRESENCE_SUSPICIOUS_CONFIRMATIONS:
            return False
        if not self._presence_relaunch_cooldown_ok():
            return False
        if not self._presence_relaunch_budget_ok():
            return False
        if not self._restart_budget_ok():
            return False
        return True

    def _record_presence_relaunch(self) -> None:
        now = time.time()
        self._last_presence_relaunch_at = now
        self._presence_relaunch_times.append(now)
        self._presence_suspicious_count = 0
        self._presence_suspicious_window_start = 0

    def _fetch_roblox_presence(self) -> Any:
        """Return a :class:`PresenceResult` for the configured account, or None.

        Resolves username → userId once (cached), then asks Roblox's public
        presence endpoint.  Never raises.  Returns None when no username /
        no user_id is configured or the API was unreachable AND we have no
        cached presence — in that case the supervisor falls back to its
        local heuristics (process-alive check).

        Always updates ``last_presence_check_at`` and ``last_presence_state``
        so the supervisor can expose them for diagnostics without blocking.
        """
        try:
            from . import roblox_presence as _rp
        except Exception:  # noqa: BLE001
            self.last_presence_state = "unavailable"
            return None
        try:
            if not self._roblox_user_id:
                if not self._roblox_username:
                    self.last_presence_state = "unavailable"
                    return None
                uid = _rp.lookup_user_id(self._roblox_username)
                if uid:
                    self._roblox_user_id = uid
            if not self._roblox_user_id:
                self.last_presence_state = "unavailable"
                return None
            pres = _rp.fetch_presence_one(
                self._roblox_user_id, cookie=self._roblox_cookie,
            )
            self.last_presence_check_at = time.time()
            # Classify and store the presence state using the module helper.
            classified = _rp.classify_presence_result(pres)
            self.last_presence_state = classified
            if pres is None or pres.is_unknown:
                return None
            return pres
        except Exception as exc:  # noqa: BLE001
            self.last_presence_state = "unavailable"
            log_event(
                self.logger, "debug", "roblox_presence_error",
                package=self.package, error=str(exc),
            )
            return None

    def _post_launch_state(self) -> str:
        """Process confirmed alive after launch → Online.

        Kaeru-style supervision (probe p-f1a4aaafe5): if the process is running,
        it is Online.  We no longer distinguish "in game" vs "lobby" via
        logcat/dumpsys because:
          1. The uiautomator probe caused SIGSEGV on Termux with App Cloner packages.
          2. Lobby detection caused an endless "Join Unconfirmed" re-send loop even
             when the user was actually playing (URL joined correctly).
          3. Kaeru works correctly with process-alive = Online logic.

        Relaunch only triggers when the process is completely dead (not running),
        not when it "might be at the lobby screen."
        """
        log_event(
            self.logger, "info", "post_launch_online",
            package=self.package,
            url_launched=self._url_launched,
        )
        return STATUS_ONLINE

    def run(self) -> None:
        # All worker setup wrapped: a thrown exception here would silently
        # kill this worker thread and the package would never reconnect.
        try:
            cfg = self.cfg
            sup = self._sup()
            interval = int(sup.get("health_check_interval_seconds") or cfg.get("health_check_interval_seconds", 30))
            grace = int(sup.get("launch_grace_seconds") or cfg.get("foreground_grace_seconds", 30))
            backoff_base = int(sup.get("restart_backoff_seconds", 10))
            # Determine URL awareness once at startup (Kaeru: desired_url)
            from .config import effective_private_server_url as _epsu
            self.desired_url = str(_epsu(self.entry, cfg) or "").strip()
            self.has_private_url = bool(self.desired_url)
            # Launching/Joining timeout: how many seconds before forcing a re-check
            _launching_timeout = max(90, grace * 4)

            # Roblox presence setup — read username + optional user_id/cookie
            # from the per-package entry, then resolve via the public API.
            self._roblox_username = str(self.entry.get("account_username") or "").strip()
            try:
                uid_raw = self.entry.get("roblox_user_id")
                if isinstance(uid_raw, int) and uid_raw > 0:
                    self._roblox_user_id = uid_raw
                elif isinstance(uid_raw, str) and uid_raw.isdigit():
                    self._roblox_user_id = int(uid_raw)
            except Exception:  # noqa: BLE001
                self._roblox_user_id = None
            self._roblox_cookie = (str(self.entry.get("roblox_cookie") or "").strip() or None)
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
                if not sup.get("enabled", True):
                    self._set_status(STATUS_OFFLINE, "supervisor disabled")
                    db.insert_heartbeat("disabled", {"package": self.package})
                    self._sleep(interval)
                    continue

                # ── Launching timeout guard ───────────────────────────────────
                # STATUS_JOINING is no longer set by live paths (Kaeru-style).
                # Only STATUS_LAUNCHING is used post-relaunch.
                current_st = self.status_map.get(self.package, "")
                if self.launching_since is not None and current_st in {STATUS_LAUNCHING, STATUS_JOINING}:
                    elapsed_since_launch = time.time() - self.launching_since
                    if elapsed_since_launch > _launching_timeout:
                        # App hasn't become healthy after 4× grace — force-check now
                        timeout_health = check_package_health(cfg, self.package)
                        if timeout_health.state == "healthy":
                            target = self._post_launch_state()
                            self._set_status(target, "Launch timeout — app confirmed running")
                        else:
                            self.failed_reason = "Launch timeout — app not responding"
                            self._set_status(STATUS_FAILED, self.failed_reason)
                        self.launching_since = None
                        # Skip normal health check this iteration — we just ran one
                        self._sleep(min(5, interval))
                        continue

                # Save state before we run the slow probes.  We do NOT
                # overwrite the public status with STATUS_CHECKING here —
                # doing so makes the row visibly flap to "Preparing" on
                # every tick and looks broken to the user.  Only show
                # Preparing when we genuinely have no idea yet (first
                # iteration before any state was established).
                prev_before_check = self.status_map.get(self.package, "")
                if not prev_before_check or prev_before_check in (
                    STATUS_UNKNOWN, "",
                ):
                    self._set_status(STATUS_CHECKING, "")
                health = check_package_health(cfg, self.package)

                # Feed the heartbeat tracker.  Use the full evidence dict so
                # decide() can see process / window / surface / foreground
                # signals independently — not just the collapsed `running`
                # field that older code paths used.
                _meta = health.meta if isinstance(health.meta, dict) else {}
                evidence_now = {
                    "running":      bool(_meta.get("running")),
                    "root_running": bool(_meta.get("root_running")),
                    "task":         bool(_meta.get("task")),
                    "window":       bool(_meta.get("window")),
                    "surface":      bool(_meta.get("surface")),
                    "foreground":   bool(_meta.get("fg_evidence")),
                }
                self._last_evidence = evidence_now
                if self._state_tracker is not None:
                    try:
                        self._state_tracker.observe(self.package, evidence_now)
                    except Exception as _exc:  # noqa: BLE001
                        log_event(
                            self.logger, "debug", "state_tracker_observe_error",
                            package=self.package, error=str(_exc),
                        )

                # ── Roblox presence (ground truth) ───────────────────────
                # Ask Roblox directly: is the configured account InGame /
                # Online / Offline / InStudio?  When the public presence
                # endpoint says InGame, the supervisor uses that as the
                # *authoritative* state regardless of local dumpsys output.
                # This is what fixes the "stuck in Preparing" symptom: even
                # when our local checks are slow or wrong, the truthful
                # state still surfaces every interval.
                presence = self._fetch_roblox_presence()
                if presence is not None:
                    if presence.is_in_game:
                        self._set_status(
                            STATUS_ONLINE,
                            f"Roblox presence: InGame ({presence.last_location[:40]})",
                        )
                        self.failure_count = 0
                        self.unhealthy_since = None
                        self.grace_start = None
                        self.bg_since = None
                        self.launching_since = None
                        self.online_since = self.online_since or time.time()
                        self.last_seen_at = time.time()
                        self._last_presence_label = presence.presence_type.label
                        # In-game confirms the account is active — clear suspicious counter
                        self._presence_suspicious_count = 0
                        self._presence_suspicious_window_start = 0
                        self._sleep(interval)
                        continue
                    # Online (lobby) — Kaeru-style: record internally but do NOT
                    # set Joining or Lobby as public state.  The process-alive
                    # check below will determine the public Online/Reopening/Failed.
                    # This eliminates the old "Joining loop" caused by presence
                    # lobby detection triggering the 120s URL resend timer.
                    if presence.is_lobby:
                        self._last_presence_label = presence.presence_type.label
                        self.last_seen_at = time.time()
                        # Fall through to process health check (no continue).
                    if presence.is_offline:
                        # Authoritative Offline.  Accumulate the suspicious counter.
                        # We do NOT immediately relaunch — that would recreate the
                        # old "Joining loop".  Controlled relaunch only happens after
                        # PRESENCE_SUSPICIOUS_CONFIRMATIONS hits within the window,
                        # cooldown passed, and hourly cap respected.
                        self._last_presence_label = presence.presence_type.label
                        now_p = time.time()
                        if (now_p - self._presence_suspicious_window_start) > PRESENCE_SUSPICIOUS_WINDOW_SECONDS:
                            # Start a fresh window
                            self._presence_suspicious_count = 1
                            self._presence_suspicious_window_start = now_p
                        else:
                            self._presence_suspicious_count += 1
                        # Check if threshold is met for a controlled presence relaunch.
                        # Only trigger when process appears alive (avoid double-relaunch
                        # with the normal dead-process path below).
                        _cur_running = bool((health.meta or {}).get("running") if isinstance(health.meta, dict) else False)
                        if self._should_relaunch_from_presence(_cur_running) and self._can_auto_reconnect():
                            log_event(
                                self.logger, "info", "presence_controlled_relaunch",
                                package=self.package,
                                suspicious_count=self._presence_suspicious_count,
                                user_id=self._roblox_user_id,
                            )
                            self._record_presence_relaunch()
                            _reapply_layout_for_package(self.package)
                            _pkg_cfg = dict(cfg)
                            _pkg_cfg["roblox_package"] = self.package
                            _pr = perform_rejoin(
                                _pkg_cfg, reason="presence_offline_controlled",
                                package_entry=self.entry,
                            )
                            self._record_restart()
                            if _pr.success:
                                self._url_launched = self.has_private_url
                                self.launching_since = time.time()
                                self._set_status(STATUS_LAUNCHING, "Presence offline — controlled relaunch")
                                _reapply_layout_for_package(self.package)
                            else:
                                self._set_status(STATUS_WARNING, "Presence relaunch failed")
                            self._sleep(max(int(cfg.get("reconnect_delay_seconds", 8)), interval))
                            continue
                        # Fall through so the existing "no evidence" path
                        # decides between Reconnecting / Offline.
                    # InStudio / Invisible / Unknown → fall through (reset suspicious on in_game handled above).

                if health.state == "healthy":
                    self.failure_count = 0
                    self.unhealthy_since = None
                    self.grace_start = None
                    self.bg_since = None
                    now_ts = time.time()
                    if self.online_since is None:
                        self.online_since = now_ts
                    self.last_seen_at = now_ts   # Kaeru: last_seen_alive_at
                    # Track foreground evidence (Kaeru: last_foreground_at)
                    _meta_fg = (health.meta or {}) if isinstance(health.meta, dict) else {}
                    if _meta_fg.get("fg_evidence") or _meta_fg.get("foreground"):
                        self.last_foreground_at = now_ts
                    # Promote from Launching/Joining to an appropriate healthy state.
                    # Use prev_before_check because STATUS_CHECKING is transient.
                    self.launching_since = None
                    if prev_before_check in {STATUS_LAUNCHING, STATUS_JOINING}:
                        # Kaeru-style: process alive = Online (stable rebuild p-9e3f2a8d1c).
                        # STATUS_JOINING is a legacy alias; live paths now use STATUS_LAUNCHING.
                        target = self._post_launch_state()
                    elif prev_before_check in _HEALTHY_STATES:
                        target = prev_before_check  # stay in current healthy state
                    else:
                        target = STATUS_ONLINE
                    self._set_status(target, "Heartbeat OK")
                    db.insert_heartbeat("healthy", {"package": self.package})
                    log_event(self.logger, "info", "heartbeat", status="healthy", package=self.package)

                    # ── Join Unconfirmed timeout: re-launch with URL ──────────
                    # If Roblox has been "healthy" but stuck at Join Unconfirmed
                    # for too long (share code expired, lobby landed, auth
                    # needed), re-send the private URL to put the user back in
                    # the server.  Only triggers when a URL was launched AND
                    # the package has been in this state past the timeout.
                    current_after = self.status_map.get(self.package, "")
                    if (
                        current_after == STATUS_JOIN_UNCONFIRMED
                        and self.has_private_url
                        and self._can_auto_reconnect()
                        and self._restart_budget_ok()
                    ):
                        if self.join_unconfirmed_since is None:
                            self.join_unconfirmed_since = now_ts
                        elif (now_ts - self.join_unconfirmed_since) > _JOIN_UNCONFIRMED_RELAUNCH_SECONDS:
                            log_event(
                                self.logger, "info", "join_unconfirmed_relaunch",
                                package=self.package,
                                elapsed_seconds=int(now_ts - self.join_unconfirmed_since),
                            )
                            self.join_unconfirmed_since = None
                            self._set_status(STATUS_LAUNCHING, "Re-sending private server URL")
                            _reapply_layout_for_package(self.package)
                            pkg_cfg = dict(cfg)
                            pkg_cfg["roblox_package"] = self.package
                            result = perform_rejoin(
                                pkg_cfg, reason="join_unconfirmed_retry",
                                package_entry=self.entry, no_force_stop=True,
                            )
                            self._record_restart()
                            if result.success:
                                self._url_launched = True
                                self.launching_since = time.time()
                            else:
                                self._set_status(STATUS_WARNING, "URL re-launch failed")
                    else:
                        if current_after != STATUS_JOIN_UNCONFIRMED:
                            self.join_unconfirmed_since = None

                    self._sleep(interval)
                    continue

                if health.state in {"network_down", "roblox_not_installed"}:
                    self._set_status(STATUS_WARNING, health.message)
                    self._sleep(interval)
                    continue

                running = bool(_meta.get("running"))
                fg = _meta.get("foreground")
                disc = _meta.get("disconnect_category")
                fg_wrong = running and health.state == "roblox_not_running" and (
                    fg not in (self.package, None) or bool(disc)
                )

                if fg_wrong and self._can_auto_reconnect():
                    now = time.time()
                    if self.bg_since is None:
                        self.bg_since = now
                    if now - self.bg_since < grace * 2:
                        # A "disconnect_category" hit from the logcat /
                        # dumpsys text scan is a concrete error-code event
                        # — show it as Disconnected so the user knows the
                        # tool *saw* the failure (not a silent timeout).
                        if disc:
                            self._set_status(
                                STATUS_DISCONNECTED, f"signal: {disc}",
                            )
                        else:
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
                    result = perform_rejoin(pkg_cfg, reason="disconnected", package_entry=self.entry)
                    self._record_restart()
                    if result.success:
                        self.last_launch_at = time.time()   # Kaeru: last_launch_at
                        self._url_launched = self.has_private_url
                        self.launching_since = self.last_launch_at
                        # Kaeru-style: always Launching regardless of URL presence.
                        # _STATE_DISPLAY_MAP shows this as "Launching" publicly.
                        self._set_status(STATUS_LAUNCHING, "Reopened — launch command sent")
                        # Reapply once more after launch (clone window may have
                        # been recreated by App Cloner with default bounds).
                        _reapply_layout_for_package(self.package)
                    else:
                        self.failure_count += 1
                        self.last_error = result.error
                        self.failed_reason = result.error or "launch failed"
                        self._set_status(STATUS_FAILED, result.error or "launch failed")
                    self._sleep(max(int(cfg.get("reconnect_delay_seconds", 8)), interval))
                    continue

                self.bg_since = None

                if running:
                    self._sleep(interval)
                    continue

                # ── Heartbeat tracker override ────────────────────────────
                # If evidence is missing right now but a recent heartbeat
                # (process / window / surface / foreground) existed within
                # the grace window, do NOT show Offline.  This is the
                # critical fix for the cloud-phone case where Roblox is
                # visibly playing but our detection blinks because the
                # cloned process name is too long for ``pidof`` to see.
                if self._state_tracker is not None:
                    try:
                        decision = self._state_tracker.decide(
                            self.package,
                            prev_before_check,
                            evidence_now,
                            url_launched=self._url_launched,
                            attempt_count=self.failure_count,
                            max_attempts=int(self._sup().get("max_failed_attempts", 0)),
                        )
                    except Exception as _exc:  # noqa: BLE001
                        log_event(
                            self.logger, "debug", "state_tracker_decide_error",
                            package=self.package, error=str(_exc),
                        )
                        decision = None
                    if decision is not None and decision.state not in (
                        # If tracker says anything OTHER than Offline/Failed,
                        # trust it: we have recent evidence the package is
                        # alive in some form.
                        "Offline", "Failed",
                    ):
                        # Map the tracker's label to public constants.
                        # Kaeru-style: all alive/uncertain states collapse to
                        # STATUS_ONLINE; _STATE_DISPLAY_MAP in commands.py
                        # provides the final user-facing label.
                        _label_map = {
                            "Playing":          STATUS_ONLINE,
                            "In Server":        STATUS_ONLINE,
                            "Online":           STATUS_ONLINE,
                            "Lobby":            STATUS_ONLINE,
                            "Background":       STATUS_ONLINE,
                            "Join Unconfirmed": STATUS_ONLINE,
                            "Recovering":       STATUS_RECONNECTING,
                            "Unknown":          STATUS_ONLINE,
                        }
                        new_st = _label_map.get(decision.state, STATUS_BACKGROUND)
                        self._set_status(new_st, decision.reason)
                        # Reset the grace clock — we still have a heartbeat.
                        self.grace_start = None
                        self._consecutive_offline_checks = 0
                        self._sleep(interval)
                        continue

                now = time.time()
                if self.grace_start is None:
                    self.grace_start = now
                elapsed = now - self.grace_start

                if elapsed < grace:
                    # During the grace window, show Dead immediately so the
                    # user sees the package is gone.  Reconnection starts
                    # once the grace period expires.
                    self._set_status(STATUS_DEAD, f"process gone ({int(grace - elapsed)}s until rejoin)")
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
                result = perform_rejoin(pkg_cfg, reason="process_missing", package_entry=self.entry)
                self._record_restart()

                if result.success:
                    self.failure_count = 0
                    self.revive_count += 1
                    self.online_since = None
                    self.last_launch_at = time.time()   # Kaeru: last_launch_at
                    self._url_launched = self.has_private_url
                    self.launching_since = self.last_launch_at
                    # Kaeru-style: always Launching regardless of URL presence.
                    self._set_status(STATUS_LAUNCHING, "Reopened — launch command sent")
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
