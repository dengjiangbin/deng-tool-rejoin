"""Auto rejoin supervisor loop."""

from __future__ import annotations

import gc
import signal
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import android, db
from .backoff import calculate_backoff_seconds
from .config import effective_private_server_url, load_config, private_url_launch_context, validate_config
from .launcher import RECOVERY_LAUNCH_REASONS, launch_package_for_current_config, perform_rejoin
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
    3. Trigger a direct (post-launch) resize via root so the relaunched
       window lands at the stored bounds.  Never call
       ``apply_window_layout_silent`` here — that re-runs global freeform
       setup and mass force-closes every app + Termux (probe p-6c644c4708,
       p-e2fe87273b).

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

        # Direct resize via root only — never poke global freeform/WM flags
        # while other clone packages and Termux are still live.  Skip
        # windowing-mode flips too: launch already used --windowingMode 5 and
        # set-task-windowing-mode during recovery mass-closes other windows
        # (probe p-f609904dd1).
        try:
            ok, detail = window_apply.force_resize_package(
                package, rects[0], skip_windowing_mode_flip=True,
            )
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
STATUS_READY             = "Ready"               # Awaiting first staggered open
STATUS_REOPENING         = "Relaunching"        # legacy constant name
STATUS_RELAUNCHING       = "Relaunching"
STATUS_WAITING           = "Waiting"
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
#   Dead          — process not running

# Presence-confirmed steady states that kick off RAM/runtime metric loops.
_METRIC_ACTIVE_STATES = frozenset({STATUS_ONLINE})
_ACCOUNT_DEAD_WEBHOOK_STATES = frozenset({
    STATUS_DEAD,
    STATUS_DISCONNECTED,
    STATUS_JOIN_FAILED,
})

