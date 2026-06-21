"""Unified root access helper for DENG Tool: Rejoin.

Used exclusively during package setup / configuration flows.
NEVER imported or called from the live supervisor Start loop.

Root detection tries these candidates in order:
  tsu, su, /system/xbin/su, /system/bin/su, [magisk su path]

Every command has a timeout.
Every error is caught.
No raw traceback in public menu.
No hanging if su shows a permission prompt.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Iterable

_log = logging.getLogger("deng.rejoin.root_access")

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

DETECT_TIMEOUT: int = 5    # seconds for root detection test
COMMAND_TIMEOUT: int = 10  # default timeout for root commands
READ_TIMEOUT: int = 8      # timeout for file-read commands
LIST_TIMEOUT: int = 8      # timeout for glob-list commands

ROOT_REQUIRED_PUBLIC_MESSAGE = (
    "unsupported: root is required for DENG Rejoin on cloudphone"
)

_MAX_DETECT_CACHE_AGE: float = 120.0  # 2 minutes

# Root tool candidates in priority order.
# Absolute paths are tried so the su subshell (which may lack Termux PATH)
# can still find system tools.
_ROOT_CANDIDATES: tuple[str, ...] = (
    "tsu",
    "su",
    "/system/xbin/su",
    "/system/bin/su",
)

# Magisk-managed su paths (checked only if standard candidates fail)
_MAGISK_SU_PATHS: tuple[str, ...] = (
    "/sbin/su",
    "/data/adb/magisk/busybox",
)


# --------------------------------------------------------------------------- #
# Public status labels
# --------------------------------------------------------------------------- #

class RootStatus:
    AVAILABLE   = "available"
    DENIED      = "denied"
    NOT_FOUND   = "not_found"
    TIMED_OUT   = "timed_out"
    ERROR       = "error"


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RootResult:
    """Result of a single root command execution."""
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.error

    @property
    def summary(self) -> str:
        text = (self.stderr or self.stdout or self.error or "").strip()
        return text[:300]


@dataclass(frozen=True)
class RootCapability:
    """Cached root detection result."""
    status: str          # one of RootStatus.*
    tool: str | None     # the working su/tsu command, or None
    detail: str          # human-readable description
    checked_at: float    # monotonic timestamp

    @property
    def available(self) -> bool:
        return self.status == RootStatus.AVAILABLE


@dataclass(frozen=True)
class RootCheckStep:
    command: str
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False


@dataclass(frozen=True)
class RootCheckReport:
    """Result of :func:`root_check` / :func:`root_required_preflight`."""
    ok: bool
    tool: str | None
    uid: str
    whoami: str
    data_dir_readable: bool
    steps: tuple[RootCheckStep, ...]
    detail: str
    error: str = ""

    def public_error(self) -> str:
        if self.ok:
            return ""
        base = ROOT_REQUIRED_PUBLIC_MESSAGE
        detail = (self.error or self.detail or "").strip()
        if detail:
            return f"{base} ({detail})"
        return base


# --------------------------------------------------------------------------- #
# Internal detection cache
# --------------------------------------------------------------------------- #

_cache_lock = threading.Lock()
_cached: RootCapability | None = None


def _run_raw(args: list[str], timeout: int) -> tuple[int, str, str, bool]:
    """Run a subprocess. Returns (returncode, stdout, stderr, timed_out). Never raises."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or "", False
    except subprocess.TimeoutExpired:
        return -1, "", "timed out", True
    except (FileNotFoundError, PermissionError, OSError):
        return 127, "", "not found", False
    except Exception as exc:  # noqa: BLE001
        return -1, "", str(exc)[:200], False


def _probe_tool(tool: str, timeout: int) -> tuple[bool, str, str]:
    """Probe one root tool. Returns (uid0_ok, status_label, detail)."""
    rc, out, err, timed_out = _run_raw([tool, "-c", "id"], timeout=timeout)
    combined = f"{out} {err}".strip()
    if timed_out:
        return False, RootStatus.TIMED_OUT, f"{tool}: timed out (no permission prompt accepted)"
    if rc == 127:
        return False, RootStatus.NOT_FOUND, f"{tool}: not found"
    if "uid=0" in combined:
        return True, RootStatus.AVAILABLE, combined[:200]
    # Returned but not uid=0 — permission denied or restricted
    return False, RootStatus.DENIED, combined[:200]


