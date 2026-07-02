"""Shared Start lifecycle flags — UI safety, cache-clear closure, launch guards."""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.RLock()
_cache_clear_closed = False
_launch_scheduled_packages: set[str] = set()
_clear_cache_phase_exited_at: float | None = None
_clear_cache_phase_exit_reason = ""
_ui_render_error_count = 0
_last_ui_render_error = ""
_scheduler_survived_ui_failure = False
_launch_scheduler_aborted_reason: str | None = None
_start_cache_clear_abort = False
_blocked_force_stop_count = 0
_last_blocked_force_stop: str = ""
_prepare_started_at: float | None = None
_prepare_finished_at: float | None = None
_prepare_wait_reasons: list[str] = []
_prepare_blockers: list[str] = []
_table_state_phase: str = ""
_table_state_source: str = "lifecycle"
_ui_phase_version: int = 0
_stale_ui_write_ignored_count: int = 0


def reset_for_start(packages: list[str]) -> None:
    """Reset lifecycle flags at the beginning of a Start session."""
    global _cache_clear_closed, _launch_scheduled_packages, _clear_cache_phase_exited_at
    global _clear_cache_phase_exit_reason, _ui_render_error_count
    global _last_ui_render_error, _scheduler_survived_ui_failure
    global _launch_scheduler_aborted_reason, _start_cache_clear_abort
    global _blocked_force_stop_count, _last_blocked_force_stop
    global _prepare_started_at, _prepare_finished_at, _prepare_wait_reasons, _prepare_blockers
    global _table_state_phase, _table_state_source, _ui_phase_version, _stale_ui_write_ignored_count
    with _lock:
        _cache_clear_closed = False
        _launch_scheduled_packages = {str(p).strip() for p in packages if str(p).strip()}
        _clear_cache_phase_exited_at = None
        _clear_cache_phase_exit_reason = ""
        _ui_render_error_count = 0
        _last_ui_render_error = ""
        _scheduler_survived_ui_failure = False
        _launch_scheduler_aborted_reason = None
        _start_cache_clear_abort = False
        _blocked_force_stop_count = 0
        _last_blocked_force_stop = ""
        _prepare_started_at = None
        _prepare_finished_at = None
        _prepare_wait_reasons = []
        _prepare_blockers = []
        _table_state_phase = ""
        _table_state_source = "lifecycle"
        _ui_phase_version = 0
        _stale_ui_write_ignored_count = 0


def mark_launch_scheduled(packages: list[str]) -> None:
    global _launch_scheduled_packages
    with _lock:
        for pkg in packages:
            text = str(pkg or "").strip()
            if text:
                _launch_scheduled_packages.add(text)


def mark_cache_clear_closed() -> None:
    global _cache_clear_closed
    with _lock:
        _cache_clear_closed = True


def is_cache_clear_closed() -> bool:
    with _lock:
        return bool(_cache_clear_closed)


def exit_clear_cache_phase(reason: str) -> None:
    global _clear_cache_phase_exited_at, _clear_cache_phase_exit_reason
    with _lock:
        if _clear_cache_phase_exited_at is None:
            _clear_cache_phase_exited_at = time.time()
        _clear_cache_phase_exit_reason = str(reason or "")[:200]


def request_abort_start_cache_clear() -> None:
    global _start_cache_clear_abort
    with _lock:
        _start_cache_clear_abort = True


def start_cache_clear_abort_requested() -> bool:
    with _lock:
        return bool(_start_cache_clear_abort)


def should_block_force_stop(package: str, *, source: str = "") -> bool:
    """Block prep/cache clear force-stop once launch scheduling has begun."""
    pkg = str(package or "").strip()
    if not pkg:
        return False
    with _lock:
        if not _cache_clear_closed:
            return False
        if pkg not in _launch_scheduled_packages:
            return False
        global _blocked_force_stop_count, _last_blocked_force_stop
        _blocked_force_stop_count += 1
        _last_blocked_force_stop = f"{source}:{pkg}"[:200]
        return True