# All healthy states — used for legacy _PackageWorker state-machine guards.
# WatchdogSupervisor never reads _HEALTHY_STATES; only _PackageWorker uses it.
# STATUS_LOBBY stays in _HEALTHY_STATES for _PackageWorker backward compat.
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
                    # check below will determine the public Online/Relaunching/Failed.
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
        Online        — Android reports current package/process evidence.
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
    # These are cosmetic pacing sleeps, NOT detection time: real detection is
    # prefetched in parallel (_prefetch_package_detection).  They were the main
    # reason "presence / dead detection" felt slow — a full round (and thus the
    # 2-evaluation dead confirmation) was gated by ~3s/package.  Cut to ~1.1s/
    # package (probe p-6c644c4708) so force-close/dead is caught ~3x sooner
    # while still showing a visible per-package "Checking" tick.
    PACKAGE_ROUND_ROBIN_SECONDS: int = 3
    PACKAGE_CHECKING_HOLD_SECONDS: float = 0.5
    PACKAGE_ROUND_ROBIN_TAIL_SECONDS: float = 0.6
    PACKAGE_STAGGER_TAIL_SECONDS: float = 0.5
    DEFAULT_DETECTION_WORKER_COUNT: int = 2

    # ── Blocking recovery gate: poll interval while fixing one package ───────
    RECOVERY_GATE_POLL_SECONDS: float = 5.0
    RECOVERY_MAX_CONSECUTIVE_LAUNCHES: int = 3
    RECOVERY_LAUNCH_THROTTLE_SECONDS: float = 60.0

    # ── Roblox presence API fault shield (429 / 5xx / timeout) ───────────────
    PRESENCE_RATE_LIMIT_BACKOFF_SECONDS: float = 15.0
    PRESENCE_API_FAULT_BACKOFF_SECONDS: float = 15.0

    # ── Lobby transition allowance after loading grace expires ───────────────
    LOBBY_TRANSITION_SECONDS: int = 180

    # ── Recovery breathing room after force-stop (LMK / phantom-process guard) ─
    RECOVERY_FORCE_STOP_BREATH_SECONDS: float = 1.5

    # ── No-Heartbeat kill-switch: force-stop after continuous stall ─────────
    NHB_KILL_SWITCH_SECONDS: int = 60
    MISSING_EVIDENCE_CONFIRM_SECONDS: float = 15.0

    # ── Post-launch transition allowance before presence becomes decisive ───
    LOADING_GRACE_SECONDS: int = 30

    # ── Main-thread dashboard repaint cadence (must be <= Checking hold) ───
    DASHBOARD_RENDER_INTERVAL_SECONDS: float = 0.5

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
            pkg: STATUS_READY for pkg in self.packages
        }
        self._package_opened: set[str] = set()
        # Normalize prior sessions to the Android-local public vocabulary.
        if initial_status:
            for pkg, st in initial_status.items():
                if pkg not in self.status_map:
                    continue
                value = str(st or "").strip()
                if value == STATUS_ONLINE:
                    self.status_map[pkg] = STATUS_ONLINE
                elif value == STATUS_PENDING:
                    self.status_map[pkg] = STATUS_READY
                elif value == STATUS_READY:
                    self.status_map[pkg] = STATUS_READY
                elif value in {
                    STATUS_RELAUNCHING,
                    STATUS_REOPENING,
                    STATUS_LAUNCHING,
                    STATUS_WAITING,
                    STATUS_CHECKING,
                    "Launching",
                    "Reconnecting",
                    "Joining",
                }:
                    self.status_map[pkg] = STATUS_LAUNCHING
                elif value in {STATUS_DISCONNECTED, STATUS_JOIN_FAILED, STATUS_FAILED}:
                    self.status_map[pkg] = value
                elif value == STATUS_DEAD:
                    self.status_map[pkg] = STATUS_DEAD
                else:
                    self.status_map[pkg] = STATUS_LAUNCHING

        self.on_status_change = on_status_change

        # Dashboard: "Checking Package X/Y" — updated by inner loop, read by callback.
        self.checking_label: str = ""

        self._round: int = 0

        # Global latch: watchdog idles until cmd_start finishes ALL staggered launches.
        self._all_launches_completed: bool = False

        # ── Per-package mutable tracking ──────────────────────────────────────
        self._prev_state: dict[str, str] = {}
        self._last_online_ts: dict[str, float] = {}   # last time confirmed Online
        self._online_start_ts: dict[str, float] = {}  # relaunch Online session start (unchanged)
        from .status_monitor_runtime import load_package_launch_started_at

        self._package_launch_started_at: dict[str, float] = load_package_launch_started_at()
        self._relaunch_runtime_active: dict[str, bool] = {}
        self._last_launched_at: dict[str, float] = {}  # monotonic ts of last open/reopen
        self._grace_until: dict[str, float] = {}      # no relaunch until this ts
        self._nhb_offline_count: dict[str, int] = {}  # consecutive offline hits
        self._nhb_since: dict[str, float] = {}  # legacy recovery timing map
        self._nhb_cooldown_until: dict[str, float] = {}  # legacy; unused by kill-switch
        self._revive_count: dict[str, int] = {}
        self._failure_count: dict[str, int] = {}
        self._recovery_launch_attempts: dict[str, int] = {}
        self._recovery_throttle_until: dict[str, float] = {}
        self._relaunch_inflight: set[str] = set()
        self._relaunch_verify_until: dict[str, float] = {}
        self._missing_evidence_since: dict[str, float] = {}
        self._landscape_repair_round: int = 0  # legacy counter; periodic repair disabled

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
        self._lobby_entered_at: dict[str, float] = {}
        self._root_info = android.detect_root()
        from .rjn_lifecycle_monitor import RjnLifecycleMonitor

        self._rjn_monitor = RjnLifecycleMonitor(
            self.packages,
            root_info=self._root_info,
            stop_event=self.stop_event,
        )

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
                target = parse_expected_target_from_url(
                    effective_private_server_url(e, self.cfg),
                    expected_place_id=e.get("expected_place_id") or self.cfg.get("expected_place_id"),
                    expected_root_place_id=e.get("expected_root_place_id") or self.cfg.get("expected_root_place_id"),
                    expected_universe_id=e.get("expected_universe_id") or self.cfg.get("expected_universe_id"),
                )
                try:
                    from .roblox_target_resolver import enrich_expected_target

                    cookie = str(e.get("roblox_cookie") or self.cfg.get("roblox_cookie") or "").strip() or None
                    target = enrich_expected_target(target, cookie=cookie)
                except Exception:  # noqa: BLE001
                    pass
                self._presence_expected_targets[pkg] = target
                # Feed the configured target into the logcat detector so it can flag
                # "Wrong Server" when the client provably joins a different placeId.
                # Only fires when the configured link/config yields a known placeId;
                # share-code-only links leave this unknown (fail-safe, never flags).
                try:
                    self._rjn_monitor.set_expected_target(
                        pkg,
                        place_id=target.expected_place_id,
                        root_place_id=target.expected_root_place_id,
                        universe_id=target.expected_universe_id,
                        private_code=target.expected_private_code,
                        share_type=target.expected_share_type,
                    )
                except Exception:  # noqa: BLE001
                    pass
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

    def _maybe_record_package_launch_started(self, pkg: str, previous_status: str | None) -> None:
        """Persist first Launching timestamp for Status Monitor first-launch runtime."""
        prev = str(previous_status or "").strip()
        if prev in {STATUS_RELAUNCHING, STATUS_REOPENING, STATUS_LAUNCHING}:
            return
        if pkg in self._package_launch_started_at:
            return
        from .status_monitor_runtime import persist_package_launch_started

        ts = persist_package_launch_started(pkg)
        self._package_launch_started_at[pkg] = ts

    def _clear_status_monitor_runtime_state(self, pkg: str) -> None:
        self._package_launch_started_at.pop(pkg, None)
        self._relaunch_runtime_active.pop(pkg, None)
        from .status_monitor_runtime import clear_online_since, clear_package_launch_started

        clear_package_launch_started(pkg)
        clear_online_since(pkg)

    def _status_monitor_runtime_started_at(self, pkg: str, status: str) -> tuple[float | None, str]:
        """Return wall-clock runtime anchor — only from gamejoinloadtime online_since."""
        from .status_monitor_runtime import load_online_since

        if status == STATUS_ONLINE:
            online_since, row = load_online_since(pkg)
            if online_since is not None:
                src = str(row.get("runtime_source") or "gamejoinloadtime")
                return online_since, src
            if pkg in self._online_start_ts:
                return self._online_start_ts[pkg], "gamejoinloadtime"
        return None, "missing"

    def _record_runtime_session_state(
        self, pkg: str, previous_state: str, state: str, now: float
    ) -> None:
        """Maintain online session timestamps from gamejoinloadtime only."""
        from .status_monitor_runtime import load_online_since

        if state in _METRIC_ACTIVE_STATES:
            self._last_online_ts[pkg] = now
            online_since, _ = load_online_since(pkg)
            if online_since is not None:
                self._online_start_ts[pkg] = online_since
            elif state == STATUS_ONLINE and previous_state != STATUS_ONLINE:
                self._online_start_ts.pop(pkg, None)
            if state == STATUS_ONLINE and previous_state != STATUS_ONLINE:
                if previous_state == STATUS_RELAUNCHING:
                    self._relaunch_runtime_active[pkg] = True
                try:
                    from . import webhook as lifecycle_webhook

                    alive_at = online_since or self._online_start_ts.get(pkg) or now
                    lifecycle_webhook.record_package_lifecycle_alive(pkg, alive_at)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    from .android_memory import finalize_launch_incremental_sample
                    finalize_launch_incremental_sample(pkg)
                except Exception:  # noqa: BLE001
                    pass
        elif state not in _METRIC_ACTIVE_STATES:
            self._online_start_ts.pop(pkg, None)

        if state in {STATUS_DEAD, STATUS_DISCONNECTED, STATUS_JOIN_FAILED}:
            self._last_online_ts.pop(pkg, None)
            if state in {STATUS_DEAD, STATUS_JOIN_FAILED}:
                self._clear_status_monitor_runtime_state(pkg)

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
        requested = str(status or "").strip()
        if requested in {STATUS_CHECKING, STATUS_PENDING, STATUS_WAITING, "Joining"}:
            return
        if requested in {
            STATUS_PREPARING,
            STATUS_CLEAR_CACHE,
            STATUS_LAUNCHING,
            STATUS_READY,
            STATUS_RELAUNCHING,
            STATUS_REOPENING,
            STATUS_JOIN_FAILED,
            STATUS_FAILED,
        }:
            status = requested
        elif requested == "Reconnecting":
            status = STATUS_LAUNCHING
        elif requested in {STATUS_ONLINE, STATUS_DEAD, STATUS_DISCONNECTED}:
            status = requested
        else:
            status = STATUS_DEAD
        if requested in {STATUS_REOPENING, STATUS_RELAUNCHING}:
            status = STATUS_RELAUNCHING
        with self._state_lock:
            old = self.status_map.get(pkg)
            self.status_map[pkg] = status
        if old != status:
            if status == STATUS_LAUNCHING and old != STATUS_LAUNCHING:
                self._maybe_record_package_launch_started(pkg, old)
            if callable(self.on_status_change):
                self.on_status_change(pkg, status)

    def watchdog_thread_alive(self) -> bool:
        thread = self._watchdog_thread
        return bool(thread is not None and thread.is_alive())

    def set_render_callback(self, render_callback: Any) -> None:
        self._render_callback = render_callback

    def _post_recovery_memory_flush(self) -> None:
        """Drop transient recovery allocations so Termux stays under LMK pressure."""
        try:
            gc.collect()
        except Exception:  # noqa: BLE001
            pass

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

    def _root_process_running(self, pkg: str) -> tuple[bool, bool]:
        """Return ``(running, checked)`` from one sanitized root ``ps`` probe."""
        try:
            package = android.validate_package_name(pkg)
        except Exception:  # noqa: BLE001
            return False, True
        root_tool = getattr(self._root_info, "tool", None)
        if not getattr(self._root_info, "available", False) or not root_tool:
            return False, False
        try:
            result = android.run_root_command(
                android.process_ps_scan_args(package), root_tool=root_tool, timeout=2,
            )
            return android.ps_output_has_live_package(result.stdout, package), True
        except Exception:  # noqa: BLE001
            return False, True

    def _set_grace(self, pkg: str, now: float, seconds: int | None = None) -> None:
        self._grace_until[pkg] = now + float(seconds or self.DEFAULT_GRACE_SECONDS)

    def mark_all_launches_completed(self) -> None:
        """Release the global launch latch so the watchdog may begin checking."""
        now = time.monotonic()
        with self._state_lock:
            for pkg in self.packages:
                self._last_launched_at.setdefault(pkg, now)
            self._all_launches_completed = True
        try:
            self._rjn_monitor.start_session()
        except Exception:  # noqa: BLE001
            pass
        log_event(
            self._logger,
            "info",
            "[DENG_REJOIN_ALL_LAUNCHES_COMPLETED]",
            packages=self.packages,
            package_count=len(self.packages),
        )

    def _watchdog_monitoring_active(self) -> bool:
        with self._state_lock:
            return bool(self._package_opened) or self._all_launches_completed

    def _opened_packages(self) -> list[str]:
        with self._state_lock:
            return [pkg for pkg in self.packages if pkg in self._package_opened]

    def _detection_worker_count(self) -> int:
        sup = self.cfg.get("supervisor")
        if not isinstance(sup, dict):
            sup = {}
        raw = sup.get("detection_worker_count", self.cfg.get("detection_worker_count"))
        if raw is None:
            raw = self.DEFAULT_DETECTION_WORKER_COUNT
        try:
            count = int(raw)
        except (TypeError, ValueError):
            count = self.DEFAULT_DETECTION_WORKER_COUNT
        return max(1, min(count, 3))

    def _round_robin_tail_seconds(self) -> float:
        with self._state_lock:
            stagger_active = bool(self._package_opened) and not self._all_launches_completed
        if stagger_active:
            return float(self.PACKAGE_STAGGER_TAIL_SECONDS)
        return float(self.PACKAGE_ROUND_ROBIN_TAIL_SECONDS)

    def _prefetch_package_detection(
        self,
        packages: list[str],
    ) -> dict[str, tuple[str, dict[str, Any], bool, str]]:
        """Best-effort parallel detection for opened packages (recovery stays sequential)."""
        if len(packages) <= 1 or self._detection_worker_count() <= 1:
            return {}

        def _detect_one(pkg: str) -> tuple[str, str, dict[str, Any], bool, str]:
            entry = self.entry_by_pkg[pkg]
            launching_eval = self._needs_launching_evaluation(pkg)
            try:
                if launching_eval:
                    state, detail = self._evaluate_launching_or_pending(pkg, entry)
                else:
                    state, detail = self._detect_package_state(pkg, entry)
                return pkg, state, detail, launching_eval, ""
            except Exception as exc:  # noqa: BLE001
                prev = str(self._prev_state.get(pkg) or self.status_map.get(pkg) or "")
                state = prev if prev in {STATUS_ONLINE, STATUS_DEAD} else STATUS_DEAD
                detail = {
                    "process_running": "unknown",
                    "in_game": "unknown",
                    "heartbeat_ok": "unknown",
                    "warning_detected": "unknown",
                    "reason": "detector_exception",
                }
                return pkg, state, detail, launching_eval, str(exc)[:180]

        prefetched: dict[str, tuple[str, dict[str, Any], bool, str]] = {}
        workers = min(self._detection_worker_count(), len(packages))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="deng-detect") as pool:
            futures = [pool.submit(_detect_one, pkg) for pkg in packages]
            for future in as_completed(futures):
                pkg, state, detail, launching_eval, error_text = future.result()
                prefetched[pkg] = (state, detail, launching_eval, error_text)
        return prefetched

    def mark_package_launched(self, pkg: str) -> None:
        """Register a launch/reopen and bind loading-grace protection."""
        first_open = False
        with self._state_lock:
            first_open = pkg not in self._package_opened
            self._package_opened.add(pkg)
        if first_open:
            try:
                self._rjn_monitor.start_session()
            except Exception:  # noqa: BLE001
                pass
        self._mark_launched(pkg)
        try:
            self._rjn_monitor.note_launch_watchdog(pkg, relaunch=False)
        except Exception:  # noqa: BLE001
            pass
        self._set_status(pkg, STATUS_LAUNCHING)

    def _mark_launched(self, pkg: str) -> None:
        """Record a fresh open/reopen and reset legacy recovery timing."""
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
        """Arm the API fault safe-state shield and round-robin cooling backoff."""
        backoff = float(self.PRESENCE_API_FAULT_BACKOFF_SECONDS)
        until = time.monotonic() + backoff
        self._presence_rate_limit_until = max(self._presence_rate_limit_until, until)
        self._watchdog_round_rate_limited = True

    def _note_presence_api_fault(self) -> None:
        """Alias for the unified API outage / rate-limit shield."""
        self._note_presence_rate_limit()

    def _presence_rate_limit_active(self) -> bool:
        return time.monotonic() < float(self._presence_rate_limit_until)

    def _presence_api_fault_active(self) -> bool:
        return self._presence_rate_limit_active()

    def _note_lobby_entered(self, pkg: str) -> None:
        self._lobby_entered_at.setdefault(pkg, time.monotonic())

    def _in_lobby_transition_allowance(self, pkg: str) -> bool:
        entered = self._lobby_entered_at.get(pkg)
        if entered is None:
            return True
        return (time.monotonic() - entered) < float(self.LOBBY_TRANSITION_SECONDS)

    def _clear_lobby_state(self, pkg: str) -> None:
        self._lobby_entered_at.pop(pkg, None)

    def _preserve_package_state_on_rate_limit(
        self,
        pkg: str,
        *,
        t0: float,
        pres_detail: dict[str, Any] | None = None,
        fault: str = "rate_limited",
    ) -> tuple[str, dict[str, Any]]:
        """Keep the last known good state when Roblox API calls fault out."""
        preserved = str(self._prev_state.get(pkg) or self.status_map.get(pkg) or "").strip()
        if preserved in {STATUS_CHECKING, STATUS_PENDING, ""}:
            preserved = STATUS_ONLINE if self._last_online_ts.get(pkg) else STATUS_LAUNCHING
        if preserved == STATUS_DEAD and self._last_online_ts.get(pkg):
            preserved = STATUS_ONLINE
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        detail = {
            "process_running": "unknown",
            "in_game": "unknown",
            "heartbeat_ok": "unknown",
            "warning_detected": "false",
            "elapsed_ms": elapsed_ms,
            "root_available": str(bool(getattr(self._root_info, "available", False))).lower(),
            "foreground_package": "",
            "activity": preserved,
            "in_game_proof": "unknown",
            "reason": f"presence_api_{fault}_preserve_state",
        }
        self._log_state_evidence(
            pkg,
            detail,
            pres_detail or self._presence_last_detail.get(pkg, {}),
            preserved,
        )
        return preserved, detail

    def _preserve_package_state_on_api_fault(
        self,
        pkg: str,
        *,
        t0: float,
        pres_detail: dict[str, Any] | None = None,
        fault: str = "fault",
    ) -> tuple[str, dict[str, Any]]:
        return self._preserve_package_state_on_rate_limit(
            pkg,
            t0=t0,
            pres_detail=pres_detail,
            fault=fault,
        )

    def _preserve_live_process_on_api_fault(
        self,
        pkg: str,
        *,
        t0: float,
        pres_detail: dict[str, Any] | None = None,
        fault: str = "fault",
    ) -> tuple[str, dict[str, Any]]:
        """Keep a root-confirmed live package out of recovery on API failure."""
        preserved = str(self._prev_state.get(pkg) or self.status_map.get(pkg) or "").strip()
        if preserved in {
            "", STATUS_CHECKING, STATUS_PENDING, STATUS_LAUNCHING,
            STATUS_WAITING, STATUS_DEAD,
        }:
            preserved = STATUS_ONLINE
        detail = {
            "process_running": "true",
            "in_game": "unknown",
            "heartbeat_ok": "unknown",
            "warning_detected": "false",
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "root_available": "true",
            "foreground_package": "",
            "activity": preserved,
            "in_game_proof": "unknown",
            "reason": f"presence_api_{fault}_process_alive_preserve_state",
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
    ) -> bool:
        """Force-stop and relaunch one package during the recovery gate."""
        logger = self._logger
        if not self._reserve_recovery_launch_attempt(pkg):
            return False
        url_context = private_url_launch_context(entry, self.cfg)
        url_configured = url_context.get("url_mode") == "private_url"
        log_event(
            logger,
            "info",
            "[DENG_REJOIN_RECOVERY_GATE_CYCLE]",
            package=pkg,
            action="force_stop_relaunch",
            private_url_configured=str(url_configured).lower(),
        )
        self._set_status(pkg, STATUS_REOPENING)
        self._mark_launched(pkg)
        if callable(render_callback):
            try:
                render_callback()
            except Exception:  # noqa: BLE001
                pass
        # Detached /data/local/tmp script only does a plain MAIN launch — no
        # private-server URL.  When a URL is configured, always use the
        # canonical perform_rejoin path so only THIS package is stopped and
        # rejoined.  Never use detached dispatch with URL mode (probe
        # p-f609904dd1).
        dispatched = False
        if not url_configured:
            root_tool = getattr(self._root_info, "tool", None)
            dispatched = android.dispatch_detached_force_stop_relaunch(
                pkg,
                root_tool=root_tool,
                sleep_seconds=3.5,
            )
            if dispatched:
                log_event(
                    logger,
                    "info",
                    "[DENG_REJOIN_RECOVERY_DETACHED_DISPATCH]",
                    package=pkg,
                    action="root_tmp_script_force_stop_activity",
                )
                success = True
            else:
                if self._root_process_running(pkg)[0]:
                    if self._force_stop_target_package(pkg):
                        time.sleep(float(self.RECOVERY_FORCE_STOP_BREATH_SECONDS))
                success = self._do_launch(pkg, entry, "recovery_gate_retry")
        else:
            if self._root_process_running(pkg)[0]:
                if self._force_stop_target_package(pkg):
                    time.sleep(float(self.RECOVERY_FORCE_STOP_BREATH_SECONDS))
            success = self._do_launch(pkg, entry, "recovery_gate_retry")
        if success:
            self._set_grace(pkg, now)
            self._mark_launched(pkg)
            self._set_status(pkg, STATUS_LAUNCHING)
        else:
            self._set_status(pkg, STATUS_DEAD)
        self._post_recovery_memory_flush()
        return True

    def _reserve_recovery_launch_attempt(self, pkg: str) -> bool:
        """Reserve one recovery launch, pausing after three unverified attempts."""
        now = time.monotonic()
        until = self._recovery_throttle_until.get(pkg, 0.0)
        if now < until:
            log_event(
                self._logger, "warning", "[DENG_REJOIN_RECOVERY_THROTTLED]",
                package=pkg,
                remaining_sec=round(until - now, 1),
                action="sleep_before_next_package_relaunch",
            )
            return False
        attempts = self._recovery_launch_attempts.get(pkg, 0) + 1
        if attempts >= self.RECOVERY_MAX_CONSECUTIVE_LAUNCHES:
            self._recovery_launch_attempts[pkg] = 0
            self._recovery_throttle_until[pkg] = now + self.RECOVERY_LAUNCH_THROTTLE_SECONDS
            log_event(
                self._logger, "warning", "[DENG_REJOIN_RECOVERY_THROTTLE_ARMED]",
                package=pkg,
                attempts=attempts,
                sleep_sec=self.RECOVERY_LAUNCH_THROTTLE_SECONDS,
                action="sleep_before_next_package_relaunch",
            )
        else:
            self._recovery_launch_attempts[pkg] = attempts
        return True

    def _recovery_throttle_remaining(self, pkg: str) -> float:
        return max(0.0, self._recovery_throttle_until.get(pkg, 0.0) - time.monotonic())

    def _clear_recovery_launch_throttle(self, pkg: str) -> None:
        self._recovery_launch_attempts.pop(pkg, None)
        self._recovery_throttle_until.pop(pkg, None)

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
                        android.process_ps_scan_args(package),
                        root_tool=root_tool,
                        timeout=3,
                    )
                    root_running = bool(
                        res.ok and android.ps_output_has_live_package(res.stdout, package)
                    )
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

            # Obtain (or refresh) the clone's cookie before identity fallback.
            # This lets the authenticated Roblox endpoint identify the exact
            # account even when local prefs are unavailable or malformed.
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

            if uid <= 0 and cookie:
                try:
                    authenticated_uid = _rp.authenticated_user_id(cookie)
                except Exception:  # noqa: BLE001
                    authenticated_uid = None
                if authenticated_uid:
                    uid = int(authenticated_uid)
                    self._presence_user_ids[pkg] = uid
                    detail["roblox_user_id_source"] = "authenticated_cookie"

            if uid <= 0:
                uname = self._resolve_presence_username(pkg)
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

            if not cookie and force_cookie_rescan:
                detail["roblox_api_used"] = "false"
                detail["roblox_api_status"] = "skipped"
                detail["presence_error"] = "missing_cookie"
                return None

            detail["roblox_api_used"] = "true"
            try:
                presence = _rp.fetch_presence_dual_verified(uid, cookie=cookie)
            except _rp.RobloxApiFaultError as exc:
                detail["roblox_api_status"] = {
                    "rate_limited": "rate_limited",
                    "server_error": "server_error",
                    "network": "network_error",
                    "timeout": "timeout",
                }.get(str(exc.fault), "failed")
                detail["presence_error"] = f"http_{exc.fault}"
                if exc.status_code is not None:
                    detail["presence_error"] = f"http_{exc.status_code}"
                self._note_presence_api_fault()
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
            from . import roblox_presence as _rp
            from . import safe_http as _sh

            if isinstance(exc, _rp.RobloxApiFaultError):
                fault = str(getattr(exc, "fault", "fault") or "fault")
                detail["roblox_api_used"] = "true"
                detail["roblox_api_status"] = {
                    "rate_limited": "rate_limited",
                    "server_error": "server_error",
                    "network": "network_error",
                    "timeout": "timeout",
                }.get(fault, "failed")
                detail["presence_error"] = f"http_{fault}"
                self._note_presence_api_fault()
                raise
            detail["roblox_api_used"] = "true" if detail.get("roblox_user_id") else "false"
            if isinstance(exc, _sh.SafeHttpStatusError) and int(getattr(exc, "status_code", 0) or 0) == 429:
                detail["roblox_api_status"] = "rate_limited"
                detail["presence_error"] = "http_429"
                self._note_presence_api_fault()
                raise _rp.RobloxRateLimitedError("presence_fetch") from exc
            if isinstance(exc, _sh.SafeHttpStatusError) and _sh.is_server_error_status(
                int(getattr(exc, "status_code", 0) or 0)
            ):
                detail["roblox_api_status"] = "server_error"
                detail["presence_error"] = f"http_{int(exc.status_code)}"
                self._note_presence_api_fault()
                raise _rp.RobloxApiFaultError(
                    "presence_fetch",
                    fault="server_error",
                    status_code=int(exc.status_code),
                ) from exc
            if isinstance(exc, _sh.SafeHttpNetworkError):
                err_text = str(exc).lower()
                fault = "timeout" if "timeout" in err_text or "timed out" in err_text else "network"
                detail["roblox_api_status"] = "timeout" if fault == "timeout" else "network_error"
                detail["presence_error"] = fault
                self._note_presence_api_fault()
                raise _rp.RobloxApiFaultError("presence_fetch", fault=fault) from exc
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
            state = str(self.status_map.get(pkg) or STATUS_DEAD)
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
        """Halt round-robin until one package reaches Online or stable Dead.

        Recovery keeps polling, but package launches are capped at three before
        a 60-second cooling period so a false-negative cannot exhaust Termux.
        """
        logger = self._logger
        log_event(
            logger,
            "info",
            "[DENG_REJOIN_RECOVERY_GATE_ENTER]",
            package=pkg,
            package_index=package_index,
            package_total=package_total,
            poll_sec=self.RECOVERY_GATE_POLL_SECONDS,
            recovery_launch_limit=self.RECOVERY_MAX_CONSECUTIVE_LAUNCHES,
            recovery_throttle_sec=self.RECOVERY_LAUNCH_THROTTLE_SECONDS,
        )
        attempt = 0
        now = time.time()
        try:
            from . import webhook as lifecycle_webhook

            gate_prev = str(
                self._prev_state.get(pkg) or self.status_map.get(pkg) or STATUS_ONLINE
            )
            pending_state, pending_detail = (
                lifecycle_webhook.load_package_lifecycle_dead_pending(pkg)
            )
            if (
                pending_state
                or not lifecycle_webhook.package_lifecycle_dead_already_notified(pkg)
            ):
                dead_state = pending_state or STATUS_DISCONNECTED
                self._maybe_send_package_dead_webhook(
                    pkg,
                    entry,
                    gate_prev,
                    dead_state,
                    now,
                    pending_detail or {},
                    allow_pending_retry=True,
                )
        except Exception:  # noqa: BLE001
            pass
        while not self.stop_event.is_set():
            self.checking_label = ""
            cb = render_callback or self._render_callback
            if callable(cb):
                try:
                    cb()
                except Exception:  # noqa: BLE001
                    pass
            previous_state = self._prev_state.get(pkg, self.status_map.get(pkg, ""))
            state = self._evaluate_package_presence_isolated(pkg, entry)
            self._set_status(pkg, state)
            self._record_runtime_session_state(pkg, previous_state, state, time.time())
            self._prev_state[pkg] = state
            if state == STATUS_ONLINE:
                self._nhb_since.pop(pkg, None)
                self._clear_recovery_launch_throttle(pkg)
                self._maybe_send_package_recovered_webhook(pkg, entry)
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
            log_event(
                logger,
                "info",
                "[DENG_REJOIN_RECOVERY_GATE_POLL]",
                package=pkg,
                state=state,
                attempt=attempt,
                recovery_launch_limit=self.RECOVERY_MAX_CONSECUTIVE_LAUNCHES,
                poll_sec=self.RECOVERY_GATE_POLL_SECONDS,
            )
            # A just-dispatched launch needs time to start.  Poll Launching
            # and Waiting without force-stopping them again; otherwise the
            # launch timestamp is reset every pass and grace can never end.
            if state not in {
                STATUS_LAUNCHING,
                STATUS_WAITING,
                STATUS_RELAUNCHING,
                STATUS_REOPENING,
                STATUS_CHECKING,
                STATUS_PREPARING,
            }:
                dispatched = self._deploy_gate_recovery_cycle(
                    pkg, entry, now, render_callback=render_callback,
                )
                if not dispatched:
                    remaining = self._recovery_throttle_remaining(pkg)
                    if remaining > 0:
                        self._interruptible_sleep(remaining)
                        continue
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
        """True only for the first post-launch cookie/identity rescan."""
        current = str(self.status_map.get(pkg) or "").strip()
        return current == STATUS_LAUNCHING

    def _is_prelaunch_pending(self, pkg: str) -> bool:
        """True while staggered launch has not opened this clone yet."""
        return str(self.status_map.get(pkg) or "").strip() == STATUS_PENDING

    def _evaluate_launching_or_pending(
        self, pkg: str, entry: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Evaluate a launched package from Android-local evidence only."""
        return self._detect_android_package_state(pkg)

        """Legacy detector body retained below this early return.

        Forces an immediate cookie rescan so cloned APK shared_prefs are read
        via root before the presence API call.
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
            state == STATUS_DEAD
            and str(detail.get("reason") or "") in {
                "presence_user_id_pending",
                "cookie_extraction_failed",
                "missing_cookie",
            }
        ):
            state = STATUS_DEAD
            detail = dict(detail)
            detail["activity"] = "Dead"
            detail["heartbeat_ok"] = "false"
            detail["reason"] = (
                "cookie_extraction_failed"
                if cookie_missing or presence_error == "missing_cookie"
                else "presence_identity_unavailable"
            )
            self._log_state_evidence(pkg, detail, pres_detail, STATUS_DEAD)
        elif state == STATUS_DEAD and api_status in {
            "failed", "rate_limited", "network_error", "timeout", "skipped",
        }:
            detail = dict(detail)
            detail["reason"] = f"presence_api_{api_status}"

        return state, detail

    def _definitive_dead_detail(self, detail: dict[str, Any] | None) -> bool:
        if not detail:
            return False
        reason = str(
            detail.get("dead_reason")
            or detail.get("reason_internal")
            or detail.get("launch_failed_reason")
            or detail.get("reason")
            or ""
        ).lower()
        tokens = (
            "process_missing",
            "with_reason",
            "launch_watchdog",
            "no_online_confirmation",
            "force_close",
            "disconnect",
            "logcat_with_reason",
            "idle_disconnect",
            "idle_disconnect_278",
            "ui_disconnect",
            "logcat_disconnect",
            "game_crash",
        )
        if any(token in reason for token in tokens):
            return True
        friendly = str((detail or {}).get("reason_user_friendly") or "").lower()
        friendly_tokens = (
            "disconnect",
            "idle",
            "error code",
            "kicked",
            "closed",
            "force-stopped",
            "crashed",
        )
        return any(token in friendly for token in friendly_tokens)

    def _package_was_steady_online(
        self,
        pkg: str,
        prev: str,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        if prev == STATUS_ONLINE:
            return True
        if self._last_online_ts.get(pkg):
            return True
        try:
            positive = float((detail or {}).get("last_positive_online_evidence_at") or 0.0)
            if positive > 0:
                return True
        except (TypeError, ValueError):
            pass
        try:
            from .status_monitor_runtime import load_online_since

            online_since, _ = load_online_since(pkg)
            if online_since is not None:
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _resolve_lifecycle_dead_reason_text(
        self,
        detail: dict[str, Any] | None,
    ) -> str:
        from .roblox_disconnect_reasons import format_lifecycle_dead_reason

        detail = detail or {}
        internal = str(
            detail.get("reason_internal")
            or detail.get("dead_reason")
            or detail.get("reason")
            or ""
        ).strip()
        matched = str(
            detail.get("disconnect_prompt_text")
            or detail.get("matched_disconnect_text")
            or ""
        ).strip()
        return format_lifecycle_dead_reason(internal, matched or None)

    def _resolve_presence_username(self, pkg: str) -> str:
        """Best-effort Roblox username for presence API when config is empty."""
        cached = str(self._presence_usernames.get(pkg) or "").strip()
        if cached:
            return cached
        entry = self.entry_by_pkg.get(pkg) or {}
        configured = str(entry.get("account_username") or "").strip()
        if configured:
            self._presence_usernames[pkg] = configured
            return configured
        try:
            from .package_identity import get_package_identity

            identity = get_package_identity(pkg) or {}
            identity_user = str(identity.get("username") or "").strip()
            if identity_user:
                self._presence_usernames[pkg] = identity_user
                return identity_user
        except Exception:  # noqa: BLE001
            pass
        try:
            from . import package_username as _pu

            scan = _pu.scan_package_username_root(pkg, timeout_seconds=4.0)
            scanned = str(scan.username or "").strip()
            if scanned:
                self._presence_usernames[pkg] = scanned
                return scanned
        except Exception:  # noqa: BLE001
            pass
        try:
            from .config import get_package_display_username

            display = str(get_package_display_username(entry, self.cfg) or "").strip()
            if display and display.lower() not in {"unknown", "n/a", "—", "-"}:
                self._presence_usernames[pkg] = display
                return display
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _package_awaiting_first_open(self, pkg: str) -> bool:
        return pkg not in self._package_opened

    def _ready_state_detail(self, pkg: str) -> dict[str, Any]:
        return {
            "internal_state": "READY",
            "online_confirmed": "false",
            "process_running": "false",
            "in_game": "false",
            "reason": "awaiting_first_launch",
            "reason_internal": "awaiting_first_launch",
            "detection_only": "true",
        }

    def _fallback_online_allowed(self, pkg: str) -> bool:
        """Weak online fallbacks wait until the post-launch window has elapsed."""
        try:
            row = self._rjn_monitor._states.get(pkg)
        except Exception:  # noqa: BLE001
            row = None
        if row is None or row.launch_started_at <= 0:
            return True
        from .rjn_lifecycle_monitor import LAUNCH_ONLINE_FALLBACK_MIN_AGE_SECONDS

        return (time.time() - row.launch_started_at) >= float(LAUNCH_ONLINE_FALLBACK_MIN_AGE_SECONDS)

    # Per-package wall-clock gate for the slow dumpsys/uiautomator disconnect scan.
    # The authoritative Roblox "Sending disconnect with reason: <code>" line is
    # caught instantly by the logcat stream (probe p-daee3387a8), so the ~8s
    # fallback scan no longer needs to run every round. Running it every round was
    # the dominant cost (~8s/package → ~38s round) that delayed acting on OTHER
    # packages' disconnects and force-closes. Skip it while the stream is fresh;
    # keep it as a slow safety net for a genuinely stalled/broken stream.
    HEAVY_DISCONNECT_SCAN_SECONDS: float = 60.0
    HEAVY_DISCONNECT_STREAM_FRESH_SECONDS: float = 40.0

    def _heavy_disconnect_scan_due(self, pkg: str) -> bool:
        try:
            stream_fresh = self._rjn_monitor.stream_fresh_for(
                pkg, self.HEAVY_DISCONNECT_STREAM_FRESH_SECONDS
            )
        except Exception:  # noqa: BLE001
            stream_fresh = False
        cache = getattr(self, "_last_heavy_disconnect_scan", None)
        if cache is None:
            cache = {}
            self._last_heavy_disconnect_scan = cache
        now = time.monotonic()
        last = cache.get(pkg, 0.0)
        # While the stream is healthy for this package, trust it and only run the
        # heavy scan on a slow cadence. When the stream is stale, scan every round.
        if stream_fresh and (now - last) < self.HEAVY_DISCONNECT_SCAN_SECONDS:
            return False
        cache[pkg] = now
        return True

    def _try_online_evidence_fallback(
        self, pkg: str, ev: Any
    ) -> Any:
        """Presence / fallback online proof when gamejoinloadtime is absent."""
        if self._package_awaiting_first_open(pkg):
            return ev
        if not self._fallback_online_allowed(pkg):
            return ev
        if ev.process_exists and self._heavy_disconnect_scan_due(pkg):
            try:
                from .package_online_evidence import detect_live_disconnect

                disconnect_reason, matched = detect_live_disconnect(
                    pkg,
                    root_info=getattr(self, "_root_info", None),
                )
                if disconnect_reason:
                    self._rjn_monitor.apply_disconnect(
                        pkg,
                        time.time(),
                        reason=disconnect_reason,
                        matched_text=matched,
                    )
                    return self._rjn_monitor.evaluate_package(pkg)
            except Exception:  # noqa: BLE001
                pass
        if ev.is_online_confirmed:
            return ev
        current = str(self.status_map.get(pkg) or "").strip()
        allow_fallback = current in {
            STATUS_LAUNCHING,
            STATUS_RELAUNCHING,
            STATUS_CHECKING,
            STATUS_WAITING,
            STATUS_PENDING,
            STATUS_JOIN_FAILED,
        }
        if not allow_fallback:
            return ev
        if not ev.process_exists:
            return ev
        presence = None
        try:
            presence = self._fetch_presence(pkg, force_cookie_rescan=True)
        except Exception:  # noqa: BLE001
            presence = None
        if presence is not None and getattr(presence, "is_in_game", False):
            try:
                self._rjn_monitor.confirm_online_evidence(
                    pkg,
                    time.time(),
                    source="presence_in_experience",
                )
            except Exception:  # noqa: BLE001
                pass
            return self._rjn_monitor.evaluate_package(pkg)
        try:
            from .package_online_evidence import (
                collect_online_evidence,
                evaluate_online_confirmed,
            )

            scan = collect_online_evidence(pkg, root_info=self._root_info)
            decision = evaluate_online_confirmed(scan)
            if bool(getattr(decision, "is_disconnected", False)):
                from .package_online_evidence import detect_live_disconnect

                disconnect_reason, matched = detect_live_disconnect(
                    pkg,
                    root_info=self._root_info,
                )
                self._rjn_monitor.apply_disconnect(
                    pkg,
                    time.time(),
                    reason=disconnect_reason or "ui_disconnect",
                    matched_text=matched,
                )
                return self._rjn_monitor.evaluate_package(pkg)
            if decision.is_online_confirmed:
                self._rjn_monitor.confirm_online_evidence(
                    pkg,
                    time.time(),
                    source="activity_in_game",
                )
                return self._rjn_monitor.evaluate_package(pkg)
        except Exception:  # noqa: BLE001
            pass
        return ev

    def _try_presence_target_verification(self, pkg: str, ev: Any) -> Any:
        """Roblox Presence API ground-truth for wrong-server (place/universe mismatch)."""
        if self._package_awaiting_first_open(pkg):
            return ev
        target = self._presence_expected_targets.get(pkg)
        if target is None:
            return ev
        has_expectation = bool(getattr(target, "has_game_target", False)) or bool(
            getattr(target, "expected_private_code", "")
        )
        if not has_expectation:
            return ev
        if not ev.process_exists:
            return ev
        try:
            presence = self._fetch_presence(pkg)
        except Exception:  # noqa: BLE001
            return ev
        if presence is None or not getattr(presence, "is_in_game", False):
            return ev
        try:
            from .roblox_target_resolver import presence_matches_target

            matched, _detail = presence_matches_target(presence, target)
            if matched:
                try:
                    self._rjn_monitor.set_observed_from_presence(pkg, presence)
                except Exception:  # noqa: BLE001
                    pass
                return ev
            self._rjn_monitor.apply_disconnect(
                pkg,
                time.time(),
                reason="wrong_server",
                matched_text="Wrong Server (presence target mismatch)",
            )
            return self._rjn_monitor.evaluate_package(pkg)
        except Exception:  # noqa: BLE001
            return ev

    def _detect_android_package_state(self, pkg: str) -> tuple[str, dict[str, Any]]:
        """Merge rjn detection with supervisor launch/relaunch state (detection only)."""
        from .rjn_lifecycle_monitor import (
            STATE_DEAD as RJN_DEAD,
            STATE_DISCONNECTED as RJN_DISCONNECTED,
            STATE_FAILED as RJN_FAILED,
        )

        t0 = time.monotonic()
        if self._package_awaiting_first_open(pkg):
            detail = self._ready_state_detail(pkg)
            detail["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
            self._log_state_evidence(pkg, detail, {}, STATUS_READY)
            return STATUS_READY, detail
        ev = self._rjn_monitor.evaluate_package(pkg)
        ev = self._try_online_evidence_fallback(pkg, ev)
        ev = self._try_presence_target_verification(pkg, ev)
        current = str(self.status_map.get(pkg) or "").strip()
        detail = dict(ev.detail)
        reason = str(detail.get("reason") or ev.reason)

        if ev.is_online_confirmed:
            state = STATUS_ONLINE
            self._relaunch_inflight.discard(pkg)
            self._relaunch_verify_until.pop(pkg, None)
            self._missing_evidence_since.pop(pkg, None)
        elif ev.internal_state == RJN_DISCONNECTED or detail.get("last_with_reason_at"):
            state = STATUS_DISCONNECTED
            reason = str(detail.get("reason_user_friendly") or reason)
            self._relaunch_inflight.discard(pkg)
            self._relaunch_verify_until.pop(pkg, None)
            self._missing_evidence_since.pop(pkg, None)
        elif (
            detail.get("launch_failed_reason") in {
                "launch_watchdog_timeout",
                "no_online_confirmation",
            }
            or ev.internal_state == RJN_FAILED
        ):
            if self._in_loading_grace(pkg) and ev.process_exists:
                state = STATUS_LAUNCHING
                reason = "launch_grace_pending_confirmation"
            else:
                state = STATUS_JOIN_FAILED
                reason = str(detail.get("reason_user_friendly") or reason)
        elif ev.internal_state == RJN_DEAD:
            state = STATUS_DEAD
            reason = str(detail.get("reason_internal") or detail.get("dead_reason") or reason)
            self._relaunch_inflight.discard(pkg)
            self._relaunch_verify_until.pop(pkg, None)
            self._missing_evidence_since.pop(pkg, None)
        elif current in {STATUS_RELAUNCHING, STATUS_REOPENING} and not ev.is_online_confirmed:
            state = STATUS_RELAUNCHING
            verify_until = self._relaunch_verify_until.get(pkg, 0.0)
            if pkg in self._relaunch_inflight and time.monotonic() < verify_until:
                reason = "android_relaunch_verification_pending"
            else:
                reason = "relaunch_pending_gamejoinloadtime"
        elif current in {
            STATUS_LAUNCHING,
            STATUS_WAITING,
            STATUS_CHECKING,
            STATUS_PENDING,
            STATUS_PREPARING,
            STATUS_READY,
        }:
            state = STATUS_LAUNCHING
            if self._in_loading_grace(pkg):
                reason = "launch_grace_started"
            else:
                reason = "launch_pending_gamejoinloadtime"
        elif current == STATUS_ONLINE and not ev.is_online_confirmed:
            if ev.process_exists:
                state = STATUS_LAUNCHING
                reason = str(detail.get("reason_internal") or "online_proof_lost")
            else:
                state = STATUS_DEAD
                reason = str(detail.get("reason_internal") or "online_proof_lost")
        elif current in {STATUS_DEAD, STATUS_DISCONNECTED, STATUS_JOIN_FAILED, STATUS_FAILED}:
            state = current
        else:
            state = current or ev.public_status

        detail.update({
            "process_evidence": str(ev.process_exists).lower(),
            "activity_evidence": "diagnostic_only",
            "window_evidence": "diagnostic_only",
            "surface_evidence": "diagnostic_only",
            "recent_task_evidence": "diagnostic_only",
            "in_game": str(ev.is_online_confirmed).lower(),
            "heartbeat_ok": "not_used",
            "warning_detected": "false",
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "root_available": str(bool(getattr(self._root_info, "available", False))).lower(),
            "foreground_package": "",
            "activity": state,
            "in_game_proof": (
                str(detail.get("online_evidence_source") or "gamejoinloadtime")
                if ev.is_online_confirmed
                else "none"
            ),
            "heartbeat_age_sec": "not_used",
            "presence_source": "not_used",
            "reason": reason,
            "dead_reason": str(detail.get("reason_internal") or detail.get("dead_reason") or ""),
            "dead_detection_evidence": str(detail.get("reason_internal") or reason),
            "online_confirmed": str(ev.is_online_confirmed).lower(),
            "failed_checks": ",".join(ev.failed_checks),
            "internal_state": ev.internal_state,
            "detection_only": "true",
        })
        self._log_state_evidence(pkg, detail, {}, state)
        return state, detail

    def _detect_package_state(
        self, pkg: str, entry: dict[str, Any], *, force_cookie_rescan: bool = False
    ) -> tuple[str, dict[str, Any]]:
        return self._detect_android_package_state(pkg)

        """Determine liveness from root process evidence, then cookie presence."""
        del entry
        t0 = time.monotonic()
        elapsed_ms = lambda: int((time.monotonic() - t0) * 1000)
        pres_detail = self._presence_last_detail.get(pkg, {})
        heartbeat_age_sec = (
            int(time.time() - self._last_online_ts[pkg])
            if self._last_online_ts.get(pkg)
            else ""
        )
        in_loading_grace = self._in_loading_grace(pkg)
        process_running, process_checked = self._root_process_running(pkg)

        def _detail_base(**overrides: Any) -> dict[str, Any]:
            detail = {
                "process_running": (
                    str(process_running).lower() if process_checked else "unknown"
                ),
                "in_game": "false",
                "heartbeat_ok": "false",
                "warning_detected": "false",
                "elapsed_ms": elapsed_ms(),
                "root_available": str(bool(getattr(self._root_info, "available", False))).lower(),
                "foreground_package": "",
                "activity": "",
                "in_game_proof": "false",
                "heartbeat_age_sec": heartbeat_age_sec,
                "presence_source": "root_ps_excluded",
                "reason": "root_ps_evaluated",
            }
            detail.update(overrides)
            return detail

        # An absent PID is definitive local evidence: do not allow delayed
        # Roblox presence to mask a manually killed or crashed package.
        if process_checked and not process_running:
            self._clear_lobby_state(pkg)
            self._nhb_offline_count[pkg] = self._nhb_offline_count.get(pkg, 0) + 1
            detail = _detail_base(
                activity="Dead",
                reason="root_ps_missing",
            )
            self._log_state_evidence(pkg, detail, pres_detail, STATUS_DEAD)
            return STATUS_DEAD, detail

        if self._presence_api_fault_active() or self._watchdog_round_rate_limited:
            if process_checked and process_running:
                return self._preserve_live_process_on_api_fault(
                    pkg,
                    t0=t0,
                    pres_detail=pres_detail,
                    fault="shield_active",
                )
            return self._preserve_package_state_on_api_fault(
                pkg,
                t0=t0,
                pres_detail=pres_detail,
                fault="shield_active",
            )

        presence = None
        try:
            presence = self._fetch_presence(pkg, force_cookie_rescan=force_cookie_rescan)
        except Exception as exc:  # noqa: BLE001
            from . import roblox_presence as _rp

            if isinstance(exc, _rp.RobloxApiFaultError):
                self._note_presence_api_fault()
                if process_checked and process_running:
                    return self._preserve_live_process_on_api_fault(
                        pkg,
                        t0=t0,
                        pres_detail=pres_detail,
                        fault=str(getattr(exc, "fault", "fault") or "fault"),
                    )
                return self._preserve_package_state_on_api_fault(
                    pkg,
                    t0=t0,
                    pres_detail=pres_detail,
                    fault=str(getattr(exc, "fault", "fault") or "fault"),
                )
            raise

        pres_detail = self._presence_last_detail.get(pkg, {})
        profile = str(pres_detail.get("roblox_presence_profile") or "")
        api_status = str(pres_detail.get("roblox_api_status") or "")
        presence_error = str(pres_detail.get("presence_error") or "")
        presence_unknown = presence is None or bool(getattr(presence, "is_unknown", False))

        if process_checked and process_running and presence_unknown and api_status in {
            "failed", "rate_limited", "network_error", "server_error", "timeout",
        }:
            return self._preserve_live_process_on_api_fault(
                pkg,
                t0=t0,
                pres_detail=pres_detail,
                fault=api_status,
            )

        def _presence_detail(**overrides: Any) -> dict[str, Any]:
            detail = _detail_base(
                presence_source="cookie_presence",
                reason="roblox_presence_evaluated",
            )
            detail.update(overrides)
            return detail

        if presence is not None and getattr(presence, "is_in_game", False):
            self._clear_lobby_state(pkg)
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

        if in_loading_grace:
            detail = _presence_detail(
                activity=STATUS_WAITING,
                in_game_proof="unknown",
                reason="presence_checked_loading_grace",
            )
            self._log_state_evidence(pkg, detail, pres_detail, STATUS_WAITING)
            return STATUS_WAITING, detail

        presence_lobby = bool(presence is not None and getattr(presence, "is_lobby", False))
        presence_offline = bool(presence is not None and getattr(presence, "is_offline", False))

        if presence_lobby or presence_offline:
            self._clear_lobby_state(pkg)
            self._nhb_offline_count[pkg] = self._nhb_offline_count.get(pkg, 0) + 1
            detail = _presence_detail(
                activity=profile or ("Lobby" if presence_lobby else "Offline"),
                reason="presence_lobby" if presence_lobby else "presence_offline",
            )
            self._log_state_evidence(pkg, detail, pres_detail, STATUS_DEAD)
            return STATUS_DEAD, detail

        if presence_unknown:
            detail = _presence_detail(
                activity="Dead",
                in_game_proof="unknown",
                reason="presence_unavailable_after_transition",
            )
            self._log_state_evidence(pkg, detail, pres_detail, STATUS_DEAD)
            return STATUS_DEAD, detail

        # Neither layer has declared the package offline.  Keep the previous
        # good state (or Launching for a first observation) instead of treating
        # a missing cookie/API response as proof that recovery is required.
        if presence is None and presence_error == "missing_user_id":
            preserved = STATUS_PENDING
        else:
            preserved = str(self._prev_state.get(pkg) or self.status_map.get(pkg) or "").strip()
        if preserved in {"", STATUS_CHECKING, STATUS_PENDING, STATUS_DEAD}:
            preserved = (
                STATUS_PENDING
                if presence is None and presence_error == "missing_user_id"
                else (STATUS_ONLINE if self._last_online_ts.get(pkg) else STATUS_LAUNCHING)
            )
        detail = _presence_detail(
            activity=preserved,
            in_game_proof="unknown" if presence_unknown else "false",
            reason="presence_unavailable_preserve_state",
        )
        self._log_state_evidence(pkg, detail, pres_detail, preserved)
        return preserved, detail

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
            activity_process_evidence=detail.get("process_evidence", "false"),
            activity_record_evidence=detail.get("activity_evidence", "false"),
            window_evidence=detail.get("window_evidence", "false"),
            surface_evidence=detail.get("surface_evidence", "false"),
            recents_evidence=detail.get("recent_task_evidence", "diagnostic_only"),
            active_process_block_id=detail.get("process_block_id", ""),
            active_activity_block_id=detail.get("activity_block_id", ""),
            active_window_block_id=detail.get("window_block_id", ""),
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
        from .launch_relaunch_trace import record_launch_attempt, sanitized_url_from_context

        url_present, url_masked = sanitized_url_from_context(url_context)
        t0 = time.monotonic()
        try:
            from .android_memory import record_launch_baseline, snapshot_mem_available_kb
            avail = snapshot_mem_available_kb()
            if avail is not None:
                record_launch_baseline(pkg, avail)
        except Exception:  # noqa: BLE001
            pass
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
                # Recovery launch already passes --windowingMode + bounds via
                # perform_rejoin.  Post-launch resize flips WM state and was
                # mass-closing Termux + other clones (probe p-f609904dd1).
                if reason not in RECOVERY_LAUNCH_REASONS:
                    _reapply_layout_for_package(pkg)
            record_launch_attempt(
                pkg,
                action=f"launch_{reason}",
                success=bool(result.success),
                launcher=launcher_label,
                url_present=url_present,
                url_sanitized=url_masked,
                command_type=launcher_label,
                error=str(result.error or ""),
                state_after=str(self.status_map.get(pkg) or ""),
            )
            return result.success
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            record_launch_attempt(
                pkg,
                action=f"launch_{reason}",
                success=False,
                launcher=launcher_label,
                url_present=url_present,
                url_sanitized=url_masked,
                command_type=launcher_label,
                error=str(exc)[:180],
                state_after=str(self.status_map.get(pkg) or ""),
            )
            log_event(
                logger, "error", "[DENG_REJOIN_RECOVERY_LAUNCH_RESULT]",
                package=pkg, reason=reason, launcher=launcher_label,
                return_code=1, success="false",
                stdout="", stderr=str(exc), elapsed_ms=elapsed_ms,
            )
            return False

    def _package_entry_username(self, entry: dict[str, Any]) -> str:
        """Legacy helper — lifecycle webhooks use _resolve_lifecycle_username instead."""
        username = str(entry.get("account_username") or entry.get("label") or "").strip()
        return username

    def _resolve_lifecycle_username(
        self, pkg: str, entry: dict[str, Any]
    ) -> tuple[str | None, str]:
        from .package_identity import record_package_identity, resolve_lifecycle_username

        username, source = resolve_lifecycle_username(
            pkg,
            entry=entry,
            supervisor=self,
            cfg=self.cfg,
        )
        if username:
            record_package_identity(
                pkg,
                username,
                source=source,
                confidence="high",
            )
        return username, source

    def _should_attempt_package_dead_webhook(
        self,
        pkg: str,
        prev: str,
        state: str,
        now: float,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        if state not in _ACCOUNT_DEAD_WEBHOOK_STATES:
            return False
        from . import webhook as lifecycle_webhook

        if lifecycle_webhook.package_lifecycle_dead_already_notified(pkg):
            return False
        was_online = self._package_was_steady_online(pkg, prev, detail)
        definitive = self._definitive_dead_detail(detail)
        if state == STATUS_DISCONNECTED and was_online:
            return True
        if was_online and definitive:
            return True
        if (self._in_loading_grace(pkg) or self._in_grace(pkg, now)) and not definitive:
            return False
        return True

    def _maybe_retry_pending_dead_webhooks(self) -> None:
        from . import webhook as lifecycle_webhook

        for entry in self.entries:
            pkg = str(entry.get("package") or "").strip()
            if not pkg or not lifecycle_webhook.package_lifecycle_dead_pending(pkg):
                continue
            pending_state, detail = lifecycle_webhook.load_package_lifecycle_dead_pending(pkg)
            if not pending_state:
                continue
            prev = str(self._prev_state.get(pkg) or STATUS_ONLINE)
            self._maybe_send_package_dead_webhook(
                pkg,
                entry,
                prev,
                pending_state,
                time.time(),
                detail,
                allow_pending_retry=True,
            )

    def _keep_termux_session_alive(self) -> None:
        try:
            from .termux_session import ensure_termux_session_alive

            ensure_termux_session_alive(self.cfg)
        except Exception:  # noqa: BLE001
            pass

    def _should_notify_package_dead(
        self,
        pkg: str,
        prev: str,
        state: str,
        now: float,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        return self._should_attempt_package_dead_webhook(pkg, prev, state, now, detail)

    def _maybe_send_package_dead_webhook(
        self,
        pkg: str,
        entry: dict[str, Any],
        prev: str,
        state: str,
        now: float,
        detail: dict[str, Any] | None = None,
        *,
        allow_pending_retry: bool = False,
    ) -> None:
        from . import webhook as lifecycle_webhook

        definitive = self._definitive_dead_detail(detail)
        was_online = self._package_was_steady_online(pkg, prev, detail)
        if state in _ACCOUNT_DEAD_WEBHOOK_STATES:
            should_arm = (
                prev == STATUS_ONLINE
                or (definitive and was_online)
                or (
                    definitive
                    and prev in {
                        STATUS_RELAUNCHING,
                        STATUS_REOPENING,
                        STATUS_LAUNCHING,
                    }
                )
            )
            if should_arm:
                lifecycle_webhook.arm_package_lifecycle_dead_episode(pkg)

        if lifecycle_webhook.package_lifecycle_dead_already_notified(pkg):
            lifecycle_webhook.clear_package_lifecycle_dead_pending(pkg)
            return
        if not allow_pending_retry and not self._should_attempt_package_dead_webhook(
            pkg, prev, state, now, detail
        ):
            if state in _ACCOUNT_DEAD_WEBHOOK_STATES and self._definitive_dead_detail(detail):
                lifecycle_webhook.record_package_lifecycle_dead_pending(
                    pkg,
                    state=state,
                    detail=dict(detail or {}),
                )
            return

        username, source = self._resolve_lifecycle_username(pkg, entry)
        if not username:
            lifecycle_webhook.record_package_lifecycle_username_failure(pkg)
            if state in _ACCOUNT_DEAD_WEBHOOK_STATES:
                lifecycle_webhook.record_package_lifecycle_dead_pending(
                    pkg,
                    state=state,
                    detail=dict(detail or {}),
                )
            log_event(
                self._logger,
                "warning",
                "[DENG_REJOIN_PACKAGE_LIFECYCLE_USERNAME]",
                package=pkg,
                event="package_dead",
                result="username_resolution_failed",
                source=source,
            )
            return

        lifecycle_webhook.clear_package_lifecycle_username_failure(pkg)
        dead_at = float(now)
        runtime_seconds = lifecycle_webhook.lifecycle_dead_runtime_seconds(
            pkg,
            dead_at,
            fallback_alive_since=self._online_start_ts.get(pkg),
        )
        if runtime_seconds is None:
            try:
                from .status_monitor_runtime import load_online_since

                online_since, _ = load_online_since(pkg)
                if online_since is not None:
                    runtime_seconds = max(0.0, dead_at - float(online_since))
            except Exception:  # noqa: BLE001
                pass
        if runtime_seconds is None and self._last_online_ts.get(pkg):
            runtime_seconds = max(0.0, dead_at - float(self._last_online_ts[pkg]))
        reason_text = self._resolve_lifecycle_dead_reason_text(detail)
        try:
            ok, _msg = lifecycle_webhook.send_package_lifecycle_alert(
                self.cfg,
                event="package_dead",
                package=pkg,
                username=username,
                runtime_seconds=runtime_seconds,
                dead_reason=reason_text,
                ram_display=str((detail or {}).get("ram_display") or ""),
            )
            if ok:
                lifecycle_webhook.mark_package_lifecycle_dead_notified(pkg, username=username)
                lifecycle_webhook.clear_package_lifecycle_dead_pending(pkg)
        except Exception:  # noqa: BLE001
            pass

    def _maybe_send_package_recovered_webhook(self, pkg: str, entry: dict[str, Any]) -> None:
        from . import webhook as lifecycle_webhook

        if not lifecycle_webhook.package_lifecycle_recover_pending(pkg):
            return

        username, source = self._resolve_lifecycle_username(pkg, entry)
        if not username:
            lifecycle_webhook.record_package_lifecycle_username_failure(pkg)
            log_event(
                self._logger,
                "warning",
                "[DENG_REJOIN_PACKAGE_LIFECYCLE_USERNAME]",
                package=pkg,
                event="package_recovered",
                result="username_resolution_failed",
                source=source,
            )
            return

        lifecycle_webhook.clear_package_lifecycle_username_failure(pkg)
        try:
            ok, _msg = lifecycle_webhook.send_package_lifecycle_alert(
                self.cfg,
                event="package_recovered",
                package=pkg,
                username=username,
            )
            if ok:
                lifecycle_webhook.mark_package_lifecycle_recovered(pkg, username=username)
        except Exception:  # noqa: BLE001
            pass

    def _handle_state(
        self,
        pkg: str,
        entry: dict[str, Any],
        state: str,
        prev: str,
        now: float,
        render_callback: Any = None,
        immediate_recovery: bool = False,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        """Apply recovery action based on current state.

        Recovery rules:
        - Dead        → launch_package_for_current_config
        - Online      → update last_online_ts, keep monitoring

        Returns True when a blocking recovery gate should run for this package.
        """
        logger = self._logger
        url_context = private_url_launch_context(entry, self.cfg)
        url_configured = url_context.get("url_mode") == "private_url"

        if state in {STATUS_DEAD, STATUS_DISCONNECTED, STATUS_JOIN_FAILED}:
            # Never let a stale relaunch-inflight flag block recovery for a fresh
            # disconnect/dead signal (probe p-9c18ae51bc: 278 detected but no relaunch).
            self._relaunch_inflight.discard(pkg)
            self._relaunch_verify_until.pop(pkg, None)
            if not self._reserve_recovery_launch_attempt(pkg):
                remaining = self._recovery_throttle_remaining(pkg)
                if remaining > 0:
                    self._interruptible_sleep(remaining)
                return False
            action = "private_url_relaunch" if url_configured else "app_only_relaunch"
            dead_label = str(state)
            log_event(
                logger, "info", "[DENG_REJOIN_RECOVERY_DECISION]",
                package=pkg, state=dead_label,
                private_url_mode=url_context.get("private_url_mode", "global"),
                url_mode=url_context.get("url_mode", "app_only"),
                url_config_source=url_context.get("url_config_source", "blank"),
                private_url_configured=str(url_configured).lower(),
                action=action,
                reason=str(state).lower(),
            )
            try:
                from .launch_relaunch_trace import record_dead_detected, record_relaunch_queued

                record_dead_detected(
                    pkg,
                    str((detail or {}).get("reason_internal") or state),
                )
                record_relaunch_queued(pkg)
            except Exception:  # noqa: BLE001
                pass
            self._relaunch_inflight.add(pkg)
            self._set_status(pkg, STATUS_REOPENING)
            from .cache_clear_phases import run_recovery_cache_clear

            cache_result = run_recovery_cache_clear(pkg, root_info=self._root_info)
            log_event(
                logger, "info", "[DENG_REJOIN_DEAD_PACKAGE_CACHE_CLEAR]",
                package=pkg,
                success=str(bool(cache_result.get("success"))).lower(),
                method=cache_result.get("method", ""),
                error=cache_result.get("error", ""),
            )
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
                self._relaunch_verify_until[pkg] = time.monotonic() + float(self.LOADING_GRACE_SECONDS)
                self._mark_launched(pkg)
                try:
                    self._rjn_monitor.note_launch_watchdog(pkg, relaunch=True)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    from .launch_relaunch_trace import record_relaunch_started

                    record_relaunch_started(
                        pkg,
                        success=True,
                        url_present=bool(url_configured),
                    )
                except Exception:  # noqa: BLE001
                    pass
                self._set_status(pkg, STATUS_RELAUNCHING)
            else:
                self._failure_count[pkg] = self._failure_count.get(pkg, 0) + 1
                self._relaunch_inflight.discard(pkg)
                self._relaunch_verify_until.pop(pkg, None)
            self._post_recovery_memory_flush()
            return True

        elif state == "__retired_state__":
            if self.status_map.get(pkg) == STATUS_ONLINE:
                self._nhb_since.pop(pkg, None)
                return False
            if self._in_loading_grace(pkg) and not immediate_recovery:
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
            if immediate_recovery:
                log_event(
                    logger, "info", "[DENG_REJOIN_ROOT_PROCESS_HARD_DROP]",
                    package=pkg,
                    action="force_stop_relaunch",
                    reason="root_ps_missing",
                )
                self._nhb_since.pop(pkg, None)
                self._nhb_cooldown_until.pop(pkg, None)
                self._set_status(pkg, STATUS_DEAD)
                self._post_recovery_memory_flush()
                return True
            nhb_since = self._nhb_since.get(pkg)
            now_mono = time.monotonic()
            if nhb_since is None:
                self._nhb_since[pkg] = now_mono
                log_event(
                    logger, "info", "[DENG_REJOIN_RETIRED_STALL_TRACK]",
                    package=pkg,
                    started_at=round(now_mono, 3),
                    kill_switch_sec=self.NHB_KILL_SWITCH_SECONDS,
                )
                return False
            elapsed = now_mono - nhb_since
            if elapsed < self.NHB_KILL_SWITCH_SECONDS:
                log_event(
                    logger, "debug", "[DENG_REJOIN_RETIRED_STALL_WAIT]",
                    package=pkg,
                    elapsed_sec=round(elapsed, 1),
                    remaining_sec=round(self.NHB_KILL_SWITCH_SECONDS - elapsed, 1),
                )
                return False
            log_event(
                logger, "info", "[DENG_REJOIN_RETIRED_STALL_KILL_SWITCH]",
                package=pkg,
                elapsed_sec=round(elapsed, 1),
                action="force_stop",
                reason="retired_stall_path",
            )
            self._nhb_since.pop(pkg, None)
            self._nhb_cooldown_until.pop(pkg, None)
            if self._force_stop_target_package(pkg):
                time.sleep(float(self.RECOVERY_FORCE_STOP_BREATH_SECONDS))
            self._set_status(pkg, STATUS_DEAD)
            if callable(render_callback):
                try:
                    render_callback()
                except Exception:  # noqa: BLE001
                    pass
            self._post_recovery_memory_flush()
            return True

        elif state in _METRIC_ACTIVE_STATES:
            self._nhb_since.pop(pkg, None)
            self._clear_recovery_launch_throttle(pkg)
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
        pss_kb: int = int(ram_result.get("pss_kb") or 0)
        if pss_kb <= 0:
            pss_kb = int(ram_result.get("rss_kb", 0))
        usage_mb: float = pss_kb / 1024.0
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

        # Cache clear is phase-2 recovery only (p-f499f7533a). High-RAM Online
        # packages are report-only below — no watchdog cache trim subprocess burst.

        if usage_mb <= restart_threshold:
            return  # Below restart threshold — nothing else to do.

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

        while not self.stop_event.is_set() and not self._watchdog_monitoring_active():
            log_event(
                logger, "debug", "[DENG_REJOIN_WATCHDOG_LAUNCH_LATCH]",
                waiting="true",
                all_launches_completed="false",
                opened_packages=len(self._package_opened),
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
            self._keep_termux_session_alive()
            self._maybe_retry_pending_dead_webhooks()

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

            try:
                self._rjn_monitor.write_probe_file()
            except Exception:  # noqa: BLE001
                pass

            checked = 0
            round_started = time.monotonic()
            opened_packages = self._opened_packages()
            monitor_total = len(opened_packages) or len(self.packages)
            prefetched = self._prefetch_package_detection(opened_packages)
            opened_index = 0
            for idx, pkg in enumerate(self.packages, 1):
                if self.stop_event.is_set():
                    break
                if self._package_awaiting_first_open(pkg):
                    continue

                opened_index += 1
                entry = self.entry_by_pkg[pkg]
                self.checking_label = ""

                _maybe_render(force=True)
                check_started = time.monotonic()
                try:
                    from . import safe_io as _safe_io
                    _safe_io.set_crash_context(
                        phase="package_check",
                        session_id=str(self.cfg.get("start_session_id") or ""),
                        screen_mode=str(self.cfg.get("screen_mode") or ""),
                        package_count=monitor_total,
                        package=pkg,
                        package_index=opened_index,
                        watchdog_round=self._round,
                    )
                except Exception:  # noqa: BLE001
                    pass
                log_event(
                    logger, "info", "[DENG_REJOIN_PACKAGE_CHECK_START]",
                    round=self._round,
                    index=opened_index,
                    total=monitor_total,
                    package=pkg,
                    detection_workers=self._detection_worker_count(),
                    prefetch_used=str(pkg in prefetched).lower(),
                )

                prev = self._prev_state.get(pkg, self.status_map.get(pkg, ""))
                error_text = ""
                launching_eval = self._needs_launching_evaluation(pkg)
                self._set_status(pkg, STATUS_CHECKING)
                _maybe_render(force=True)
                self._interruptible_sleep(self.PACKAGE_CHECKING_HOLD_SECONDS)
                if pkg in prefetched:
                    state, detail, launching_eval, error_text = prefetched[pkg]
                else:
                    try:
                        if launching_eval:
                            state, detail = self._evaluate_launching_or_pending(pkg, entry)
                        else:
                            state, detail = self._detect_package_state(pkg, entry)
                    except Exception as exc:  # noqa: BLE001
                        error_text = str(exc)[:180]
                        state = prev if prev in {STATUS_ONLINE, STATUS_DEAD} else STATUS_DEAD
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
                    index=opened_index,
                    total=monitor_total,
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
                    index=opened_index,
                    total=monitor_total,
                    package=pkg,
                    state=state,
                    elapsed_ms=detail.get("elapsed_ms", int((time.monotonic() - check_started) * 1000)),
                    error=error_text,
                )
                checked += 1

                if state == STATUS_FAILED:
                    state = STATUS_JOIN_FAILED
                process_hard_drop = (
                    state == STATUS_DEAD
                    and str(detail.get("reason") or "") == "root_ps_missing"
                )

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

                if False:
                    if process_hard_drop or not self._in_loading_grace(pkg):
                        self._nhb_since.setdefault(pkg, now_mono)
                else:
                    self._nhb_since.pop(pkg, None)

                self._record_runtime_session_state(pkg, prev, state, now)
                if state in _ACCOUNT_DEAD_WEBHOOK_STATES:
                    self._maybe_send_package_dead_webhook(pkg, entry, prev, state, now, detail)
                    _maybe_render(force=True)

                recovery_gate = False
                if state not in {STATUS_WRONG_GAME, STATUS_UNKNOWN}:
                    if state in {STATUS_DEAD, STATUS_DISCONNECTED, STATUS_JOIN_FAILED}:
                        recovery_gate = self._handle_state(
                            pkg, entry, state, prev, now,
                            render_callback=render_callback,
                            detail=detail,
                        )
                    elif state == STATUS_ONLINE:
                        recovery_gate = self._handle_state(
                            pkg, entry, state, prev, now, render_callback=render_callback,
                            detail=detail,
                        )

                    if self.status_map.get(pkg) == STATUS_DEAD and state != STATUS_DEAD:
                        recovery_gate = self._handle_state(
                            pkg, entry, STATUS_DEAD, prev, now, render_callback=render_callback,
                            detail=detail,
                        ) or recovery_gate

                if recovery_gate:
                    self._run_blocking_recovery_gate(
                        pkg,
                        entry,
                        package_index=opened_index,
                        package_total=monitor_total,
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
                        next_package_index=(opened_index + 1) if opened_index < monitor_total else 1,
                        checking_hold_sec=self.PACKAGE_CHECKING_HOLD_SECONDS,
                        tail_pause_sec=self._round_robin_tail_seconds(),
                    )
                    self._interruptible_sleep(self._round_robin_tail_seconds())

            _counts = {
                "online":       sum(1 for v in self.status_map.values() if v == STATUS_ONLINE),
                "dead":         sum(1 for v in self.status_map.values() if v == STATUS_DEAD),
            }
            log_event(
                logger, "info", "[DENG_REJOIN_WATCHDOG_CONTINUES]",
                online_packages=_counts["online"],
                dead_packages=_counts["dead"],
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
                self.checking_label = ""

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
            pss_mb: int = 0
            if status in _METRIC_ACTIVE_STATES:
                try:
                    ram_result = android.get_package_ram_usage(pkg, self._root_info)
                    ram_mb = str(ram_result.get("usage_mb") or "")
                    raw_pss = ram_result.get("pss_kb")
                    if raw_pss is not None:
                        try:
                            pss_mb = max(0, int(raw_pss) // 1024)
                        except (TypeError, ValueError):
                            pss_mb = 0
                except Exception:  # noqa: BLE001
                    ram_mb = None
                    pss_mb = 0
            pres = self._presence_last_detail.get(pkg, {})
            runtime_started_at, runtime_source = self._status_monitor_runtime_started_at(pkg, status)
            snapshot.append(
                {
                    "package":      pkg,
                    "username":     entry_map.get(pkg, ""),
                    "status":       status,
                    "presence_profile": pres.get("roblox_presence_profile") or "",
                    "revive_count": self._revive_count.get(pkg, 0),
                    "failure_count": self._failure_count.get(pkg, 0),
                    "last_error":   None,
                    "online_since": runtime_started_at or self._online_start_ts.get(pkg) or self._last_online_ts.get(pkg),
                    "status_monitor_runtime_started_at": runtime_started_at,
                    "package_launch_started_at": self._package_launch_started_at.get(pkg),
                    "runtime_source": runtime_source,
                    "last_seen_at": self._last_online_ts.get(pkg),
                    "ram_mb":       ram_mb,
                    "pss_mb":       pss_mb,
                }
            )
        return snapshot
