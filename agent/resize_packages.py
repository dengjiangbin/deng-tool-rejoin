"""Trusted package list for resize — uses existing config identity only."""

from __future__ import annotations

from typing import Any

from .config import enabled_package_entries
from .window_layout import layout_exclusion_reason


def get_trusted_resize_packages(
    cfg: dict[str, Any],
    entries: list[dict[str, Any]] | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    """Return packages eligible for resize from our own config system.

    Never scans ``pm list packages``, prefix files, or third-party lists.
    """
    source = entries if entries is not None else enabled_package_entries(cfg)
    trusted: list[str] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in source:
        if not isinstance(entry, dict):
            continue
        if entry.get("enabled", True) is False:
            continue
        pkg = str(entry.get("package") or "").strip()
        if not pkg or pkg in seen:
            continue
        reason = layout_exclusion_reason(pkg)
        if reason:
            skipped.append({"package": pkg, "reason": reason})
            continue
        seen.add(pkg)
        trusted.append(pkg)
    return trusted, skipped