def _detect_now(timeout: int = DETECT_TIMEOUT) -> RootCapability:
    """Full root detection. Tries all candidates; returns the first available."""
    now = time.monotonic()
    all_candidates = list(_ROOT_CANDIDATES)

    # Try magisk paths if they look executable
    import os
    for mp in _MAGISK_SU_PATHS:
        if os.path.isfile(mp):
            all_candidates.append(mp)

    last_status = RootStatus.NOT_FOUND
    last_detail = "no root tool found"

    for tool in all_candidates:
        ok, status, detail = _probe_tool(tool, timeout)
        if ok:
            return RootCapability(
                status=RootStatus.AVAILABLE,
                tool=tool,
                detail=f"root via {tool}: {detail[:100]}",
                checked_at=now,
            )
        if status == RootStatus.TIMED_OUT:
            # A timeout means su is installed but waiting for permission — stop trying
            return RootCapability(
                status=RootStatus.TIMED_OUT,
                tool=tool,
                detail=detail,
                checked_at=now,
            )
        if status == RootStatus.DENIED:
            last_status = RootStatus.DENIED
            last_detail = detail
            continue
        # NOT_FOUND — try next

    return RootCapability(
        status=last_status,
        tool=None,
        detail=last_detail,
        checked_at=now,
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def detect(*, force: bool = False, timeout: int = DETECT_TIMEOUT) -> RootCapability:
    """Detect root capability. Result is cached for up to 2 minutes.

    Safe to call from any thread. Never raises.
    """
    global _cached
    now = time.monotonic()
    with _cache_lock:
        if not force and _cached is not None:
            if (now - _cached.checked_at) < _MAX_DETECT_CACHE_AGE:
                return _cached
    try:
        cap = _detect_now(timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        _log.debug("root detect error: %s", exc)
        cap = RootCapability(
            status=RootStatus.ERROR,
            tool=None,
            detail=str(exc)[:200],
            checked_at=now,
        )
    with _cache_lock:
        _cached = cap
    return cap


def has_root(*, force: bool = False) -> bool:
    """Return True if a working root tool is available. Never raises."""
    return detect(force=force).available


def run_root(
    cmd: str | Iterable[str],
    *,
    timeout: int = COMMAND_TIMEOUT,
    detect_timeout: int | None = None,
) -> RootResult:
    """Run one shell command via ``su -c``. Accepts a string or argv list."""
    if isinstance(cmd, str):
        return run_root_command(["sh", "-c", cmd], timeout=timeout, detect_timeout=detect_timeout)
    return run_root_command(cmd, timeout=timeout, detect_timeout=detect_timeout)


def root_check(*, timeout: int = DETECT_TIMEOUT) -> RootCheckReport:
    """Verify root with ``id``, ``whoami``, and ``/data/data`` access."""
    cap = detect(force=True, timeout=timeout)
    if not cap.available or not cap.tool:
        return RootCheckReport(
            ok=False,
            tool=cap.tool,
            uid="",
            whoami="",
            data_dir_readable=False,
            steps=(),
            detail=cap.detail,
            error=cap.detail,
        )
    steps: list[RootCheckStep] = []
    uid_text = ""
    whoami_text = ""
    data_ok = False
    for shell_cmd in ("id", "whoami", "ls /data/data >/dev/null 2>&1 && echo ok"):
        started = time.monotonic()
        result = run_root_command(["sh", "-c", shell_cmd], timeout=timeout, detect_timeout=timeout)
        step = RootCheckStep(
            command=shell_cmd,
            returncode=result.returncode,
            stdout=(result.stdout or "")[:200],
            stderr=(result.stderr or result.error or "")[:200],
            duration_ms=int((time.monotonic() - started) * 1000),
            timed_out=result.timed_out,
        )
        steps.append(step)
        if shell_cmd == "id" and result.ok:
            uid_text = (result.stdout or result.stderr or "").strip()[:120]
        elif shell_cmd == "whoami" and result.ok:
            whoami_text = (result.stdout or "").strip()[:40]
        elif "data/data" in shell_cmd:
            data_ok = result.ok and "ok" in (result.stdout or "")

    ok = bool(uid_text and "uid=0" in uid_text and data_ok)
    detail = f"tool={cap.tool} uid={uid_text[:60]} whoami={whoami_text or '?'} data_dir={'ok' if data_ok else 'denied'}"
    error = "" if ok else (cap.detail if not uid_text else f"data_dir={'ok' if data_ok else 'unreadable'}")
    return RootCheckReport(
        ok=ok,
        tool=cap.tool,
        uid=uid_text,
        whoami=whoami_text,
        data_dir_readable=data_ok,
        steps=tuple(steps),
        detail=detail,
        error=error,
    )


def root_required_preflight(*, timeout: int = DETECT_TIMEOUT) -> RootCheckReport:
    """Fail fast when root is unavailable. Never raises."""
    report = root_check(timeout=timeout)
    if report.ok:
        return report
    err = (report.error or report.detail or "root unavailable").strip()
    return RootCheckReport(
        ok=False,
        tool=report.tool,
        uid=report.uid,
        whoami=report.whoami,
        data_dir_readable=report.data_dir_readable,
        steps=report.steps,
        detail=report.detail,
        error=err,
    )


def run_root_command(
    args: Iterable[str],
    *,
    timeout: int = COMMAND_TIMEOUT,
    detect_timeout: int | None = None,
) -> RootResult:
    """Run a command via the best available root tool.

    Returns a :class:`RootResult`.  ``ok`` is False if root is unavailable,
    the command times out, or any error occurs.  Never raises.
    """
    cap = detect(timeout=detect_timeout or DETECT_TIMEOUT)
    if not cap.available or not cap.tool:
        return RootResult(
            returncode=127,
            stdout="",
            stderr="",
            error=f"root unavailable: {cap.detail}",
        )
    try:
        tokens = list(str(a) for a in args)
        command = shlex.join(tokens)
        rc, out, err, timed_out = _run_raw([cap.tool, "-c", command], timeout=timeout)
        return RootResult(
            returncode=rc,
            stdout=(out or "").rstrip("\n"),
            stderr=(err or "").rstrip("\n"),
            timed_out=timed_out,
        )
    except Exception as exc:  # noqa: BLE001
        return RootResult(
            returncode=-1, stdout="", stderr="", error=str(exc)[:200],
        )


def read_root_file(
    path: str,
    *,
    max_bytes: int = 131_072,
    timeout: int = READ_TIMEOUT,
    detect_timeout: int | None = None,
) -> str | None:
    """Read a root-protected file.  Returns content (capped) or None.

    Uses ``head -c <max_bytes>`` to avoid reading large binaries.
    Never prints or logs raw content.  Never raises.
    """
    if not path or "/" not in path:
        return None
    try:
        safe_path = path.replace("'", "'\"'\"'")
        inner = f"test -f '{safe_path}' && head -c {int(max_bytes)} '{safe_path}' 2>/dev/null"
        result = run_root_command(["sh", "-c", inner], timeout=timeout, detect_timeout=detect_timeout)
        if not result.ok or not result.stdout:
            return None
        return result.stdout
    except Exception:  # noqa: BLE001
        return None


def list_root_glob(
    pattern: str,
    *,
    timeout: int = LIST_TIMEOUT,
    max_results: int = 64,
) -> list[str]:
    """List files matching a shell glob pattern via root.

    Returns a list of absolute paths.  Never raises.  Returns [] on any error.
    """
    if not pattern:
        return []
    try:
        safe_pat = pattern.replace("'", "'\"'\"'")
        inner = f"ls {safe_pat} 2>/dev/null | head -{int(max_results)}"
        result = run_root_command(["sh", "-c", inner], timeout=timeout)
        if not result.ok or not result.stdout:
            return []
        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        return lines[:max_results]
    except Exception:  # noqa: BLE001
        return []


def root_status_summary() -> str:
    """Return a short, public-safe status string about root availability.

    Suitable for printing in setup menus. Never exposes internal paths or tokens.
    """
    cap = detect()
    if cap.status == RootStatus.AVAILABLE:
        return "Root available"
    if cap.status == RootStatus.TIMED_OUT:
        return "Root timed out (permission prompt not accepted)"
    if cap.status == RootStatus.DENIED:
        return "Root denied (su permission was rejected)"
    if cap.status == RootStatus.NOT_FOUND:
        return "Root command not found (tsu/su not installed)"
    return "Root not available"


def clear_cache() -> None:
    """For tests: invalidate the cached detection result."""
    global _cached
    with _cache_lock:
        _cached = None
