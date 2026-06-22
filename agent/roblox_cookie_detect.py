"""Backward-compatible shim — production code must import from ``agent.roblox_presence``.

Release tarballs exclude this filename (artifact packer skips paths containing
``cookie``).  The real implementation lives in :mod:`agent.roblox_presence`.
"""

from __future__ import annotations

from agent.roblox_presence import (  # noqa: F401
    cookie_from_pref_xml,
    detect_roblox_cookie,
    looks_like_roblox_cookie,
    roblox_cookie_detect,
)

__all__ = [
    "cookie_from_pref_xml",
    "detect_roblox_cookie",
    "looks_like_roblox_cookie",
    "roblox_cookie_detect",
]
