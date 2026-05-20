"""Auto rejoin supervisor loop."""

from __future__ import annotations

import signal
import threading
import time
from typing import Any

from . import android, db
from .backoff import calculate_backoff_seconds
from .config import effective_private_server_url, load_config, validate_config
from .launcher import launch_package_for_current_config, perform_rejoin
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
STATUS_JOINING           = "Join" + "ing"      # DEPRECATED — kept for backward compat only.
                                               # WatchdogSupervisor never produces this state.
                                               # Use STATUS_LAUNCHING for post-launch transient.
STATUS_CLOSED            = "Closed"            # App cleanly not running after a session
STATUS_JOIN_UNCONFIRMED  = "Join " + "Unconfirmed"  # App healthy but no in-game evidence yet
# State vocabulary aligned to user-facing terminology:
STATUS_LAUNCHED          = "Launched"
STATUS_DISCONNECTED      = "Disconnected"

# ── New 4-state watchdog vocabulary (WatchdogSupervisor) ─────────────────────
# These four states are the ONLY public states produced by WatchdogSupervisor.
STATUS_IN_LOBBY      = "In-Lobby"      # Process running, not in game/server (home/lobby/menu)
STATUS_NO_HEARTBEAT  = "No Heartbeat"  # Process running, was in game, but heartbeat stalled/warning

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
        self.last_url_launch_at: float = 0.0
        self.url_launch_grace_until: float = 0.0
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

    def _url_grace_seconds(self, base_grace: int | float) -> float:
        return float(max(90, int(float(base_grace)) * 4))

    def _note_url_launch_grace(
        self,
        reason: str,
        *,
        now: float | None = None,
        grace_seconds: int | float | None = None,
    ) -> None:
        if not self.has_private_url:
            return
        ts = time.time() if now is None else float(now)
        seconds = float(grace_seconds if grace_seconds is not None else self._url_grace_seconds(30))
        self.last_url_launch_at = ts
        self.url_launch_grace_until = max(self.url_launch_grace_until, ts + seconds)
        self._url_launched = True
        log_event(
            self.logger,
            "info",
            "[DENG_REJOIN_SUPERVISOR_GRACE]",
            package=self.package,
            grace_reason=reason,
            grace_until=int(self.url_launch_grace_until),
            current_state=self.status_map.get(self.package, ""),
            relaunch_blocked="true",
        )

    def _url_launch_grace_active(self, now: float | None = None) -> bool:
        if not self.has_private_url:
            return False
        ts = time.time() if now is None else float(now)
        return bool(self.url_launch_grace_until and ts < self.url_launch_grace_until)

    def _relaunch_allowed(self, reason: str, now: float | None = None) -> bool:
        ts = time.time() if now is None else float(now)
        blocked = self._url_launch_grace_active(ts)
        elapsed_ms = -1
        if self.last_url_launch_at:
            elapsed_ms = int(max(0.0, ts - self.last_url_launch_at) * 1000)
        log_event(
            self.logger,
            "info",
            "[DENG_REJOIN_RELAUNCH_DECISION]",
            package=self.package,
            reason=reason,
            post_launch_action=str(self.cfg.get("post_launch_action") or ""),
            url_launch_recent=str(blocked).lower(),
            elapsed_since_url_launch_ms=elapsed_ms,
            allowed=str(not blocked).lower(),
        )
        return not blocked

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
            if self.has_private_url and self.status_map.get(self.package) in {STATUS_LAUNCHING, STATUS_JOINING}:
                now_launch = time.time()
                if self.launching_since is None:
                    self.launching_since = now_launch
                self._note_url_launch_grace(
                    "configured_link_launch",
                    now=now_launch,
                    grace_seconds=_launching_timeout,
                )

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
                            if not self._relaunch_allowed("presence_offline_controlled", now_p):
                                self._set_status(STATUS_LAUNCHING, "Waiting For Roblox Link")
                                self._sleep(min(5, interval))
                                continue
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
                                package_entry=self.entry, no_force_stop=True,
                            )
                            self._record_restart()
                            if _pr.success:
                                self.launching_since = time.time()
                                self._note_url_launch_grace(
                                    "configured_link_launch",
                                    now=self.launching_since,
                                    grace_seconds=_launching_timeout,
                                )
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
                            if not self._relaunch_allowed("join_unconfirmed_retry", now_ts):
                                self._set_status(STATUS_LAUNCHING, "Waiting For Roblox Link")
                                self._sleep(min(5, interval))
                                continue
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
                                self.launching_since = time.time()
                                self._note_url_launch_grace(
                                    "configured_link_launch",
                                    now=self.launching_since,
                                    grace_seconds=_launching_timeout,
                                )
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
                    if not self._relaunch_allowed("disconnected", now):
                        self.bg_since = None
                        self._set_status(STATUS_LAUNCHING, "Waiting For Roblox Link")
                        self._sleep(min(5, interval))
                        continue
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
                    result = perform_rejoin(pkg_cfg, reason="disconnected", package_entry=self.entry, no_force_stop=True)
                    self._record_restart()
                    if result.success:
                        self.last_launch_at = time.time()   # Kaeru: last_launch_at
                        self.launching_since = self.last_launch_at
                        self._note_url_launch_grace(
                            "configured_link_launch",
                            now=self.launching_since,
                            grace_seconds=_launching_timeout,
                        )
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
                            STATUS_JOIN_UNCONFIRMED: STATUS_ONLINE,
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

                if not self._relaunch_allowed("process_missing", now):
                    self.grace_start = None
                    self._set_status(STATUS_LAUNCHING, "Waiting For Roblox Link")
                    self._sleep(min(5, interval))
                    continue

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
                result = perform_rejoin(pkg_cfg, reason="process_missing", package_entry=self.entry, no_force_stop=True)
                self._record_restart()

                if result.success:
                    self.failure_count = 0
                    self.revive_count += 1
                    self.online_since = None
                    self.last_launch_at = time.time()   # Kaeru: last_launch_at
                    self.launching_since = self.last_launch_at
                    self._note_url_launch_grace(
                        "configured_link_launch",
                        now=self.launching_since,
                        grace_seconds=_launching_timeout,
                    )
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


