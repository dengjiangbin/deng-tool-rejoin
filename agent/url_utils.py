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


@dataclass(frozen=True)
class RobloxExpectedTarget:
    original_url: str = ""
    expected_place_id: int | None = None
    expected_root_place_id: int | None = None
    expected_universe_id: int | None = None
    expected_private_code: str = ""
    # Share-link type from URL (Server, ExperienceInvite, …) — used for resolve-link API.
    expected_share_type: str = ""

    @property
    def has_game_target(self) -> bool:
        return any(
            value is not None
            for value in (
                self.expected_place_id,
                self.expected_root_place_id,
                self.expected_universe_id,
            )
        )


def _normalized_host(parsed: ParseResult) -> str:
    return (parsed.hostname or "").lower().strip(".")


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    text = str(value or "").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


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


def to_roblox_deep_link(url: str | None) -> str | None:
    """Convert a Roblox web URL to its ``roblox://`` deep-link equivalent.

    Real-device evidence (Kaeru probe ``p-1239f2b5f9`` on cloud SM-N9810
    /Android 10): the dumpsys recents block for a successfully joined
    clone showed::

        dat=roblox://navigation/share_links?code=<CODE>&type=Server

    while our launches were sending the ``https://www.roblox.com/share?...``
    form which Android resolved to the *browser* — landing the user in a
    Roblox lobby instead of the private server they configured.  This
    helper performs the conversion so we behave the same way Kaeru does.

    Returns the original URL unchanged for non-Roblox web URLs and for
    any ``roblox://`` URL.  Returns ``None`` for falsy / empty input.

    Known conversions
    -----------------

    * ``https://www.roblox.com/share?code=X&type=Server``
        → ``roblox://navigation/share_links?code=X&type=Server``
    * ``https://www.roblox.com/share?code=X``  (no type)
        → ``roblox://navigation/share_links?code=X``
    * ``https://www.roblox.com/games/123/abc?privateServerLinkCode=Y``
        → ``roblox://placeId=123&privateServerLinkCode=Y``  (legacy
          private-server format Roblox still accepts)
    * Everything else is returned unchanged.

    The function NEVER raises — bad input falls back to the original URL.
    """
    if not url:
        return url
    raw = url.strip()
    if not raw:
        return url
    try:
        parsed = urlparse(raw)
    except Exception:  # noqa: BLE001
        return raw
    scheme = (parsed.scheme or "").lower()
    if scheme == "roblox":
        return raw  # already a deep link
    if scheme not in {"http", "https"}:
        return raw
    host = _normalized_host(parsed)
    if host not in APPROVED_WEB_HOSTS:
        return raw

    path = (parsed.path or "/").rstrip("/")
    query = parsed.query or ""

    # ── /share | /share-links | /share/ ── new share-link format
    if path in ("/share", "/share-links"):
        # Preserve every original query param (code, type, possibly more)
        return f"roblox://navigation/share_links?{query}" if query else \
               "roblox://navigation/share_links"

    # ── /games/<placeId>/<name>?privateServerLinkCode=...  (legacy)
    m = re.match(r"^/games/(\d+)/?", path + "/")
    if m:
        place_id = m.group(1)
        params = dict(parse_qsl(query, keep_blank_values=True))
        # `gameInstanceId`, `linkCode`, `privateServerLinkCode` all flow through.
        passthrough = []
        for k, v in params.items():
            passthrough.append(f"{k}={v}")
        suffix = "&" + "&".join(passthrough) if passthrough else ""
        return f"roblox://placeId={place_id}{suffix}"

    # ── /games/start?placeId=... — also used by Roblox shortcuts
    if path == "/games/start":
        params = dict(parse_qsl(query, keep_blank_values=True))
        place_id = params.pop("placeId", "")
        if place_id:
            passthrough = [f"{k}={v}" for k, v in params.items()]
            suffix = "&" + "&".join(passthrough) if passthrough else ""
            return f"roblox://placeId={place_id}{suffix}"

    # Path we don't know how to translate — return original; the OS will
    # still launch it via the Roblox app's https intent filters.
    return raw


