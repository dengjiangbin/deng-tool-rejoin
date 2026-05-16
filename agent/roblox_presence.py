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
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterable, Mapping, Sequence

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
    """
    try:
        data = json.dumps(body).encode("utf-8")
    except (TypeError, ValueError):
        return None

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", _USER_AGENT)

    if cookie:
        # Some endpoints want X-CSRF-TOKEN even for trivial GETs once a
        # cookie is present.  Roblox returns the token in a header on a
        # 403, then accepts the retry — but that handshake is fragile.
        # For presence + username lookups, an anonymous POST works fine.
        # We pass the cookie only when the caller explicitly opts in,
        # and we keep the field name safe (no leaking in error text).
        masked = mask_cookie(cookie)
        _log.debug("attaching cookie (masked=%s)", masked)
        req.add_header("Cookie", f".ROBLOSECURITY={cookie}")

    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read(1 << 18)  # 256 KB cap
            txt = raw.decode("utf-8", errors="replace")
            try:
                return json.loads(txt)
            except json.JSONDecodeError:
                return None
    except (urllib.error.URLError, urllib.error.HTTPError, ssl.SSLError, TimeoutError):
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
    place_id = row.get("placeId")
    root_place = row.get("rootPlaceId")
    return PresenceResult(
        user_id=uid,
        presence_type=ptype,
        place_id=int(place_id) if isinstance(place_id, int) else None,
        root_place_id=int(root_place) if isinstance(root_place, int) else None,
        last_location=str(row.get("lastLocation") or "")[:80],
        last_online_iso=str(row.get("lastOnline") or "")[:32],
    )


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


def clear_presence_cache() -> None:
    """For tests.  Drops both presence and username caches."""
    with _PRESENCE_LOCK:
        _PRESENCE_CACHE.clear()
    with _USERNAME_LOCK:
        _USERNAME_CACHE.clear()
