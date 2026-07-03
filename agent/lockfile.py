"""PID and lockfile safety for the auto supervisor."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import DATA_DIR, LOCK_PATH, PID_PATH, PRODUCT_NAME, RUN_DIR


class LockError(RuntimeError):
    """Raised when another DENG agent is already running."""


class LockPermissionError(LockError):
    """Raised when the instance lock cannot be created in a writable runtime dir."""


_LAST_LOCK_TRACE: dict[str, Any] = {}


def lock_acquire_trace() -> dict[str, Any]:
    """Return debug/probe details from the most recent lock acquire attempt."""
    return dict(_LAST_LOCK_TRACE)


def _record_lock_trace(**fields: Any) -> None:
    global _LAST_LOCK_TRACE
    _LAST_LOCK_TRACE = {**fields}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    proc_path = Path(f"/proc/{pid}")
    if proc_path.exists():
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError) as exc:
        if os.name == "nt" and isinstance(exc, OSError) and getattr(exc, "winerror", None) == 87:
            # Invalid parameter / non-existent PID — avoid tasklist hang on fake huge PIDs in tests.
            return False
        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
                shell=False,
            )
            return str(pid) in result.stdout
        return False


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def read_pid(pid_path: Path = PID_PATH) -> int | None:
    try:
        text = pid_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def is_deng_process(pid: int, lock_path: Path = LOCK_PATH) -> bool:
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        cmdline = proc_cmdline.read_text(encoding="utf-8", errors="ignore").replace("\x00", " ")
        if "deng_tool_rejoin.py" in cmdline and ("--start" in cmdline or " start" in cmdline):
            return True
    except OSError:
        pass
    metadata = _read_json(lock_path)
    return metadata.get("product") == PRODUCT_NAME and metadata.get("pid") == pid


def _candidate_runtime_dirs() -> list[Path]:
    seen: set[str] = set()
    candidates: list[Path] = []
    for raw in (RUN_DIR, DATA_DIR / "runtime"):
        path = Path(raw).expanduser()
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(path)
    return candidates


def _probe_dir_writable(directory: Path) -> tuple[bool, str | None, int | None]:
    """Return (writable, error_type, errno) after mkdir + exclusive probe write."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".write_probe_{os.getpid()}"
        fd = os.open(str(probe), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, b"ok")
        finally:
            os.close(fd)
        probe.unlink(missing_ok=True)
        return True, None, None
    except FileExistsError:
        try:
            probe = directory / f".write_probe_{os.getpid()}"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return True, None, None
        except OSError as exc:
            return False, type(exc).__name__, getattr(exc, "errno", None)
    except OSError as exc:
        return False, type(exc).__name__, getattr(exc, "errno", None)


def resolve_writable_instance_lock_paths() -> tuple[Path, Path]:
    """Pick a Termux-safe writable runtime directory for Start instance locks."""
    last_error: tuple[str | None, int | None, str] | None = None
    for run_dir in _candidate_runtime_dirs():
        ok, err_type, errno = _probe_dir_writable(run_dir)
        if ok:
            _record_lock_trace(
                runtime_dir=str(run_dir),
                pid_path=str(run_dir / "agent.pid"),
                lock_path=str(run_dir / "agent.lock"),
                runtime_dir_probe="ok",
            )
            return run_dir / "agent.pid", run_dir / "agent.lock"
        last_error = (err_type, errno, str(run_dir))
    err_type, errno, run_dir = last_error or ("OSError", 1, str(RUN_DIR))
    _record_lock_trace(
        runtime_dir=run_dir,
        runtime_dir_probe="failed",
        error_type=err_type,
        errno=errno,
        operation="probe_write",
    )
    raise LockPermissionError(
        f"no writable runtime directory for Start lock (last={run_dir}, errno={errno})"
    )


