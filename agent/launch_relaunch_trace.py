"""Launch/relaunch audit trail for probe (does not control launch behavior)."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from .constants import DATA_DIR
from .url_utils import mask_launch_url

_TRACE_PATH = DATA_DIR / "launch-relaunch-trace.json"


def _load() -> dict[str, Any]:
    try:
        if _TRACE_PATH.is_file():
            parsed = json.loads(_TRACE_PATH.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                return parsed
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return {}


def _save(data: dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _TRACE_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def record_start_pressed() -> None:
    data = _load()
    data["last_start_pressed_at"] = time.time()
    _save(data)


def record_launch_attempt(
    package: str,
    *,
    action: str,
    success: bool,
    launcher: str,
    url_present: bool,
    url_sanitized: str = "",
    command_type: str = "",
    error: str = "",
    state_after: str = "",
) -> None:
    pkg = str(package or "").strip()
    if not pkg:
        return
    data = _load()
    now = time.time()
    row = {
        "package": pkg,
        "action": str(action or "")[:80],
        "success": bool(success),
        "launcher": str(launcher or "")[:40],
        "private_server_url_present": bool(url_present),
        "launch_url_used_sanitized": str(url_sanitized or "")[:200],
        "last_launch_command_type": str(command_type or "")[:80],
        "last_launch_error": str(error or "")[:180],
        "last_launch_state": str(state_after or "")[:40],
        "at": now,
    }
    data["last_launch"] = row
    if "relaunch" in action.lower():
        data["last_relaunch_at"] = now
        data["last_relaunch"] = row
    else:
        data["last_launch_at"] = now
    data["termux_still_alive"] = True
    data["main_process_pid"] = os.getpid()
    _save(data)


def probe_snapshot() -> dict[str, Any]:
    data = _load()
    return {
        "termux_still_alive": bool(data.get("termux_still_alive", True)),
        "main_process_pid": data.get("main_process_pid") or os.getpid(),
        "last_start_pressed_at": data.get("last_start_pressed_at"),
        "last_launch_at": data.get("last_launch_at"),
        "last_relaunch_at": data.get("last_relaunch_at"),
        "last_launch_state": (data.get("last_launch") or {}).get("last_launch_state"),
        "last_launch_action": (data.get("last_launch") or {}).get("action"),
        "last_launch_error": (data.get("last_launch") or {}).get("last_launch_error"),
        "configured_private_server_url_present": (data.get("last_launch") or {}).get(
            "private_server_url_present"
        ),
        "launch_url_used_sanitized": (data.get("last_launch") or {}).get(
            "launch_url_used_sanitized"
        ),
        "launch_method": (data.get("last_launch") or {}).get("launcher"),
        "last_launch_command_type": (data.get("last_launch") or {}).get(
            "last_launch_command_type"
        ),
        "opened_package": (data.get("last_launch") or {}).get("package"),
        "last_relaunch": data.get("last_relaunch"),
    }


def sanitized_url_from_context(url_context: dict[str, Any]) -> tuple[bool, str]:
    url = str(url_context.get("url") or url_context.get("effective_url") or "").strip()
    present = bool(url) and url_context.get("url_mode") == "private_url"
    masked = mask_launch_url(url) if url else ""
    return present, masked or ""
