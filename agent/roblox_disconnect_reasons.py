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
                cleaned = _ERROR_CODE_RE.sub("", chunk).strip(" :-")
                if cleaned:
                    return cleaned[:180]
    cleaned = _ERROR_CODE_RE.sub("", raw).strip(" :-")
    return (cleaned or raw)[:180]


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
    prompt = _prompt_from_matched_text(text, code) or ROBLOX_ERROR_CODE_PROMPTS.get(code, "")
    if prompt:
        return f"Error Code: {code} {prompt}"
    return f"Error Code: {code}"


def format_lifecycle_dead_reason(
    internal_key: str,
    matched_text: str | None = None,
) -> str:
    """Best webhook Reason string: error-code prompt when known, else friendly fallback."""
    from .lifecycle_reasons import format_user_friendly_dead_reason

    coded = format_error_code_reason(matched_text, internal_key=internal_key)
    if coded:
        return coded
    return format_user_friendly_dead_reason(internal_key)
