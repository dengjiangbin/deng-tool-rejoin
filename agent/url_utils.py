"""Roblox launch URL validation, normalization, and masking."""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import ParseResult, parse_qsl, urlencode, urlparse, urlunparse

from .constants import SENSITIVE_URL_PARAM_NAMES

APPROVED_WEB_HOSTS = {"roblox.com", "www.roblox.com"}
APPROVED_WEB_PATH_PREFIXES = (
    "/games/",
    "/share",
    "/share-links",
    "/communities/",
    "/catalog/",
    "/users/",
    "/games/start",
)


class UrlValidationError(ValueError):
    """Raised when a launch URL is not safe enough to use."""


@dataclass(frozen=True)
class UrlValidationResult:
    valid: bool
    warning: str | None = None


def _normalized_host(parsed: ParseResult) -> str:
    return (parsed.hostname or "").lower().strip(".")


def detect_launch_mode_from_url(url: str | None) -> str:
    """Infer the DENG launch mode from a URL-like value."""
    if not url or not url.strip():
        return "app"
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() == "roblox":
        return "deeplink"
    if parsed.scheme.lower() in {"http", "https"}:
        return "web_url"
    return "web_url"


def normalize_launch_url(url: str) -> tuple[str, str | None]:
    """Normalize a launch URL where it is unambiguous.

    The function intentionally avoids over-normalizing Roblox links. Some share
    and private-server URLs are opaque, so preserving the original link is safer.
    """
    raw = (url or "").strip()
    if not raw:
        raise UrlValidationError("launch URL is empty")
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if scheme == "roblox":
        return raw, None
    if scheme not in {"http", "https"}:
        raise UrlValidationError("launch URL must use roblox://, http://, or https://")

    host = _normalized_host(parsed)
    if host not in APPROVED_WEB_HOSTS:
        raise UrlValidationError("web launch URL must be on roblox.com")

    rebuilt = parsed._replace(scheme="https", netloc=host)
    normalized = urlunparse(rebuilt)
    return normalized, None


def validate_launch_url(url: str | None, launch_mode: str | None = None, *, allow_uncertain: bool = False) -> UrlValidationResult:
    """Validate that a URL is appropriate for Android VIEW launching.

    `allow_uncertain=True` permits same-domain Roblox URLs that are not in the
    known path allow-list. Callers should show the returned warning before use.
    """
    if launch_mode == "app":
        return UrlValidationResult(True)
    if not url or not url.strip():
        raise UrlValidationError("launch URL is required for deeplink or web_url mode")

    raw = url.strip()
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()

    if launch_mode == "deeplink" and scheme != "roblox":
        raise UrlValidationError("deeplink mode requires a roblox:// URL")
    if launch_mode == "web_url" and scheme not in {"http", "https"}:
        raise UrlValidationError("web_url mode requires a Roblox http(s) URL")

    if scheme == "roblox":
        if not parsed.netloc:
            raise UrlValidationError("roblox:// URL must include a target")
        return UrlValidationResult(True)

    if scheme not in {"http", "https"}:
        raise UrlValidationError("launch URL must use roblox://, http://, or https://")

    host = _normalized_host(parsed)
    if host not in APPROVED_WEB_HOSTS:
        raise UrlValidationError("web launch URL must be on roblox.com")

    path = parsed.path or "/"
    if path == "/" or any(path.startswith(prefix) for prefix in APPROVED_WEB_PATH_PREFIXES):
        return UrlValidationResult(True)

    if allow_uncertain:
        return UrlValidationResult(True, "Roblox URL path is not in the known allow-list; it will be launched unchanged")

    raise UrlValidationError("Roblox URL path is not approved for launch")


def _should_mask_param(name: str) -> bool:
    lowered = name.lower()
    if lowered in SENSITIVE_URL_PARAM_NAMES:
        return True
    return any(marker in lowered for marker in ("private", "token", "secret", "cookie", "session"))


def mask_launch_url(url: str | None) -> str | None:
    """Mask private/sensitive query parameters while preserving readability."""
    if url is None:
        return None
    raw = str(url)
    if not raw:
        return raw
    parsed = urlparse(raw)
    if not parsed.query:
        return raw

    masked_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        masked_pairs.append((key, "***MASKED***" if _should_mask_param(key) and value else value))
    query = urlencode(masked_pairs, doseq=True, safe="*")
    return urlunparse(parsed._replace(query=query))


URL_IN_TEXT_RE = re.compile(r"(roblox://[^\s\"']+|https?://(?:www\.)?roblox\.com/[^\s\"']+)", re.IGNORECASE)


def mask_urls_in_text(text: str) -> str:
    """Mask any Roblox launch URLs embedded in a log/status line."""
    return URL_IN_TEXT_RE.sub(lambda match: mask_launch_url(match.group(0)) or match.group(0), text)
