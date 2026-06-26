"""Shared compact elapsed-time renderer for terminal and webhook monitoring."""

from __future__ import annotations


def format_runtime_compact(seconds: float) -> str:
    """Render the largest elapsed unit plus one smaller unit."""
    total = max(0, int(seconds))
    days, remainder = divmod(total, 86_400)
    if days:
        return f"{days}D {remainder // 3_600}H"
    hours, remainder = divmod(total, 3_600)
    if hours:
        return f"{hours}H {remainder // 60}m"
    minutes, remainder = divmod(total, 60)
    if minutes:
        return f"{minutes}m {remainder}s"
    return f"{total}s"


def format_lifecycle_dead_runtime(seconds: float | None) -> str | None:
    """Human-readable Package Dead runtime — maximum two units, no filler words."""
    if seconds is None:
        return None
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    if total < 3_600:
        minutes, secs = divmod(total, 60)
        return f"{minutes}m {secs}s"
    if total < 86_400:
        hours, remainder = divmod(total, 3_600)
        minutes = remainder // 60
        return f"{hours}h {minutes:02d}m"
    days, remainder = divmod(total, 86_400)
    hours = remainder // 3_600
    return f"{days}d {hours:02d}h"
