"""Per-package dead/recovery detection from process + logcat evidence."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import android
from .android_logcat_detector import LogcatPackageEvent, poll_logcat_events
from .constants import DATA_DIR

PROCESS_MISSING_CONFIRM_CHECKS = 2


@dataclass
class PackageRuntimeState:
    package: str
    android_uid: str = ""
    last_pid_seen: str = ""
    last_process_seen_at: float = 0.0
    last_logcat_event_at: float = 0.0
    last_gamejoinloadtime_at: float = 0.0
    alive_since: float = 0.0
    dead_since: float = 0.0
    relaunching_since: float = 0.0
    last_real_ram_sample: dict[str, Any] = field(default_factory=dict)
    last_dead_reason: str = ""
    process_missing_streak: int = 0
    dead_confirmed: bool = False
    dead_notified: bool = False
    last_event: str = ""


@dataclass
class PackageCheckResult:
    package: str
    process_alive: bool
    confirmed_dead: bool
    confirmed_recovered: bool
    dead_reason: str = ""
    runtime_source: str = ""
    ram_display: str = "N/A"
    ram_source: str = "none"
    logcat_event: str = ""


class PackageStateDetector:
    def __init__(self, packages: list[str], *, root_info: Any = None) -> None:
        self.packages = [str(p).strip() for p in packages if str(p).strip()]
        self._root_info = root_info
        self._states: dict[str, PackageRuntimeState] = {
            pkg: PackageRuntimeState(package=pkg) for pkg in self.packages
        }
        self._uid_map: dict[str, str] = {}
        self._logcat_state: dict[str, Any] = {}
        self._dead_transition_count = 0
        self._recovered_transition_count = 0
        self._last_logcat_events: list[dict[str, Any]] = []
        self._last_process_check_at = 0.0
        self.refresh_uid_map()

    def refresh_uid_map(self) -> dict[str, str]:
        from .android_logcat_detector import package_uid_map

        self._uid_map = package_uid_map(self.packages)
        for pkg, uid in self._uid_map.items():
            if pkg in self._states:
                self._states[pkg].android_uid = uid
        return dict(self._uid_map)

    def poll_logcat(self) -> list[LogcatPackageEvent]:
        events, state = poll_logcat_events(self.packages, uid_map=self._uid_map)
        self._logcat_state = {
            "started": state.started,
            "permission_ok": state.permission_ok,
            "error": state.error,
            "last_poll_at": state.last_poll_at,
        }
        self._last_logcat_events = list(state.last_events)
        now = time.time()
        for event in events:
            row = self._states.get(event.package)
            if not row:
                continue
            row.last_logcat_event_at = event.at
            row.last_event = event.event
            if event.event == "package_logcat_game_join_loaded":
                row.last_gamejoinloadtime_at = event.at
                row.process_missing_streak = 0
                row.dead_confirmed = False
                row.last_dead_reason = ""
                if not row.alive_since:
                    row.alive_since = event.at
            elif event.event == "package_logcat_reason":
                row.last_dead_reason = "logcat_with_reason"
                row.process_missing_streak = PROCESS_MISSING_CONFIRM_CHECKS
            elif event.event == "package_process_missing":
                row.last_dead_reason = "logcat_process_died"
                row.process_missing_streak = PROCESS_MISSING_CONFIRM_CHECKS
        return events

    def _process_alive(self, package: str) -> tuple[bool, str]:
        pkg = android.validate_package_name(package)
        root_tool = getattr(self._root_info, "tool", None) if self._root_info else None
        if getattr(self._root_info, "available", False) and root_tool:
            try:
                res = android.run_root_command(["pidof", pkg], root_tool=root_tool, timeout=2)
                if res.ok and (res.stdout or "").strip():
                    return True, (res.stdout or "").strip().split()[0]
            except Exception:  # noqa: BLE001
                pass
            try:
                if android.is_process_running_any(pkg, root_tool):
                    return True, ""
            except Exception:  # noqa: BLE001
                pass
        try:
            if android.is_process_running(pkg):
                res = android.run_command(["pidof", pkg], timeout=2)
                pid = (res.stdout or "").strip().split()[0] if res.ok else ""
                return True, pid
        except Exception:  # noqa: BLE001
            pass
        return False, ""

    def _sample_ram(self, package: str) -> tuple[str, str, dict[str, Any]]:
        try:
            sample = android.get_package_ram_usage(package, self._root_info)
        except Exception as exc:  # noqa: BLE001
            return "N/A", "error", {"error": str(exc)[:120], "fake": False}
        usage = str(sample.get("usage_mb") or "N/A")
        method = str(sample.get("method") or "unknown")
        if usage == "N/A" or not sample.get("success"):
            return "N/A", method or "unavailable", {
                **sample,
                "source": method or "unavailable",
                "fake": False,
            }
        return usage, method, {
            **sample,
            "source": method,
            "fake": False,
        }

    def check_package(
        self,
        package: str,
        *,
        current_status: str,
        was_metric_active: bool,
    ) -> PackageCheckResult:
        pkg = str(package or "").strip()
        row = self._states.setdefault(pkg, PackageRuntimeState(package=pkg))
        now = time.time()
        self._last_process_check_at = now
        process_alive, pid = self._process_alive(pkg)
        ram_display, ram_source, ram_sample = self._sample_ram(pkg)
        row.last_real_ram_sample = ram_sample

        if process_alive:
            row.last_process_seen_at = now
            row.process_missing_streak = 0
            row.last_pid_seen = pid or row.last_pid_seen
            row.dead_confirmed = False
            row.dead_notified = False
            row.dead_since = 0.0
            row.last_dead_reason = ""
            if not row.alive_since and current_status in {"Online", "Launching", "Relaunching"}:
                row.alive_since = now
            return PackageCheckResult(
                package=pkg,
                process_alive=True,
                confirmed_dead=False,
                confirmed_recovered=False,
                ram_display=ram_display,
                ram_source=ram_source,
            )

        if was_metric_active or row.last_process_seen_at > 0 or row.alive_since > 0:
            row.process_missing_streak += 1
        if row.last_dead_reason in {"logcat_with_reason", "logcat_process_died"}:
            confirmed_dead = True
            dead_reason = row.last_dead_reason
        elif row.process_missing_streak >= PROCESS_MISSING_CONFIRM_CHECKS:
            confirmed_dead = True
            dead_reason = "process_missing"
        else:
            confirmed_dead = False
            dead_reason = "process_missing_pending"

        if confirmed_dead:
            row.dead_confirmed = True
            row.dead_since = row.dead_since or now
            row.last_dead_reason = dead_reason
            if not row.dead_notified:
                self._dead_transition_count += 1
                row.dead_notified = True
            return PackageCheckResult(
                package=pkg,
                process_alive=False,
                confirmed_dead=True,
                confirmed_recovered=False,
                dead_reason=dead_reason,
                ram_display=ram_display,
                ram_source=ram_source,
                logcat_event=row.last_event,
            )

        return PackageCheckResult(
            package=pkg,
            process_alive=False,
            confirmed_dead=False,
            confirmed_recovered=False,
            dead_reason=dead_reason,
            ram_display=ram_display,
            ram_source=ram_source,
        )

    def mark_recovered(self, package: str) -> None:
        row = self._states.get(package)
        if not row:
            return
        row.dead_confirmed = False
        row.dead_notified = False
        row.dead_since = 0.0
        row.process_missing_streak = 0
        row.last_dead_reason = ""
        row.alive_since = time.time()
        self._recovered_transition_count += 1

    def probe_snapshot(self) -> dict[str, Any]:
        return {
            "package_state_detector_enabled": True,
            "logcat_detector_started": bool(self._logcat_state.get("started")),
            "logcat_permission_ok": bool(self._logcat_state.get("permission_ok")),
            "logcat_error": self._logcat_state.get("error") or "",
            "package_uid_map": dict(self._uid_map),
            "per_package_state": [
                {
                    "package": s.package,
                    "android_uid": s.android_uid,
                    "last_pid_seen": s.last_pid_seen,
                    "last_process_seen_at": s.last_process_seen_at,
                    "last_logcat_event_at": s.last_logcat_event_at,
                    "last_gamejoinloadtime_at": s.last_gamejoinloadtime_at,
                    "alive_since": s.alive_since,
                    "dead_since": s.dead_since,
                    "last_dead_reason": s.last_dead_reason,
                    "process_missing_streak": s.process_missing_streak,
                    "dead_confirmed": s.dead_confirmed,
                    "last_ram_sample": s.last_real_ram_sample,
                }
                for s in self._states.values()
            ],
            "last_process_check": self._last_process_check_at,
            "last_logcat_events": self._last_logcat_events[-16:],
            "last_ram_samples": [
                {
                    "package": s.package,
                    "display": s.last_real_ram_sample.get("usage_mb") or "N/A",
                    "source": s.last_real_ram_sample.get("source")
                    or s.last_real_ram_sample.get("method")
                    or "none",
                }
                for s in self._states.values()
            ],
            "ram_fake_detected": False,
            "dead_detection_evidence": [
                {
                    "package": s.package,
                    "reason": s.last_dead_reason,
                    "dead_confirmed": s.dead_confirmed,
                    "process_missing_streak": s.process_missing_streak,
                }
                for s in self._states.values()
            ],
            "package_dead_transition_count": self._dead_transition_count,
            "package_recovered_transition_count": self._recovered_transition_count,
        }

    def write_probe_file(self) -> None:
        path = DATA_DIR / "package-state-detector.json"
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.probe_snapshot(), indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass
