"""Lime-style detection speed tracking for DENG Rejoin.

Tracks per-package timestamps for:
  process_dead_detected_at, logcat_dead_detected_at, ocr_dead_detected_at,
  online_evidence_at, checking_committed_state_at, recovery_requested_at,
  detection_latency_ms

Integrates process poll + live logcat (via force_close_race) and OCR fallback.
Does NOT read Roblox cookies from any storage.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .constants import DATA_DIR
from .force_close_race import (
    ForceCloseRaceDetector,
    get_active_force_close_race_detector,
    probe_force_close_race_snapshot,
    set_active_force_close_race_detector,
)
from .ocr_screen_detector import (
    OcrMatch,
    OcrScreenDetector,
    get_active_ocr_detector,
    probe_ocr_snapshot,
    set_active_ocr_detector,
)

LIME_STATE_PATH = DATA_DIR / "lime-detection-speed-state.json"
LIME_STATE_MAX_AGE_SECONDS = 20.0
LIME_STATE_WRITE_MIN_INTERVAL_SECONDS = 0.5

from .lime_channel import lime_detection_enabled as _lime_channel_enabled


def _lime_enabled() -> bool:
    return _lime_channel_enabled()

PROCESS_POLL_INTERVAL_SECONDS = float(
    os.environ.get("DENG_REJOIN_LIME_PROCESS_POLL_SEC", "0.5") or "0.5"
)

_active_tracker: "LimeDetectionSpeedTracker | None" = None
_active_lock = threading.Lock()


def get_active_lime_tracker() -> "LimeDetectionSpeedTracker | None":
    with _active_lock:
        return _active_tracker


def set_active_lime_tracker(tracker: "LimeDetectionSpeedTracker | None") -> None:
    global _active_tracker
    with _active_lock:
        _active_tracker = tracker


@dataclass
class PackageSpeedTimestamps:
    package: str
    process_dead_detected_at: float | None = None
    logcat_dead_detected_at: float | None = None
    ocr_dead_detected_at: float | None = None
    online_evidence_at: float | None = None
    online_evidence_source: str = ""
    checking_committed_state_at: float | None = None
    checking_committed_state: str = ""
    recovery_requested_at: float | None = None
    detection_latency_ms: float | None = None
    last_event_at: float | None = None
    last_event_kind: str = ""
    evidence_baseline_at: float | None = None


class LimeDetectionSpeedTracker:
    """Unified Lime-style speed tracker — process, logcat, OCR, online, checking."""

    def __init__(
        self,
        packages: list[str],
        *,
        monitor: Any,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.packages = [str(p).strip() for p in packages if str(p).strip()]
        self._monitor = monitor
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._session_active = False
        self._last_state_write_at = 0.0
        self._race: ForceCloseRaceDetector | None = None
        self._ocr: OcrScreenDetector | None = None
        self._packages: dict[str, PackageSpeedTimestamps] = {
            pkg: PackageSpeedTimestamps(package=pkg) for pkg in self.packages
        }

    def start(self) -> None:
        if not _lime_enabled():
            return
        with self._lock:
            if self._session_active:
                return
            self._session_active = True
            self._race = ForceCloseRaceDetector(
                self.packages,
                monitor=self._monitor,
                clock=self._clock,
            )
            self._race.start()
            self._ocr = OcrScreenDetector(
                self.packages,
                should_scan=self._should_run_ocr,
                on_match=self._on_ocr_match,
                clock=self._clock,
            )
            self._ocr.start()
            set_active_lime_tracker(self)
        self._write_state_file(force=True)

    def stop(self) -> None:
        ocr = self._ocr
        race = self._race
        if ocr is not None:
            try:
                ocr.stop()
            except Exception:  # noqa: BLE001
                pass
        if race is not None:
            try:
                race.stop()
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self._session_active = False
            self._ocr = None
            self._race = None
        if get_active_lime_tracker() is self:
            set_active_lime_tracker(None)
        try:
            LIME_STATE_PATH.unlink()
        except OSError:
            pass

    def _row(self, package: str) -> PackageSpeedTimestamps:
        pkg = str(package or "").strip()
        return self._packages.setdefault(pkg, PackageSpeedTimestamps(package=pkg))

    def _recompute_latency(self, row: PackageSpeedTimestamps) -> None:
        baseline = row.evidence_baseline_at
        if baseline is None or baseline <= 0:
            return
        candidates = [
            row.process_dead_detected_at,
            row.logcat_dead_detected_at,
            row.ocr_dead_detected_at,
        ]
        first = min((t for t in candidates if t is not None and t >= baseline), default=None)
        if first is not None:
            row.detection_latency_ms = round(max(0.0, (first - baseline) * 1000.0), 1)

    def _note_event(self, package: str, kind: str, at: float) -> None:
        with self._lock:
            row = self._row(package)
            row.last_event_at = at
            row.last_event_kind = kind
            self._recompute_latency(row)

    def set_evidence_baseline(self, package: str, at: float | None = None) -> None:
        """Mark when a speed-test scenario started (force-close, kick screen, etc.)."""
        now = float(at if at is not None else self._clock())
        with self._lock:
            row = self._row(package)
            row.evidence_baseline_at = now
            row.process_dead_detected_at = None
            row.logcat_dead_detected_at = None
            row.ocr_dead_detected_at = None
            row.detection_latency_ms = None

    def note_process_dead(self, package: str, *, at: float | None = None) -> None:
        now = float(at if at is not None else self._clock())
        with self._lock:
            row = self._row(package)
            if row.process_dead_detected_at is None:
                row.process_dead_detected_at = now
                self._recompute_latency(row)
        self._note_event(package, "process_dead", now)
        self._write_state_file()

    def note_logcat_dead(
        self,
        package: str,
        *,
        at: float | None = None,
        evidence: str = "",
    ) -> None:
        now = float(at if at is not None else self._clock())
        with self._lock:
            row = self._row(package)
            if row.logcat_dead_detected_at is None:
                row.logcat_dead_detected_at = now
                self._recompute_latency(row)
        self._note_event(package, f"logcat_dead:{evidence}"[:80], now)
        self._write_state_file()

    def note_ocr_dead(
        self,
        package: str,
        *,
        at: float | None = None,
        phrase: str = "",
    ) -> None:
        now = float(at if at is not None else self._clock())
        with self._lock:
            row = self._row(package)
            if row.ocr_dead_detected_at is None:
                row.ocr_dead_detected_at = now
                self._recompute_latency(row)
        self._note_event(package, f"ocr_dead:{phrase}"[:80], now)
        self._write_state_file()

    def note_online_evidence(
        self,
        package: str,
        *,
        at: float | None = None,
        source: str = "",
    ) -> None:
        now = float(at if at is not None else self._clock())
        with self._lock:
            row = self._row(package)
            row.online_evidence_at = now
            row.online_evidence_source = str(source or "")[:80]
        self._note_event(package, f"online:{source}"[:80], now)
        race = self._race
        if race is not None:
            try:
                race.note_online(package, at=now)
            except Exception:  # noqa: BLE001
                pass
        self._write_state_file()

    def note_checking_committed(
        self,
        package: str,
        *,
        at: float | None = None,
        state: str = "",
    ) -> None:
        now = float(at if at is not None else self._clock())
        with self._lock:
            row = self._row(package)
            row.checking_committed_state_at = now
            row.checking_committed_state = str(state or "")[:40]
        self._note_event(package, f"checking:{state}"[:80], now)
        self._write_state_file()

    def note_recovery_requested(self, package: str, *, at: float | None = None) -> None:
        now = float(at if at is not None else self._clock())
        with self._lock:
            row = self._row(package)
            if row.recovery_requested_at is None:
                row.recovery_requested_at = now
        self._note_event(package, "recovery_requested", now)
        self._write_state_file()

    def note_current_detector(
        self,
        package: str,
        *,
        at: float | None = None,
        state: str,
        reason: str,
        source: str = "current_lifecycle",
    ) -> None:
        race = self._race
        if race is not None:
            try:
                race.note_current_detector(
                    package,
                    at=at,
                    state=state,
                    reason=reason,
                    source=source,
                )
            except Exception:  # noqa: BLE001
                pass

    def _should_run_ocr(self, package: str) -> bool:
        try:
            from .lime_channel import lime_detection_enabled

            if lime_detection_enabled():
                from .test_latest2_monitoring_relay import get_active_relay

                relay = get_active_relay()
                if relay is not None:
                    focused = str(
                        getattr(relay, "_focus_package", "") or ""
                    ).strip()
                    if focused == package:
                        return True
                    if relay.has_pending_dead(package):
                        return True
        except Exception:  # noqa: BLE001
            pass
        try:
            import importlib

            cp = importlib.import_module(".checker_pointer", __package__)
            ptr = cp.get()
            focused = str(ptr.checking_active_package or ptr.active_focus_package or "").strip()
            if focused == package:
                return True
            if ptr.has_pending_dead(package):
                return True
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            row = self._packages.get(package)
            if row is not None and row.logcat_dead_detected_at:
                return True
            if row is not None and row.process_dead_detected_at:
                return True
        try:
            from .rjn_lifecycle_monitor import STATE_DISCONNECTED

            mon = self._monitor
            with mon._lock:
                mrow = mon._states.get(package)
                if mrow is not None and mrow.internal_state == STATE_DISCONNECTED:
                    return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _on_ocr_match(self, package: str, at: float, match: OcrMatch) -> None:
        self.note_ocr_dead(package, at=at, phrase=match.phrase)
        try:
            from .lime_channel import lime_detection_enabled

            if lime_detection_enabled():
                from .test_latest2_monitoring_relay import submit_raw_evidence

                phrase = str(getattr(match, "phrase", "") or "")
                hint = "kicked" if any(x in phrase.lower() for x in ("kick", "left", "disconnect")) else "dead"
                submit_raw_evidence(
                    package,
                    hint=hint,
                    source="ocr",
                    evidence=phrase[:120],
                    at=at,
                    ocr_result=phrase[:120],
                )
        except Exception:  # noqa: BLE001
            pass

    def _package_probe_row(self, pkg: str, row: PackageSpeedTimestamps) -> dict[str, Any]:
        race_pkg = {}
        if self._race is not None:
            snap = self._race.probe_snapshot()
            race_pkg = (snap.get("packages") or {}).get(pkg) or {}
        ocr_pkg = {}
        ocr_snap = probe_ocr_snapshot()
        ocr_pkg = (ocr_snap.get("packages") or {}).get(pkg) or {}
        online_latency_ms = None
        if row.evidence_baseline_at and row.online_evidence_at:
            online_latency_ms = round(
                max(0.0, (row.online_evidence_at - row.evidence_baseline_at) * 1000.0), 1
            )
        recovery_latency_ms = None
        if row.checking_committed_state_at and row.recovery_requested_at:
            recovery_latency_ms = round(
                max(
                    0.0,
                    (row.recovery_requested_at - row.checking_committed_state_at) * 1000.0,
                ),
                1,
            )
        return {
            "process_dead_detected_at": row.process_dead_detected_at,
            "logcat_dead_detected_at": row.logcat_dead_detected_at,
            "ocr_dead_detected_at": row.ocr_dead_detected_at,
            "online_evidence_at": row.online_evidence_at,
            "online_evidence_source": row.online_evidence_source or None,
            "checking_committed_state_at": row.checking_committed_state_at,
            "checking_committed_state": row.checking_committed_state or None,
            "recovery_requested_at": row.recovery_requested_at,
            "detection_latency_ms": row.detection_latency_ms,
            "online_latency_ms": online_latency_ms,
            "recovery_latency_ms": recovery_latency_ms,
            "evidence_baseline_at": row.evidence_baseline_at,
            "last_event_at": row.last_event_at,
            "last_event_kind": row.last_event_kind or None,
            "process_poll": (race_pkg.get("methods") or {}).get("process_poll"),
            "logcat_crash": (race_pkg.get("methods") or {}).get("logcat_crash"),
            "ocr": ocr_pkg,
            "targets": {
                "process_dead_ms": 1000,
                "logcat_dead_ms": 1000,
                "ocr_dead_ms": None,
                "online_ms": 1000,
                "recovery_after_checking_ms": 1000,
            },
        }

    def probe_snapshot(self) -> dict[str, Any]:
        with self._lock:
            packages_out = {
                pkg: self._package_probe_row(pkg, row)
                for pkg, row in self._packages.items()
            }
            race_snap = probe_force_close_race_snapshot(clock=self._clock)
            ocr_snap = probe_ocr_snapshot()
            return {
                "enabled": self._session_active and _lime_enabled(),
                "cookie_auto_extract": False,
                "launch_requires_cookie": False,
                "process_poll_interval_ms": round(PROCESS_POLL_INTERVAL_SECONDS * 1000.0, 0),
                "force_close_race": race_snap,
                "ocr_detector": ocr_snap,
                "packages": packages_out,
            }

    def _write_state_file(self, *, force: bool = False) -> None:
        now = self._clock()
        if not force and (now - self._last_state_write_at) < LIME_STATE_WRITE_MIN_INTERVAL_SECONDS:
            return
        self._last_state_write_at = now
        try:
            LIME_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "written_at": time.time(),
                "snapshot": self.probe_snapshot(),
            }
            tmp = LIME_STATE_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, LIME_STATE_PATH)
        except Exception:  # noqa: BLE001
            pass


def read_lime_state_file(*, max_age_s: float = LIME_STATE_MAX_AGE_SECONDS) -> dict[str, Any] | None:
    try:
        data = json.loads(LIME_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        age = time.time() - float(data.get("written_at"))
    except (TypeError, ValueError):
        return None
    if age > max_age_s:
        return None
    snap = data.get("snapshot")
    if not isinstance(snap, dict):
        return None
    out = dict(snap)
    out["source"] = "state_file"
    out["state_file_age_s"] = round(age, 2)
    return out


def probe_lime_detection_speed_snapshot(
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    live = get_active_lime_tracker()
    if live is not None and live._session_active:
        snap = live.probe_snapshot()
        try:
            live._write_state_file()
        except Exception:  # noqa: BLE001
            pass
        return snap
    disk = read_lime_state_file()
    if disk is not None:
        return disk
    return {
        "enabled": False,
        "cookie_auto_extract": False,
        "launch_requires_cookie": False,
        "force_close_race": probe_force_close_race_snapshot(clock=clock),
        "ocr_detector": probe_ocr_snapshot(),
        "packages": {},
        "reason": "watchdog_session_not_active",
    }


def start_lime_tracker_for_monitor(monitor: Any) -> LimeDetectionSpeedTracker | None:
    """Start Lime tracker when RJN session starts (replaces bare force_close_race start)."""
    if not _lime_enabled():
        return None
    existing = get_active_force_close_race_detector()
    if existing is not None:
        try:
            existing.stop()
        except Exception:  # noqa: BLE001
            pass
        set_active_force_close_race_detector(None)
    existing_ocr = get_active_ocr_detector()
    if existing_ocr is not None:
        try:
            existing_ocr.stop()
        except Exception:  # noqa: BLE001
            pass
        set_active_ocr_detector(None)
    tracker = LimeDetectionSpeedTracker(monitor.packages, monitor=monitor)
    tracker.start()
    return tracker
