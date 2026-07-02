"""Deterministic first-launch scheduler — independent of cache clear / Online.

When the user presses ``3. Start`` the Clear Cache phase stamps a monotonic
anchor (``clear_cache_started_at``).  Package *i* must receive its launch
command at::

    clear_cache_started_at + first_launch_delay_seconds + (i * interval_seconds)

Default: first package at T+5s, then every 30s.  The scheduler never waits for
cache clear completion, Online, heartbeat, Dead, prior launch completion, or
adb hangs (probe p-a5e6f62d28).
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

DEFAULT_FIRST_LAUNCH_DELAY_S = 0.0
DEFAULT_INTERVAL_S = 30.0
DEFAULT_POST_CLEAR_CACHE_MAX_DELAY_S = 0.5
DEFAULT_COMMAND_TIMEOUT_S = 5.0
DEFAULT_BEFORE_LAUNCH_TIMEOUT_S = 0.15


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
    username: str = ""
    command: str = "perform_rejoin"
    error: str = ""

    def to_probe_dict(self, *, anchor: float | None) -> dict[str, Any]:
        delta_ms = None
        if anchor is not None and self.fired_at is not None:
            delta_ms = round((self.fired_at - anchor) * 1000.0, 1)
        return {
            "package": self.package,
            "index": self.index,
            "username": self.username or None,
            "due_at": self.due_at,
            "fired_at": self.fired_at,
            "called_at": self.command_started_at,
            "skew_ms": self.skew_ms,
            "delta_from_clear_cache_ms": delta_ms,
            "command_started_at": self.command_started_at,
            "command_finished_at": self.command_finished_at,
            "command_timeout": self.command_timeout,
            "command": self.command,
            "result": self.result,
            "error": self.error or None,
        }


@dataclass
class LaunchScheduler:
    """Monotonic launch orchestrator for the Start session."""

    session_id: str
    packages: list[str]
    first_launch_delay_seconds: float = DEFAULT_FIRST_LAUNCH_DELAY_S
    interval_seconds: float = DEFAULT_INTERVAL_S
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_S
    before_launch_timeout_seconds: float = DEFAULT_BEFORE_LAUNCH_TIMEOUT_S

    clear_cache_started_at: float | None = None
    clear_cache_finished_at: float | None = None
    clear_cache_duration_ms: float | None = None
    clear_cache_timeout: bool = False
    first_launch_due_at: float | None = None
    launch_scheduler_started_at: float | None = None
    first_launch_called_at: float | None = None
    checking_system_started_at: float | None = None
    lifecycle_blocker: str = ""
    scheduler_alive: bool = False
    blocked_by_clear_cache: bool = False
    blocked_by_online_wait: bool = False
    launch_scheduler_aborted_reason: str | None = None
    scheduler_survived_ui_failure: bool = False
    all_packages_dispatched_at: float | None = None
    all_packages_launched_at: float | None = None
    post_clear_cache_delay_ms: float | None = None
    first_launch_delay_from_clear_cache_finish_ms: float | None = None
    launch_anchor_mode: str = "clear_cache_start"

    _attempts: list[LaunchAttemptRecord] = field(default_factory=list)
    _launch_interval_observed_ms: list[float] = field(default_factory=list)
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
            self.lifecycle_blocker = ""
            self._rebuild_attempts()
            self._persist(force=True)
        return now

    def record_clear_cache_finished(
        self,
        *,
        finished_at: float | None = None,
        duration_ms: float | None = None,
        timed_out: bool = False,
        reanchor_launches: bool = True,
    ) -> None:
        with self._lock:
            self.clear_cache_finished_at = float(
                finished_at if finished_at is not None else time.monotonic()
            )
            if duration_ms is not None:
                self.clear_cache_duration_ms = float(duration_ms)
            elif self.clear_cache_started_at is not None:
                self.clear_cache_duration_ms = round(
                    (self.clear_cache_finished_at - self.clear_cache_started_at) * 1000.0,
                    1,
                )
            self.clear_cache_timeout = bool(timed_out)
            self.blocked_by_clear_cache = False
            if reanchor_launches:
                self._reanchor_launches_from_cache_finish_locked()
            self._persist(force=True)

    def _reanchor_launches_from_cache_finish_locked(self) -> None:
        """Schedule first launch from cache-finish + delay (not cache-start + delay)."""
        finish = self.clear_cache_finished_at
        if finish is None:
            return
        delay = min(
            max(0.0, float(self.first_launch_delay_seconds)),
            DEFAULT_POST_CLEAR_CACHE_MAX_DELAY_S,
        )
        self.launch_anchor_mode = "clear_cache_finish"
        self.first_launch_due_at = finish + delay
        for i, row in enumerate(self._attempts):
            row.due_at = finish + delay + (i * self.interval_seconds)
            row.fired_at = None
            row.skew_ms = None
            row.command_started_at = None
            row.command_finished_at = None
            row.result = "pending"
            row.error = ""

    def reanchor_launches_from_getting_ready_finished(
        self, *, finished_at: float | None = None
    ) -> None:
        """Schedule first launch immediately after the post-cache Getting Ready bridge."""
        now = float(finished_at if finished_at is not None else time.monotonic())
        with self._lock:
            self.launch_anchor_mode = "getting_ready_finish"
            self.first_launch_due_at = now
            for i, row in enumerate(self._attempts):
                row.due_at = now + (i * self.interval_seconds)
                row.fired_at = None
                row.skew_ms = None
                row.command_started_at = None
                row.command_finished_at = None
                row.result = "pending"
                row.error = ""
            self._persist(force=True)

    def mark_scheduler_started(self, *, monotonic_now: float | None = None) -> None:
        with self._lock:
            self.launch_scheduler_started_at = float(
                monotonic_now if monotonic_now is not None else time.monotonic()
            )
            self._persist(force=True)

    def mark_checking_system_started(self, *, monotonic_now: float | None = None) -> None:
        with self._lock:
            self.checking_system_started_at = float(
                monotonic_now if monotonic_now is not None else time.monotonic()
            )
            self._persist(force=True)

    def mark_monitoring_started(self, *, monotonic_now: float | None = None) -> None:
        self.mark_checking_system_started(monotonic_now=monotonic_now)

    def set_lifecycle_blocker(self, reason: str) -> None:
        with self._lock:
            self.lifecycle_blocker = str(reason or "")[:200]
            self._persist(force=True)

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
            if index == 0:
                self.first_launch_called_at = ts
                if self.clear_cache_finished_at is not None:
                    self.first_launch_delay_from_clear_cache_finish_ms = round(
                        (ts - self.clear_cache_finished_at) * 1000.0, 1
                    )
                    self.post_clear_cache_delay_ms = (
                        self.first_launch_delay_from_clear_cache_finish_ms
                    )
            elif index > 0:
                prev = self._attempts[index - 1].fired_at
                if prev is not None:
                    self._launch_interval_observed_ms.append(
                        round((ts - prev) * 1000.0, 1)
                    )
            self._persist(force=True)

    def record_command_started(
        self,
        index: int,
        *,
        started_at: float | None = None,
        username: str = "",
    ) -> None:
        ts = float(started_at if started_at is not None else time.monotonic())
        with self._lock:
            if 0 <= index < len(self._attempts):
                row = self._attempts[index]
                row.command_started_at = ts
                if username:
                    row.username = username
                if row.result in ("pending", "fired"):
                    row.result = "launching"
                if index == 0 and self.first_launch_called_at is None:
                    self.first_launch_called_at = ts
                self._persist(force=True)

    def record_command_finished(
        self,
        index: int,
        *,
        finished_at: float | None = None,
        result: str,
        error: str = "",
    ) -> None:
        ts = float(finished_at if finished_at is not None else time.monotonic())
        with self._lock:
            if 0 <= index < len(self._attempts):
                row = self._attempts[index]
                row.command_finished_at = ts
                row.result = result
                row.error = str(error or "")[:200]
                self._persist(force=True)

    def _run_before_launch_bounded(
        self,
        callback: Callable[[int, str], None] | None,
        index: int,
        package: str,
        *,
        monotonic_fn: Callable[[], float],
        sleep_fn: Callable[[float], None],
    ) -> None:
        if callback is None:
            return
        done = threading.Event()
        exc_holder: list[BaseException] = []

        def _invoke() -> None:
            try:
                callback(index, package)
            except BaseException as exc:  # noqa: BLE001
                exc_holder.append(exc)
            finally:
                done.set()

        worker = threading.Thread(
            target=_invoke,
            name=f"before-launch-{index}",
            daemon=True,
        )
        worker.start()
        deadline = monotonic_fn() + max(0.05, float(self.before_launch_timeout_seconds))
        while not done.is_set():
            if monotonic_fn() >= deadline:
                self.set_lifecycle_blocker(
                    f"before_launch_timeout:{package}"
                )
                return
            sleep_fn(0.02)

    def run_schedule(
        self,
        launch_one: Callable[[int, str], str],
        *,
        on_before_launch: Callable[[int, str], None] | None = None,
        on_after_launch: Callable[[int, str, str], None] | None = None,
        stop_event: threading.Event | None = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
        username_for_index: Callable[[int, str], str] | None = None,
    ) -> None:
        """Fire each package at its due time (blocking caller thread).

        Launch commands are dispatched on independent daemon threads so a slow
        or hung ``launch_one`` never delays the next package's due time.
        """
        with self._lock:
            self.scheduler_alive = True
            self._done_event.clear()
            self.mark_scheduler_started(monotonic_now=monotonic_fn())
            self._persist(force=True)
        workers: list[threading.Thread] = []
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
                self.record_command_started(row.index, started_at=fired_at, username="")
                username = ""
                if username_for_index is not None:
                    try:
                        username = str(
                            username_for_index(row.index, row.package) or ""
                        )
                    except Exception:  # noqa: BLE001
                        username = ""
                    if username:
                        with self._lock:
                            if 0 <= row.index < len(self._attempts):
                                self._attempts[row.index].username = username
                                self._persist(force=True)
                self._run_before_launch_bounded(
                    on_before_launch,
                    row.index,
                    row.package,
                    monotonic_fn=monotonic_fn,
                    sleep_fn=sleep_fn,
                )

                def _worker(
                    idx: int = row.index,
                    pkg: str = row.package,
                    uname: str = username,
                ) -> None:
                    try:
                        result = launch_one(idx, pkg)
                    except Exception as exc:  # noqa: BLE001
                        result = f"error:{str(exc)[:120]}"
                        self.record_command_finished(
                            idx,
                            finished_at=monotonic_fn(),
                            result=result,
                            error=str(exc)[:200],
                        )
                        if on_after_launch is not None:
                            try:
                                on_after_launch(idx, pkg, result)
                            except Exception:  # noqa: BLE001
                                pass
                        return
                    err = ""
                    if result.startswith("error:"):
                        err = result[6:]
                    elif result.startswith("failed:"):
                        err = result[7:]
                    self.record_command_finished(
                        idx,
                        finished_at=monotonic_fn(),
                        result=result,
                        error=err,
                    )
                    if on_after_launch is not None:
                        try:
                            on_after_launch(idx, pkg, result)
                        except Exception:  # noqa: BLE001
                            pass

                thread = threading.Thread(
                    target=_worker,
                    name=f"launch-worker-{row.index}-{row.package[:16]}",
                    daemon=True,
                )
                thread.start()
                workers.append(thread)
            with self._lock:
                dispatched_at = monotonic_fn()
                self.all_packages_dispatched_at = dispatched_at
                self.all_packages_launched_at = dispatched_at
                self._persist(force=True)
            try:
                from . import start_lifecycle as _start_lifecycle

                _start_lifecycle.mark_all_packages_dispatched()
            except Exception:  # noqa: BLE001
                pass
        except BaseException as exc:  # noqa: BLE001
            with self._lock:
                self.launch_scheduler_aborted_reason = str(exc)[:200]
                self._persist(force=True)
            raise
        finally:
            for thread in workers:
                thread.join()
            with self._lock:
                self.mark_monitoring_started(monotonic_now=monotonic_fn())
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
        username_for_index: Callable[[int, str], str] | None = None,
    ) -> threading.Thread:
        """Run :meth:`run_schedule` on a daemon thread."""
        thread = threading.Thread(
            target=self.run_schedule,
            kwargs={
                "launch_one": launch_one,
                "on_before_launch": on_before_launch,
                "on_after_launch": on_after_launch,
                "stop_event": stop_event,
                "username_for_index": username_for_index,
            },
            name=f"launch-scheduler-{self.session_id[:24]}",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        return thread

    def wait_until_complete(self, timeout: float | None = None) -> bool:
        return self._done_event.wait(timeout=timeout)

    def first_launch_delay_from_clear_cache_start_ms(self) -> float | None:
        with self._lock:
            if self.clear_cache_started_at is None or self.first_launch_called_at is None:
                return None
            return round(
                (self.first_launch_called_at - self.clear_cache_started_at) * 1000.0,
                1,
            )

    def launch_calls_probe(self) -> list[dict[str, Any]]:
        with self._lock:
            anchor = self.clear_cache_started_at
            return [row.to_probe_dict(anchor=anchor) for row in self._attempts]

    def probe_snapshot(self) -> dict[str, Any]:
        with self._lock:
            anchor = self.clear_cache_started_at
            snap = {
                "session_id": self.session_id or None,
                "clear_cache_started_at": anchor,
                "clear_cache_finished_at": self.clear_cache_finished_at,
                "clear_cache_duration_ms": self.clear_cache_duration_ms,
                "clear_cache_timeout": self.clear_cache_timeout,
                "first_launch_due_at": self.first_launch_due_at,
                "launch_scheduler_started_at": self.launch_scheduler_started_at,
                "first_launch_called_at": self.first_launch_called_at,
                "first_launch_delay_from_clear_cache_start_ms": (
                    self.first_launch_delay_from_clear_cache_start_ms()
                ),
                "checking_system_started_at": self.checking_system_started_at,
                "lifecycle_blocker": self.lifecycle_blocker or None,
                "interval_seconds": self.interval_seconds,
                "first_launch_delay_seconds": self.first_launch_delay_seconds,
                "scheduler_alive": self.scheduler_alive,
                "launch_scheduler_alive": self.scheduler_alive,
                "blocked_by_clear_cache": self.blocked_by_clear_cache,
                "blocked_by_online_wait": self.blocked_by_online_wait,
                "launch_scheduler_aborted_reason": self.launch_scheduler_aborted_reason,
                "scheduler_survived_ui_failure": self.scheduler_survived_ui_failure,
                "all_packages_dispatched_at": self.all_packages_dispatched_at,
                "all_packages_launched_at": self.all_packages_launched_at,
                "post_clear_cache_delay_ms": self.post_clear_cache_delay_ms,
                "first_launch_delay_from_clear_cache_finish_ms": (
                    self.first_launch_delay_from_clear_cache_finish_ms
                ),
                "launch_anchor_mode": self.launch_anchor_mode,
                "launch_interval_observed_ms": list(self._launch_interval_observed_ms),
                "launch_calls": self.launch_calls_probe(),
                "launch_attempts": [
                    row.to_probe_dict(anchor=anchor) for row in self._attempts
                ],
            }
        try:
            from . import start_lifecycle as _start_lifecycle

            lifecycle = _start_lifecycle.probe_snapshot()
            dispatched_mono = self.all_packages_dispatched_at
            checking_mono = self.checking_system_started_at
            snap.update(lifecycle)
            snap["all_packages_dispatched_at"] = dispatched_mono
            if checking_mono is not None:
                snap["checking_system_started_at"] = checking_mono
            if lifecycle.get("all_packages_dispatched_at") is not None:
                snap["all_packages_dispatched_at_wall"] = lifecycle[
                    "all_packages_dispatched_at"
                ]
        except Exception:  # noqa: BLE001
            pass
        return snap

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
        "clear_cache_finished_at": None,
        "clear_cache_duration_ms": None,
        "clear_cache_timeout": False,
        "first_launch_due_at": None,
        "launch_scheduler_started_at": None,
        "first_launch_called_at": None,
        "first_launch_delay_from_clear_cache_start_ms": None,
        "checking_system_started_at": None,
        "lifecycle_blocker": None,
        "interval_seconds": DEFAULT_INTERVAL_S,
        "first_launch_delay_seconds": DEFAULT_FIRST_LAUNCH_DELAY_S,
        "scheduler_alive": False,
        "blocked_by_clear_cache": False,
        "blocked_by_online_wait": False,
        "launch_scheduler_aborted_reason": None,
        "scheduler_survived_ui_failure": False,
        "all_packages_dispatched_at": None,
        "launch_interval_observed_ms": [],
        "launch_calls": [],
        "launch_attempts": [],
        "_source": "none",
    }
