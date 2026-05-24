"""Disabled compatibility stub for the deferred script feature."""
from __future__ import annotations

import hashlib
from typing import Any

MAX_AUTO_EXECUTE_SCRIPTS = 20
MAX_AUTO_EXECUTE_SCRIPT_CHARS = 20000
AUTO_EXECUTE_DISABLED_MESSAGE = "Auto Execute is disabled in this build."


def is_invalid_script_entry(script: Any) -> bool:
    return True


def normalize_scripts(value: Any) -> list[str]:
    return []


def script_id(script: str) -> str:
    return hashlib.sha256(str(script or "").encode("utf-8")).hexdigest()[:16]


def build_execute_command(script: str) -> str:
    raise RuntimeError(AUTO_EXECUTE_DISABLED_MESSAGE)


def script_preview(script: str, limit: int = 72) -> str:
    return ""


def send_execute_command(package: str, script: str) -> dict[str, Any]:
    return {
        "success": False,
        "package": package,
        "script_id": script_id(script),
        "script_len": len(script or ""),
        "method": "disabled",
        "error": AUTO_EXECUTE_DISABLED_MESSAGE,
    }


def run_auto_execute_for_package(
    cfg: dict[str, Any],
    package: str,
    already_ran: set[tuple[str, str]],
    *,
    logger: Any | None = None,
) -> list[dict[str, Any]]:
    return []
