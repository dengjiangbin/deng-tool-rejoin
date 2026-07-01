"""Keep the Termux session alive while the rejoin agent runs."""

from __future__ import annotations

import subprocess
import time
from typing import Any

_WAKE_LOCK_ACQUIRED_AT = 0.0
_WAKE_LOCK_LAST_RENEW_AT = 0.0
_WAKE_LOCK_RENEW_INTERVAL_SECONDS = 1800.0


def _run_termux_cmd(argv: list[str], *, timeout: float = 12.0) -> dict[str, Any]:
    try:
        res = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": res.returncode == 0,
            "returncode": res.returncode,
            "stdout": (res.stdout or "").strip()[:240],
            "stderr": (res.stderr or "").strip()[:240],
        }
    except FileNotFoundError:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": "command_not_found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": "timeout"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": str(exc)[:240]}


def acquire_termux_wake_lock(*, force: bool = False) -> dict[str, Any]:
    """Acquire ``termux-wake-lock`` so Android is less likely to kill Termux."""
    global _WAKE_LOCK_ACQUIRED_AT, _WAKE_LOCK_LAST_RENEW_AT
    now = time.time()
    if (
        not force
        and _WAKE_LOCK_ACQUIRED_AT > 0
        and (now - _WAKE_LOCK_LAST_RENEW_AT) < _WAKE_LOCK_RENEW_INTERVAL_SECONDS
    ):
        return {
            "ok": True,
            "skipped": True,
            "reason": "recently_renewed",
            "acquired_at": _WAKE_LOCK_ACQUIRED_AT,
        }
    result = _run_termux_cmd(["termux-wake-lock"])
    if result.get("ok"):
        if _WAKE_LOCK_ACQUIRED_AT <= 0:
            _WAKE_LOCK_ACQUIRED_AT = now
        _WAKE_LOCK_LAST_RENEW_AT = now
    return {
        **result,
        "acquired_at": _WAKE_LOCK_ACQUIRED_AT,
        "renewed_at": _WAKE_LOCK_LAST_RENEW_AT,
    }


def release_termux_wake_lock() -> dict[str, Any]:
    """Release ``termux-wake-unlock`` when the agent stops."""
    global _WAKE_LOCK_ACQUIRED_AT, _WAKE_LOCK_LAST_RENEW_AT
    result = _run_termux_cmd(["termux-wake-unlock"])
    _WAKE_LOCK_ACQUIRED_AT = 0.0
    _WAKE_LOCK_LAST_RENEW_AT = 0.0
    return result


def ensure_termux_session_alive(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Honor ``optimization.keep_screen_awake`` and renew the wake lock periodically."""
    keep_awake = True
    if isinstance(cfg, dict):
        optimization = cfg.get("optimization")
        if isinstance(optimization, dict):
            keep_awake = bool(optimization.get("keep_screen_awake", True))
    if not keep_awake:
        return {"ok": True, "skipped": True, "reason": "keep_screen_awake_disabled"}
    return acquire_termux_wake_lock()