def parse_expected_target_from_url(
    url: str | None,
    *,
    expected_place_id: object = None,
    expected_root_place_id: object = None,
    expected_universe_id: object = None,
) -> RobloxExpectedTarget:
    """Extract verifiable target hints from a Roblox launch/private-server URL.

    Parses roblox:// and https:// forms aligned with roblox-deeplink-parser:
    experiences/start, navigation/share_links, navigation/game_details, games/{id},
    games/start, legacy privateServerLinkCode/linkCode/accessCode/gameInstanceId, etc.
    Share links often expose only an opaque private code; place/universe may be filled
    later via :func:`agent.roblox_target_resolver.enrich_expected_target`.
    """
    raw = str(url or "").strip()
    place_id = _int_or_none(expected_place_id)
    root_place_id = _int_or_none(expected_root_place_id)
    universe_id = _int_or_none(expected_universe_id)
    private_code = ""
    share_type = ""
    if not raw:
        return RobloxExpectedTarget(
            original_url="",
            expected_place_id=place_id,
            expected_root_place_id=root_place_id,
            expected_universe_id=universe_id,
        )
    try:
        parsed = urlparse(raw)
        params = dict(parse_qsl(parsed.query or "", keep_blank_values=True))
        share_type = str(params.get("type") or params.get("linkType") or "").strip()

        for key in (
            "privateServerLinkCode",
            "linkCode",
            "gameInstanceId",
            "accessCode",
            "code",
        ):
            if params.get(key):
                private_code = str(params[key]).strip()
                break

        for key in ("placeId", "placeid", "rootPlaceId", "rootplaceid", "gameId"):
            parsed_place = _int_or_none(params.get(key))
            if parsed_place is not None:
                place_id = parsed_place
                break

        path = (parsed.path or "").rstrip("/")
        if place_id is None:
            match = re.match(r"^/games/(\d+)(?:/|$)", path + "/")
            if match:
                place_id = int(match.group(1))

        scheme = parsed.scheme.lower()
        netloc = (parsed.netloc or "").lower()
        if scheme == "roblox":
            haystack = raw
            if place_id is None:
                match = re.search(r"(?:^|[/?&])placeId=(\d+)", haystack, re.IGNORECASE)
                if match:
                    place_id = int(match.group(1))
            if not private_code:
                match = re.search(
                    r"(?:^|[/?&])(privateServerLinkCode|linkCode|gameInstanceId|accessCode|code)=([^&]+)",
                    haystack,
                    re.IGNORECASE,
                )
                if match:
                    private_code = match.group(2).strip()
            if not share_type:
                match = re.search(r"(?:^|[/?&])type=([^&]+)", haystack, re.IGNORECASE)
                if match:
                    share_type = match.group(1).strip()
            # roblox://navigation/share_links/Server/CODE path form
            if not private_code and "share_links" in haystack.lower():
                match = re.search(
                    r"share_links/(?:[^/?&]+/)?([A-Za-z0-9_\-]{4,})",
                    haystack,
                    re.IGNORECASE,
                )
                if match:
                    private_code = match.group(1).strip()
            # roblox://navigation/game_details?gameId=…
            if place_id is None and "game_details" in haystack.lower():
                match = re.search(r"(?:^|[/?&])gameId=(\d+)", haystack, re.IGNORECASE)
                if match:
                    place_id = int(match.group(1))

        if path in ("/share", "/share-links") and not share_type:
            share_type = str(params.get("type") or "Server").strip() or "Server"

        if root_place_id is None and place_id is not None:
            root_place_id = place_id
    except Exception:  # noqa: BLE001
        pass
    return RobloxExpectedTarget(
        original_url=raw,
        expected_place_id=place_id,
        expected_root_place_id=root_place_id,
        expected_universe_id=universe_id,
        expected_private_code=private_code[:256],
        expected_share_type=share_type[:64],
    )


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
