"""Ground-truth Roblox presence detection via the official public API.

The cloud-phone screenshot shows clones playing in-game while our local
``dumpsys``-based detection says ``Offline``.  That happens because
``dumpsys window``/``pidof`` are not authoritative for App Cloner clones —
process names are truncated, surfaces are reported with varying markers,
and tasks linger after a window closes.

Roblox themselves know the truth: their presence API returns each user's
state (Offline / Online / In-Game / In-Studio) plus the place they are in.
This module wraps that API so the supervisor can ground every decision
in what Roblox actually reports for the configured account.

Endpoints used (all public, all documented):

    POST https://users.roblox.com/v1/usernames/users
      body: {"usernames": ["alice"], "excludeBannedUsers": false}
      returns: [{"id": 12345, "name": "alice", ...}]

    POST https://presence.roblox.com/v1/presence/users
      body: {"userIds": [12345, ...]}
      returns: {"userPresences": [
          {"userPresenceType": 0|1|2|3|4,
           "lastLocation": "...",
           "placeId": int|null,
           "userId": int,
           "rootPlaceId": int|null,
           "lastOnline": "ISO-8601 timestamp"} ...]}

      userPresenceType values:
        0 = Offline    (logged out / app not connected)
        1 = Online     (lobby / home / browsing)
        2 = InGame     (in a place — what we want for "Online" in our table)
        3 = InStudio   (irrelevant for end users)
        4 = Invisible  (rare, treat as Offline)

An optional ``.ROBLOSECURITY`` cookie can be supplied per user; the API
returns the same shape for anonymous callers but with reduced placeId
detail.  Cookies are NEVER printed or logged — only their first/last
chars when masking is needed.

Threading / caching: results are cached per user id for ``PRESENCE_TTL``
seconds.  ``fetch_presence`` is reentrant and safe across threads.

Never raises.  Network/JSON/SSL failures all return safe defaults.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Iterable, Mapping, Sequence

from . import root_access, safe_http
from .config import validate_package_name, validate_roblosecurity_cookie
from .safe_http import is_rate_limited_status
from .url_utils import RobloxExpectedTarget

_log = logging.getLogger("deng.rejoin.roblox_presence")


class RobloxRateLimitedError(Exception):
    """Raised when Roblox returns HTTP 429 Too Many Requests."""

# Public endpoints — both accept anonymous POST with a JSON body.
_USERNAME_LOOKUP_URL = "https://users.roblox.com/v1/usernames/users"
_PRESENCE_URL = "https://presence.roblox.com/v1/presence/users"

PRESENCE_TTL: float = 8.0           # cache window for a single user's presence
USERNAME_LOOKUP_TTL: float = 86400.0  # username → id resolution rarely changes
HTTP_TIMEOUT: float = 14.0  # strictly < 15s presence poll budget

# User-Agent must look like a real client; some Roblox edge nodes 403 on
# default Python urllib UA.  We don't impersonate a browser — just identify
# ourselves clearly so the request is accepted by their CDN.
_USER_AGENT = "DENG-Tool-Rejoin/1.0 (+presence-check)"


class PresenceType(IntEnum):
    OFFLINE = 0
    ONLINE = 1
    IN_GAME = 2
    IN_STUDIO = 3
    INVISIBLE = 4
    UNKNOWN = -1   # network failure / not yet observed

    @property
    def label(self) -> str:
        return {
            0: "Offline",
            1: "Online",
            2: "InGame",
            3: "InStudio",
            4: "Invisible",
        }.get(int(self), "Unknown")


@dataclass(frozen=True)
class PresenceResult:
    user_id: int
    presence_type: PresenceType = PresenceType.UNKNOWN
    place_id: int | None = None
    root_place_id: int | None = None
    universe_id: int | None = None
    game_id: str = ""
    last_location: str = ""
    last_online_iso: str = ""

    @property
    def is_in_game(self) -> bool:
        return self.presence_type == PresenceType.IN_GAME

    @property
    def is_lobby(self) -> bool:
        return self.presence_type == PresenceType.ONLINE

    @property
    def is_offline(self) -> bool:
        return self.presence_type in (
            PresenceType.OFFLINE, PresenceType.INVISIBLE,
        )

    @property
    def is_unknown(self) -> bool:
        return self.presence_type == PresenceType.UNKNOWN


@dataclass(frozen=True)
class PresenceResolution:
    state: str
    reason: str
    server_verification: str = "unavailable"
    expected_place_id: int | None = None
    expected_root_place_id: int | None = None
    expected_universe_id: int | None = None
    expected_private_code: str = ""
    actual_place_id: int | None = None
    actual_root_place_id: int | None = None
    actual_universe_id: int | None = None
    actual_game_id: str = ""


# ─── in-memory caches ────────────────────────────────────────────────────────

_PRESENCE_LOCK = threading.Lock()
_PRESENCE_CACHE: dict[int, tuple[float, PresenceResult]] = {}

_USERNAME_LOCK = threading.Lock()
_USERNAME_CACHE: dict[str, tuple[float, int | None]] = {}


# ─── http helper ─────────────────────────────────────────────────────────────

def _extract_csrf_token(response_headers: Mapping[str, str]) -> str:
    for key, value in response_headers.items():
        if str(key).lower() == "x-csrf-token" and str(value).strip():
            return str(value).strip()
    return ""


def _decode_post_json_body(body_bytes: bytes) -> Mapping[str, object] | None:
    if not body_bytes:
        return None
    try:
        payload = json.loads(body_bytes.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, ValueError, UnicodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _roblox_post_once(
    url: str,
    body: Mapping[str, object],
    *,
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, dict[str, str], Mapping[str, object] | None]:
    json_bytes = json.dumps(dict(body), separators=(",", ":")).encode("utf-8")
    status, resp_headers, resp_body = safe_http.post_with_response(
        url,
        json_bytes,
        headers=headers,
        timeout=max(1, int(timeout)),
    )
    return status, resp_headers, _decode_post_json_body(resp_body)


def _post_json(
    url: str,
    body: Mapping[str, object],
    *,
    cookie: str | None = None,
    timeout: float = HTTP_TIMEOUT,
) -> Mapping[str, object] | None:
    """POST JSON to a Roblox endpoint and return parsed JSON, or None.

    When a ``.ROBLOSECURITY`` cookie is supplied, Roblox requires an
    ``X-CSRF-TOKEN`` handshake: the first POST may return HTTP 403 with
    ``x-csrf-token`` in the response headers; the same POST is retried
    immediately with ``X-CSRF-TOKEN`` set.
    """
    if not isinstance(body, Mapping):
        return None

    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    if cookie:
        masked = mask_cookie(cookie)
        _log.debug("attaching cookie (masked=%s)", masked)
        headers["Cookie"] = f".ROBLOSECURITY={cookie}"

    try:
        status, resp_headers, payload = _roblox_post_once(
            url, body, headers=headers, timeout=timeout,
        )
        if cookie and status == 403:
            csrf = _extract_csrf_token(resp_headers)
            if csrf:
                retry_headers = dict(headers)
                retry_headers["X-CSRF-TOKEN"] = csrf
                _log.debug("roblox CSRF retry for %s", url)
                status, resp_headers, payload = _roblox_post_once(
                    url, body, headers=retry_headers, timeout=timeout,
                )
        if is_rate_limited_status(status):
            _log.debug("presence POST rate limited: %s", url)
            raise RobloxRateLimitedError(url)
        if status >= 400:
            _log.debug("presence POST HTTP %s: %s", status, url)
            return None
        return payload if isinstance(payload, Mapping) else None
    except safe_http.SafeHttpNetworkError as exc:
        _log.debug("presence POST network error: %s", exc)
        return None
    except RobloxRateLimitedError:
        raise
    except safe_http.SafeHttpError as exc:
        _log.debug("presence POST safe_http error: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        _log.debug("presence POST error: %s", exc)
        return None


def mask_cookie(cookie: str | None) -> str:
    """Return a short masked representation of a ROBLOSECURITY cookie.

    NEVER returns more than 12 chars of the original value.  Safe to log.
    """
    if not cookie:
        return ""
    s = str(cookie).strip()
    if len(s) <= 12:
        return "***"
    return f"{s[:4]}…{s[-4:]}"


# ─── username → userId ───────────────────────────────────────────────────────

_USERNAME_VALID_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")


def resolve_username_to_user_id(username: str | None) -> int | None:
    """Resolve a Roblox username to a numeric user ID.  Returns None on failure.

    Alias for :func:`lookup_user_id` — provided so callers can use the longer,
    more descriptive name.  Result is cached for ``USERNAME_LOOKUP_TTL`` seconds.
    Never raises.
    """
    return lookup_user_id(username)


def lookup_user_id(username: str | None) -> int | None:
    """Resolve a Roblox username to a numeric user_id.  Returns None on failure.

    Result is cached for ``USERNAME_LOOKUP_TTL`` seconds.  Never raises.
    """
    if not username:
        return None
    name = str(username).strip()
    if not _USERNAME_VALID_RE.match(name):
        return None
    now = time.monotonic()
    with _USERNAME_LOCK:
        cached = _USERNAME_CACHE.get(name.lower())
        if cached and (now - cached[0]) < USERNAME_LOOKUP_TTL:
            return cached[1]
    body = {"usernames": [name], "excludeBannedUsers": False}
    payload = _post_json(_USERNAME_LOOKUP_URL, body)
    user_id: int | None = None
    if payload is None:
        # Network/CDN/SSL failures must not poison the username cache with
        # ``None`` for a day.  The watchdog will retry later or use rooted
        # per-clone prefs evidence.
        return None
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                raw = first.get("id")
                if isinstance(raw, int) and raw > 0:
                    user_id = raw
                elif isinstance(raw, str) and raw.isdigit():
                    user_id = int(raw)
    with _USERNAME_LOCK:
        _USERNAME_CACHE[name.lower()] = (now, user_id)
    return user_id


# ─── presence fetch ──────────────────────────────────────────────────────────

def _parse_presence_row(row: Mapping[str, object]) -> PresenceResult | None:
    try:
        uid_raw = row.get("userId")
        if isinstance(uid_raw, str) and uid_raw.isdigit():
            uid = int(uid_raw)
        elif isinstance(uid_raw, int):
            uid = uid_raw
        else:
            return None
    except Exception:  # noqa: BLE001
        return None
    pres_raw = row.get("userPresenceType")
    try:
        ptype = PresenceType(int(pres_raw))
    except (TypeError, ValueError):
        ptype = PresenceType.UNKNOWN
    place_id = _coerce_int(row.get("placeId"))
    root_place = _coerce_int(row.get("rootPlaceId"))
    universe_id = _coerce_int(row.get("universeId"))
    return PresenceResult(
        user_id=uid,
        presence_type=ptype,
        place_id=place_id,
        root_place_id=root_place,
        universe_id=universe_id,
        game_id=str(row.get("gameId") or "")[:128],
        last_location=str(row.get("lastLocation") or "")[:80],
        last_online_iso=str(row.get("lastOnline") or "")[:32],
    )


def _coerce_int(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    text = str(value or "").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def fetch_presence(
    user_ids: Sequence[int],
    *,
    cookie: str | None = None,
    refresh: bool = False,
) -> dict[int, PresenceResult]:
    """Fetch presence for a batch of user ids.  Cached per-user for ``PRESENCE_TTL``.

    Returns a dict ``{user_id: PresenceResult}``.  IDs the API failed to
    return are mapped to ``PresenceType.UNKNOWN`` rather than missing keys,
    so callers can rely on the dict containing every requested id.
    Never raises.
    """
    out: dict[int, PresenceResult] = {}
    if not user_ids:
        return out
    ids = sorted({int(u) for u in user_ids if isinstance(u, int) and u > 0})
    if not ids:
        return out

    now = time.monotonic()
    fresh: list[int] = []
    if not refresh:
        with _PRESENCE_LOCK:
            for uid in ids:
                hit = _PRESENCE_CACHE.get(uid)
                if hit and (now - hit[0]) < PRESENCE_TTL:
                    out[uid] = hit[1]
                else:
                    fresh.append(uid)
    else:
        fresh = list(ids)

    if not fresh:
        return out

    # Roblox limits the batch to 200 ids per request; we never hit that in
    # practice but chunk defensively anyway.
    BATCH = 100
    for i in range(0, len(fresh), BATCH):
        batch = fresh[i:i + BATCH]
        try:
            payload = _post_json(
                _PRESENCE_URL, {"userIds": batch}, cookie=cookie,
            )
        except RobloxRateLimitedError:
            raise
        seen_in_batch: set[int] = set()
        if isinstance(payload, dict):
            rows = payload.get("userPresences")
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    pr = _parse_presence_row(row)
                    if pr is None:
                        continue
                    out[pr.user_id] = pr
                    seen_in_batch.add(pr.user_id)
                    with _PRESENCE_LOCK:
                        _PRESENCE_CACHE[pr.user_id] = (now, pr)
        # For any id the server didn't return, surface an Unknown so the
        # supervisor's decision tree can keep its prior heartbeat.
        for uid in batch:
            if uid not in out:
                out[uid] = PresenceResult(
                    user_id=uid, presence_type=PresenceType.UNKNOWN,
                )

    return out


def fetch_presence_one(
    user_id: int | None,
    *,
    cookie: str | None = None,
    refresh: bool = False,
) -> PresenceResult:
    """Convenience: presence for a single user id.  Returns Unknown if None."""
    if not user_id or user_id <= 0:
        return PresenceResult(user_id=0, presence_type=PresenceType.UNKNOWN)
    out = fetch_presence([user_id], cookie=cookie, refresh=refresh)
    return out.get(int(user_id), PresenceResult(
        user_id=int(user_id), presence_type=PresenceType.UNKNOWN,
    ))


def _presence_needs_public_fallback(result: PresenceResult) -> bool:
    """True when the cookie pass cannot prove in-game and may be stale."""
    return bool(result.is_offline or result.is_unknown)


def fetch_presence_dual_verified(
    user_id: int | None,
    *,
    cookie: str | None = None,
    refresh: bool = True,
) -> PresenceResult:
    """Cookie-authenticated pass first, then public user-id fallback.

    Roblox can return stale or empty cookie-authenticated presence rows when
    multiple clones on one IP poll simultaneously.  When the cookie pass says
    offline/unknown, re-check via the public endpoint before declaring NHB.
    """
    if not user_id or user_id <= 0:
        return PresenceResult(user_id=0, presence_type=PresenceType.UNKNOWN)

    if cookie:
        cookie_result = fetch_presence_one(user_id, cookie=cookie, refresh=refresh)
        if cookie_result.is_in_game:
            return cookie_result
        if not _presence_needs_public_fallback(cookie_result):
            return cookie_result
        _log.info(
            "dual_verify public fallback uid=%s cookie_type=%s",
            user_id,
            cookie_result.presence_type.name,
        )
        public_result = fetch_presence_one(user_id, cookie=None, refresh=refresh)
        if public_result.is_in_game:
            _log.info("dual_verify public pass rescued in_game uid=%s", user_id)
            return public_result
        if cookie_result.is_unknown and not public_result.is_unknown:
            return public_result
        return cookie_result

    return fetch_presence_one(user_id, cookie=None, refresh=refresh)


def fetch_presence_for_user_ids(
    user_ids: Sequence[int],
    *,
    cookie: str | None = None,
    refresh: bool = False,
) -> dict[int, PresenceResult]:
    """Fetch presence for a list of user IDs.  Alias for :func:`fetch_presence`.

    Returns a dict ``{user_id: PresenceResult}`` with an entry for every
    requested id (UNKNOWN if the API didn't return it).  Never raises.
    """
    return fetch_presence(user_ids, cookie=cookie, refresh=refresh)


def clear_presence_cache() -> None:
    """For tests.  Drops both presence and username caches."""
    with _PRESENCE_LOCK:
        _PRESENCE_CACHE.clear()
    with _USERNAME_LOCK:
        _USERNAME_CACHE.clear()


# ─── Supervisor integration helpers ──────────────────────────────────────────


def classify_presence_result(result: PresenceResult | None) -> str:
    """Classify a :class:`PresenceResult` into a stable internal state string.

    Returns one of:
      ``"unknown"``           — no data / not yet resolved / UNKNOWN type
      ``"unavailable"``       — result is None (API timeout / error)
      ``"offline"``           — account is Offline or Invisible
      ``"online_not_in_game"``— account is in app lobby (PresenceType.ONLINE)
      ``"in_experience"``     — account is actively in a Roblox experience
      ``"in_studio"``         — account is in Roblox Studio (treated like in_experience)

    Never raises.
    """
    if result is None:
        return "unavailable"
    try:
        ptype = result.presence_type
        if ptype == PresenceType.UNKNOWN:
            return "unknown"
        if ptype == PresenceType.IN_GAME:
            return "in_experience"
        if ptype == PresenceType.IN_STUDIO:
            return "in_studio"
        if ptype == PresenceType.ONLINE:
            return "online_not_in_game"
        if ptype in (PresenceType.OFFLINE, PresenceType.INVISIBLE):
            return "offline"
        return "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def resolve_presence_state(
    presence: PresenceResult | None,
    expected: RobloxExpectedTarget | None = None,
    *,
    process_alive: bool,
    launch_elapsed_seconds: float | None = None,
    join_timeout_seconds: float = 90.0,
    local_warning_detected: bool = False,
    local_stuck_detected: bool = False,
) -> PresenceResolution:
    """Resolve authenticated presence + local signals into a public state.

    This helper is intentionally conservative: API failure/UNKNOWN never asks
    callers to relaunch. Dead is controlled by Android process evidence before
    this function is normally called, but the process flag is accepted so unit
    tests and future callers share the same invariant.
    """
    target = expected or RobloxExpectedTarget()
    if not process_alive:
        return PresenceResolution(
            state="Dead",
            reason="android_process_not_alive",
            server_verification="not_checked",
        )

    if local_stuck_detected:
        return PresenceResolution(
            state="No Heartbeat",
            reason="local_stuck_detector",
            server_verification="local_override",
        )
    if local_warning_detected:
        return PresenceResolution(
            state="Join Failed",
            reason="local_warning_detector",
            server_verification="local_override",
        )

    is_in_game = bool(getattr(presence, "is_in_game", False))
    is_offline = bool(getattr(presence, "is_offline", False))
    is_lobby = bool(getattr(presence, "is_lobby", False))
    is_unknown = bool(getattr(presence, "is_unknown", False))

    if presence is None or is_unknown:
        return PresenceResolution(
            state="Unknown",
            reason="presence_unavailable_or_unknown",
            server_verification="unavailable",
        )

    actual_place = presence.place_id
    actual_root = presence.root_place_id
    actual_universe = presence.universe_id
    common = {
        "expected_place_id": target.expected_place_id,
        "expected_root_place_id": target.expected_root_place_id,
        "expected_universe_id": target.expected_universe_id,
        "expected_private_code": target.expected_private_code,
        "actual_place_id": actual_place,
        "actual_root_place_id": actual_root,
        "actual_universe_id": actual_universe,
        "actual_game_id": presence.game_id,
    }

    if is_in_game:
        expected_pairs = [
            ("place", target.expected_place_id, actual_place),
            ("root_place", target.expected_root_place_id, actual_root),
            ("universe", target.expected_universe_id, actual_universe),
        ]
        known_expectations = [(name, exp, got) for name, exp, got in expected_pairs if exp is not None]
        missing = [(name, exp) for name, exp, got in known_expectations if got is None]
        mismatches = [
            (name, exp, got)
            for name, exp, got in known_expectations
            if got is not None and int(got) != int(exp)
        ]
        if mismatches:
            return PresenceResolution(
                state="Wrong Game / Wrong Server",
                reason="presence_target_mismatch",
                server_verification="mismatch",
                **common,
            )
        if missing:
            return PresenceResolution(
                state="Online",
                reason="presence_playing_partial_target_fields",
                server_verification="partial",
                **common,
            )
        return PresenceResolution(
            state="Online",
            reason="presence_playing_target_match" if known_expectations else "presence_playing_no_expected_target",
            server_verification="matched" if known_expectations else "partial",
            **common,
        )

    if is_offline:
        return PresenceResolution(
            state="No Heartbeat",
            reason="presence_offline",
            server_verification="presence_offline",
            **common,
        )

    if is_lobby:
        elapsed = float(launch_elapsed_seconds or 0.0)
        if elapsed > float(join_timeout_seconds):
            return PresenceResolution(
                state="No Heartbeat",
                reason="presence_lobby_join_timeout",
                server_verification="not_playing",
                **common,
            )
        return PresenceResolution(
            state="No Heartbeat",
            reason="presence_online_not_playing",
            server_verification="not_playing",
            **common,
        )

    return PresenceResolution(
        state="Unknown",
        reason="presence_type_not_supported",
        server_verification="unavailable",
        **common,
    )


def map_presence_profile(result: PresenceResult | None) -> str:
    """Map Roblox presence API result to a public profile label."""
    if result is None or result.is_unknown:
        return ""
    if result.is_in_game:
        return "Online"
    if result.is_lobby:
        return "In Lobby"
    if result.is_offline:
        return "Offline"
    return "Online"


def poll_presence_gate_state(
    user_id: int,
    *,
    cookie: str | None = None,
    process_alive: bool = True,
) -> str:
    """Classify one presence poll for sequential launch gating.

    Returns one of: ``Online``, ``Checking``, ``Pending``, ``Dead``.
    Never raises.
    """
    if not process_alive:
        return "Dead"
    if user_id <= 0:
        return "Pending"
    try:
        result = fetch_presence_one(user_id, cookie=cookie, refresh=True)
    except Exception:  # noqa: BLE001
        return "Checking"
    if result.presence_type == PresenceType.IN_GAME:
        return "Online"
    return "Checking"


def get_presence_state_for_package(package_entry: dict | None) -> str:  # type: ignore[type-arg]
    """Convenience: look up and classify the presence state for one package entry.

    ``package_entry`` is a dict from the config's ``roblox_packages`` list.
    Expected keys:
      ``account_username``  — display name / Roblox username
      ``roblox_user_id``    — (optional) pre-resolved numeric user_id

    Returns one of the :func:`classify_presence_result` state strings.
    Falls back to ``"unavailable"`` for any missing data or network error.
    Never raises.
    """
    if not package_entry or not isinstance(package_entry, dict):
        return "unavailable"
    try:
        user_id: int | None = None
        raw_uid = package_entry.get("roblox_user_id")
        if isinstance(raw_uid, int) and raw_uid > 0:
            user_id = raw_uid
        elif isinstance(raw_uid, str) and raw_uid.isdigit():
            user_id = int(raw_uid)
        if not user_id:
            username = str(package_entry.get("account_username") or "").strip()
            if username:
                user_id = lookup_user_id(username)
        if not user_id:
            return "unavailable"
        cookie = str(package_entry.get("roblox_cookie") or "").strip() or None
        result = fetch_presence_one(user_id, cookie=cookie)
        return classify_presence_result(result)
    except Exception:  # noqa: BLE001
        return "unavailable"


# ── Root-assisted .ROBLOSECURITY detection (ships in protected runtime) ───────
# ``agent/roblox_cookie_detect.py`` is excluded from release tarballs because the
# artifact packer skips paths containing ``cookie``.  Keep detection here so
# production installs can import ``detect_roblox_cookie`` from this module.

_ROBLOX_COOKIE_PREFIX = "_|WARNING:-DO-NOT-SHARE-THIS"
_COOKIE_KEY_NAMES = frozenset({".roblosecurity", "roblosecurity"})
_WEBVIEW_COOKIE_PATHS = (
    "app_webview/Default/Cookies",
    "app_webview/Cookies",
    "app_webview/Network/Cookies",
    "databases/Cookies",
)
_COOKIE_INLINE_RE = re.compile(
    r"(?:\.?ROBLOSECURITY=)?(\_\|WARNING:-DO-NOT-SHARE-THIS\.[^\s\"'<>;]{16,})",
    re.IGNORECASE,
)


def _normalize_cookie_value(raw: str | None) -> str:
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    if text.lower().startswith(".roblosecurity="):
        text = text.split("=", 1)[1].strip()
    try:
        return validate_roblosecurity_cookie(text)
    except Exception:  # noqa: BLE001
        return ""


def looks_like_roblox_cookie(raw: str | None) -> bool:
    cookie = _normalize_cookie_value(raw)
    if not cookie:
        return False
    if cookie.startswith(_ROBLOX_COOKIE_PREFIX):
        return True
    return len(cookie) >= 64 and " " not in cookie and "\n" not in cookie


def cookie_from_pref_xml(xml_text: str) -> str:
    """Extract .ROBLOSECURITY from Android shared_prefs XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    for child in root:
        key = (child.attrib.get("name") or "").strip()
        if not key:
            continue
        key_low = key.lower()
        raw_val = child.attrib.get("value")
        if raw_val is None and child.text:
            raw_val = str(child.text).strip()
        if key_low in _COOKIE_KEY_NAMES or ".roblosecurity" in key_low:
            cookie = _normalize_cookie_value(raw_val)
            if cookie:
                return cookie
        if raw_val and looks_like_roblox_cookie(str(raw_val)):
            return _normalize_cookie_value(str(raw_val))
    match = _COOKIE_INLINE_RE.search(xml_text or "")
    if match:
        return _normalize_cookie_value(match.group(1))
    return ""


def _cookie_from_webview_db(tmp_db_path: str) -> str:
    try:
        conn = sqlite3.connect(f"file:{tmp_db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return ""
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT value FROM cookies WHERE lower(name) IN ('.roblosecurity', 'roblosecurity') "
            "ORDER BY creation_utc DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row and row[0]:
            cookie = _normalize_cookie_value(str(row[0]))
            if cookie:
                return cookie
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return ""


def _root_scan_shared_prefs(package: str, *, timeout: int, max_bytes: int) -> str:
    """Aggressively scan clone shared_prefs via ``su -c`` for .ROBLOSECURITY."""
    glob_patterns = (
        f"/data/data/{package}/shared_prefs/*.xml",
        f"/data/user/0/{package}/shared_prefs/*.xml",
        f"/data/user_de/0/{package}/shared_prefs/*.xml",
        f"/data_mirror/data_ce/null/0/{package}/shared_prefs/*.xml",
    )
    hints = ("cookie", "auth", "session", "roblox", "account", "user", "pkg_preferences", "roblosecurity")
    seen: set[str] = set()
    ordered: list[str] = []
    for pattern in glob_patterns:
        try:
            files = root_access.list_root_glob(pattern, timeout=timeout, max_results=32)
        except Exception:  # noqa: BLE001
            files = []
        priority = [path for path in files if any(h in path.lower() for h in hints)]
        for path in priority + [p for p in files if p not in priority]:
            if path not in seen:
                seen.add(path)
                ordered.append(path)
    for abs_path in ordered[:32]:
        content = root_access.read_root_file(abs_path, max_bytes=max_bytes, timeout=timeout)
        if not content:
            continue
        cookie = cookie_from_pref_xml(content)
        if cookie:
            _log.info("Auto-detected ROBLOSECURITY for %s via root_shared_prefs", package)
            return cookie
    return ""


def _root_scan_webview_cookies(package: str, *, timeout: int) -> str:
    if not root_access.has_root():
        return ""
    for rel in _WEBVIEW_COOKIE_PATHS:
        abs_path = f"/data/data/{package}/{rel}"
        tmp = tempfile.mktemp(suffix=".cookies.db", prefix="deng_roblox_")
        try:
            copied = root_access.run_root_command(["cp", abs_path, tmp], timeout=timeout)
            if copied.returncode != 0:
                continue
            cookie = _cookie_from_webview_db(tmp)
            if cookie:
                _log.info("Auto-detected ROBLOSECURITY for %s via webview cookies", package)
                return cookie
        except Exception as exc:  # noqa: BLE001
            _log.debug("WebView cookie scan failed for %s (%s): %s", package, rel, exc)
        finally:
            try:
                import os

                if os.path.isfile(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
    return ""


def detect_roblox_cookie(
    package_name: str,
    *,
    entry: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    use_root: bool = True,
    force_rescan: bool = False,
) -> str:
    """Detect .ROBLOSECURITY for one package. Returns '' when unavailable."""
    try:
        package_name = validate_package_name(package_name)
    except Exception:
        return ""

    if entry and not force_rescan:
        existing = str(entry.get("roblox_cookie") or "").strip()
        if existing:
            try:
                return validate_roblosecurity_cookie(existing)
            except Exception:  # noqa: BLE001
                pass

    if not use_root or not root_access.has_root():
        return ""

    settings = {}
    if isinstance(config, dict):
        raw = config.get("account_detection")
        if isinstance(raw, dict):
            settings = raw
    if settings.get("enabled", True) is False:
        return ""

    timeout = int(settings.get("scan_timeout_seconds", 8) or 8)
    max_bytes = int(settings.get("max_file_size_kb", 512) or 512) * 1024

    try:
        cookie = _root_scan_shared_prefs(package_name, timeout=timeout, max_bytes=max_bytes)
        if cookie:
            return cookie
        cookie = _root_scan_webview_cookies(package_name, timeout=timeout)
        if cookie:
            return cookie
    except Exception as exc:  # noqa: BLE001
        _log.debug("ROBLOSECURITY auto-detect failed for %s: %s", package_name, exc)
    return ""


roblox_cookie_detect = detect_roblox_cookie
