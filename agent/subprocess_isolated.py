"""TTY-safe subprocess execution for Termux/Android.

Every external command is spawned with ``stdin=DEVNULL`` and ``close_fds=True``
so child processes cannot claim the interactive terminal.  Pipe reads are
bounded and drained via ``communicate(timeout=...)`` to avoid saturation
deadlocks when stdout/stderr buffers fill.
"""

from __future__ import annotations

import os
import subprocess
import threading
from typing import Iterable

_DEFAULT_MAX_STDOUT = 512 * 1024
_DEFAULT_MAX_STDERR = 128 * 1024


def _popen_kwargs(*, env: dict[str, str] | None = None) -> dict:
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
    }
    if env is not None:
        kwargs["env"] = env
    if os.name != "nt":
        kwargs["close_fds"] = True
    return kwargs


def _communicate_bounded(
    proc: subprocess.Popen[bytes],
    *,
    timeout: float,
    max_stdout: int,
    max_stderr: int,
) -> tuple[int, bytes, bytes, bool]:
    try:
        stdout_b, stderr_b = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.communicate(timeout=2)
        except Exception:  # noqa: BLE001
            pass
        return -1, b"", b"timed out", True

    rc = proc.returncode if proc.returncode is not None else -1
    return (
        rc,
        (stdout_b or b"")[:max_stdout],
        (stderr_b or b"")[:max_stderr],
        False,
    )


def run_isolated_text(
    args: Iterable[str],
    *,
    timeout: float,
    env: dict[str, str] | None = None,
    lock: threading.Lock | None = None,
    max_stdout: int = _DEFAULT_MAX_STDOUT,
    max_stderr: int = _DEFAULT_MAX_STDERR,
) -> tuple[int, str, str, bool]:
    """Run ``args`` with TTY isolation.  Returns (rc, stdout, stderr, timed_out)."""
    cmd = [str(a) for a in args]

    def _execute() -> tuple[int, str, str, bool]:
        try:
            proc = subprocess.Popen(cmd, **_popen_kwargs(env=env))
            rc, out_b, err_b, timed_out = _communicate_bounded(
                proc,
                timeout=timeout,
                max_stdout=max_stdout,
                max_stderr=max_stderr,
            )
            out = out_b.decode("utf-8", errors="replace")
            err = err_b.decode("utf-8", errors="replace")
            return rc, out, err, timed_out
        except FileNotFoundError:
            return 127, "", "not found", False
        except PermissionError as exc:
            return 127, "", str(exc)[:200], False
        except OSError as exc:
            return -1, "", str(exc)[:200], False
        except Exception as exc:  # noqa: BLE001
            return -1, "", str(exc)[:200], False

    if lock is not None:
        with lock:
            return _execute()
    return _execute()


def run_isolated_bytes(
    args: Iterable[str],
    *,
    timeout: float,
    lock: threading.Lock | None = None,
    max_stdout: int = _DEFAULT_MAX_STDOUT + 64,
    max_stderr: int = _DEFAULT_MAX_STDERR,
) -> tuple[int, bytes, bytes, bool]:
    """Run ``args`` with TTY isolation.  Returns (rc, stdout, stderr, timed_out)."""
    cmd = [str(a) for a in args]

    def _execute() -> tuple[int, bytes, bytes, bool]:
        try:
            proc = subprocess.Popen(cmd, **_popen_kwargs())
            return _communicate_bounded(
                proc,
                timeout=timeout,
                max_stdout=max_stdout,
                max_stderr=max_stderr,
            )
        except FileNotFoundError:
            return 127, b"", b"not found", False
        except (PermissionError, OSError) as exc:
            return -1, b"", str(exc)[:200].encode("utf-8", errors="replace"), False
        except Exception as exc:  # noqa: BLE001
            return -1, b"", str(exc)[:200].encode("utf-8", errors="replace"), False

    if lock is not None:
        with lock:
            return _execute()
    return _execute()


def spawn_detached(args: Iterable[str], *, env: dict[str, str] | None = None) -> bool:
    """Start ``args`` and return immediately without waiting (phantom-process guard)."""
    cmd = [str(a) for a in args]
    try:
        kwargs: dict = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "shell": False,
        }
        if env is not None:
            kwargs["env"] = env
        if os.name != "nt":
            kwargs["close_fds"] = True
            kwargs["start_new_session"] = True
        subprocess.Popen(cmd, **kwargs)
        return True
    except OSError:
        return False
