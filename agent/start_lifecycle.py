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


def reset_for_start(packages: list[str]) -> None:
    """Reset lifecycle flags at the beginning of a Start session."""
    global _cache_clear_closed, _launch_scheduled_packages, _clear_cache_phase_exited_at
    global _clear_cache_phase_exit_reason, _ui_render_error_count
    global _last_ui_render_error, _scheduler_survived_ui_failure
    global _launch_scheduler_aborted_reason, _start_cache_clear_abort
    global _blocked_force_stop_count, _last_blocked_force_stop
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


def probe_snapshot() -> dict[str, Any]:
    with _lock:
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
        }
