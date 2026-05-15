"""Who may use fixed internal test installers (e.g. GET /install/test/latest).

Uses env lists only — no discord.py imports (safe for license API).
"""

from __future__ import annotations

import os
from functools import lru_cache


@lru_cache(maxsize=4)
def _parse_env_discord_ids_cached(raw_norm: str) -> frozenset[str]:
    out: set[str] = set()
    for part in raw_norm.split(","):
        p = part.strip()
        if p.isdigit():
            out.add(p)
    return frozenset(out)


def parse_env_discord_ids(raw: str | None) -> frozenset[str]:
    """Comma-separated numeric Discord IDs from an env-style string."""
    return _parse_env_discord_ids_cached(raw or "")


def internal_test_install_allowlisted_discord_ids() -> frozenset[str]:
    """Union of LICENSE_OWNER_DISCORD_IDS and REJOIN_TESTER_DISCORD_IDS."""
    owners = parse_env_discord_ids(os.environ.get("LICENSE_OWNER_DISCORD_IDS"))
    testers = parse_env_discord_ids(os.environ.get("REJOIN_TESTER_DISCORD_IDS"))
    return frozenset(owners | testers)


def is_internal_test_install_allowed(owner_discord_id: str | None) -> bool:
    """True if *owner_discord_id* may authorize ``requested_version=test-latest``."""
    if owner_discord_id is None:
        return False
    oid = str(owner_discord_id).strip()
    if not oid.isdigit():
        return False
    return oid in internal_test_install_allowlisted_discord_ids()


def clear_install_internal_access_cache() -> None:
    """For tests only — reset :func:`lru_cache` on env parsers."""
    _parse_env_discord_ids_cached.cache_clear()
