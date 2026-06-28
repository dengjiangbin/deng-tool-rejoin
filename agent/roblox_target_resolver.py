"""Resolve configured Roblox launch/deeplink targets into verifiable place/universe ids.

Share/private-server links often expose only an opaque ``code`` (no placeId in the
URL). Other tools resolve those via Roblox's share-links API — we do the same so
Wrong-Server detection can compare placeId/rootPlaceId/universeId even when the
user configured ``https://www.roblox.com/share?code=...&type=Server``.

Also enriches partial targets (placeId only → universeId) using public Roblox APIs.
Never logs or stores raw private codes beyond what url_utils already parsed.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from typing import Any

from . import safe_http
from .url_utils import RobloxExpectedTarget

_log = logging.getLogger("deng.rejoin.roblox_target_resolver")

_RESOLVE_URL = "https://apis.roblox.com/sharelinks/v1/resolve-link"
_UNIVERSE_URL = "https://apis.roblox.com/universes/v1/places/{place_id}/universe"
_USER_AGENT = "DENG-Tool-Rejoin/1.0 (+target-resolve)"
_HTTP_TIMEOUT = 12.0
_CACHE_TTL = 3600.0

_RESOLVE_LOCK = threading.Lock()
_RESOLVE_CACHE: dict[str, tuple[float, RobloxExpectedTarget]] = {}


def _cache_key(code: str, link_type: str) -> str:
    return f"{str(link_type or '').strip().lower()}:{str(code or '').strip()}"


def _post_json(url: str, body: dict[str, Any], *, cookie: str | None = None) -> dict[str, Any] | None:
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
    }
    if cookie:
        headers["Cookie"] = f".ROBLOSECURITY={cookie.lstrip()}"
    try:
        return safe_http.post_json(url, body, headers=headers, timeout=_HTTP_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        _log.debug("target resolve POST failed: %s", exc)
        return None


def _get_json(url: str) -> dict[str, Any] | None:
    try:
        return safe_http.get_json(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=_HTTP_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("target resolve GET failed: %s", exc)
        return None


def _dig_place_universe(block: object) -> tuple[int | None, int | None]:
    if not isinstance(block, dict):
        return None, None
    place = block.get("placeId") or block.get("place_id") or block.get("rootPlaceId")
    universe = block.get("universeId") or block.get("universe_id")
    try:
        place_id = int(place) if place not in (None, "", 0) else None
    except (TypeError, ValueError):
        place_id = None
    try:
        universe_id = int(universe) if universe not in (None, "", 0) else None
    except (TypeError, ValueError):
        universe_id = None
    return place_id, universe_id


def resolve_share_link(
    link_code: str,
    link_type: str = "Server",
    *,
    cookie: str | None = None,
) -> RobloxExpectedTarget | None:
    """Resolve a Roblox share-link ``code`` (+ ``type``) to place/universe ids.

    Uses ``POST /sharelinks/v1/resolve-link``. Works anonymously for many Server
    links; authenticated cookie improves success rate when Roblox requires it."""
    code = str(link_code or "").strip()
    ltype = str(link_type or "Server").strip() or "Server"
    if not code:
        return None
    cache_key = _cache_key(code, ltype)
    now = time.time()
    with _RESOLVE_LOCK:
        cached = _RESOLVE_CACHE.get(cache_key)
        if cached and (now - cached[0]) < _CACHE_TTL:
            return cached[1]

    payloads = [
        {"linkId": code, "linkType": ltype},
        {"linkCode": code, "linkType": ltype},
        {"code": code, "type": ltype},
        {"linkId": code, "linkType": ltype.upper()},
    ]
    place_id: int | None = None
    universe_id: int | None = None
    for body in payloads:
        data = _post_json(_RESOLVE_URL, body, cookie=cookie)
        if not data:
            continue
        for key in (
            "privateServerInviteData",
            "experienceDetailsInviteData",
            "notificationExperienceInviteData",
            "experienceInviteData",
            "experienceAffiliateData",
        ):
            p, u = _dig_place_universe(data.get(key))
            if p:
                place_id = p
            if u:
                universe_id = u
        if place_id or universe_id:
            break

    if not place_id and not universe_id:
        return None

    result = RobloxExpectedTarget(
        original_url="",
        expected_place_id=place_id,
        expected_root_place_id=place_id,
        expected_universe_id=universe_id,
        expected_private_code=code,
        expected_share_type=ltype,
    )
    with _RESOLVE_LOCK:
        _RESOLVE_CACHE[cache_key] = (now, result)
    return result


def resolve_universe_for_place(place_id: int) -> int | None:
    """Public API: placeId → universeId (GetPlayerPlaceInstanceAsync-adjacent metadata)."""
    try:
        pid = int(place_id)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    data = _get_json(_UNIVERSE_URL.format(place_id=pid))
    if not data:
        return None
    universe = data.get("universeId") or data.get("universeID")
    try:
        uid = int(universe)
    except (TypeError, ValueError):
        return None
    return uid if uid > 0 else None


def enrich_expected_target(
    target: RobloxExpectedTarget | None,
    *,
    cookie: str | None = None,
) -> RobloxExpectedTarget:
    """Fill missing place/universe fields from share-link resolve + universe API."""
    base = target or RobloxExpectedTarget()
    place_id = base.expected_place_id
    root_place_id = base.expected_root_place_id
    universe_id = base.expected_universe_id
    private_code = base.expected_private_code
    share_type = base.expected_share_type or "Server"

    if not place_id and private_code:
        resolved = resolve_share_link(private_code, share_type, cookie=cookie)
        if resolved:
            place_id = place_id or resolved.expected_place_id
            root_place_id = root_place_id or resolved.expected_root_place_id
            universe_id = universe_id or resolved.expected_universe_id

    if place_id and not universe_id:
        universe_id = resolve_universe_for_place(int(place_id))

    if (
        place_id == base.expected_place_id
        and root_place_id == base.expected_root_place_id
        and universe_id == base.expected_universe_id
    ):
        return base

    return replace(
        base,
        expected_place_id=place_id,
        expected_root_place_id=root_place_id or place_id,
        expected_universe_id=universe_id,
        expected_share_type=share_type,
    )


def presence_matches_target(
    presence: Any,
    target: RobloxExpectedTarget | None,
) -> tuple[bool, str]:
    """Compare Roblox Presence API fields to configured target (TeleportService-like).

    Returns (matched, detail_reason). Fail-safe: missing expected fields → matched.
    """
    if target is None:
        return True, "no_expected_target"
    expected_pairs = [
        ("place", target.expected_place_id, getattr(presence, "place_id", None)),
        ("root_place", target.expected_root_place_id, getattr(presence, "root_place_id", None)),
        ("universe", target.expected_universe_id, getattr(presence, "universe_id", None)),
    ]
    known = [(name, exp, got) for name, exp, got in expected_pairs if exp is not None]
    if not known:
        return True, "partial_expected_target"
    mismatches = [
        f"{name}:{exp}!={got}"
        for name, exp, got in known
        if got is not None and int(got) != int(exp)
    ]
    if mismatches:
        return False, "presence_" + ",".join(mismatches)
    return True, "presence_target_match"
