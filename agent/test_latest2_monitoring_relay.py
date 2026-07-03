"""Central Monitoring / Checking relay for test/latest2 only.

All real presence states (Online, No Heartbeat, Dead, Kicked/Left Game,
Closed/Crashed) are committed ONLY through :func:`commit_presence_state`.
Raw detectors (process poll, logcat, OCR, RJN lifecycle) submit evidence via
:func:`submit_raw_evidence` and never write supervisor presence directly.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from .constants import DATA_DIR
from .lime_channel import lime_detection_enabled

RELAY_VERSION = "test-latest2-monitoring-v1"
STATE_FILENAME = "test-latest2-monitoring-relay.json"
STATE_MAX_AGE_S = 15.0
PERSIST_MIN_INTERVAL_S = 0.35

# Committed presence vocabulary (maps to supervisor public states).
PRESENCE_ONLINE = "Online"
PRESENCE_NO_HEARTBEAT = "No Heartbeat"
PRESENCE_DEAD = "Dead"
PRESENCE_KICKED = "Kicked"
PRESENCE_CLOSED = "Closed/Crashed"

COMMITTED_PRESENCE = frozenset(
    {
        PRESENCE_ONLINE,
        PRESENCE_NO_HEARTBEAT,
        PRESENCE_DEAD,
        PRESENCE_KICKED,
        PRESENCE_CLOSED,
    }
)

# Supervisor-facing recovery triggers.
_RECOVERY_PRESENCE = frozenset({PRESENCE_DEAD, PRESENCE_KICKED, PRESENCE_CLOSED})

TICK_INTERVAL_S = float(os.environ.get("DENG_REJOIN_MONITORING_TICK_SEC", "0.5") or "0.5")
FOCUS_WINDOW_S = float(os.environ.get("DENG_REJOIN_MONITORING_FOCUS_SEC", "7") or "7")

_active_relay: "MonitoringRelay | None" = None
_active_lock = threading.Lock()


def _state_path():
    return DATA_DIR / STATE_FILENAME


@dataclass
class PackageRelayRow:
    package: str
    committed_state: str = ""
    committed_at: float | None = None
    committed_source: str = ""
    committed_writer: str = ""
    raw_online_pending: bool = False
    raw_dead_pending: bool = False
    raw_kicked_pending: bool = False
    last_raw_source: str = ""
    last_raw_hint: str = ""
    last_raw_at: float | None = None
    last_process_exists: bool | None = None
    last_process_check_at: float | None = None
    last_logcat_result: str = ""
    last_logcat_at: float | None = None
    last_ocr_result: str = ""
    last_ocr_at: float | None = None
    last_detector_duration_ms: float | None = None
    last_recovery_trigger_at: float | None = None
    recovery_triggered: bool = False
    launch_at: float | None = None
    blocked_by_package: str = ""


@dataclass
class MonitoringRelay:
    """Single relay point for test/latest2 presence + recovery."""

    packages: list[str]
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _rows: dict[str, PackageRelayRow] = field(default_factory=dict)
    _supervisor: Any = None
    _entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _last_tick_at: float | None = None
    _last_tick_duration_ms: float | None = None
    _tick_interval_s: float = TICK_INTERVAL_S
    _focus_package: str = ""
    _focus_started_at: float | None = None
    _monitoring_started_at: float | None = None
    _last_state_write_at: float = 0.0
    _recovery_inflight: set[str] = field(default_factory=set)
    _direct_set_status: Callable[..., None] | None = None

    def __post_init__(self) -> None:
        for pkg in self.packages:
            if pkg and pkg not in self._rows:
                self._rows[pkg] = PackageRelayRow(package=pkg)

    def bind_supervisor(
        self,
        supervisor: Any,
        *,
        entries: dict[str, dict[str, Any]] | None = None,
        direct_set_status: Callable[..., None] | None = None,
    ) -> None:
        with self._lock:
            self._supervisor = supervisor
            self._entries = dict(entries or {})
            self._direct_set_status = direct_set_status

    def start(self) -> None:
        if not lime_detection_enabled():
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            if self._monitoring_started_at is None:
                self._monitoring_started_at = time.time()
            self._thread = threading.Thread(
                target=self._loop,
                name="test-latest2-monitoring",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def note_launch(self, package: str, *, at: float | None = None) -> None:
        now = float(at if at is not None else time.time())
        with self._lock:
            row = self._row(package)
            row.launch_at = now
            row.committed_state = ""
            row.raw_online_pending = False
            row.raw_dead_pending = False
            row.raw_kicked_pending = False
        self._persist()

    def submit_raw_evidence(
        self,
        package: str,
        *,
        hint: str,
        source: str,
        evidence: str = "",
        at: float | None = None,
        process_exists: bool | None = None,
        logcat_result: str = "",
        ocr_result: str = "",
        duration_ms: float | None = None,
    ) -> None:
        """Queue detector output — does NOT commit presence."""
        pkg = str(package or "").strip()
        if not pkg:
            return
        now = float(at if at is not None else time.time())
        hint_norm = str(hint or "").strip().lower()
        source_norm = str(source or "").strip()[:80]
        with self._lock:
            row = self._row(pkg)
            row.last_raw_source = source_norm
            row.last_raw_hint = hint_norm[:80]
            row.last_raw_at = now
            if process_exists is not None:
                row.last_process_exists = bool(process_exists)
                row.last_process_check_at = now
            if logcat_result:
                row.last_logcat_result = str(logcat_result)[:120]
                row.last_logcat_at = now
            if ocr_result:
                row.last_ocr_result = str(ocr_result)[:120]
                row.last_ocr_at = now
            if duration_ms is not None:
                row.last_detector_duration_ms = float(duration_ms)
            if hint_norm in {"online", "heartbeat", "gamejoin"}:
                row.raw_online_pending = True
            elif hint_norm in {"dead", "closed", "crashed", "process_missing", "force_close"}:
                row.raw_dead_pending = True
            elif hint_norm in {"kicked", "left", "disconnect", "disconnected"}:
                row.raw_kicked_pending = True
                row.raw_dead_pending = True
        self._persist()

    def _recovery_allowed(self, pkg: str, state: str) -> bool:
        """Respect v1.3.0 stagger + loading grace — no recovery during first launch."""
        if state == PRESENCE_ONLINE:
            return True
        sup = self._supervisor
        if sup is None:
            return True
        if not getattr(sup, "_all_launches_completed", True):
            inflight = getattr(sup, "_initial_launch_inflight", set())
            if pkg in inflight:
                return False
        if hasattr(sup, "_in_loading_grace") and sup._in_loading_grace(pkg):
            inflight = getattr(sup, "_initial_launch_inflight", set())
            if pkg in inflight:
                return False
        row = self._rows.get(pkg)
        if row is not None and row.launch_at:
            age = time.time() - float(row.launch_at)
            if age < 20.0 and state in _RECOVERY_PRESENCE:
                had_online = row.committed_state == PRESENCE_ONLINE
                if not had_online and not row.recovery_triggered:
                    return False
        return True

    def commit_presence_state(
        self,
        package: str,
        state: str,
        *,
        source: str = "monitoring_relay",
        writer: str = "monitoring_relay",
        evidence: str = "",
        trigger_recovery: bool | None = None,
    ) -> bool:
        """THE only writer of real presence states on test/latest2."""
        pkg = str(package or "").strip()
        state_norm = str(state or "").strip()
        if not pkg or state_norm not in COMMITTED_PRESENCE:
            return False
        if not self._recovery_allowed(pkg, state_norm) and state_norm in _RECOVERY_PRESENCE:
            self.submit_raw_evidence(
                pkg,
                hint="dead" if state_norm != PRESENCE_KICKED else "kicked",
                source=source,
                evidence=f"deferred:{evidence or state_norm}",
            )
            return False
        now = time.time()
        changed = False
        with self._lock:
            row = self._row(pkg)
            if row.committed_state != state_norm:
                changed = True
            row.committed_state = state_norm
            row.committed_at = now
            row.committed_source = str(source or "")[:80]
            row.committed_writer = str(writer or "")[:80]
            row.raw_online_pending = False
            row.raw_dead_pending = False
            row.raw_kicked_pending = False
            if state_norm == PRESENCE_ONLINE:
                row.recovery_triggered = False
                self._recovery_inflight.discard(pkg)
        self._persist(force=True)
        self._apply_supervisor_status(pkg, state_norm)
        self._notify_lime_committed(pkg, state_norm, now)
        do_recovery = (
            trigger_recovery
            if trigger_recovery is not None
            else state_norm in _RECOVERY_PRESENCE
        )
        if do_recovery and changed and not self._recovery_allowed(pkg, state_norm):
            do_recovery = False
        if do_recovery and changed:
            self._trigger_immediate_recovery(pkg, state_norm, evidence or source)
        return True

    def committed_presence_state(self, package: str) -> str:
        with self._lock:
            row = self._rows.get(package)
            return "" if row is None else str(row.committed_state or "")

    def has_pending_dead(self, package: str) -> bool:
        with self._lock:
            row = self._rows.get(package)
            return bool(row and (row.raw_dead_pending or row.raw_kicked_pending))

    def _row(self, package: str) -> PackageRelayRow:
        pkg = str(package or "").strip()
        row = self._rows.get(pkg)
        if row is None:
            row = PackageRelayRow(package=pkg)
            self._rows[pkg] = row
        return row

    def _apply_supervisor_status(self, pkg: str, state: str) -> None:
        sup = self._supervisor
        if sup is None:
            return
        from . import supervisor as sup_mod

        mapped = state
        if state == PRESENCE_KICKED:
            mapped = sup_mod.STATUS_DISCONNECTED
        elif state == PRESENCE_CLOSED:
            mapped = sup_mod.STATUS_DEAD
        elif state == PRESENCE_NO_HEARTBEAT:
            mapped = getattr(sup_mod, "STATUS_NO_HEARTBEAT", "No Heartbeat")
        setter = self._direct_set_status
        if setter is None:
            setter = sup._set_status  # noqa: SLF001
        try:
            setattr(sup, "_monitoring_relay_commit", True)
            setter(pkg, mapped)
        finally:
            try:
                setattr(sup, "_monitoring_relay_commit", False)
            except Exception:  # noqa: BLE001
                pass

    def _notify_lime_committed(self, pkg: str, state: str, at: float) -> None:
        try:
            from .lime_detection_speed import get_active_lime_tracker

            lime = get_active_lime_tracker()
            if lime is not None:
                lime.note_checking_committed(pkg, at=at, state=state)
        except Exception:  # noqa: BLE001
            pass

    def _trigger_immediate_recovery(
        self, pkg: str, state: str, reason: str
    ) -> None:
        with self._lock:
            if pkg in self._recovery_inflight:
                return
            self._recovery_inflight.add(pkg)
            row = self._row(pkg)
            row.last_recovery_trigger_at = time.time()
            row.recovery_triggered = True
        self._persist(force=True)
        try:
            from .lime_detection_speed import get_active_lime_tracker

            lime = get_active_lime_tracker()
            if lime is not None:
                lime.note_recovery_requested(pkg)
        except Exception:  # noqa: BLE001
            pass

        sup = self._supervisor
        if sup is None:
            return
        if not self._recovery_allowed(pkg, state):
            with self._lock:
                self._recovery_inflight.discard(pkg)
            return
        entry = self._entries.get(pkg) or {}
        try:
            from . import supervisor as sup_mod

            dead_state = sup_mod.STATUS_DISCONNECTED if state == PRESENCE_KICKED else sup_mod.STATUS_DEAD
            now = time.time()
            prev = str(sup.status_map.get(pkg) or sup_mod.STATUS_LAUNCHING)
            detail = {"reason_internal": reason, "source": "monitoring_relay"}
            threading.Thread(
                target=lambda: sup._handle_state(  # noqa: SLF001
                    pkg,
                    entry,
                    dead_state,
                    prev,
                    now,
                    render_callback=getattr(sup, "_render_callback", None),
                    detail=detail,
                ),
                name=f"monitoring-recovery-{pkg.split('.')[-1][:12]}",
                daemon=True,
            ).start()
        except Exception:  # noqa: BLE001
            with self._lock:
                self._recovery_inflight.discard(pkg)

    def _loop(self) -> None:
        while not self._stop.is_set():
            tick_started = time.perf_counter()
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                pass
            self._last_tick_at = time.time()
            self._last_tick_duration_ms = round(
                (time.perf_counter() - tick_started) * 1000.0, 1
            )
            self._persist()
            self._stop.wait(max(0.05, self._tick_interval_s))

    def _tick(self) -> None:
        pkgs = list(self.packages)
        if not pkgs:
            return
        try:
            from .lime_package_discovery import discover_roblox_packages

            discover_roblox_packages(force=False)
        except Exception:  # noqa: BLE001
            pass

        sup = self._supervisor
        monitor = None
        if sup is not None:
            monitor = getattr(sup, "_rjn_monitor", None)

        def _scan_one(pkg: str) -> tuple[str, dict[str, Any]]:
            started = time.perf_counter()
            result: dict[str, Any] = {
                "process_exists": None,
                "hint": "",
                "source": "",
                "evidence": "",
            }
            try:
                if monitor is not None:
                    from .rjn_lifecycle_monitor import (
                        STATE_DEAD,
                        STATE_DISCONNECTED,
                        STATE_ONLINE_CONFIRMED,
                    )

                    ev = monitor.evaluate_package(pkg, hot_lane_only=True)
                    detail = dict(ev.detail or {})
                    proc = detail.get("process_running")
                    if proc == "true":
                        result["process_exists"] = True
                    elif proc == "false":
                        result["process_exists"] = False
                    if ev.is_online_confirmed:
                        result["hint"] = "online"
                        result["source"] = str(detail.get("runtime_source") or "heartbeat")
                        result["evidence"] = str(detail.get("reason_internal") or "online")
                    elif ev.internal_state == STATE_DEAD:
                        result["hint"] = "dead"
                        result["source"] = "process"
                        result["evidence"] = str(detail.get("reason_internal") or "process_missing")
                    elif ev.internal_state == STATE_DISCONNECTED:
                        result["hint"] = "kicked"
                        result["source"] = "logcat"
                        result["evidence"] = str(detail.get("reason_internal") or "disconnected")
                else:
                    from . import android

                    exists = android.is_process_running(pkg)
                    result["process_exists"] = bool(exists)
                    if not exists:
                        result["hint"] = "dead"
                        result["source"] = "process"
                        result["evidence"] = "process_missing"
            except Exception as exc:  # noqa: BLE001
                result["evidence"] = str(exc)[:80]
            result["duration_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
            return pkg, result

        workers = min(6, max(1, len(pkgs)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mon-scan") as pool:
            futures = [pool.submit(_scan_one, p) for p in pkgs]
            for fut in as_completed(futures):
                pkg, scan = fut.result()
                if scan.get("hint"):
                    self.submit_raw_evidence(
                        pkg,
                        hint=str(scan["hint"]),
                        source=str(scan.get("source") or "monitoring_tick"),
                        evidence=str(scan.get("evidence") or ""),
                        process_exists=scan.get("process_exists"),
                        duration_ms=scan.get("duration_ms"),
                    )
                self._maybe_commit_from_pending(pkg, scan)

    def _maybe_commit_from_pending(self, pkg: str, scan: dict[str, Any]) -> None:
        with self._lock:
            row = self._rows.get(pkg)
            if row is None:
                return
            if row.raw_dead_pending and not row.raw_online_pending:
                hint = row.last_raw_hint
                if hint in {"kicked", "left", "disconnect", "disconnected"}:
                    state = PRESENCE_KICKED
                else:
                    state = PRESENCE_CLOSED if hint in {"closed", "crashed"} else PRESENCE_DEAD
                source = row.last_raw_source or "process"
                evidence = row.last_raw_hint
            elif row.raw_online_pending:
                state = PRESENCE_ONLINE
                source = row.last_raw_source or "heartbeat"
                evidence = "online_confirmed"
            else:
                return
        self.commit_presence_state(
            pkg,
            state,
            source=source,
            writer="monitoring_tick",
            evidence=evidence,
            trigger_recovery=state in _RECOVERY_PRESENCE,
        )

    def _persist(self, *, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_state_write_at) < PERSIST_MIN_INTERVAL_S:
            return
        self._last_state_write_at = now
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _state_path().write_text(
                json.dumps(self.probe_snapshot(now=now), indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def probe_snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        ts = float(now if now is not None else time.time())
        with self._lock:
            pkg_rows = {}
            for pkg, row in self._rows.items():
                pkg_rows[pkg] = {
                    "committed_state": row.committed_state or None,
                    "committed_source": row.committed_source or None,
                    "committed_at": row.committed_at,
                    "committed_writer": row.committed_writer or None,
                    "raw_online_pending": row.raw_online_pending,
                    "raw_dead_pending": row.raw_dead_pending,
                    "raw_kicked_pending": row.raw_kicked_pending,
                    "last_raw_source": row.last_raw_source or None,
                    "last_raw_at": row.last_raw_at,
                    "last_process_exists": row.last_process_exists,
                    "last_process_check_at": row.last_process_check_at,
                    "last_logcat_result": row.last_logcat_result or None,
                    "last_logcat_at": row.last_logcat_at,
                    "last_ocr_result": row.last_ocr_result or None,
                    "last_ocr_at": row.last_ocr_at,
                    "last_detector_duration_ms": row.last_detector_duration_ms,
                    "last_recovery_trigger_at": row.last_recovery_trigger_at,
                    "recovery_triggered": row.recovery_triggered,
                    "launch_at": row.launch_at,
                    "blocked_by_package": row.blocked_by_package or None,
                }
            return {
                "enabled": lime_detection_enabled(),
                "relay_version": RELAY_VERSION,
                "channel": "test/latest2",
                "detector_mode": "central_monitoring_relay",
                "monitoring_started_at": self._monitoring_started_at,
                "last_tick_at": self._last_tick_at,
                "last_tick_duration_ms": self._last_tick_duration_ms,
                "tick_interval_s": self._tick_interval_s,
                "focus_window_s": FOCUS_WINDOW_S,
                "focus_package": self._focus_package or None,
                "packages": pkg_rows,
                "package_count": len(pkg_rows),
                "captured_at": ts,
            }


def get_active_relay() -> MonitoringRelay | None:
    with _active_lock:
        return _active_relay


def set_active_relay(relay: MonitoringRelay | None) -> None:
    global _active_relay
    with _active_lock:
        _active_relay = relay


def start_monitoring_relay(
    supervisor: Any,
    packages: list[str],
    *,
    entries: dict[str, dict[str, Any]] | None = None,
    direct_set_status: Callable[..., None] | None = None,
) -> MonitoringRelay | None:
    if not lime_detection_enabled():
        return None
    relay = MonitoringRelay(packages=[str(p).strip() for p in packages if str(p).strip()])
    relay.bind_supervisor(supervisor, entries=entries, direct_set_status=direct_set_status)
    relay.start()
    set_active_relay(relay)
    return relay


def probe_monitoring_relay_snapshot(*, max_age_s: float = STATE_MAX_AGE_S) -> dict[str, Any]:
    relay = get_active_relay()
    if relay is not None:
        snap = relay.probe_snapshot()
        snap["live_process"] = True
        return snap
    path = _state_path()
    try:
        if path.is_file():
            age = time.time() - path.stat().st_mtime
            if age <= max_age_s:
                parsed = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    parsed["live_process"] = False
                    parsed["state_file_age_s"] = round(age, 2)
                    return parsed
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return {
        "enabled": lime_detection_enabled(),
        "relay_version": RELAY_VERSION,
        "channel": "test/latest2",
        "detector_mode": "central_monitoring_relay",
        "live_process": False,
        "packages": {},
    }


def commit_presence_state(
    package: str,
    state: str,
    *,
    source: str = "monitoring_relay",
    writer: str = "monitoring_relay",
    evidence: str = "",
    trigger_recovery: bool | None = None,
) -> bool:
    """Module-level commit — routes to active relay or no-op."""
    relay = get_active_relay()
    if relay is None:
        return False
    return relay.commit_presence_state(
        package,
        state,
        source=source,
        writer=writer,
        evidence=evidence,
        trigger_recovery=trigger_recovery,
    )


def submit_raw_evidence(
    package: str,
    *,
    hint: str,
    source: str,
    evidence: str = "",
    at: float | None = None,
    **kwargs: Any,
) -> None:
    relay = get_active_relay()
    if relay is None:
        return
    relay.submit_raw_evidence(
        package,
        hint=hint,
        source=source,
        evidence=evidence,
        at=at,
        **kwargs,
    )
