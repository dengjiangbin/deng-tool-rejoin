"""Central owner-ID guard for the DENG Tool: Rejoin license panel bot."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

OWNER_ENV_VAR = "LICENSE_OWNER_DISCORD_IDS"


@lru_cache(maxsize=4)
def _parse_ids_cached(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for part in raw.split(","):
        p = part.strip()
        if p.isdigit():
            ids.add(int(p))
    return frozenset(ids)


def parse_owner_discord_ids() -> frozenset[int]:
    """Parse LICENSE_OWNER_DISCORD_IDS into a frozenset of numeric Discord user IDs."""
    return _parse_ids_cached(os.environ.get(OWNER_ENV_VAR, "") or "")


def owner_guard_enabled() -> bool:
    """True when at least one owner Discord ID is configured."""
    return len(parse_owner_discord_ids()) > 0


def is_bot_owner(user: Any) -> bool:
    """True if *user* (discord.User/Member-like with .id) is a configured owner."""
    if user is None:
        return False
    uid = getattr(user, "id", None)
    if uid is None:
        return False
    try:
        return int(uid) in parse_owner_discord_ids()
    except (TypeError, ValueError):
        return False
