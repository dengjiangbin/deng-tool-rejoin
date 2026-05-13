"""Exponential backoff helper."""

from __future__ import annotations

from .constants import MAX_BACKOFF_SECONDS, MIN_BACKOFF_SECONDS


def calculate_backoff_seconds(failure_count: int, min_seconds: int = MIN_BACKOFF_SECONDS, max_seconds: int = MAX_BACKOFF_SECONDS) -> int:
    """Return capped exponential backoff seconds for a consecutive failure count."""
    failure_count = max(0, int(failure_count))
    min_seconds = max(MIN_BACKOFF_SECONDS, int(min_seconds))
    max_seconds = min(MAX_BACKOFF_SECONDS, max(min_seconds, int(max_seconds)))
    if failure_count <= 0:
        return min_seconds
    delay = min_seconds * (2 ** (failure_count - 1))
    return min(max(delay, min_seconds), max_seconds)
