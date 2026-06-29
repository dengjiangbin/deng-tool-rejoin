"""Roblox disconnect / kick prompt parsing for lifecycle webhook Reason text."""

from __future__ import annotations

import re

# Known Roblox client error codes → default prompt when UI text is truncated.
ROBLOX_ERROR_CODE_PROMPTS: dict[int, str] = {
    260: "Connection lost",
    261: "You were kicked from this experience",
    262: "You were kicked from this server",
    263: "You were kicked by this experience",
    264: "Same account launched experience",
    265: "You were disconnected",
    266: "Failed to connect to the experience",
    267: "You were kicked",
    268: "Lost connection to the server",
    269: "Unable to connect to the server",
    270: "Disconnected from server",
    271: "Server is full",
    272: "Experience is restricted",
    273: "Moderated experience",
    274: "Teleport failed",
    275: "Rejoin failed",
    276: "Connection timed out",
    277: "Reconnect to server",
    278: "You were disconnected for being idle",
    279: "You were disconnected from the server",
    280: "Lost connection",
    517: "Server shutting down",
    522: "Roblox is experiencing connection issues",
    524: "A timeout was reached",
    529: "A Http error has occurred",
}

# Codes that should render as a clean human phrase WITHOUT the "Error Code: N"
# prefix or the raw FLog junk that follows them.  285 is the disconnect Roblox
# sends when a client leaves the actual map and lingers in the lobby/menu place;
# the user wants that reported plainly (probe p-630c95f7cc #2).
CLEAN_DISCONNECT_REASONS: dict[int, str] = {
    285: "Account stays too long in the lobby",
}

_ERROR_CODE_RE = re.compile(r"Error\s*Code\s*:?\s*(\d+)", re.I)
# Roblox FLog::Network line: "Sending disconnect with reason: <code>". The reason
# code matches the user-facing error code for the 26x/27x/28x disconnect range.
_WITH_REASON_CODE_RE = re.compile(r"with\s+reason:?\s*(\d+)", re.I)
_IDLE_HINT_RE = re.compile(r"\b(idle|being idle)\b", re.I)


def parse_roblox_error_code(text: str) -> int | None:
    raw = str(text or "")
    match = _ERROR_CODE_RE.search(raw)
    if not match:
        match = _WITH_REASON_CODE_RE.search(raw)
    if not match:
        return None
    try:
        code = int(match.group(1))
    except (TypeError, ValueError):
        return None
    # Only treat values in the known Roblox disconnect range as error codes so a
    # stray "with reason: 0/1" handshake line does not masquerade as Error Code.
    if 200 <= code <= 599:
        return code
    return None


def _prompt_from_matched_text(text: str, code: int | None) -> str:
    raw = re.sub(r"\s+", " ", str(text or "").strip())
    if not raw:
        if code is not None:
            return ROBLOX_ERROR_CODE_PROMPTS.get(code, "")
        return ""
    # The raw FLog::Network line ("... Sending disconnect with reason: 278") is not
    # human-friendly; prefer the canonical prompt when the text isn't a real
    # "Error Code:" UI string.
    if code is not None and not _ERROR_CODE_RE.search(raw) and _WITH_REASON_CODE_RE.search(raw):
        canonical = ROBLOX_ERROR_CODE_PROMPTS.get(code, "")
        if canonical:
            return canonical
    # Prefer the sentence containing the error code when present.
    if code is not None:
        for part in re.split(r"[.\n\r|]+", raw):
            chunk = part.strip()
            if not chunk:
                continue
            if str(code) in chunk or _ERROR_CODE_RE.search(chunk):
                cleaned = _ERROR_CODE_RE.sub("", chunk).strip(" :-()")
                if cleaned:
                    return cleaned[:180]
    cleaned = _ERROR_CODE_RE.sub("", raw).strip(" :-()")
    if cleaned:
        return cleaned[:180]
    # Stripping the code left nothing (matched text was just "Error Code: N");
    # prefer the canonical prompt so the reason is not the doubled
    # "Error Code: N Error Code: N".
    if code is not None:
        return ROBLOX_ERROR_CODE_PROMPTS.get(code, "")
    return raw[:180]


def format_error_code_reason(
    matched_text: str | None,
    *,
    internal_key: str = "",
) -> str | None:
    """Return ``Error Code: <n> <prompt>`` when evidence exists."""
    text = str(matched_text or "").strip()
    code = parse_roblox_error_code(text)
    if code is None and internal_key == "idle_disconnect_278":
        code = 278
    if code is None and _IDLE_HINT_RE.search(text):
        code = 278
    if code is None:
        return None
    if code in CLEAN_DISCONNECT_REASONS:
        return CLEAN_DISCONNECT_REASONS[code]
    prompt = _prompt_from_matched_text(text, code) or ROBLOX_ERROR_CODE_PROMPTS.get(code, "")
    if prompt:
        return f"Error Code: {code} {prompt}"
    return f"Error Code: {code}"


def internal_reason_for_disconnect_code(code: int | None) -> str:
    """Map a parsed Roblox disconnect reason code to a stable internal lifecycle key.

    Every code in the Roblox disconnect range (200–599) gets its own key so recovery
    treats idle kicks, kicks, connection loss, server full, etc. uniformly — not only
    Error 278. Codes outside that range fall back to generic logcat disconnect keys."""
    try:
        n = int(code)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "logcat_disconnect"
    if n == 278:
        return "idle_disconnect_278"
    if 200 <= n <= 599:
        return f"disconnect_code_{n}"
    return "logcat_disconnect"


def format_lifecycle_dead_reason(
    internal_key: str,
    matched_text: str | None = None,
) -> str:
    """Best webhook Reason string: error-code prompt when known, else friendly fallback."""
    from .lifecycle_reasons import format_user_friendly_dead_reason

    key = str(internal_key or "").strip()
    if key == "captcha_verification":
        return "Captcha Verification"
    if key.startswith("disconnect_code_"):
        try:
            code = int(key.split("_", 2)[2])
        except (IndexError, TypeError, ValueError):
            code = None
        if code in CLEAN_DISCONNECT_REASONS:
            return CLEAN_DISCONNECT_REASONS[code]
        coded = format_error_code_reason(matched_text, internal_key=key)
        if coded:
            return coded
        if code is not None:
            prompt = ROBLOX_ERROR_CODE_PROMPTS.get(code, "")
            if prompt:
                return f"Error Code: {code} {prompt}"
            return f"Error Code: {code}"

    coded = format_error_code_reason(matched_text, internal_key=internal_key)
    if coded:
        return coded
    return format_user_friendly_dead_reason(internal_key)
