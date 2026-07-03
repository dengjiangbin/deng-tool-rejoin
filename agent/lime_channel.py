"""Channel gating for Lime-style detection (test/latest2 only).

Lime detection must never activate on stable releases or test/latest (main-dev).
"""

from __future__ import annotations

import os

_LIME_CHANNELS = frozenset(
    {
        "test-latest2",
        "test_latest2",
        "test/latest2",
    }
)


def is_lime_detection_channel(channel: str | None = None) -> bool:
    """True only for the isolated test/latest2 channel."""
    if os.environ.get("DENG_REJOIN_FORCE_LIME", "").strip().lower() in {"1", "true", "yes"}:
        return True
    if os.environ.get("DENG_REJOIN_DISABLE_LIME_SPEED", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return False
    if channel is None:
        try:
            from .license import installed_channel

            channel = installed_channel()
        except Exception:  # noqa: BLE001
            channel = ""
    ch = str(channel or "").strip().lower()
    return ch in {c.lower() for c in _LIME_CHANNELS}


def lime_detection_enabled() -> bool:
    return is_lime_detection_channel()
