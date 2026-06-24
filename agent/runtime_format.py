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
