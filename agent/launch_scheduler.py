"""Deterministic first-launch scheduler — independent of cache clear / Online.

When the user presses ``3. Start`` the Clear Cache phase stamps a monotonic
anchor (``clear_cache_started_at``).  Package *i* must receive its launch
command at::

    clear_cache_started_at + first_launch_delay_seconds + (i * interval_seconds)

Default: first package at T+5s, then every 30s.  The scheduler never waits for
cache clear completion, Online, heartbeat, Dead, or adb hangs (probe
p-bf0b2feb55).
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

_STATE_FILENAME = "launch-schedule-state.json"
_PERSIST_MIN_INTERVAL_S = 0.25
_STATE_MAX_AGE_S = 600.0

DEFAULT_FIRST_LAUNCH_DELAY_S = 5.0
DEFAULT_INTERVAL_S = 30.0
DEFAULT_COMMAND_TIMEOUT_S = 5.0


def _state_file_path():
    try:
        from .constants import DATA_DIR

        return DATA_DIR / _STATE_FILENAME
    except Exception:  # noqa: BLE001
        return None


@dataclass
class LaunchAttemptRecord:
    package: str
    index: int
    due_at: float
    fired_at: float | None = None
    skew_ms: float | None = None
    command_started_at: float | None = None
    command_finished_at: float | None = None
    command_timeout: float = DEFAULT_COMMAND_TIMEOUT_S
    result: str = "pending"

    def to_probe_dict(self, *, anchor: float | None) -> dict[str, Any]:
        delta_ms = None
        if anchor is not None and self.fired_at is not None:
            delta_ms = round((self.fired_at - anchor) * 1000.0, 1)
        return {
            "package": self.package,
            "index": self.index,
            "due_at": self.due_at,
            "fired_at": self.fired_at,
            "skew_ms": self.skew_ms,
            "delta_from_clear_cache_ms": delta_ms,
            "command_started_at": self.command_started_at,
            "command_finished_at": self.command_finished_at,
            "command_timeout": self.command_timeout,
            "result": self.result,
        }


@dataclass
class LaunchScheduler:
    """Monotonic launch orchestrator for the Start session."""

    session_id: str
    packages: list[str]
    first_launch_delay_seconds: float = DEFAULT_FIRST_LAUNCH_DELAY_S
    interval_seconds: float = DEFAULT_INTERVAL_S
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_S

    clear_cache_started_at: float | None = None
    first_launch_due_at: float | None = None
    scheduler_alive: bool = False
    blocked_by_clear_cache: bool = False
    blocked_by_online_wait: bool = False

    _attempts: list[LaunchAttemptRecord] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _done_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _persist_enabled: bool = False
    _last_persist_at: float = 0.0

    def __post_init__(self) -> None:
        if not self._attempts and self.packages:
            self._rebuild_attempts()

    def _rebuild_attempts(self) -> None:
        self._attempts = []
        anchor = self.clear_cache_started_at
        if anchor is None:
            return
        for i, pkg in enumerate(self.packages):
            due = anchor + self.first_launch_delay_seconds + (i * self.interval_seconds)
            self._attempts.append(
                LaunchAttemptRecord(
                    package=pkg,
                    index=i,
                    due_at=due,
                    command_timeout=self.command_timeout_seconds,
                )
            )

    def enable_persistence(self) -> None:
        with self._lock:
            self._persist_enabled = True
        self.write_state_file(force=True)

    def mark_clear_cache_started(self, *, monotonic_now: float | None = None) -> float:
        """Stamp the schedule anchor when the Clear Cache phase begins."""
        now = float(monotonic_now if monotonic_now is not None else time.monotonic())
        with self._lock:
            self.clear_cache_started_at = now
            self.first_launch_due_at = now + self.first_launch_delay_seconds
            self.blocked_by_clear_cache = False
            self.blocked_by_online_wait = False
            self._rebuild_attempts()
            self._persist(force=True)
        return now

    def due_at_for_index(self, index: int) -> float | None:
        with self._lock:
            if index < 0 or index >= len(self._attempts):
                return None
            return self._attempts[index].due_at

    def wait_until_due(
        self,
        index: int,
        *,
        monotonic_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
        stop_event: threading.Event | None = None,
        poll_s: float = 0.05,
    ) -> bool:
        """Sleep until package ``index`` is due. Returns False if stopped."""
        due = self.due_at_for_index(index)
        if due is None:
            return False
        while True:
            if stop_event is not None and stop_event.is_set():
                return False
            remaining = due - monotonic_fn()
            if remaining <= 0:
                return True
            sleep_fn(min(poll_s, remaining))

    def record_fired(self, index: int, *, fired_at: float | None = None) -> None:
        ts = float(fired_at if fired_at is not None else time.monotonic())
        with self._lock:
            if index < 0 or index >= len(self._attempts):
                return
            row = self._attempts[index]
            row.fired_at = ts
            row.skew_ms = round((ts - row.due_at) * 1000.0, 1)
            if row.result == "pending":
                row.result = "fired"
            self._persist(force=True)

    def record_command_started(self, index: int, *, started_at: float | None = None) -> None:
        ts = float(started_at if started_at is not None else time.monotonic())
        with self._lock:
            if 0 <= index < len(self._attempts):
                self._attempts[index].command_started_at = ts
                self._persist()

    def record_command_finished(
        self,
        index: int,
        *,
        finished_at: float | None = None,
        result: str,
    ) -> None:
        ts = float(finished_at if finished_at is not None else time.monotonic())
        with self._lock:
            if 0 <= index < len(self._attempts):
                row = self._attempts[index]
                row.command_finished_at = ts
                row.result = result
                self._persist(force=True)

    def run_schedule(
        self,
        launch_one: Callable[[int, str], str],
        *,
        on_before_launch: Callable[[int, str], None] | None = None,
        on_after_launch: Callable[[int, str, str], None] | None = None,
        stop_event: threading.Event | None = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        """Fire each package at its due time (blocking caller thread)."""
        with self._lock:
            self.scheduler_alive = True
            self._done_event.clear()
            self._persist(force=True)
        try:
            for row in list(self._attempts):
                if stop_event is not None and stop_event.is_set():
                    break
                if not self.wait_until_due(
                    row.index,
                    monotonic_fn=monotonic_fn,
                    sleep_fn=sleep_fn,
                    stop_event=stop_event,
                ):
                    break
                fired_at = monotonic_fn()
                self.record_fired(row.index, fired_at=fired_at)
                if on_before_launch is not None:
                    try:
                        on_before_launch(row.index, row.package)
                    except Exception:  # noqa: BLE001
                        pass
                self.record_command_started(row.index, started_at=fired_at)
                try:
                    result = launch_one(row.index, row.package)
                except Exception as exc:  # noqa: BLE001
                    result = f"error:{str(exc)[:120]}"
                self.record_command_finished(
                    row.index, finished_at=monotonic_fn(), result=result
                )
                if on_after_launch is not None:
                    try:
                        on_after_launch(row.index, row.package, result)
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            with self._lock:
                self.scheduler_alive = False
                self._done_event.set()
                self._persist(force=True)

    def start_background(
        self,
        launch_one: Callable[[int, str], str],
        *,
        on_before_launch: Callable[[int, str], None] | None = None,
        on_after_launch: Callable[[int, str, str], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> threading.Thread:
        """Run :meth:`run_schedule` on a daemon thread."""
        thread = threading.Thread(
            target=self.run_schedule,
            kwargs={
                "launch_one": launch_one,
                "on_before_launch": on_before_launch,
                "on_after_launch": on_after_launch,
                "stop_event": stop_event,
            },
            name=f"launch-scheduler-{self.session_id[:24]}",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        return thread

    def wait_until_complete(self, timeout: float | None = None) -> bool:
        return self._done_event.wait(timeout=timeout)

    def probe_snapshot(self) -> dict[str, Any]:
        with self._lock:
            anchor = self.clear_cache_started_at
            return {
                "session_id": self.session_id or None,
                "clear_cache_started_at": anchor,
                "first_launch_due_at": self.first_launch_due_at,
                "interval_seconds": self.interval_seconds,
                "first_launch_delay_seconds": self.first_launch_delay_seconds,
                "scheduler_alive": self.scheduler_alive,
                "blocked_by_clear_cache": self.blocked_by_clear_cache,
                "blocked_by_online_wait": self.blocked_by_online_wait,
                "launch_attempts": [
                    row.to_probe_dict(anchor=anchor) for row in self._attempts
                ],
            }

    def _persist(self, *, force: bool = False) -> None:
        if not self._persist_enabled:
            return
        now = time.time()
        if not force and (now - self._last_persist_at) < _PERSIST_MIN_INTERVAL_S:
            return
        self._last_persist_at = now
        self._write_state_file_locked()

    def write_state_file(self, *, force: bool = False) -> None:
        with self._lock:
            if force:
                self._last_persist_at = time.time()
            self._write_state_file_locked()

    def _write_state_file_locked(self) -> None:
        path = _state_file_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "written_at": time.time(),
                "pid": os.getpid(),
                "snapshot": self.probe_snapshot(),
            }
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001
            pass


_INSTANCE: LaunchScheduler | None = None
_INSTANCE_LOCK = threading.Lock()


def get() -> LaunchScheduler | None:
    return _INSTANCE


def set_active(scheduler: LaunchScheduler | None) -> None:
    global _INSTANCE
    with _INSTANCE_LOCK:
        _INSTANCE = scheduler


def read_state_file(*, max_age_s: float = _STATE_MAX_AGE_S) -> dict[str, Any] | None:
    path = _state_file_path()
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    written_at = data.get("written_at")
    try:
        age = time.time() - float(written_at)
    except (TypeError, ValueError):
        return None
    if age > max_age_s:
        return None
    snap = data.get("snapshot")
    if not isinstance(snap, dict):
        return None
    snap = dict(snap)
    snap["_source"] = "state_file"
    snap["_state_file_age_s"] = round(age, 2)
    snap["_state_file_pid"] = data.get("pid")
    return snap


def probe_snapshot() -> dict[str, Any]:
    live = get()
    if live is not None and (
        live.scheduler_alive or live.clear_cache_started_at is not None
    ):
        snap = live.probe_snapshot()
        snap["_source"] = "live"
        return snap
    disk = read_state_file()
    if disk is not None:
        return disk
    if live is not None:
        snap = live.probe_snapshot()
        snap["_source"] = "live_idle"
        return snap
    return {
        "session_id": None,
        "clear_cache_started_at": None,
        "first_launch_due_at": None,
        "interval_seconds": DEFAULT_INTERVAL_S,
        "first_launch_delay_seconds": DEFAULT_FIRST_LAUNCH_DELAY_S,
        "scheduler_alive": False,
        "blocked_by_clear_cache": False,
        "blocked_by_online_wait": False,
        "launch_attempts": [],
        "_source": "none",
    }
