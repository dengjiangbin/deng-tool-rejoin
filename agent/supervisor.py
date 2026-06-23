"""Auto rejoin supervisor loop."""

from __future__ import annotations

import os
import signal
import threading
import time
import traceback
from typing import Any

from . import android, db
from .backoff import calculate_backoff_seconds
from .config import effective_private_server_url, load_config, private_url_launch_context, validate_config
from .launcher import launch_package_for_current_config, perform_rejoin
from .lockfile import LockManager
from .logger import configure_logging, log_event
from .monitor import check_package_health, check_roblox_health
from .roblox_health import categorize_unhealthy


def _load_stored_rect_for_package(cfg: dict[str, Any], package: str):
    """Return the WindowRect that the initial Start computed for *package*.

    Bug 3 (probe ``p-52aeb6420f``): a single-package recovery used to
    recompute the layout as if only THIS package existed — collapsing a 3-pkg grid into a single
    1-pkg rect.  Visually this looked like "all packages went to the
    same slot".  The original Start path persists the full layout to
    ``cfg["last_layout_preview"]`` (and ``cfg["_layout_rects"]`` for the
    in-memory variant); we look that up and rebuild the matching
    :class:`WindowRect` so the relaunched window keeps its original slot.

    Returns ``None`` when no stored rect matches *package*.  Callers must
    fall back to the 1-package layout in that case.
    """
    try:
        from . import window_layout
    except Exception:  # noqa: BLE001
        return None
    sources = (cfg.get("_layout_rects"), cfg.get("last_layout_preview"))
    for source in sources:
        if not isinstance(source, list):
            continue
        for entry in source:
            if not isinstance(entry, dict):
                continue
            if entry.get("package") != package:
                continue
            try:
                return window_layout.WindowRect(
                    package=str(entry.get("package", "")),
                    left=int(entry.get("left", 0)),
                    top=int(entry.get("top", 0)),
                    right=int(entry.get("right", 0)),
                    bottom=int(entry.get("bottom", 0)),
                )
            except (TypeError, ValueError):
                continue
    return None


def _reapply_layout_for_package(package: str) -> None:
    """Best-effort layout re-apply for ONE package during recovery.

    1. Look up the package's original slot rect from
       ``cfg["last_layout_preview"]`` (or ``cfg["_layout_rects"]``).
       This preserves the deterministic per-package slot across single-
       package relaunches (Bug 3, probe ``p-52aeb6420f``).
    2. If no stored slot is found (cold supervisor, layout never ran),
       fall back to the 1-package right-pane layout the legacy code
       used.  This keeps single-package installs working.
    3. Write XML + Set-enable booleans, then trigger a direct (post-
       launch) resize via root so the relaunched window really lands at
       the stored bounds.

    Never raises.  All details go to the file logger.  Emits
    ``[DENG_REJOIN_REAPPLY_LAYOUT]`` so the slot-preservation fix is
    visible in probes.
    """
    try:
        from . import window_layout
        from . import window_apply
        from .config import DEFAULT_SCREEN_MODE, validate_screen_mode
        import logging as _logging
        _slog = _logging.getLogger("deng.rejoin.supervisor")
        cfg = load_config()

        # ── Preferred path: reuse the slot that initial Start computed.
        stored = _load_stored_rect_for_package(cfg, package)
        rect_source = "stored_slot"
        if stored is not None:
            rects = [stored]
        else:
            # Fallback: 1-package right-pane layout.
            display = window_layout.detect_display_info()
            rects = window_layout.calculate_split_layout(
                [package], display.width, display.height,
                termux_log_fraction=0.0,
                screen_mode=validate_screen_mode(
                    cfg.get("screen_mode", DEFAULT_SCREEN_MODE)
                ),
            )
            rect_source = "fallback_single_package"

        if not rects:
            return

        # Emit the slot-preservation probe so Bug 3 regressions are
        # easy to spot.  We log via the dedicated logger so this lands
        # in agent.log alongside [DENG_REJOIN_LAYOUT_BOUNDS].
        try:
            r0 = rects[0]
            _slog.info(
                "[DENG_REJOIN_REAPPLY_LAYOUT] package=%s rect_source=%s"
                " desired_x=%d desired_y=%d desired_w=%d desired_h=%d",
                package, rect_source,
                r0.left, r0.top, r0.win_w, r0.win_h,
            )
        except Exception:  # noqa: BLE001
            pass

        window_apply.apply_window_layout_silent(
            rects,
            force_stop_before=False,
            verify_after=False,
            retries=0,
            screen_mode=validate_screen_mode(
                cfg.get("screen_mode", DEFAULT_SCREEN_MODE)
            ),
        )
        # Direct resize via root, with freeform mode flip.  This is what
        # actually moves the visible window without waiting for the next
        # force-stop / relaunch cycle.
        try:
            ok, detail = window_apply.force_resize_package(package, rects[0])
            _slog.debug(
                "force_resize_package(%s) ok=%s detail=%s",
                package, ok, detail,
            )
        except Exception as exc:  # noqa: BLE001
            _slog.debug(
                "force_resize_package(%s) error: %s", package, exc,
            )
    except Exception as exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger("deng.rejoin.supervisor").debug(
            "reapply_layout_for_package(%s) error: %s", package, exc,
        )


# ─── Status constants (shown in terminal and webhook) ─────────────────────────

STATUS_ONLINE            = "Online"
STATUS_IN_GAME           = "Online"            # presence type 2 (legacy alias)
STATUS_IN_LOBBY          = "In Lobby"          # Roblox presence type 1 (home/lobby)
STATUS_OFFLINE           = "Offline"
STATUS_DEAD              = "Dead"              # Process confirmed gone (force-closed / crashed)
STATUS_LAUNCHING         = "Launching"
STATUS_REOPENING         = "Reopening"
STATUS_RELAUNCHING       = "Reopening"         # legacy alias
STATUS_CHECKING          = "Checking"
STATUS_PENDING           = "Pending"
STATUS_PREPARING         = "Preparing"
STATUS_CLEAR_CACHE       = "Clear Cache"
STATUS_CHECKING_LEGACY   = "Preparing"
STATUS_BACKGROUND        = "Background"
STATUS_RECONNECTING      = "Reconnecting"
STATUS_WARNING           = "Warning"
STATUS_FAILED            = "Failed"
STATUS_UNKNOWN           = "Unknown"
STATUS_JOIN_FAILED       = "Join Failed"
STATUS_WRONG_GAME        = "Wrong Game / Wrong Server"
# Richer state constants for improved UX
STATUS_LOBBY             = "Lobby"             # App open at home/lobby, no URL join active
STATUS_IN_SERVER         = "In Server"         # Strong evidence: game experience loaded
STATUS_CLOSED            = "Closed"            # App cleanly not running after a session
STATUS_JOIN_UNCONFIRMED  = "Join " + "Unconfirmed"  # App healthy but no in-game evidence yet
# State vocabulary aligned to user-facing terminology:
STATUS_LAUNCHED          = "Launched"
STATUS_DISCONNECTED      = "Disconnected"

# ── Live watchdog vocabulary (WatchdogSupervisor) ────────────────────────────
# These three states are the ONLY public steady states produced by WatchdogSupervisor:
#   Online        — process running and in-game
#   No Heartbeat  — process running but NOT playing (lobby, stuck, frozen, no heartbeat)
#   Dead          — process not running
# Running-but-not-in-game now maps directly to No Heartbeat.
STATUS_NO_HEARTBEAT  = "No Heartbeat"  # Process running but not actively playing
STATUS_SUSPENDED     = "Suspended"     # Recovery circuit breaker tripped

# Presence-confirmed steady states that kick off RAM/runtime metric loops.
_METRIC_ACTIVE_STATES = frozenset({
    STATUS_ONLINE,
    STATUS_IN_GAME,
    STATUS_IN_LOBBY,
})

# All healthy states — used for legacy _PackageWorker state-machine guards.
# WatchdogSupervisor never reads _HEALTHY_STATES; only _PackageWorker uses it.
# STATUS_LOBBY stays in _HEALTHY_STATES for _PackageWorker backward compat.
# WatchdogSupervisor never produces Lobby; its display map maps Lobby → No Heartbeat.
_HEALTHY_STATES = frozenset({
    STATUS_ONLINE, STATUS_LAUNCHED,
    # Legacy aliases for _PackageWorker only; never produced by WatchdogSupervisor.
    STATUS_LOBBY, STATUS_IN_SERVER, STATUS_JOIN_UNCONFIRMED,
})

# Legacy re-launch threshold.  With the Kaeru-style _post_launch_state
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
            time.sleep(max(0.0, min(1.0, deadline - time.time())))

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
        self.launching_since: float | None = None
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
        # Account/presence mapping is disabled in this release; supervision
        # relies on local Android package/process evidence only.
        self._roblox_user_id: int | None = None
        self._roblox_username: str = ""
        self._roblox_cookie: str | None = None
        self._presence_resolved: bool = False
        self._last_presence_label: str = ""
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
            time.sleep(max(0.0, min(1.0, deadline - time.time())))

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
        """Presence/account mapping is disabled; use local health evidence."""
        self.last_presence_state = "disabled"
        return None

    def _post_launch_state(self) -> str:
        """Process confirmed alive after launch → Online.

        Kaeru-style supervision (probe p-f1a4aaafe5): if the process is running,
        it is Online.  We no longer distinguish "in game" vs "lobby" via
        logcat/dumpsys because:
          1. The uiautomator probe caused SIGSEGV on Termux with App Cloner packages.
          2. Lobby detection caused an endless private-link re-send loop even
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
            # Launch timeout: how many seconds before forcing a re-check
            _launching_timeout = max(90, grace * 4)
            if self.has_private_url and self.status_map.get(self.package) in {STATUS_LAUNCHING, "Joining"}:
                now_launch = time.time()
                if self.launching_since is None:
                    self.launching_since = now_launch
                self._note_url_launch_grace(
                    "configured_link_launch",
                    now=now_launch,
                    grace_seconds=_launching_timeout,
                )

            self._roblox_username = ""
            self._roblox_user_id = None
            self._roblox_cookie = None
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
                if self.launching_since is not None and current_st in {STATUS_LAUNCHING, "Joining"}:
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
                    # set legacy transient or Lobby as public state.  The process-alive
                    # check below will determine the public Online/Reopening/Failed.
                    # This eliminates the old transient loop caused by presence
                    # lobby detection triggering the 120s URL resend timer.
                    if presence.is_lobby:
                        self._last_presence_label = presence.presence_type.label
                        self.last_seen_at = time.time()
                        # Fall through to process health check (no continue).
                    if presence.is_offline:
                        # Authoritative Offline.  Accumulate the suspicious counter.
                        # We do NOT immediately relaunch — that would recreate the
                        # old transient loop.  Controlled relaunch only happens after
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
                    # Promote from Launching to an appropriate healthy state.
                    # Use prev_before_check because STATUS_CHECKING is transient.
                    self.launching_since = None
                    if prev_before_check in {STATUS_LAUNCHING, "Joining"}:
                        # Kaeru-style: process alive = Online (stable rebuild p-9e3f2a8d1c).
                        # STATUS_JOINING is a legacy alias; live paths now use STATUS_LAUNCHING.
                        target = self._post_launch_state()
                    elif prev_before_check == STATUS_LOBBY:
                        target = STATUS_ONLINE
                    elif prev_before_check in _HEALTHY_STATES:
                        target = prev_before_check  # stay in current healthy state
                    else:
                        target = STATUS_ONLINE
                    self._set_status(target, "Heartbeat OK")
                    db.insert_heartbeat("healthy", {"package": self.package})
                    log_event(self.logger, "info", "heartbeat", status="healthy", package=self.package)

                    # ── Private-link timeout: re-launch with URL ──────────────
                    # If Roblox has been healthy but stuck without in-game proof
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

                if self.status_map.get(self.package) == STATUS_LOBBY:
                    self._set_status(STATUS_ONLINE, "Legacy lobby state normalized")

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
                            "Lobby":            STATUS_DEAD,
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
        from . import package_username as _pu
        for e in use:
            pkg = str(e.get("package") or "")
            entry_map[pkg] = _pu.username_display_for_package(pkg).username_display

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
    """Continuous sequential watchdog with deterministic live detection.

    States produced (public):
        Online        — Process running, in game, healthy heartbeat/presence.
        No Heartbeat  — Process running, but not playing normally, stalled, or offline signal.
        Dead          — Process not running (force-closed, crashed, stopped).
        Launching     — Transient: initial launch sent, waiting for first state detection.

    Key design guarantees (vs old _PackageWorker):
    - Process check runs FIRST before any presence check.
      Force-closing a package is always detected in the next round regardless of
      what the Roblox Presence API last reported.
    - Sequential loop: checks packages one by one, never stops after Online.
    - "Checking Package X/Y" updated on each package check (stored in
      self.checking_label, read by the dashboard callback).
    - The removed join-pending state is NEVER produced.  All post-launch transients use Launching.
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

    # ── Grace window after launch — do not check immediately ────────────────
    DEFAULT_GRACE_SECONDS: int = 30

    # ── Staggered launch: strict pause between package opens ────────────────
    LAUNCH_STAGGER_SECONDS: int = 15

    # ── Round-robin watchdog: visible Checking hold + tail pause per package ─
    PACKAGE_ROUND_ROBIN_SECONDS: int = 3
    PACKAGE_CHECKING_HOLD_SECONDS: float = 1.0
    PACKAGE_ROUND_ROBIN_TAIL_SECONDS: float = 2.0

    # ── Blocking recovery gate: poll interval while fixing one package ───────
    RECOVERY_GATE_POLL_SECONDS: float = 5.0
    RECOVERY_GATE_MAX_ATTEMPTS: int = 3

    # ── Roblox presence API 429 shield ───────────────────────────────────────
    PRESENCE_RATE_LIMIT_BACKOFF_SECONDS: float = 15.0

    # ── No-Heartbeat kill-switch: force-stop after continuous stall ─────────
    NHB_KILL_SWITCH_SECONDS: int = 60

    # ── Post-launch loading grace: suppress NHB kill-switch timer ───────────
    LOADING_GRACE_SECONDS: int = 120

    # ── Main-thread dashboard repaint cadence (must be <= Checking hold) ───
    DASHBOARD_RENDER_INTERVAL_SECONDS: float = 1.0

    # ── Presence poll must complete within strictly < 15 seconds ────────────
    PRESENCE_POLL_TIMEOUT_SECONDS: int = 14

    # Legacy alias — superseded by NHB_KILL_SWITCH_SECONDS.
    NHB_RELAUNCH_COOLDOWN_SEC: int = 60

    # ── Presence offline confirmation counter retained for probe history ─────
    NHB_OFFLINE_CONFIRMATIONS: int = 1

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
        self._state_lock = threading.RLock()
        self._watchdog_thread: threading.Thread | None = None
        self._render_callback: Any = None
        self._display_interval: float = self.DASHBOARD_RENDER_INTERVAL_SECONDS

        # Public status dict — mutated in-place (read by dashboard callback).
        self.status_map: dict[str, str] = {
            pkg: STATUS_LAUNCHING for pkg in self.packages
        }
        # Normalize initial_status: remove legacy/transient labels.
        # Running-but-not-in-game is Dead.
        # Any lobby-like state from old sessions also maps to Dead.
        if initial_status:
            _legacy_to_launching = {
                STATUS_JOIN_UNCONFIRMED, "Join Failed", "Reconnecting", "Joining",
            }
            _legacy_to_dead = {
                "In" + "-Lobby", "Lobby",
            }
            for pkg, st in initial_status.items():
                if pkg in self.status_map:
                    if st in _legacy_to_dead:
                        self.status_map[pkg] = STATUS_DEAD
                    elif st in _legacy_to_launching:
                        self.status_map[pkg] = STATUS_LAUNCHING
                    else:
                        self.status_map[pkg] = st

        self.on_status_change = on_status_change

        # Dashboard: "Checking Package X/Y" — updated by inner loop, read by callback.
        self.checking_label: str = f"Checking Package 0/{len(self.packages)}" if self.packages else "Checking Package 0/0"

        self._round: int = 0

        # Global latch: watchdog idles until cmd_start finishes ALL staggered launches.
        self._all_launches_completed: bool = False

        # ── Per-package mutable tracking ──────────────────────────────────────
        self._prev_state: dict[str, str] = {}
        self._last_online_ts: dict[str, float] = {}   # last time confirmed Online
        self._online_start_ts: dict[str, float] = {}  # when package first became Online (for Runtime display)
        self._last_launched_at: dict[str, float] = {}  # monotonic ts of last open/reopen
        self._grace_until: dict[str, float] = {}      # no relaunch until this ts
        self._nhb_offline_count: dict[str, int] = {}  # consecutive offline hits
        self._nhb_since: dict[str, float] = {}  # when package entered No Heartbeat
        self._nhb_cooldown_until: dict[str, float] = {}  # legacy; unused by kill-switch
        self._revive_count: dict[str, int] = {}
        self._failure_count: dict[str, int] = {}

        # ── Per-package RAM optimization tracking ─────────────────────────────
        self._ram_last_check_at: dict[str, float] = {}   # last RAM measurement ts
        self._ram_last_trim_at: dict[str, float] = {}    # last cache trim ts
        self._ram_cooldown_until: dict[str, float] = {}  # no RAM restart until this ts

        # ── Per-package presence tracking ─────────────────────────────────────
        self._presence_user_ids: dict[str, int] = {}
        self._presence_usernames: dict[str, str] = {}
        self._presence_cookies: dict[str, str | None] = {}
        self._presence_cookie_lookup_at: dict[str, float] = {}
        self._presence_id_resolved: set[str] = set()  # username→id done
        self._presence_lookup_attempt_at: dict[str, float] = {}
        self._presence_last_detail: dict[str, dict[str, Any]] = {}
        self._presence_expected_targets: dict[str, Any] = {}
        self._presence_rate_limit_until: float = 0.0
        self._watchdog_round_rate_limited: bool = False
        self._recovery_gate_attempts: dict[str, int] = {}
        self._root_info = android.detect_root()

        for e in self.entries:
            pkg = str(e.get("package") or "").strip()
            if not pkg:
                continue
            uname = str(e.get("account_username") or "").strip()
            if uname:
                self._presence_usernames[pkg] = uname
            raw_uid = e.get("roblox_user_id")
            try:
                uid = int(raw_uid) if raw_uid not in (None, "") else 0
            except (TypeError, ValueError):
                uid = 0
            if uid > 0:
                self._presence_user_ids[pkg] = uid
            cookie = str(e.get("roblox_cookie") or "").strip()
            self._presence_cookies[pkg] = cookie or None
            try:
                from .url_utils import parse_expected_target_from_url
                self._presence_expected_targets[pkg] = parse_expected_target_from_url(
                    effective_private_server_url(e, self.cfg),
                    expected_place_id=e.get("expected_place_id") or self.cfg.get("expected_place_id"),
                    expected_root_place_id=e.get("expected_root_place_id") or self.cfg.get("expected_root_place_id"),
                    expected_universe_id=e.get("expected_universe_id") or self.cfg.get("expected_universe_id"),
                )
            except Exception:  # noqa: BLE001
                self._presence_expected_targets[pkg] = None

        for e in self.entries:
            pkg = str(e.get("package") or "")
            if pkg and pkg not in self._presence_cookies:
                self._presence_cookies[pkg] = None

        self._logger = configure_logging(level=cfg.get("log_level", "INFO"))
        self.stop_source: str = ""
        self.stop_signal: str = ""
        self.stop_stack: str = ""
        log_event(
            self._logger, "info", "[DENG_REJOIN_SEGFAULT_FIX]",
            probe_id="p-79933739d8",
            segfault_source="python_ssl_urllib_presence_api",
            disabled_path="roblox_presence._post_json urllib_ssl",
            replacement_path="safe_http.post_json curl_on_termux",
            live_start_safe="true",
            joining_removed="true",
            uiautomator_live="false",
            logcat_live="false",
            xml_dump_live="false",
        )

    # ─── Internal helpers ─────────────────────────────────────────────────────

    def _handle_stop(self, signum: Any, frame: Any) -> None:
        source = "sigterm" if signum == signal.SIGTERM else ("sigint" if signum == signal.SIGINT else f"signal_{signum}")
        self.stop_source = source
        self.stop_signal = str(signum)
        try:
            self.stop_stack = "".join(traceback.format_stack(frame, limit=8))[:1800] if frame is not None else ""
        except Exception:  # noqa: BLE001
            self.stop_stack = ""
        log_event(
            self._logger,
            "info",
            "[DENG_REJOIN_STOP_REQUEST]",
            source=source,
            stack=self.stop_stack,
            allowed="true",
        )
        self.stop_event.set()

    def _set_status(self, pkg: str, status: str) -> None:
        if str(status or "").strip() == "Joining":
            status = STATUS_LAUNCHING
        with self._state_lock:
            old = self.status_map.get(pkg)
            self.status_map[pkg] = status
        if old != status and callable(self.on_status_change):
            self.on_status_change(pkg, status)

    def watchdog_thread_alive(self) -> bool:
        thread = self._watchdog_thread
        return bool(thread is not None and thread.is_alive())

    def set_render_callback(self, render_callback: Any) -> None:
        self._render_callback = render_callback

    def start_daemon(
        self,
        *,
        display_interval: float = DASHBOARD_RENDER_INTERVAL_SECONDS,
        render_callback: Any = None,
    ) -> None:
        """Launch the watchdog loop on a dedicated daemon thread (non-blocking)."""
        if self.watchdog_thread_alive():
            return
        self._display_interval = float(display_interval)
        self._render_callback = render_callback
        self.stop_event.clear()
        self.stop_source = ""
        self._watchdog_thread = threading.Thread(
            target=self._run_watchdog_loop,
            kwargs={
                "display_interval": float(display_interval),
                "render_callback": render_callback,
            },
            name="deng-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()
        log_event(
            self._logger,
            "info",
            "[DENG_REJOIN_WATCHDOG_DAEMON_STARTED]",
            packages=self.packages,
            thread_name="deng-watchdog",
            daemon="true",
        )

    def _process_alive_fast(self, pkg: str) -> bool:
        """Non-blocking liveness probe via pidof + ``os.kill(pid, 0)``."""
        try:
            package = android.validate_package_name(pkg)
        except Exception:  # noqa: BLE001
            return False
        pid_str = ""
        try:
            pid_str = android.get_package_pid(package, self._root_info)
        except Exception:  # noqa: BLE001
            pid_str = ""
        if not pid_str:
            try:
                res = android.run_command(["pidof", "-s", package], timeout=2)
                if res.ok and res.stdout.strip().isdigit():
                    pid_str = res.stdout.strip()
            except Exception:  # noqa: BLE001
                pid_str = ""
        if not pid_str or not str(pid_str).strip().isdigit():
            return False
        try:
            os.kill(int(str(pid_str).strip()), 0)
            return True
        except (ProcessLookupError, ValueError, OSError):
            return False
        except Exception:  # noqa: BLE001
            return False

    def _set_grace(self, pkg: str, now: float, seconds: int | None = None) -> None:
        self._grace_until[pkg] = now + float(seconds or self.DEFAULT_GRACE_SECONDS)

    def mark_all_launches_completed(self) -> None:
        """Release the global launch latch so the watchdog may begin checking."""
        now = time.monotonic()
        with self._state_lock:
            for pkg in self.packages:
                self._last_launched_at.setdefault(pkg, now)
            self._all_launches_completed = True
        log_event(
            self._logger,
            "info",
            "[DENG_REJOIN_ALL_LAUNCHES_COMPLETED]",
            packages=self.packages,
            package_count=len(self.packages),
        )

    def mark_package_launched(self, pkg: str) -> None:
        """Register a launch/reopen and bind loading-grace protection."""
        self._mark_launched(pkg)
        self._set_status(pkg, STATUS_LAUNCHING)

    def _mark_launched(self, pkg: str) -> None:
        """Record a fresh open/reopen and reset No Heartbeat kill-switch tracking."""
        with self._state_lock:
            self._last_launched_at[pkg] = time.monotonic()
            self._nhb_since.pop(pkg, None)

    def _ensure_launch_timestamp(self, pkg: str) -> float:
        """Return launch monotonic ts, treating a missing entry as freshly opened."""
        with self._state_lock:
            ts = self._last_launched_at.get(pkg)
            if not ts:
                ts = time.monotonic()
                self._last_launched_at[pkg] = ts
                log_event(
                    self._logger,
                    "info",
                    "[DENG_REJOIN_LAUNCH_TIMESTAMP_DEFAULTED]",
                    package=pkg,
                    action="bind_fresh_grace_anchor",
                )
            return float(ts)

    def _in_loading_grace(self, pkg: str) -> bool:
        """True while a package is within the post-launch connection grace window."""
        launched = self._ensure_launch_timestamp(pkg)
        return (time.monotonic() - launched) < float(self.LOADING_GRACE_SECONDS)

    def _note_presence_rate_limit(self) -> None:
        """Arm the 429 safe-state shield and round-robin cooling backoff."""
        until = time.monotonic() + float(self.PRESENCE_RATE_LIMIT_BACKOFF_SECONDS)
        self._presence_rate_limit_until = max(self._presence_rate_limit_until, until)
        self._watchdog_round_rate_limited = True

    def _presence_rate_limit_active(self) -> bool:
        return time.monotonic() < float(self._presence_rate_limit_until)

    def _preserve_package_state_on_rate_limit(
        self,
        pkg: str,
        *,
        t0: float,
        pres_detail: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Keep the last known good state when Roblox returns HTTP 429."""
        preserved = str(self._prev_state.get(pkg) or self.status_map.get(pkg) or "").strip()
        if preserved in {STATUS_CHECKING, STATUS_PENDING, ""}:
            preserved = STATUS_ONLINE if self._last_online_ts.get(pkg) else STATUS_LAUNCHING
        if preserved == STATUS_NO_HEARTBEAT and self._last_online_ts.get(pkg):
            preserved = STATUS_ONLINE
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        detail = {
            "process_running": "true",
            "in_game": "unknown",
            "heartbeat_ok": "unknown",
            "warning_detected": "false",
            "elapsed_ms": elapsed_ms,
            "root_available": str(bool(getattr(self._root_info, "available", False))).lower(),
            "foreground_package": "",
            "activity": preserved,
            "in_game_proof": "unknown",
            "reason": "presence_api_rate_limited_preserve_state",
        }
        self._log_state_evidence(
            pkg,
            detail,
            pres_detail or self._presence_last_detail.get(pkg, {}),
            preserved,
        )
        return preserved, detail

    def _deploy_gate_recovery_cycle(
        self,
        pkg: str,
        entry: dict[str, Any],
        now: float,
        render_callback: Any = None,
    ) -> None:
        """Force-stop and relaunch one package during the recovery gate."""
        logger = self._logger
        log_event(
            logger,
            "info",
            "[DENG_REJOIN_RECOVERY_GATE_CYCLE]",
            package=pkg,
            action="force_stop_relaunch",
        )
        if self._process_alive_fast(pkg):
            if self._force_stop_target_package(pkg):
                time.sleep(1.5)
        self._set_status(pkg, STATUS_REOPENING)
        self._mark_launched(pkg)
        if callable(render_callback):
            try:
                render_callback()
            except Exception:  # noqa: BLE001
                pass
        success = self._do_launch(pkg, entry, "recovery_gate_retry")
        if success:
            self._set_grace(pkg, now)
            self._mark_launched(pkg)
            self._set_status(pkg, STATUS_LAUNCHING)
        else:
            self._set_status(pkg, STATUS_DEAD)

    def _in_grace(self, pkg: str, now: float) -> bool:
        return now < self._grace_until.get(pkg, 0.0)

    def _sup_interval(self) -> int:
        sup = self.cfg.get("supervisor") if isinstance(self.cfg.get("supervisor"), dict) else {}
        raw = sup.get("health_check_interval_seconds") or self.cfg.get("health_check_interval_seconds", 30)
        return max(10, int(raw))

    def _fast_alive_evidence(self, pkg: str) -> dict[str, Any]:
        """Bounded package evidence for the live watchdog.

        Probe p-0899246178 showed the watchdog getting stuck after
        ``[DENG_REJOIN_PACKAGE_CHECK_START]`` for package 1.  The old path used
        the full ``android.get_package_alive_evidence`` helper, which can stack
        repeated process scans before the loop ever reaches package 2.  Start
        needs a short, deterministic per-package read: cached root process
        proof first, then lightweight foreground / visual hints.
        """
        dead: dict[str, Any] = {
            "running": False,
            "task": False,
            "window": False,
            "root_running": False,
            "surface": False,
            "foreground": False,
            "foreground_package": "",
            "alive": False,
            "strict_alive": False,
        }
        try:
            package = android.validate_package_name(pkg)
        except Exception:  # noqa: BLE001
            return dead

        running = False
        root_running = False
        process_check_attempted = False
        root_tool = getattr(self._root_info, "tool", None)
        if getattr(self._root_info, "available", False) and root_tool:
            try:
                process_check_attempted = True
                res = android.run_root_command(
                    ["pidof", package],
                    root_tool=root_tool,
                    timeout=2,
                )
                root_running = bool(res.ok and res.stdout.strip())
            except Exception:  # noqa: BLE001
                root_running = False
            if not root_running:
                try:
                    process_check_attempted = True
                    res = android.run_root_command(
                        android.process_cmdline_scan_args(package),
                        root_tool=root_tool,
                        timeout=3,
                    )
                    root_running = bool(res.ok and (res.stdout or "").strip())
                except Exception:  # noqa: BLE001
                    root_running = False
        else:
            try:
                process_check_attempted = True
                res = android.run_command(["pidof", package], timeout=2)
                running = bool(res.ok and res.stdout.strip())
            except Exception:  # noqa: BLE001
                running = False

        foreground_package = ""
        try:
            foreground_package = android.current_foreground_package() or ""
        except Exception:  # noqa: BLE001
            foreground_package = ""
        foreground = bool(
            foreground_package
            and (foreground_package == package or package in foreground_package)
        )

        # Visual/window/surface hints can be stale after force-close.  They may
        # support Online only when process detection is unavailable, never when
        # a process check ran and found no PID.
        process_alive = bool(running or root_running)
        process_missing = bool(process_check_attempted and not process_alive)
        need_visual = foreground or (not process_alive and not process_missing)
        window = False
        surface = False
        if need_visual:
            try:
                window = bool(android.is_package_window_visible(package))
            except Exception:  # noqa: BLE001
                window = False
            try:
                surface = bool(android.is_package_surface_in_surfaceflinger(package))
            except Exception:  # noqa: BLE001
                surface = False

        visual_alive = bool(window or surface or foreground)
        strict_alive = bool(process_alive or (visual_alive and not process_missing))
        return {
            "running": running,
            "task": False,
            "window": window,
            "root_running": root_running,
            "surface": surface,
            "foreground": foreground,
            "foreground_package": foreground_package,
            "alive": strict_alive,
            "strict_alive": strict_alive,
            "process_check_attempted": process_check_attempted,
            "process_missing": process_missing,
        }

    # ─── Presence fetching (process-check-first; presence is supplementary) ──

    def _fetch_presence(self, pkg: str, *, force_cookie_rescan: bool = False) -> Any:
        """Fetch Roblox presence for the package account via isolated HTTP.

        Returns a PresenceResult or None.  Called only when the Android
        process is alive — force-close is detected by PID loss first.
        """
        detail: dict[str, Any] = {
            "roblox_api_used": "false",
            "roblox_api_status": "skipped",
            "roblox_user_id": self._presence_user_ids.get(pkg, 0),
            "roblox_user_id_source": "config" if self._presence_user_ids.get(pkg) else "",
            "roblox_presence_type": "",
            "roblox_presence_profile": "",
            "roblox_place_id": "",
            "roblox_root_place_id": "",
            "roblox_universe_id": "",
            "roblox_game_id": "",
            "expected_place_id": "",
            "expected_root_place_id": "",
            "expected_universe_id": "",
            "expected_private_code": "",
            "server_verification": "unavailable",
            "presence_error": "",
        }
        target = self._presence_expected_targets.get(pkg)
        if target is not None:
            detail["expected_place_id"] = getattr(target, "expected_place_id", None) or ""
            detail["expected_root_place_id"] = getattr(target, "expected_root_place_id", None) or ""
            detail["expected_universe_id"] = getattr(target, "expected_universe_id", None) or ""
            detail["expected_private_code"] = "configured" if getattr(target, "expected_private_code", "") else ""
        self._presence_last_detail[pkg] = detail
        try:
            from . import roblox_presence as _rp
        except Exception as exc:  # noqa: BLE001
            detail["roblox_api_status"] = "failed"
            detail["presence_error"] = f"import_failed:{exc}"[:160]
            return None
        try:
            uid = int(self._presence_user_ids.get(pkg) or 0)

            if uid <= 0 and pkg not in self._presence_id_resolved:
                try:
                    pref_uid = android.discover_roblox_user_id_from_prefs(pkg, timeout=3)
                    if pref_uid:
                        uid = int(pref_uid)
                        self._presence_user_ids[pkg] = uid
                        detail["roblox_user_id_source"] = "prefs"
                except Exception:  # noqa: BLE001
                    pass

            if uid <= 0:
                uname = self._presence_usernames.get(pkg, "")
                last_attempt = self._presence_lookup_attempt_at.get(pkg, 0.0)
                should_try = bool(uname) and (time.monotonic() - last_attempt) >= 30.0
                if should_try:
                    self._presence_lookup_attempt_at[pkg] = time.monotonic()
                    try:
                        uid_lookup = _rp.lookup_user_id(uname)
                    except Exception:  # noqa: BLE001
                        uid_lookup = None
                    if uid_lookup:
                        uid = int(uid_lookup)
                        self._presence_user_ids[pkg] = uid
                        detail["roblox_user_id_source"] = "username"

            if uid > 0:
                self._presence_id_resolved.add(pkg)
                detail["roblox_user_id"] = uid
            elif not self._presence_usernames.get(pkg):
                self._presence_id_resolved.add(pkg)

            if not uid:
                detail["roblox_api_status"] = "skipped"
                detail["presence_error"] = "missing_user_id"
                return None

            cookie = self._presence_cookies.get(pkg)
            if not cookie or force_cookie_rescan:
                last_cookie_attempt = self._presence_cookie_lookup_at.get(pkg, 0.0)
                should_try_cookie = force_cookie_rescan or (
                    (time.monotonic() - last_cookie_attempt) >= 120.0
                )
                if should_try_cookie:
                    self._presence_cookie_lookup_at[pkg] = time.monotonic()
                    try:
                        from agent.roblox_presence import detect_roblox_cookie

                        cookie = detect_roblox_cookie(
                            pkg,
                            entry=self.entry_by_pkg.get(pkg),
                            config=self.cfg,
                            use_root=True,
                            force_rescan=force_cookie_rescan,
                        )
                        if cookie:
                            self._presence_cookies[pkg] = cookie
                            detail["roblox_cookie_source"] = "auto_detect"
                    except Exception:  # noqa: BLE001
                        pass

            if not cookie and force_cookie_rescan:
                detail["roblox_api_used"] = "false"
                detail["roblox_api_status"] = "skipped"
                detail["presence_error"] = "missing_cookie"
                return None

            detail["roblox_api_used"] = "true"
            try:
                presence = _rp.fetch_presence_dual_verified(uid, cookie=cookie)
            except _rp.RobloxRateLimitedError:
                detail["roblox_api_status"] = "rate_limited"
                detail["presence_error"] = "http_429"
                self._note_presence_rate_limit()
                raise
            ptype = getattr(presence, "presence_type", None)
            if getattr(presence, "is_unknown", False):
                detail["roblox_api_status"] = "network_error"
            else:
                detail["roblox_api_status"] = "success"
            detail["roblox_presence_type"] = getattr(ptype, "name", str(ptype))
            detail["roblox_presence_profile"] = _rp.map_presence_profile(presence)
            detail["roblox_place_id"] = getattr(presence, "place_id", "") or ""
            detail["roblox_root_place_id"] = getattr(presence, "root_place_id", "") or ""
            detail["roblox_universe_id"] = getattr(presence, "universe_id", "") or ""
            detail["roblox_game_id"] = getattr(presence, "game_id", "") or ""
            return presence
        except Exception as exc:  # noqa: BLE001
            from . import safe_http as _sh

            if isinstance(exc, _rp.RobloxRateLimitedError):
                detail["roblox_api_used"] = "true"
                detail["roblox_api_status"] = "rate_limited"
                detail["presence_error"] = "http_429"
                self._note_presence_rate_limit()
                raise
            detail["roblox_api_used"] = "true" if detail.get("roblox_user_id") else "false"
            if isinstance(exc, _sh.SafeHttpStatusError) and int(getattr(exc, "status_code", 0) or 0) == 429:
                detail["roblox_api_status"] = "rate_limited"
                detail["presence_error"] = "http_429"
                self._note_presence_rate_limit()
                raise _rp.RobloxRateLimitedError("presence_fetch") from exc
            if isinstance(exc, _sh.SafeHttpNetworkError):
                detail["roblox_api_status"] = "network_error"
            else:
                err_text = str(exc).lower()
                if "timeout" in err_text or "timed out" in err_text:
                    detail["roblox_api_status"] = "timeout"
                else:
                    detail["roblox_api_status"] = "failed"
            detail["presence_error"] = str(exc)[:160]
            return None

    def _evaluate_package_presence_isolated(
        self, pkg: str, entry: dict[str, Any]
    ) -> str:
        """Re-evaluate a single package during the blocking recovery gate."""
        self._set_status(pkg, STATUS_CHECKING)
        try:
            if self._needs_launching_evaluation(pkg):
                state, _detail = self._evaluate_launching_or_pending(pkg, entry)
            else:
                state, _detail = self._detect_package_state(pkg, entry)
        except Exception:  # noqa: BLE001
            state = str(self.status_map.get(pkg) or STATUS_NO_HEARTBEAT)
        return state

    def _run_blocking_recovery_gate(
        self,
        pkg: str,
        entry: dict[str, Any],
        *,
        package_index: int,
        package_total: int,
        render_callback: Any = None,
    ) -> None:
        """Halt round-robin until one package reaches Online or stable Dead."""
        logger = self._logger
        log_event(
            logger,
            "info",
            "[DENG_REJOIN_RECOVERY_GATE_ENTER]",
            package=pkg,
            package_index=package_index,
            package_total=package_total,
            poll_sec=self.RECOVERY_GATE_POLL_SECONDS,
            max_attempts=self.RECOVERY_GATE_MAX_ATTEMPTS,
        )
        attempt = 0
        now = time.time()
        while not self.stop_event.is_set():
            self.checking_label = f"Recovering Package {package_index}/{package_total}"
            cb = render_callback or self._render_callback
            if callable(cb):
                try:
                    cb()
                except Exception:  # noqa: BLE001
                    pass
            state = self._evaluate_package_presence_isolated(pkg, entry)
            self._set_status(pkg, state)
            self._prev_state[pkg] = state
            if state == STATUS_ONLINE:
                self._nhb_since.pop(pkg, None)
                self._recovery_gate_attempts.pop(pkg, None)
                log_event(
                    logger,
                    "info",
                    "[DENG_REJOIN_RECOVERY_GATE_EXIT]",
                    package=pkg,
                    result="online",
                    attempts=attempt,
                )
                break
            if state == STATUS_DEAD:
                self._recovery_gate_attempts.pop(pkg, None)
                log_event(
                    logger,
                    "info",
                    "[DENG_REJOIN_RECOVERY_GATE_EXIT]",
                    package=pkg,
                    result="dead",
                    attempts=attempt,
                )
                break

            attempt += 1
            self._recovery_gate_attempts[pkg] = attempt
            if attempt >= self.RECOVERY_GATE_MAX_ATTEMPTS:
                self._set_status(pkg, STATUS_SUSPENDED)
                self._recovery_gate_attempts.pop(pkg, None)
                log_event(
                    logger,
                    "warning",
                    "[DENG_REJOIN_RECOVERY_CIRCUIT_BREAKER]",
                    package=pkg,
                    attempts=attempt,
                    result="suspended",
                    action="release_watchdog_queue",
                )
                break

            log_event(
                logger,
                "info",
                "[DENG_REJOIN_RECOVERY_GATE_POLL]",
                package=pkg,
                state=state,
                attempt=attempt,
                max_attempts=self.RECOVERY_GATE_MAX_ATTEMPTS,
                poll_sec=self.RECOVERY_GATE_POLL_SECONDS,
            )
            self._deploy_gate_recovery_cycle(pkg, entry, now, render_callback=render_callback)
            self._interruptible_sleep(self.RECOVERY_GATE_POLL_SECONDS)
        else:
            log_event(
                logger,
                "info",
                "[DENG_REJOIN_RECOVERY_GATE_EXIT]",
                package=pkg,
                result="stopped",
            )

    # ─── State detection ─────────────────────────────────────────────────────

    def _needs_launching_evaluation(self, pkg: str) -> bool:
        """True when a launched package awaits first post-launch presence proof."""
        current = str(self.status_map.get(pkg) or "").strip()
        return current == STATUS_LAUNCHING

    def _is_prelaunch_pending(self, pkg: str) -> bool:
        """True while staggered launch has not opened this clone yet."""
        return str(self.status_map.get(pkg) or "").strip() == STATUS_PENDING

    def _evaluate_launching_or_pending(
        self, pkg: str, entry: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Launching → Checking → Online | No Heartbeat | Dead.

        Forces an immediate cookie rescan so cloned APK shared_prefs are read
        via root before the presence API call.  Cookie/identity failures map
        to No Heartbeat so the 60s kill-switch can recover the package.
        """
        self._set_status(pkg, STATUS_CHECKING)
        self._presence_cookie_lookup_at.pop(pkg, None)
        self._presence_lookup_attempt_at.pop(pkg, None)

        state, detail = self._detect_package_state(
            pkg, entry, force_cookie_rescan=True
        )

        pres_detail = self._presence_last_detail.get(pkg, {})
        presence_error = str(pres_detail.get("presence_error") or "")
        cookie_missing = not str(self._presence_cookies.get(pkg) or "").strip()
        api_status = str(pres_detail.get("roblox_api_status") or "")

        if state in {STATUS_PENDING, STATUS_CHECKING} or (
            state == STATUS_NO_HEARTBEAT
            and str(detail.get("reason") or "") in {
                "presence_user_id_pending",
                "cookie_extraction_failed",
                "missing_cookie",
            }
        ):
            state = STATUS_NO_HEARTBEAT
            detail = dict(detail)
            detail["activity"] = "No Heartbeat"
            detail["heartbeat_ok"] = "false"
            detail["reason"] = (
                "cookie_extraction_failed"
                if cookie_missing or presence_error == "missing_cookie"
                else "presence_identity_unavailable"
            )
            self._log_state_evidence(pkg, detail, pres_detail, STATUS_NO_HEARTBEAT)
        elif state == STATUS_NO_HEARTBEAT and api_status in {
            "failed", "rate_limited", "network_error", "timeout", "skipped",
        }:
            detail = dict(detail)
            detail["reason"] = f"presence_api_{api_status}"

        return state, detail

    def _detect_package_state(
        self, pkg: str, entry: dict[str, Any], *, force_cookie_rescan: bool = False
    ) -> tuple[str, dict[str, Any]]:
        """Detect package state using cookie presence as the sole source of truth.

        Android PID/window/foreground probes are intentionally excluded — a hung
        background clone can still hold a valid PID while the Roblox session is
        dead.  Only the authenticated cookie presence engine decides liveness.
        """
        del entry
        t0 = time.monotonic()

        if self._presence_rate_limit_active() or self._watchdog_round_rate_limited:
            return self._preserve_package_state_on_rate_limit(
                pkg,
                t0=t0,
                pres_detail=self._presence_last_detail.get(pkg, {}),
            )

        presence = None
        try:
            presence = self._fetch_presence(pkg, force_cookie_rescan=force_cookie_rescan)
        except Exception as exc:  # noqa: BLE001
            from . import roblox_presence as _rp

            if isinstance(exc, _rp.RobloxRateLimitedError):
                self._note_presence_rate_limit()
                return self._preserve_package_state_on_rate_limit(
                    pkg,
                    t0=t0,
                    pres_detail=self._presence_last_detail.get(pkg, {}),
                )
            raise

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        pres_detail = self._presence_last_detail.get(pkg, {})
        profile = str(pres_detail.get("roblox_presence_profile") or "")
        heartbeat_age_sec = (
            int(time.time() - self._last_online_ts[pkg])
            if self._last_online_ts.get(pkg)
            else ""
        )
        in_loading_grace = self._in_loading_grace(pkg)

        def _presence_detail(**overrides: Any) -> dict[str, Any]:
            detail = {
                "process_running": "unknown",
                "in_game": "false",
                "heartbeat_ok": "false",
                "warning_detected": "false",
                "elapsed_ms": elapsed_ms,
                "root_available": "unknown",
                "foreground_package": "",
                "activity": "",
                "in_game_proof": "false",
                "heartbeat_age_sec": heartbeat_age_sec,
                "presence_source": "cookie_dual_verify",
                "reason": "roblox_presence_evaluated",
            }
            detail.update(overrides)
            return detail

        if presence is not None and getattr(presence, "is_in_game", False):
            self._nhb_offline_count[pkg] = 0
            detail = _presence_detail(
                in_game="true",
                heartbeat_ok="true",
                in_game_proof="true",
                activity=profile or "Online",
                reason="roblox_presence_in_game",
            )
            self._log_state_evidence(pkg, detail, pres_detail, STATUS_ONLINE)
            return STATUS_ONLINE, detail

        api_status = str(pres_detail.get("roblox_api_status") or "")
        presence_error = str(pres_detail.get("presence_error") or "")
        presence_lobby = bool(presence is not None and getattr(presence, "is_lobby", False))
        presence_offline = bool(presence is not None and getattr(presence, "is_offline", False))
        presence_unknown = presence is None or bool(getattr(presence, "is_unknown", False))

        if presence is None and presence_error == "missing_user_id":
            pending_state = STATUS_PENDING if not force_cookie_rescan else STATUS_NO_HEARTBEAT
            detail = _presence_detail(
                activity="Pending" if pending_state == STATUS_PENDING else "No Heartbeat",
                in_game_proof="unknown",
                reason="presence_user_id_pending",
            )
            self._log_state_evidence(pkg, detail, pres_detail, pending_state)
            return pending_state, detail

        if presence is None and presence_error == "missing_cookie":
            detail = _presence_detail(
                activity="No Heartbeat",
                in_game_proof="unknown",
                reason="cookie_extraction_failed",
            )
            self._log_state_evidence(pkg, detail, pres_detail, STATUS_NO_HEARTBEAT)
            return STATUS_NO_HEARTBEAT, detail

        if presence is None and api_status in {
            "failed",
            "network_error",
            "timeout",
            "skipped",
        }:
            detail = _presence_detail(
                activity="No Heartbeat",
                in_game_proof="unknown",
                reason=f"presence_api_{api_status}",
            )
            self._log_state_evidence(pkg, detail, pres_detail, STATUS_NO_HEARTBEAT)
            return STATUS_NO_HEARTBEAT, detail

        if presence_lobby or presence_offline:
            if in_loading_grace:
                detail = _presence_detail(
                    activity="Launching",
                    in_game_proof="unknown",
                    reason=(
                        "presence_offline_loading_grace"
                        if presence_offline
                        else "presence_lobby_loading_grace"
                    ),
                )
                self._log_state_evidence(pkg, detail, pres_detail, STATUS_LAUNCHING)
                return STATUS_LAUNCHING, detail
            self._nhb_offline_count[pkg] = self._nhb_offline_count.get(pkg, 0) + 1
            detail = _presence_detail(
                activity=profile or ("Offline" if presence_offline else "In Lobby"),
                reason="presence_offline" if presence_offline else "roblox_presence_in_lobby",
            )
            self._log_state_evidence(pkg, detail, pres_detail, STATUS_NO_HEARTBEAT)
            return STATUS_NO_HEARTBEAT, detail

        reason = (
            "presence_not_in_game_no_heartbeat"
            if presence is not None and not presence_unknown
            else "missing_in_game_proof_no_heartbeat"
        )
        if in_loading_grace and presence_unknown:
            detail = _presence_detail(
                activity="Launching",
                in_game_proof="unknown",
                reason="presence_unknown_loading_grace",
            )
            self._log_state_evidence(pkg, detail, pres_detail, STATUS_LAUNCHING)
            return STATUS_LAUNCHING, detail

        detail = _presence_detail(
            activity="No Heartbeat",
            in_game_proof="unknown" if presence_unknown else "false",
            reason=reason,
        )
        self._log_state_evidence(pkg, detail, pres_detail, STATUS_NO_HEARTBEAT)
        return STATUS_NO_HEARTBEAT, detail

    def _log_state_evidence(
        self,
        pkg: str,
        detail: dict[str, Any],
        presence_detail: dict[str, Any],
        final_state: str,
    ) -> None:
        log_event(
            self._logger, "info", "[DENG_REJOIN_STATE_EVIDENCE]",
            package=pkg,
            process_running=detail.get("process_running", "unknown"),
            pid="",
            root_available=detail.get("root_available", "false"),
            foreground_package=detail.get("foreground_package", ""),
            activity=detail.get("activity", ""),
            roblox_user_id=presence_detail.get("roblox_user_id", 0),
            roblox_user_id_source=presence_detail.get("roblox_user_id_source", ""),
            roblox_api_used=presence_detail.get("roblox_api_used", "false"),
            roblox_api_status=presence_detail.get("roblox_api_status", "skipped"),
            roblox_presence_type=presence_detail.get("roblox_presence_type", ""),
            roblox_place_id=presence_detail.get("roblox_place_id", ""),
            roblox_root_place_id=presence_detail.get("roblox_root_place_id", ""),
            roblox_universe_id=presence_detail.get("roblox_universe_id", ""),
            roblox_game_id=presence_detail.get("roblox_game_id", ""),
            expected_place_id=presence_detail.get("expected_place_id", ""),
            expected_root_place_id=presence_detail.get("expected_root_place_id", ""),
            expected_universe_id=presence_detail.get("expected_universe_id", ""),
            expected_private_code=presence_detail.get("expected_private_code", ""),
            server_verification=presence_detail.get("server_verification", "unavailable"),
            in_game_proof=detail.get("in_game_proof", "unknown"),
            heartbeat_age_sec=detail.get("heartbeat_age_sec", ""),
            heartbeat_ok=detail.get("heartbeat_ok", "unknown"),
            warning_detected=detail.get("warning_detected", "unknown"),
            final_state=final_state,
            reason=detail.get("reason", ""),
        )

    # ─── Recovery ─────────────────────────────────────────────────────────────

    def _do_launch(
        self, pkg: str, entry: dict[str, Any], reason: str
    ) -> bool:
        """Launch the package using the canonical launcher.  Returns success."""
        logger = self._logger
        url_context = private_url_launch_context(entry, self.cfg)
        url_configured = url_context.get("url_mode") == "private_url"
        launcher_label = "private_url" if url_configured else "app_only"
        t0 = time.monotonic()
        # Ensure package key license file is present before relaunch.
        # Separate from DENG Tool license — writes FREE_ key to the Roblox data dir.
        try:
            from .package_key import ensure_package_key_for_start as _epkfs
            _pk = _epkfs(
                pkg, self.cfg,
                root_enabled=bool(self.cfg.get("root_mode_enabled", False)),
            )
            log_event(
                logger, "info", "[DENG_REJOIN_PACKAGE_KEY]",
                package=pkg, mode="recovery_ensure", reason=reason,
                path=_pk.get("path", ""),
                key_masked=_pk.get("key_masked", ""),
                write_needed=str(_pk.get("write_needed", False)).lower(),
                write_attempted=str(_pk.get("write_attempted", False)).lower(),
                method=_pk.get("method", "skipped"),
                success=str(_pk.get("success", True)).lower(),
                error=_pk.get("error", ""),
            )
        except Exception as _pk_exc:  # noqa: BLE001
            logger.debug("package_key ensure error (non-fatal): %s", _pk_exc)
        try:
            result = launch_package_for_current_config(entry, self.cfg, reason)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            log_event(
                logger, "info", "[DENG_REJOIN_RECOVERY_LAUNCH_RESULT]",
                package=pkg,
                reason=reason,
                launcher=launcher_label,
                private_url_mode=url_context.get("private_url_mode", "global"),
                url_mode=url_context.get("url_mode", "app_only"),
                url_config_source=url_context.get("url_config_source", "blank"),
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
        render_callback: Any = None,
    ) -> bool:
        """Apply recovery action based on current state.

        Recovery rules:
        - Dead        → launch_package_for_current_config
        - No Heartbeat → track stall; after 60s force-stop → Dead → Reopening
        - Online      → update last_online_ts, keep monitoring

        Returns True when a blocking recovery gate should run for this package.
        """
        logger = self._logger
        url_context = private_url_launch_context(entry, self.cfg)
        url_configured = url_context.get("url_mode") == "private_url"

        if state == STATUS_DEAD:
            action = "private_url_relaunch" if url_configured else "app_only_relaunch"
            log_event(
                logger, "info", "[DENG_REJOIN_RECOVERY_DECISION]",
                package=pkg, state="Dead",
                private_url_mode=url_context.get("private_url_mode", "global"),
                url_mode=url_context.get("url_mode", "app_only"),
                url_config_source=url_context.get("url_config_source", "blank"),
                private_url_configured=str(url_configured).lower(),
                action=action,
                reason="process_not_running",
            )
            self._set_status(pkg, STATUS_REOPENING)
            self._mark_launched(pkg)
            if callable(render_callback):
                try:
                    render_callback()
                except Exception:  # noqa: BLE001
                    pass
            success = self._do_launch(pkg, entry, "dead_recovery")
            if success:
                self._revive_count[pkg] = self._revive_count.get(pkg, 0) + 1
                self._set_grace(pkg, now)
                self._mark_launched(pkg)
                self._set_status(pkg, STATUS_LAUNCHING)
            else:
                self._failure_count[pkg] = self._failure_count.get(pkg, 0) + 1
            return True

        elif state == STATUS_NO_HEARTBEAT:
            if self.status_map.get(pkg) == STATUS_ONLINE:
                self._nhb_since.pop(pkg, None)
                return False
            if self._in_loading_grace(pkg):
                self._nhb_since.pop(pkg, None)
                log_event(
                    logger, "info", "[DENG_REJOIN_LOADING_GRACE]",
                    package=pkg,
                    grace_sec=self.LOADING_GRACE_SECONDS,
                    elapsed_sec=round(
                        time.monotonic() - self._ensure_launch_timestamp(pkg), 1
                    ),
                    action="suppress_nhb_kill_switch",
                )
                return False
            nhb_since = self._nhb_since.get(pkg)
            now_mono = time.monotonic()
            if nhb_since is None:
                self._nhb_since[pkg] = now_mono
                log_event(
                    logger, "info", "[DENG_REJOIN_NO_HEARTBEAT_TRACK]",
                    package=pkg,
                    started_at=round(now_mono, 3),
                    kill_switch_sec=self.NHB_KILL_SWITCH_SECONDS,
                )
                return False
            elapsed = now_mono - nhb_since
            if elapsed < self.NHB_KILL_SWITCH_SECONDS:
                log_event(
                    logger, "debug", "[DENG_REJOIN_NO_HEARTBEAT_WAIT]",
                    package=pkg,
                    elapsed_sec=round(elapsed, 1),
                    remaining_sec=round(self.NHB_KILL_SWITCH_SECONDS - elapsed, 1),
                )
                return False
            log_event(
                logger, "info", "[DENG_REJOIN_NO_HEARTBEAT_KILL_SWITCH]",
                package=pkg,
                elapsed_sec=round(elapsed, 1),
                action="force_stop",
                reason="continuous_no_heartbeat_exceeded_60s",
            )
            self._nhb_since.pop(pkg, None)
            self._nhb_cooldown_until.pop(pkg, None)
            if self._force_stop_target_package(pkg):
                time.sleep(1.5)
            self._set_status(pkg, STATUS_DEAD)
            if callable(render_callback):
                try:
                    render_callback()
                except Exception:  # noqa: BLE001
                    pass
            return True

        elif state in _METRIC_ACTIVE_STATES:
            self._nhb_since.pop(pkg, None)
            log_event(
                logger, "info", "[DENG_REJOIN_ONLINE_STABLE]",
                package=pkg,
                state=state,
                action="monitor_only",
            )
            self._check_ram_optimization(pkg, entry, now, render_callback=render_callback)

        return False

    # ─── RAM optimization ─────────────────────────────────────────────────────

    def _check_ram_optimization(
        self,
        pkg: str,
        entry: dict[str, Any],
        now: float,
        render_callback: Any = None,
    ) -> None:
        """Check per-package RAM usage and apply trim / restart if required.

        Decision ladder (all thresholds from config):
          1. Package must have been Online for at least ram_check_delay_after_online_sec.
          2. Check at most once per ram_trim_interval_sec.
          3. RAM ≤ effective target  →  no action.
          4. RAM > effective target  →  try safe cache trim (non-disruptive).
          5. RAM > ram_restart_threshold_mb → log high RAM, but do not stop
             or relaunch an Online package.

        Probe p-52aeb6420f evidence: Roblox uses 1.3–1.4 GB on the SM-N9810
        (Android 10) so the default 900 MB threshold tripped EVERY package
        on EVERY cooldown cycle (180 s), force-closing Online-and-in-game
        packages forever.  Per user spec, Online + in-game packages MUST
        NOT be relaunched.  The non-disruptive cache trim still fires above
        the soft target, but high RAM alone never force-stops, relaunches,
        or reopens the private URL.

        Probe tags:
          [DENG_REJOIN_RAM_CHECK]
          [DENG_REJOIN_RAM_TRIM]
          [DENG_REJOIN_RAM_RESTART_SKIPPED]   ← emitted when high RAM is
              reported for an Online package; no kill/relaunch is allowed.
        """
        cfg = self.cfg
        if not cfg.get("ram_optimization_enabled", True):
            return

        online_since = self._online_start_ts.get(pkg, now)
        delay_sec = int(cfg.get("ram_check_delay_after_online_sec", 30))
        if now - online_since < delay_sec:
            return  # Allow stabilization after coming Online.

        trim_interval = int(cfg.get("ram_trim_interval_sec", 120))
        if now - self._ram_last_check_at.get(pkg, 0.0) < trim_interval:
            return  # Not yet time for the next RAM check.

        self._ram_last_check_at[pkg] = now
        logger = self._logger

        # ── Measure RAM ───────────────────────────────────────────────────────
        ram_result = android.get_package_ram_usage(pkg, self._root_info)
        rss_kb: int = int(ram_result.get("rss_kb", 0))
        usage_mb: float = rss_kb / 1024.0
        usage_display: str = str(ram_result.get("usage_mb", "0MB"))
        method: str = str(ram_result.get("method", "unknown"))

        target_normal    = int(cfg.get("ram_target_normal_mb", 700))
        target_good      = int(cfg.get("ram_target_good_mb", 500))
        target_aggressive = int(cfg.get("ram_target_aggressive_mb", 300))
        restart_threshold = int(cfg.get("ram_restart_threshold_mb", 900))
        aggressive_mode   = bool(cfg.get("ram_aggressive_mode", False))
        effective_target  = target_aggressive if aggressive_mode else target_normal

        log_event(
            logger, "info", "[DENG_REJOIN_RAM_CHECK]",
            package=pkg,
            usage_mb=round(usage_mb, 1),
            usage_display=usage_display,
            method=method,
            target_normal_mb=target_normal,
            target_good_mb=target_good,
            target_aggressive_mb=target_aggressive,
            restart_threshold_mb=restart_threshold,
            aggressive_mode=str(aggressive_mode).lower(),
            effective_target_mb=effective_target,
        )

        if usage_mb <= effective_target:
            return  # RAM is within acceptable range — nothing to do.

        # ── Safe cache trim ───────────────────────────────────────────────────
        if now - self._ram_last_trim_at.get(pkg, 0.0) >= trim_interval:
            self._ram_last_trim_at[pkg] = now
            try:
                trim_result = android.clear_package_cache_verified(pkg)
                log_event(
                    logger, "info", "[DENG_REJOIN_RAM_TRIM]",
                    package=pkg,
                    usage_mb_before=round(usage_mb, 1),
                    cache_cleared=str(not trim_result.get("skipped", True)).lower(),
                    skipped=str(trim_result.get("skipped", True)).lower(),
                    skipped_reason=str(trim_result.get("skipped_reason", "")),
                    trim_success=str(trim_result.get("success", False)).lower(),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("RAM trim error (non-fatal): %s", exc)

        if usage_mb <= restart_threshold:
            return  # Below restart threshold — trim is sufficient.

        # ── RAM restart forbidden for Online packages ────────────────────────
        # _check_ram_optimization is only reached from _handle_state when
        # state == STATUS_ONLINE (probe p-52aeb6420f), so a "true Online
        # package" reaching this point is by definition healthy and
        # in-game.  Per user spec, do NOT relaunch.  We log a skip event so
        # the high-RAM observation remains visible in probes.
        log_event(
            logger, "info", "[DENG_REJOIN_RAM_RESTART_SKIPPED]",
            package=pkg,
            usage_mb=round(usage_mb, 1),
            usage_display=usage_display,
            restart_threshold_mb=restart_threshold,
            reason="online_state_protected",
            policy="high_ram_report_only",
        )
        return

    def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep in 1s slices so stop_event can interrupt promptly."""
        deadline = time.monotonic() + max(0.0, float(seconds))
        while not self.stop_event.is_set() and time.monotonic() < deadline:
            time.sleep(max(0.0, min(1.0, deadline - time.monotonic())))

    def _force_stop_target_package(self, pkg: str) -> bool:
        """Laser-focused ``am force-stop`` for one configured clone only."""
        target = str(pkg or "").strip()
        if not target or target not in self.packages:
            return False
        if android._is_force_stop_protected(target):
            log_event(
                self._logger,
                "warning",
                "[DENG_REJOIN_FORCE_STOP_BLOCKED]",
                package=target,
                reason="protected_package",
            )
            return False
        try:
            android.force_stop_package(target, self._root_info)
            return True
        except Exception as exc:  # noqa: BLE001
            log_event(
                self._logger,
                "warning",
                "[DENG_REJOIN_FORCE_STOP_FAILED]",
                package=target,
                error=str(exc)[:160],
            )
            return False

    # ─── Main loop ────────────────────────────────────────────────────────────

    def run_forever(
        self,
        *,
        display_interval: float = DASHBOARD_RENDER_INTERVAL_SECONDS,
        render_callback: Any = None,
    ) -> None:
        """Run the watchdog loop on the current thread (tests / legacy path)."""
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, self._handle_stop)
            signal.signal(signal.SIGINT, self._handle_stop)
        self._run_watchdog_loop(
            display_interval=display_interval,
            render_callback=render_callback,
        )

    def _run_watchdog_loop(
        self,
        *,
        display_interval: float = DASHBOARD_RENDER_INTERVAL_SECONDS,
        render_callback: Any = None,
    ) -> None:
        """Sequential watchdog monitor loop. Safe to run on a daemon thread."""
        render_callback = render_callback if render_callback is not None else self._render_callback
        display_interval = float(display_interval or self._display_interval or self.DASHBOARD_RENDER_INTERVAL_SECONDS)

        logger = self._logger
        log_event(
            logger, "info", "watchdog_supervisor_started",
            packages=self.packages,
            session_id=str(self.cfg.get("start_session_id") or ""),
            daemon_thread=str(threading.current_thread().daemon).lower(),
        )
        db.insert_event(
            "INFO", "watchdog_supervisor_started",
            f"watching {len(self.packages)} packages: {', '.join(self.packages)}",
        )

        _next_render = time.time()

        def _maybe_render(*, force: bool = False) -> None:
            nonlocal _next_render
            cb = render_callback or self._render_callback
            if cb is not None and (force or time.time() >= _next_render):
                try:
                    cb()
                except Exception:  # noqa: BLE001
                    pass
                _next_render = time.time() + display_interval

        while not self.stop_event.is_set() and not self._all_launches_completed:
            log_event(
                logger, "debug", "[DENG_REJOIN_WATCHDOG_LAUNCH_LATCH]",
                waiting="true",
                all_launches_completed="false",
            )
            self._interruptible_sleep(1.0)

        if self.stop_event.is_set():
            log_event(logger, "info", "watchdog_supervisor_stopped")
            return

        log_event(
            logger, "info", "[DENG_REJOIN_WATCHDOG_LAUNCH_LATCH_RELEASED]",
            all_launches_completed="true",
            package_count=len(self.packages),
        )

        while not self.stop_event.is_set():
            self._round += 1
            now = time.time()
            now_mono = time.monotonic()
            total = len(self.packages)
            round_robin_sec = float(self.PACKAGE_ROUND_ROBIN_SECONDS)
            self._watchdog_round_rate_limited = False

            _any_url = any(
                bool(str(effective_private_server_url(e, self.cfg) or "").strip())
                for e in self.entries
            )
            log_event(
                logger, "info", "[DENG_REJOIN_WATCHDOG_ROUND]",
                round=self._round,
                total_packages=total,
                round_robin_sec=round_robin_sec,
                private_url_configured=str(_any_url).lower(),
            )
            log_event(
                logger, "info", "[DENG_REJOIN_WATCHDOG_ROUND_START]",
                round=self._round,
                total=total,
            )

            checked = 0
            round_started = time.monotonic()
            for idx, pkg in enumerate(self.packages, 1):
                if self.stop_event.is_set():
                    break

                if str(self.status_map.get(pkg) or "").strip() == STATUS_SUSPENDED:
                    log_event(
                        logger, "info", "[DENG_REJOIN_WATCHDOG_SKIP_SUSPENDED]",
                        round=self._round,
                        package=pkg,
                    )
                    continue

                entry = self.entry_by_pkg[pkg]
                self.checking_label = f"Checking Package {idx}/{total}"

                _maybe_render(force=True)
                check_started = time.monotonic()
                try:
                    from . import safe_io as _safe_io
                    _safe_io.set_crash_context(
                        phase="package_check",
                        session_id=str(self.cfg.get("start_session_id") or ""),
                        screen_mode=str(self.cfg.get("screen_mode") or ""),
                        package_count=total,
                        package=pkg,
                        package_index=idx,
                        watchdog_round=self._round,
                    )
                except Exception:  # noqa: BLE001
                    pass
                log_event(
                    logger, "info", "[DENG_REJOIN_PACKAGE_CHECK_START]",
                    round=self._round,
                    index=idx,
                    total=total,
                    package=pkg,
                )

                prev = self._prev_state.get(pkg, self.status_map.get(pkg, ""))
                error_text = ""
                launching_eval = self._needs_launching_evaluation(pkg)
                self._set_status(pkg, STATUS_CHECKING)
                _maybe_render(force=True)
                self._interruptible_sleep(self.PACKAGE_CHECKING_HOLD_SECONDS)
                try:
                    if launching_eval:
                        state, detail = self._evaluate_launching_or_pending(pkg, entry)
                    else:
                        state, detail = self._detect_package_state(pkg, entry)
                except Exception as exc:  # noqa: BLE001
                    error_text = str(exc)[:180]
                    state = prev if prev in {STATUS_ONLINE, STATUS_NO_HEARTBEAT, STATUS_DEAD} else STATUS_NO_HEARTBEAT
                    detail = {
                        "process_running": "unknown",
                        "in_game": "unknown",
                        "heartbeat_ok": "unknown",
                        "warning_detected": "unknown",
                        "elapsed_ms": int((time.monotonic() - check_started) * 1000),
                        "reason": "detector_exception",
                    }

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
                log_event(
                    logger, "info", "[DENG_REJOIN_PACKAGE_CHECK_END]",
                    round=self._round,
                    index=idx,
                    total=total,
                    package=pkg,
                    state=state,
                    elapsed_ms=detail.get("elapsed_ms", int((time.monotonic() - check_started) * 1000)),
                    error=error_text,
                )
                checked += 1

                if state == STATUS_FAILED:
                    state = STATUS_NO_HEARTBEAT

                prev_pinned = self._prev_state.get(pkg)
                pin_states = {STATUS_LAUNCHING, STATUS_REOPENING}
                if (
                    not launching_eval
                    and self._in_grace(pkg, now)
                    and prev_pinned in pin_states
                    and state in pin_states
                    and state not in {STATUS_ONLINE, STATUS_JOIN_FAILED, STATUS_WRONG_GAME, STATUS_FAILED}
                ):
                    state = prev_pinned
                elif state in {STATUS_DEAD, STATUS_FAILED, STATUS_JOIN_FAILED}:
                    self._grace_until.pop(pkg, None)
                    from . import package_state as _ps
                    _ps.clear_launch_lock(pkg, "supervisor_dead_state")
                self._set_status(pkg, state)
                self._prev_state[pkg] = state

                if state == STATUS_NO_HEARTBEAT and prev != STATUS_NO_HEARTBEAT:
                    if not self._in_loading_grace(pkg):
                        self._nhb_since.setdefault(pkg, now_mono)
                elif state != STATUS_NO_HEARTBEAT:
                    self._nhb_since.pop(pkg, None)

                if state in _METRIC_ACTIVE_STATES:
                    self._last_online_ts[pkg] = now
                    if prev not in _METRIC_ACTIVE_STATES:
                        self._online_start_ts[pkg] = now
                elif pkg in self._online_start_ts and state not in _METRIC_ACTIVE_STATES:
                    del self._online_start_ts[pkg]
                if state == STATUS_DEAD:
                    self._last_online_ts.pop(pkg, None)
                    self._online_start_ts.pop(pkg, None)
                    _maybe_render(force=True)

                recovery_gate = False
                if state not in {STATUS_JOIN_FAILED, STATUS_WRONG_GAME, STATUS_UNKNOWN}:
                    if state == STATUS_NO_HEARTBEAT and not self._in_loading_grace(pkg):
                        recovery_gate = self._handle_state(
                            pkg, entry, state, prev, now, render_callback=render_callback
                        )
                    elif state != STATUS_NO_HEARTBEAT and (
                        not self._in_grace(pkg, now) or state in {
                            STATUS_DEAD, STATUS_FAILED, STATUS_JOIN_FAILED
                        }
                    ):
                        recovery_gate = self._handle_state(
                            pkg, entry, state, prev, now, render_callback=render_callback
                        )

                    if self.status_map.get(pkg) == STATUS_DEAD and state != STATUS_DEAD:
                        recovery_gate = self._handle_state(
                            pkg, entry, STATUS_DEAD, prev, now, render_callback=render_callback
                        ) or recovery_gate

                if recovery_gate:
                    self._run_blocking_recovery_gate(
                        pkg,
                        entry,
                        package_index=idx,
                        package_total=total,
                        render_callback=render_callback,
                    )
                    _maybe_render()
                    continue

                _maybe_render()
                if not self.stop_event.is_set():
                    log_event(
                        logger, "info", "[DENG_REJOIN_WATCHDOG_ROUND_ROBIN_PAUSE]",
                        round=self._round,
                        package=pkg,
                        next_package_index=(idx + 1) if idx < total else 1,
                        checking_hold_sec=self.PACKAGE_CHECKING_HOLD_SECONDS,
                        tail_pause_sec=self.PACKAGE_ROUND_ROBIN_TAIL_SECONDS,
                    )
                    self._interruptible_sleep(self.PACKAGE_ROUND_ROBIN_TAIL_SECONDS)

            _counts = {
                "online":       sum(1 for v in self.status_map.values() if v == STATUS_ONLINE),
                "dead":         sum(1 for v in self.status_map.values() if v == STATUS_DEAD),
                "no_heartbeat": sum(1 for v in self.status_map.values() if v == STATUS_NO_HEARTBEAT),
            }
            log_event(
                logger, "info", "[DENG_REJOIN_WATCHDOG_CONTINUES]",
                online_packages=_counts["online"],
                dead_packages=_counts["dead"],
                no_heartbeat_packages=_counts["no_heartbeat"],
                next_round_robin_sec=round_robin_sec,
            )
            log_event(
                logger, "info", "[DENG_REJOIN_WATCHDOG_ROUND_END]",
                round=self._round,
                checked=checked,
                expected=total,
                duration_ms=int((time.monotonic() - round_started) * 1000),
            )

            if not self.stop_event.is_set():
                self.checking_label = f"Checking Package 1/{total}"

            if self._watchdog_round_rate_limited and not self.stop_event.is_set():
                log_event(
                    logger,
                    "info",
                    "[DENG_REJOIN_PRESENCE_RATE_LIMIT_BACKOFF]",
                    backoff_sec=self.PRESENCE_RATE_LIMIT_BACKOFF_SECONDS,
                    until=round(self._presence_rate_limit_until, 3),
                )
                self._interruptible_sleep(self.PRESENCE_RATE_LIMIT_BACKOFF_SECONDS)

        db.insert_event("INFO", "watchdog_supervisor_stopped", "session ended by user")
        log_event(logger, "info", "watchdog_supervisor_stopped")

    def stop(self, source: str = "programmatic") -> None:
        """Signal the supervisor loop to stop."""
        self.stop_source = source or "programmatic"
        try:
            self.stop_stack = "".join(traceback.format_stack(limit=8))[:1800]
        except Exception:  # noqa: BLE001
            self.stop_stack = ""
        log_event(
            self._logger,
            "info",
            "[DENG_REJOIN_STOP_REQUEST]",
            source=self.stop_source,
            stack=self.stop_stack,
            allowed="true",
        )
        self.stop_event.set()

    def get_status_snapshot(
        self, entries: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        entry_map: dict[str, str] = {}
        use = entries or self.entries
        from . import package_username as _pu
        for e in use:
            pkg = str(e.get("package") or "")
            entry_map[pkg] = _pu.username_display_for_package(pkg).username_display

        snapshot: list[dict[str, Any]] = []
        for pkg in self.packages:
            status = self.status_map.get(pkg, STATUS_DEAD)
            ram_mb: str | None = None
            if status in _METRIC_ACTIVE_STATES:
                try:
                    ram_result = android.get_package_ram_usage(pkg, self._root_info)
                    ram_mb = str(ram_result.get("usage_mb") or "")
                except Exception:  # noqa: BLE001
                    ram_mb = None
            pres = self._presence_last_detail.get(pkg, {})
            snapshot.append(
                {
                    "package":      pkg,
                    "username":     entry_map.get(pkg, ""),
                    "status":       status,
                    "presence_profile": pres.get("roblox_presence_profile") or "",
                    "revive_count": self._revive_count.get(pkg, 0),
                    "failure_count": self._failure_count.get(pkg, 0),
                    "last_error":   None,
                    "online_since": self._online_start_ts.get(pkg) or self._last_online_ts.get(pkg),
                    "last_seen_at": self._last_online_ts.get(pkg),
                    "ram_mb":       ram_mb,
                }
            )
        return snapshot
