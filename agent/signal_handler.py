"""Centralized POSIX signal trapping and deterministic teardown for DENG Rejoin.

Traps SIGINT, SIGTERM, and SIGHUP (when available), restores the TTY, erases
runtime lock/PID files owned by the current process, and exits with
``128 + signum`` — without joining background threads or blocking on I/O locks.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .constants import (
    LOCK_PATH,
    MONITOR_LOCK_PATH,
    MONITOR_PID_PATH,
    MONITOR_STATUS_PATH,
    PID_PATH,
)
from . import safe_io

TEARDOWN_BUDGET_SECONDS = 1.5

_teardown_lock = threading.Lock()
_teardown_done = False
_handlers_installed = False
_extra_runtime_paths: list[Path] = []


def register_runtime_path(path: Path | str) -> None:
    """Register an additional lock/state file to erase on signal teardown."""
    try:
        resolved = Path(path).expanduser()
    except Exception:  # noqa: BLE001
        return
    with _teardown_lock:
        if resolved not in _extra_runtime_paths:
            _extra_runtime_paths.append(resolved)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _path_owned_by_current_process(path: Path) -> bool:
    """Return True when ``path`` appears to belong to this PID (or is missing)."""
    if not path.exists():
        return False
    name = path.name.lower()
    if name.endswith(".pid"):
        stored = _read_pid(path)
        return stored in {None, os.getpid()}
    if name.endswith(".lock") or name.endswith(".json"):
        meta = _read_json(path)
        pid = meta.get("pid")
        if pid is None:
            return True
        try:
            return int(pid) == os.getpid()
        except (TypeError, ValueError):
            return False
    return True


def _unlink_if_owned(path: Path) -> None:
    if not _path_owned_by_current_process(path):
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def erase_runtime_locks() -> None:
    """Atomically remove lock/PID/state files owned by the running instance."""
    current = os.getpid()
    for path in (
        PID_PATH,
        LOCK_PATH,
        MONITOR_PID_PATH,
        MONITOR_LOCK_PATH,
        MONITOR_STATUS_PATH,
        *_extra_runtime_paths,
    ):
        _unlink_if_owned(path)

    # Fallback: if PID file still points at us, force removal.
    for path in (PID_PATH, MONITOR_PID_PATH):
        try:
            if path.exists() and _read_pid(path) == current:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def run_teardown_pipeline(signum: int, *, exit_process: bool = True) -> int:
    """Execute TTY rescue, lock erasure, and hard exit within a bounded budget."""
    global _teardown_done

    try:
        with _teardown_lock:
            if _teardown_done:
                code = 128 + int(signum)
                os._exit(code)
            _teardown_done = True

        deadline = time.monotonic() + TEARDOWN_BUDGET_SECONDS
        try:
            safe_io.restore_terminal()
        except BaseException:  # noqa: BLE001
            pass

        if time.monotonic() < deadline:
            try:
                erase_runtime_locks()
            except BaseException:  # noqa: BLE001
                pass

        exit_code = 128 + int(signum)
        if exit_process:
            os._exit(exit_code)
        return exit_code
    except BaseException as exc:  # noqa: BLE001
        try:
            sys.stderr.write(f"DENG Rejoin: safe shutdown failed ({exc})\n")
            sys.stderr.flush()
        except BaseException:  # noqa: BLE001
            pass
        fallback = 130 if int(signum) == getattr(signal, "SIGINT", 2) else 128 + int(signum)
        if exit_process:
            os._exit(fallback)
        return fallback


def _handle_signal(signum: int, _frame: Any) -> None:
    try:
        run_teardown_pipeline(signum, exit_process=True)
    except BaseException as exc:  # noqa: BLE001
        try:
            sys.stderr.write(f"DENG Rejoin: signal handler failed ({exc})\n")
            sys.stderr.flush()
        except BaseException:  # noqa: BLE001
            pass
        fallback = 130 if int(signum) == getattr(signal, "SIGINT", 2) else 128 + int(signum)
        os._exit(fallback)


def install_signal_handlers(*, force: bool = False) -> None:
    """Register SIGINT, SIGTERM, and SIGHUP handlers for the CLI process."""
    global _handlers_installed
    if _handlers_installed and not force:
        return

    for sig_name in ("SIGINT", "SIGTERM", "SIGHUP"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handle_signal)
        except (OSError, ValueError):  # noqa: PERF203
            pass

    _handlers_installed = True
