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

import logging
import re
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable, Mapping, Sequence

from . import safe_http
from .url_utils import RobloxExpectedTarget

_log = logging.getLogger("deng.rejoin.roblox_presence")

# Public endpoints — both accept anonymous POST with a JSON body.
_USERNAME_LOOKUP_URL = "https://users.roblox.com/v1/usernames/users"
_PRESENCE_URL = "https://presence.roblox.com/v1/presence/users"

PRESENCE_TTL: float = 8.0           # cache window for a single user's presence
USERNAME_LOOKUP_TTL: float = 86400.0  # username → id resolution rarely changes
HTTP_TIMEOUT: float = 6.0

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

def _post_json(
    url: str,
    body: Mapping[str, object],
    *,
    cookie: str | None = None,
    timeout: float = HTTP_TIMEOUT,
) -> Mapping[str, object] | None:
    """POST JSON to a Roblox endpoint and return parsed JSON, or None.

    Never raises.  Returns None on any failure.  Caller decides whether
    "no data" should mean Unknown or Offline.

    Probe p-79933739d8: live Start reached watchdog package 1 and then the
    process vanished before state evidence was logged.  The only native-risky
    path entered there was Python urllib/ssl for the Roblox Presence API.
    Route through ``safe_http`` so Termux uses curl in a child process; if curl
    crashes, Python receives a clean network error instead of SIGSEGV.
    """
    if not isinstance(body, Mapping):
        return None

    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    if cookie:
        # Some endpoints want X-CSRF-TOKEN even for trivial GETs once a
        # cookie is present.  Roblox returns the token in a header on a
        # 403, then accepts the retry — but that handshake is fragile.
        # For presence + username lookups, an anonymous POST works fine.
        # We pass the cookie only when the caller explicitly opts in,
        # and we keep the field name safe (no leaking in error text).
        masked = mask_cookie(cookie)
        _log.debug("attaching cookie (masked=%s)", masked)
        headers["Cookie"] = f".ROBLOSECURITY={cookie}"

    try:
        payload = safe_http.post_json(
            url,
            dict(body),
            headers=headers,
            timeout=max(1, int(timeout)),
        )
        return payload if isinstance(payload, Mapping) else None
    except safe_http.SafeHttpError:
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
        payload = _post_json(
            _PRESENCE_URL, {"userIds": batch}, cookie=cookie,
        )
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
            state="Dead",
            reason="presence_offline",
            server_verification="presence_offline",
            **common,
        )

    if is_lobby:
        elapsed = float(launch_elapsed_seconds or 0.0)
        timed_out = launch_elapsed_seconds is not None and elapsed >= float(join_timeout_seconds)
        return PresenceResolution(
            state="Join Failed" if timed_out else "In-Lobby",
            reason="presence_online_not_playing_timeout" if timed_out else "presence_online_not_playing",
            server_verification="not_playing",
            **common,
        )

    return PresenceResolution(
        state="Unknown",
        reason="presence_type_not_supported",
        server_verification="unavailable",
        **common,
    )


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
        result = fetch_presence_one(user_id)
        return classify_presence_result(result)
    except Exception:  # noqa: BLE001
        return "unavailable"