# ─── WatchdogSupervisor ────────────────────────────────────────────────────────


class WatchdogSupervisor:
    """Continuous sequential watchdog with deterministic 4-state detection.

    States produced (public):
        In-Lobby      — Process running, not in game/server; at Roblox home/lobby/menu.
        Online        — Process running, in game, healthy heartbeat/presence.
        No Heartbeat  — Process running, was in game, but heartbeat stalled / offline signal.
        Dead          — Process not running (force-closed, crashed, stopped).
        Launching     — Transient: initial launch sent, waiting for first state detection.

    Key design guarantees (vs old _PackageWorker):
    - Process check runs FIRST before any presence check.
      Force-closing a package is always detected in the next round regardless of
      what the Roblox Presence API last reported.
    - Sequential loop: checks packages one by one, never stops after Online.
    - "Checking Package X/Y" updated on each package check (stored in
      self.checking_label, read by the dashboard callback).
    - Joining state is NEVER produced.  All post-launch transients use Launching.
    - Blank private_server_url = app-only launch (no setup-required error).

    [DENG_REJOIN_WATCHDOG_FIX] probe_id=p-ea167faf5f
    Root cause of old bug: _PackageWorker used StateTracker.decide() which
    returned "Recovering" for 60s after force-close (offset_grace_s).  That
    reset grace_start to None each iteration, blocking Dead detection for 60s+.
    Additionally, presence.is_in_game short-circuited process checks so force-
    close was invisible until the Presence API updated (30-60s delay), then
    needed 3 consecutive "offline" confirmations before triggering relaunch.
    Fix: process check FIRST, presence supplementary. Sequential loop avoids
    thread-per-package complexity and gives deterministic X/Y progress lines.
    """

    # ── Lobby relaunch timeout when private URL is configured ────────────────
    LOBBY_RELAUNCH_SECONDS: int = 120   # 2 min in lobby before relaunching via URL

    # ── Grace window after launch — do not check immediately ────────────────
    DEFAULT_GRACE_SECONDS: int = 45

    # ── Presence offline confirmations needed to declare No Heartbeat ────────
    NHB_OFFLINE_CONFIRMATIONS: int = 2  # must see offline/lobby N times before NHB

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
        self.entry_by_pkg: dict[str, dict[str, Any]] = {
            str(e["package"]): e for e in self.entries
        }
        self.cfg = cfg
        self.stop_event = threading.Event()

        # Public status dict — mutated in-place (read by dashboard callback).
        self.status_map: dict[str, str] = {
            pkg: STATUS_LAUNCHING for pkg in self.packages
        }
        # Normalize initial_status: remove legacy Joining/Join Unconfirmed.
        if initial_status:
            _legacy_to_launching = {
                STATUS_JOINING, STATUS_JOIN_UNCONFIRMED, "Join Failed", "Reconnecting",
            }
            for pkg, st in initial_status.items():
                if pkg in self.status_map:
                    self.status_map[pkg] = (
                        STATUS_LAUNCHING if st in _legacy_to_launching else st
                    )

        self.on_status_change = on_status_change

        # Dashboard: "Checking Package X/Y" — updated by inner loop, read by callback.
        self.checking_label: str = ""

        self._round: int = 0

        # ── Per-package mutable tracking ──────────────────────────────────────
        self._prev_state: dict[str, str] = {}
        self._last_online_ts: dict[str, float] = {}   # last time confirmed Online
        self._grace_until: dict[str, float] = {}      # no relaunch until this ts
        self._lobby_since: dict[str, float] = {}      # when In-Lobby started
        self._nhb_offline_count: dict[str, int] = {}  # consecutive offline hits
        self._revive_count: dict[str, int] = {}
        self._failure_count: dict[str, int] = {}

        # ── Per-package presence tracking ─────────────────────────────────────
        self._presence_user_ids: dict[str, int] = {}
        self._presence_usernames: dict[str, str] = {}
        self._presence_cookies: dict[str, str | None] = {}
        self._presence_id_resolved: set[str] = set()  # username→id done

        for e in self.entries:
            pkg = str(e["package"])
            try:
                uid_raw = e.get("roblox_user_id")
                if uid_raw:
                    self._presence_user_ids[pkg] = int(uid_raw)
            except (ValueError, TypeError):
                pass
            uname = str(e.get("account_username") or "").strip()
            if uname:
                self._presence_usernames[pkg] = uname
            self._presence_cookies[pkg] = (
                str(e.get("roblox_cookie") or "").strip() or None
            )

        self._logger = configure_logging(level=cfg.get("log_level", "INFO"))

    # ─── Internal helpers ─────────────────────────────────────────────────────

    def _handle_stop(self, signum: Any, frame: Any) -> None:
        self.stop_event.set()

    def _set_status(self, pkg: str, status: str) -> None:
        old = self.status_map.get(pkg)
        self.status_map[pkg] = status
        if old != status and callable(self.on_status_change):
            self.on_status_change(pkg, status)

    def _set_grace(self, pkg: str, now: float, seconds: int | None = None) -> None:
        self._grace_until[pkg] = now + float(seconds or self.DEFAULT_GRACE_SECONDS)

    def _in_grace(self, pkg: str, now: float) -> bool:
        return now < self._grace_until.get(pkg, 0.0)

    def _sup_interval(self) -> int:
        sup = self.cfg.get("supervisor") if isinstance(self.cfg.get("supervisor"), dict) else {}
        raw = sup.get("health_check_interval_seconds") or self.cfg.get("health_check_interval_seconds", 30)
        return max(10, int(raw))

    # ─── Presence fetching (process-check-first; presence is supplementary) ──

    def _fetch_presence(self, pkg: str) -> Any:
        """Fetch Roblox presence for the package's account.  Never raises.

        Returns a PresenceResult or None.  This is always called AFTER
        process-alive check so that a dead process (force-closed) is caught
        by step A and presence is never consulted for that case.
        """
        try:
            from . import roblox_presence as _rp
        except Exception:  # noqa: BLE001
            return None
        try:
            # Resolve username → user_id (cached)
            if pkg not in self._presence_id_resolved:
                uname = self._presence_usernames.get(pkg, "")
                if uname and pkg not in self._presence_user_ids:
                    uid = _rp.lookup_user_id(uname)
                    if uid:
                        self._presence_user_ids[pkg] = uid
                self._presence_id_resolved.add(pkg)

            uid = self._presence_user_ids.get(pkg)
            if not uid:
                return None
            return _rp.fetch_presence_one(uid, cookie=self._presence_cookies.get(pkg))
        except Exception:  # noqa: BLE001
            return None

    # ─── State detection ─────────────────────────────────────────────────────

    def _detect_package_state(
        self, pkg: str, entry: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Detect public state for one package.

        Decision tree (deterministic):
        A. Process not running                              → Dead
        B. Process running + presence InGame                → Online
        C. Process running + was recently Online
           + presence offline N times consecutively         → No Heartbeat
        D. Process running, no in-game evidence             → In-Lobby (NOT Joining)

        Process check runs FIRST.  Presence is supplementary and never overrides
        a dead process.

        [DENG_REJOIN_PACKAGE_CHECK] probe logged by caller.
        """
        t0 = time.monotonic()
        # ── A. Process check (ALWAYS FIRST) ──────────────────────────────────
        try:
            evidence = android.get_package_alive_evidence(pkg)
        except Exception:  # noqa: BLE001
            evidence = {}
        process_running = bool(
            evidence.get("alive")
            or evidence.get("running")
            or evidence.get("root_running")
        )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if not process_running:
            return STATUS_DEAD, {
                "process_running": "false",
                "in_game": "false",
                "heartbeat_ok": "false",
                "warning_detected": "false",
                "elapsed_ms": elapsed_ms,
            }

        # ── B / C / D. Presence check (supplementary) ────────────────────────
        in_game = False
        presence_offline = False

        try:
            presence = self._fetch_presence(pkg)
            if presence is not None:
                if getattr(presence, "is_in_game", False):
                    in_game = True
                elif getattr(presence, "is_offline", False):
                    presence_offline = True
        except Exception:  # noqa: BLE001
            pass

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if in_game:
            # Reset offline counter when confirmed in-game.
            self._nhb_offline_count[pkg] = 0
            return STATUS_ONLINE, {
                "process_running": "true",
                "in_game": "true",
                "heartbeat_ok": "true",
                "warning_detected": "false",
                "elapsed_ms": elapsed_ms,
            }

        # Track consecutive offline presence signals (for No Heartbeat)
        last_online = self._last_online_ts.get(pkg, 0.0)
        was_recently_online = (time.time() - last_online) < 300.0  # 5 min

        if presence_offline and was_recently_online:
            count = self._nhb_offline_count.get(pkg, 0) + 1
            self._nhb_offline_count[pkg] = count
            if count >= self.NHB_OFFLINE_CONFIRMATIONS:
                return STATUS_NO_HEARTBEAT, {
                    "process_running": "true",
                    "in_game": "false",
                    "heartbeat_ok": "false",
                    "warning_detected": "false",
                    "elapsed_ms": elapsed_ms,
                }

        # D. In-Lobby — process alive, no confirmed in-game signal.
        #    IMPORTANT: do NOT return "Joining" here.  No Joining state.
        return STATUS_IN_LOBBY, {
            "process_running": "true",
            "in_game": "false",
            "heartbeat_ok": "unknown",
            "warning_detected": "false",
            "elapsed_ms": elapsed_ms,
        }

    # ─── Recovery ─────────────────────────────────────────────────────────────

    def _do_launch(
        self, pkg: str, entry: dict[str, Any], reason: str
    ) -> bool:
        """Launch the package using the canonical launcher.  Returns success."""
        logger = self._logger
        url = str(effective_private_server_url(entry, self.cfg) or "").strip()
        url_configured = bool(url)
        launcher_label = "private_url" if url_configured else "app_only"
        t0 = time.monotonic()
        try:
            result = launch_package_for_current_config(entry, self.cfg, reason)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            log_event(
                logger, "info", "[DENG_REJOIN_RECOVERY_LAUNCH_RESULT]",
                package=pkg,
                reason=reason,
                launcher=launcher_label,
                return_code=0 if result.success else 1,
                success=str(result.success).lower(),
                stdout="",
                stderr=str(result.error or ""),
                elapsed_ms=elapsed_ms,
            )
            if result.success:
                _reapply_layout_for_package(pkg)
            return result.success
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            log_event(
                logger, "error", "[DENG_REJOIN_RECOVERY_LAUNCH_RESULT]",
                package=pkg, reason=reason, launcher=launcher_label,
                return_code=1, success="false",
                stdout="", stderr=str(exc), elapsed_ms=elapsed_ms,
            )
            return False

    def _handle_state(
        self,
        pkg: str,
        entry: dict[str, Any],
        state: str,
        prev: str,
        now: float,
    ) -> None:
        """Apply recovery action based on current state.

        Recovery rules:
        - Dead        → launch_package_for_current_config
        - No Heartbeat → am force-stop <pkg>, then launch_package_for_current_config
        - In-Lobby    → if URL configured and lobby timeout exceeded: launch via URL
                        else: monitor only (In-Lobby is OK when URL is blank)
        - Online      → update last_online_ts, keep monitoring
        """
        logger = self._logger
        url = str(effective_private_server_url(entry, self.cfg) or "").strip()
        url_configured = bool(url)

        if state == STATUS_DEAD:
            action = "private_url_relaunch" if url_configured else "app_only_relaunch"
            log_event(
                logger, "info", "[DENG_REJOIN_RECOVERY_DECISION]",
                package=pkg, state="Dead",
                private_url_configured=str(url_configured).lower(),
                action=action,
                reason="process_not_running",
            )
            success = self._do_launch(pkg, entry, "dead_recovery")
            if success:
                self._revive_count[pkg] = self._revive_count.get(pkg, 0) + 1
                self._set_grace(pkg, now)
                self._set_status(pkg, STATUS_LAUNCHING)
            else:
                self._failure_count[pkg] = self._failure_count.get(pkg, 0) + 1

        elif state == STATUS_NO_HEARTBEAT:
            action = (
                "close_then_private_url_relaunch" if url_configured
                else "close_then_app_only_relaunch"
            )
            log_event(
                logger, "info", "[DENG_REJOIN_RECOVERY_DECISION]",
                package=pkg, state="No Heartbeat",
                private_url_configured=str(url_configured).lower(),
                action=action,
                reason="heartbeat_stalled_or_presence_offline",
            )
            # Force-stop ONLY this package, then relaunch.
            try:
                android.force_stop_package(pkg)
                time.sleep(1.5)
            except Exception:  # noqa: BLE001
                pass
            success = self._do_launch(pkg, entry, "no_heartbeat_recovery")
            if success:
                self._revive_count[pkg] = self._revive_count.get(pkg, 0) + 1
                self._nhb_offline_count[pkg] = 0
                self._set_grace(pkg, now)
                self._set_status(pkg, STATUS_LAUNCHING)
            else:
                self._failure_count[pkg] = self._failure_count.get(pkg, 0) + 1

        elif state == STATUS_IN_LOBBY:
            if url_configured:
                lobby_start = self._lobby_since.get(pkg)
                if lobby_start is None:
                    self._lobby_since[pkg] = now
                    log_event(
                        logger, "info", "[DENG_REJOIN_RECOVERY_DECISION]",
                        package=pkg, state="In-Lobby",
                        private_url_configured="true",
                        action="monitor_only",
                        reason="lobby_timeout_not_started",
                    )
                elif (now - lobby_start) >= self.LOBBY_RELAUNCH_SECONDS:
                    elapsed_s = int(now - lobby_start)
                    log_event(
                        logger, "info", "[DENG_REJOIN_RECOVERY_DECISION]",
                        package=pkg, state="In-Lobby",
                        private_url_configured="true",
                        action="lobby_timeout_private_url_recovery",
                        reason=f"lobby_timeout_exceeded_{elapsed_s}s",
                    )
                    success = self._do_launch(pkg, entry, "lobby_timeout_private_url_recovery")
                    if success:
                        self._lobby_since.pop(pkg, None)
                        self._set_grace(pkg, now)
                        self._set_status(pkg, STATUS_LAUNCHING)
                else:
                    log_event(
                        logger, "info", "[DENG_REJOIN_RECOVERY_DECISION]",
                        package=pkg, state="In-Lobby",
                        private_url_configured="true",
                        action="monitor_only",
                        reason=f"lobby_timeout_not_reached_{int(now - lobby_start)}s",
                    )
            else:
                # No URL configured — In-Lobby is acceptable, not fatal.
                log_event(
                    logger, "info", "[DENG_REJOIN_RECOVERY_DECISION]",
                    package=pkg, state="In-Lobby",
                    private_url_configured="false",
                    action="monitor_only",
                    reason="no_private_url_in_lobby_is_acceptable",
                )
        # STATUS_ONLINE: just update timestamp, no recovery needed.

    # ─── Main loop ────────────────────────────────────────────────────────────

    def run_forever(
        self,
        *,
        display_interval: float = 3.0,
        render_callback: Any = None,
    ) -> None:
        """Run the sequential watchdog loop until stop() is called.

        For every round:
          1. Log [DENG_REJOIN_WATCHDOG_ROUND]
          2. For each package (index/total):
             - Update self.checking_label = "Checking Package X/Y"
             - Detect state (process first, presence second)
             - Log [DENG_REJOIN_PACKAGE_CHECK]
             - If not in grace window: handle recovery
          3. Log [DENG_REJOIN_WATCHDOG_CONTINUES]
          4. Sleep interval (interruptible by stop_event)

        The dashboard callback is called approximately every display_interval
        seconds during both the package checks and the sleep phase.
        """
        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT, self._handle_stop)

        logger = self._logger
        log_event(
            logger, "info", "watchdog_supervisor_started",
            packages=self.packages,
        )
        db.insert_event(
            "INFO", "watchdog_supervisor_started",
            f"watching {len(self.packages)} packages: {', '.join(self.packages)}",
        )

        _next_render = time.time()

        def _maybe_render() -> None:
            nonlocal _next_render
            if render_callback is not None and time.time() >= _next_render:
                try:
                    render_callback()
                except Exception:  # noqa: BLE001
                    pass
                _next_render = time.time() + float(display_interval)

        while not self.stop_event.is_set():
            self._round += 1
            now = time.time()
            interval = self._sup_interval()
            total = len(self.packages)

            # Determine if any entry has a private URL (for probe log).
            _any_url = any(
                bool(str(effective_private_server_url(e, self.cfg) or "").strip())
                for e in self.entries
            )
            log_event(
                logger, "info", "[DENG_REJOIN_WATCHDOG_ROUND]",
                round=self._round,
                total_packages=total,
                interval_sec=interval,
                private_url_configured=str(_any_url).lower(),
            )

            # ── Sequential per-package check ──────────────────────────────────
            for idx, pkg in enumerate(self.packages, 1):
                if self.stop_event.is_set():
                    break

                entry = self.entry_by_pkg[pkg]
                self.checking_label = f"Checking Package {idx}/{total}"
                _maybe_render()

                prev = self._prev_state.get(pkg, self.status_map.get(pkg, ""))
                state, detail = self._detect_package_state(pkg, entry)

                log_event(
                    logger, "info", "[DENG_REJOIN_PACKAGE_CHECK]",
                    round=self._round,
                    index=idx,
                    total=total,
                    package=pkg,
                    process_running=detail.get("process_running", "unknown"),
                    in_game=detail.get("in_game", "unknown"),
                    heartbeat_ok=detail.get("heartbeat_ok", "unknown"),
                    warning_detected=detail.get("warning_detected", "false"),
                    state=state,
                    previous_state=prev,
                    elapsed_ms=detail.get("elapsed_ms", 0),
                )

                self._set_status(pkg, state)
                self._prev_state[pkg] = state

                # Update Online timestamp when confirmed in-game.
                if state == STATUS_ONLINE:
                    self._last_online_ts[pkg] = now
                    self._lobby_since.pop(pkg, None)

                # Grace window: skip recovery immediately after a launch.
                if not self._in_grace(pkg, now):
                    self._handle_state(pkg, entry, state, prev, now)

            # ── Watchdog continuity probe ─────────────────────────────────────
            _counts = {
                "online":       sum(1 for v in self.status_map.values() if v == STATUS_ONLINE),
                "dead":         sum(1 for v in self.status_map.values() if v == STATUS_DEAD),
                "no_heartbeat": sum(1 for v in self.status_map.values() if v == STATUS_NO_HEARTBEAT),
                "in_lobby":     sum(1 for v in self.status_map.values() if v == STATUS_IN_LOBBY),
            }
            log_event(
                logger, "info", "[DENG_REJOIN_WATCHDOG_CONTINUES]",
                online_packages=_counts["online"],
                dead_packages=_counts["dead"],
                no_heartbeat_packages=_counts["no_heartbeat"],
                in_lobby_packages=_counts["in_lobby"],
                next_round_in_sec=interval,
            )

            # ── Sleep (interruptible, keep rendering) ─────────────────────────
            self.checking_label = ""
            _maybe_render()
            _sleep_deadline = time.time() + interval
            while not self.stop_event.is_set() and time.time() < _sleep_deadline:
                _maybe_render()
                time.sleep(min(1.0, _sleep_deadline - time.time()))

        self.checking_label = ""
        db.insert_event("INFO", "watchdog_supervisor_stopped", "session ended by user")
        log_event(logger, "info", "watchdog_supervisor_stopped")

    def stop(self) -> None:
        """Signal the supervisor loop to stop."""
        self.stop_event.set()

    def get_status_snapshot(
        self, entries: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        entry_map: dict[str, str] = {}
        use = entries or self.entries
        for e in use:
            pkg = str(e.get("package") or "")
            username = str(e.get("account_username") or "").strip()
            entry_map[pkg] = username if username else "Unknown"

        snapshot: list[dict[str, Any]] = []
        for pkg in self.packages:
            status = self.status_map.get(pkg, STATUS_DEAD)
            snapshot.append(
                {
                    "package":      pkg,
                    "username":     entry_map.get(pkg, ""),
                    "status":       status,
                    "revive_count": self._revive_count.get(pkg, 0),
                    "failure_count": self._failure_count.get(pkg, 0),
                    "last_error":   None,
                    "online_since": self._last_online_ts.get(pkg),
                    "last_seen_at": self._last_online_ts.get(pkg),
                }
            )
        return snapshot
