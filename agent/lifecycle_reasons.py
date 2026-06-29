"""User-facing lifecycle reason text (webhook/status)."""

from __future__ import annotations

_USER_FRIENDLY: dict[str, str] = {
    "launch_watchdog_timeout": "Roblox did not finish joining the server in time",
    "with_reason": "Roblox disconnected or kicked the account",
    "logcat_with_reason": "Roblox disconnected or kicked the account",
    "idle_disconnect_278": "Roblox disconnected for being idle (Error 278)",
    "ui_disconnect": "Roblox disconnected or kicked the account",
    "logcat_disconnect": "Roblox disconnected or kicked the account",
    "heartbeat_lost": "Roblox left the live server (in-game detection stopped)",
    "process_missing": "Roblox was closed or force-stopped",
    "force_close": "Roblox was closed or force-stopped",
    "package_force_stopped": "Roblox was force-stopped",
    "game_crash": "Roblox crashed",
    "wrong_server": "Account is not in configured server",
    "private_server_launch_failed": "Could not open the private server link",
    "no_gamejoinloadtime": "Roblox opened but did not finish joining the server",
    "no_online_confirmation": "Roblox opened but could not be confirmed inside the game",
    "unknown": "Roblox stopped responding or could not be confirmed online",
}


def normalize_internal_reason(reason: str) -> str:
    text = str(reason or "").strip().lower()
    text = text.replace(" ", "_").replace("-", "_")
    if text.startswith("logcat_"):
        text = text[7:]
    return text or "unknown"


def format_user_friendly_dead_reason(reason: str) -> str:
    text = str(reason or "").strip()
    if not text:
        return _USER_FRIENDLY["unknown"]
    if text in _USER_FRIENDLY.values():
        return text
    key = normalize_internal_reason(text)
    if key in _USER_FRIENDLY:
        return _USER_FRIENDLY[key]
    if "wrong_server" in key or "wrong_game" in key or "target_mismatch" in key:
        return _USER_FRIENDLY["wrong_server"]
    if "with_reason" in key or "disconnect" in key:
        return _USER_FRIENDLY["logcat_with_reason"]
    if "process_missing" in key or "force" in key and "stop" in key:
        return _USER_FRIENDLY["process_missing"]
    if "gamejoin" in key or "join" in key and "timeout" in key:
        return _USER_FRIENDLY["launch_watchdog_timeout"]
    if "private" in key and "url" in key:
        return _USER_FRIENDLY["private_server_launch_failed"]
    return _USER_FRIENDLY["unknown"]
