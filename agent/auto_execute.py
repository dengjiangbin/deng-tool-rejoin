"""Auto Execute support for saved Roblox scripts.

The user-facing flow stores script text locally, then the watchdog sends
``/execute <script>`` only after a package is confirmed Online/in-game.
"""
from __future__ import annotations

import hashlib
import re
import shlex
import time
from typing import Any

from . import android
from .logger import configure_logging, log_event

MAX_AUTO_EXECUTE_SCRIPTS = 20
MAX_AUTO_EXECUTE_SCRIPT_CHARS = 20000


def normalize_scripts(value: Any) -> list[str]:
    """Return sanitized saved scripts, preserving order and removing blanks."""
    if not isinstance(value, list):
        return []
    scripts: list[str] = []
    seen: set[str] = set()
    for item in value:
        script = str(item or "").strip()
        if not script:
            continue
        script = script.replace("\r\n", "\n").replace("\r", "\n")
        if len(script) > MAX_AUTO_EXECUTE_SCRIPT_CHARS:
            script = script[:MAX_AUTO_EXECUTE_SCRIPT_CHARS]
        if script in seen:
            continue
        seen.add(script)
        scripts.append(script)
        if len(scripts) >= MAX_AUTO_EXECUTE_SCRIPTS:
            break
    return scripts


def script_id(script: str) -> str:
    return hashlib.sha256(str(script or "").encode("utf-8")).hexdigest()[:16]


def build_execute_command(script: str) -> str:
    return "/execute " + str(script or "").strip()


def script_preview(script: str, limit: int = 72) -> str:
    text = " ".join(str(script or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _android_input_text_arg(text: str) -> str:
    """Escape text for Android ``input text``.

    This is a fallback path.  The primary path uses clipboard paste because
    scripts usually contain quotes, parentheses, spaces, and newlines.
    """
    text = str(text or "").replace("\n", " ")
    text = text.replace("%", "%s")
    text = text.replace(" ", "%s")
    text = text.replace("'", "\\'")
    text = text.replace('"', '\\"')
    return text


def _set_clipboard_with_cmd(text: str) -> android.CommandResult:
    quoted = shlex.quote(str(text or ""))
    return android.run_android_command(
        ["sh", "-c", f"cmd clipboard set {quoted}"],
        timeout=8,
        prefer_root=True,
    )


def _set_clipboard_with_service(text: str) -> android.CommandResult:
    escaped = str(text or "").replace("\\", "\\\\").replace('"', '\\"')
    # Android 10+ service call shape for clipboard text.  Some OEM builds
    # reject it, so this stays a fallback behind ``cmd clipboard``.
    return android.run_android_command(
        [
            "sh",
            "-c",
            f'service call clipboard 2 i32 0 s16 "com.android.shell" s16 "{escaped}"',
        ],
        timeout=8,
        prefer_root=True,
    )


def _focus_package(package: str) -> android.CommandResult:
    """Bring the package to foreground without force-stopping it."""
    return android.launch_app(package)


def send_execute_command(package: str, script: str) -> dict[str, Any]:
    """Send ``/execute <script>`` to the foreground Roblox chat.

    Returns a small result dict and never raises.
    """
    result: dict[str, Any] = {
        "success": False,
        "package": package,
        "script_id": script_id(script),
        "script_len": len(script or ""),
        "method": "",
        "error": "",
    }
    command = build_execute_command(script)
    try:
        focus = _focus_package(package)
        if not focus.ok:
            result["error"] = f"focus failed: {focus.summary}"
            return result
        time.sleep(0.8)

        # Open Roblox chat, then paste the command.
        android.run_android_command(["input", "keyevent", "KEYCODE_SLASH"], timeout=5)
        time.sleep(0.2)

        clip = _set_clipboard_with_cmd(command)
        if not clip.ok:
            clip = _set_clipboard_with_service(command)
        if clip.ok:
            paste = android.run_android_command(["input", "keyevent", "279"], timeout=5)
            if paste.ok:
                result["method"] = "clipboard_paste"
            else:
                result["error"] = f"paste failed: {paste.summary}"
                return result
        else:
            # Fallback for very small/simple commands only.
            if re.search(r"[^A-Za-z0-9_./:=?&%+\\- ]", command):
                result["error"] = f"clipboard failed: {clip.summary}"
                return result
            typed = android.run_android_command(
                ["input", "text", _android_input_text_arg(command)],
                timeout=10,
            )
            if not typed.ok:
                result["error"] = f"type failed: {typed.summary}"
                return result
            result["method"] = "input_text"

        enter = android.run_android_command(["input", "keyevent", "KEYCODE_ENTER"], timeout=5)
        if not enter.ok:
            result["error"] = f"enter failed: {enter.summary}"
            return result
        result["success"] = True
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)[:200]
        return result


def run_auto_execute_for_package(
    cfg: dict[str, Any],
    package: str,
    already_ran: set[tuple[str, str]],
    *,
    logger: Any | None = None,
) -> list[dict[str, Any]]:
    scripts = normalize_scripts(cfg.get("auto_execute_scripts"))
    if not scripts:
        return []
    logger = logger or configure_logging(level=cfg.get("log_level", "INFO"))
    results: list[dict[str, Any]] = []
    for script in scripts:
        sid = script_id(script)
        key = (package, sid)
        if key in already_ran:
            continue
        already_ran.add(key)
        log_event(
            logger,
            "info",
            "[DENG_REJOIN_AUTO_EXECUTE]",
            package=package,
            script_id=sid,
            script_len=len(script),
            action="send",
        )
        res = send_execute_command(package, script)
        results.append(res)
        log_event(
            logger,
            "info" if res.get("success") else "warning",
            "[DENG_REJOIN_AUTO_EXECUTE_RESULT]",
            package=package,
            script_id=sid,
            script_len=len(script),
            method=res.get("method", ""),
            success=str(bool(res.get("success"))).lower(),
            error=res.get("error", ""),
        )
    return results