def _unlink_or_quarantine(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        try:
            stale = path.with_name(f"{path.name}.stale.{int(time.time())}")
            path.rename(stale)
        except OSError:
            pass


@dataclass
class LockManager:
    pid_path: Path = field(default_factory=lambda: PID_PATH)
    lock_path: Path = field(default_factory=lambda: LOCK_PATH)

    def acquire(self) -> None:
        started = time.time()
        _record_lock_trace(
            start_lock_create_started_at=started,
            pid_path=str(self.pid_path),
            lock_path=str(self.lock_path),
            operation="acquire_begin",
        )
        try:
            self.pid_path.parent.mkdir(parents=True, exist_ok=True)
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _record_lock_trace(
                start_lock_create_result="failed",
                start_lock_create_error_type=type(exc).__name__,
                start_lock_create_errno=getattr(exc, "errno", None),
                operation="mkdir",
            )
            raise LockPermissionError(
                f"cannot create runtime directory for Start lock: {exc}"
            ) from exc

        pid = read_pid(self.pid_path)
        if pid and is_process_alive(pid):
            if is_deng_process(pid, self.lock_path):
                _record_lock_trace(start_lock_create_result="already_running", operation="read_pid")
                raise LockError(f"DENG Tool: Rejoin is already running with PID {pid}")
            _record_lock_trace(start_lock_create_result="foreign_pid", operation="read_pid")
            raise LockError(f"PID file points to a live non-DENG process ({pid}); refusing to overwrite")

        self.cleanup()
        current_pid = os.getpid()
        payload = (
            json.dumps(
                {
                    "product": PRODUCT_NAME,
                    "pid": current_pid,
                    "created_at": _utc_now(),
                    "command": "agent-start",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        try:
            self.pid_path.write_text(f"{current_pid}\n", encoding="utf-8")
            self.lock_path.write_text(payload, encoding="utf-8")
        except OSError as exc:
            _record_lock_trace(
                start_lock_create_result="failed",
                start_lock_create_error_type=type(exc).__name__,
                start_lock_create_errno=getattr(exc, "errno", None),
                operation="write_text",
            )
            raise LockPermissionError(f"cannot write Start lock files: {exc}") from exc

        _record_lock_trace(
            start_lock_create_result="ok",
            start_lock_create_error_type=None,
            start_lock_create_errno=None,
            operation="write_text",
        )

    def cleanup(self) -> None:
        for path in (self.pid_path, self.lock_path):
            _unlink_or_quarantine(path)

    def release(self) -> None:
        pid = read_pid(self.pid_path)
        if pid in {None, os.getpid()}:
            self.cleanup()

    def is_running(self) -> bool:
        pid = read_pid(self.pid_path)
        return bool(pid and is_process_alive(pid) and is_deng_process(pid, self.lock_path))

    def __enter__(self) -> "LockManager":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def stop_running_agent(pid_path: Path = PID_PATH, lock_path: Path = LOCK_PATH, *, timeout: int = 10) -> tuple[bool, str]:
    pid = read_pid(pid_path)
    manager = LockManager(pid_path=pid_path, lock_path=lock_path)
    if not pid:
        manager.cleanup()
        return False, "agent is not running"
    if not is_process_alive(pid):
        manager.cleanup()
        return False, f"stale PID {pid} cleaned"
    if not is_deng_process(pid, lock_path):
        return False, f"PID {pid} is not confirmed as DENG Tool: Rejoin; refusing to stop it"

    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_process_alive(pid):
            manager.cleanup()
            return True, f"stopped agent PID {pid}"
        time.sleep(0.25)

    # SIGTERM ignored. A confirmed-DENG process that won't exit is almost always
    # a STALE OLD-BUILD watchdog left over from a previous install (probe
    # p-70897e1166: PID 22344 kept running pre-fix code — still emitting the
    # removed RAM_TRIM cache clear — across a reinstall).  Escalate to SIGKILL so
    # old crashy code can never linger and run cache-clear bursts after Start.
    sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
    try:
        os.kill(pid, sigkill)
    except OSError:
        pass
    kill_deadline = time.time() + max(3, timeout // 2)
    while time.time() < kill_deadline:
        if not is_process_alive(pid):
            manager.cleanup()
            return True, f"force-killed stale agent PID {pid}"
        time.sleep(0.25)
    return False, f"sent stop signal to PID {pid}, but it still appears alive"
