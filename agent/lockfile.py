"""PID and lockfile safety for the auto supervisor."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .constants import LOCK_PATH, PID_PATH, PRODUCT_NAME


class LockError(RuntimeError):
    """Raised when another DENG agent is already running."""


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
    except (OSError, ValueError):
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


@dataclass
class LockManager:
    pid_path: Path = PID_PATH
    lock_path: Path = LOCK_PATH

    def acquire(self) -> None:
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        pid = read_pid(self.pid_path)
        if pid and is_process_alive(pid):
            if is_deng_process(pid, self.lock_path):
                raise LockError(f"DENG Tool: Rejoin is already running with PID {pid}")
            raise LockError(f"PID file points to a live non-DENG process ({pid}); refusing to overwrite")
        self.cleanup()
        current_pid = os.getpid()
        self.pid_path.write_text(f"{current_pid}\n", encoding="utf-8")
        self.lock_path.write_text(
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
            + "\n",
            encoding="utf-8",
        )

    def cleanup(self) -> None:
        for path in (self.pid_path, self.lock_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

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
    return False, f"sent stop signal to PID {pid}, but it still appears alive"