def record_ui_render_error(exc: BaseException) -> None:
    global _ui_render_error_count, _last_ui_render_error
    with _lock:
        _ui_render_error_count += 1
        _last_ui_render_error = f"{type(exc).__name__}:{exc}"[:200]


def mark_scheduler_survived_ui_failure() -> None:
    global _scheduler_survived_ui_failure
    with _lock:
        _scheduler_survived_ui_failure = True


def set_launch_scheduler_aborted_reason(reason: str) -> None:
    global _launch_scheduler_aborted_reason
    with _lock:
        _launch_scheduler_aborted_reason = str(reason or "")[:200] or None


def mark_prepare_started() -> None:
    global _prepare_started_at
    with _lock:
        if _prepare_started_at is None:
            _prepare_started_at = time.time()


def mark_prepare_finished() -> None:
    global _prepare_finished_at
    with _lock:
        _prepare_finished_at = time.time()


def note_prepare_wait(reason: str) -> None:
    text = str(reason or "").strip()[:120]
    if not text:
        return
    with _lock:
        if text not in _prepare_wait_reasons:
            _prepare_wait_reasons.append(text)


def note_prepare_blocker(reason: str) -> None:
    text = str(reason or "").strip()[:120]
    if not text:
        return
    with _lock:
        if text not in _prepare_blockers:
            _prepare_blockers.append(text)


def bump_ui_phase_version(*, phase: str, source: str = "lifecycle") -> int:
    global _ui_phase_version, _table_state_phase, _table_state_source
    with _lock:
        _ui_phase_version += 1
        _table_state_phase = str(phase or "")[:80]
        _table_state_source = str(source or "lifecycle")[:80]
        return _ui_phase_version


def try_write_table_phase(phase: str, version: int, *, source: str = "lifecycle") -> bool:
    """Return False when an older phase tries to overwrite a newer table state."""
    global _stale_ui_write_ignored_count, _table_state_phase, _table_state_source, _ui_phase_version
    with _lock:
        if version < _ui_phase_version:
            _stale_ui_write_ignored_count += 1
            return False
        if version > _ui_phase_version:
            _ui_phase_version = version
        _table_state_phase = str(phase or "")[:80]
        _table_state_source = str(source or "lifecycle")[:80]
        return True


def prepare_duration_ms() -> float | None:
    with _lock:
        if _prepare_started_at is None or _prepare_finished_at is None:
            return None
        return round((_prepare_finished_at - _prepare_started_at) * 1000.0, 1)


def probe_snapshot() -> dict[str, Any]:
    with _lock:
        duration = None
        if _prepare_started_at is not None and _prepare_finished_at is not None:
            duration = round((_prepare_finished_at - _prepare_started_at) * 1000.0, 1)
        return {
            "cache_clear_closed": bool(_cache_clear_closed),
            "clear_cache_phase_exited_at": _clear_cache_phase_exited_at,
            "clear_cache_phase_exit_reason": _clear_cache_phase_exit_reason or None,
            "ui_render_error_count": int(_ui_render_error_count),
            "last_ui_render_error": _last_ui_render_error or None,
            "scheduler_survived_ui_failure": bool(_scheduler_survived_ui_failure),
            "launch_scheduler_aborted_reason": _launch_scheduler_aborted_reason,
            "launch_scheduled_packages": sorted(_launch_scheduled_packages),
            "blocked_force_stop_count": int(_blocked_force_stop_count),
            "last_blocked_force_stop": _last_blocked_force_stop or None,
            "prepare_started_at": _prepare_started_at,
            "prepare_finished_at": _prepare_finished_at,
            "prepare_duration_ms": duration,
            "prepare_wait_reasons": list(_prepare_wait_reasons),
            "prepare_blockers": list(_prepare_blockers),
            "table_state_phase": _table_state_phase or None,
            "table_state_source": _table_state_source or None,
            "ui_phase_version": int(_ui_phase_version),
            "stale_ui_write_ignored_count": int(_stale_ui_write_ignored_count),
        }
