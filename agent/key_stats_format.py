"""Formatting helpers for Discord Key Stats (no Discord imports)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Embed colors (Discord integer colors)
COLOR_STATS_UNUSED = 0x27AE60
COLOR_STATS_USED = 0x2F80ED
COLOR_STATS_BAD = 0xE74C3C


def relative_time_ago(iso_str: str | None) -> str | None:
    """Human-readable relative time like '3 day(s) ago', or None if unparseable."""
    if not iso_str:
        return None
    try:
        text = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        secs = int(max(0, delta.total_seconds()))
        if secs < 60:
            return "just now"
        mins = secs // 60
        if mins < 60:
            return f"{mins} minute(s) ago" if mins != 1 else "1 minute ago"
        hours = mins // 60
        if hours < 24:
            return f"{hours} hour(s) ago" if hours != 1 else "1 hour ago"
        days = hours // 24
        if days < 30:
            return f"{days} day(s) ago" if days != 1 else "1 day ago"
        months = days // 30
        if months < 12:
            return f"{months} month(s) ago" if months != 1 else "1 month ago"
        years = months // 12
        return f"{years} year(s) ago" if years != 1 else "1 year ago"
    except (ValueError, TypeError):
        return None


def format_stats_header_plain(*, total: int, page: int, total_pages: int) -> str:
    """Plain (non-embed) header line for Key Stats messages."""
    return f"Your License Keys (Total: {total} | Page {page + 1}/{total_pages})"


def format_stats_embed_title(*, total: int, page: int, total_pages: int) -> str:
    """Deprecated: use :func:`format_stats_header_plain` for Key Stats."""
    return format_stats_header_plain(total=total, page=page, total_pages=total_pages)


def build_key_stats_embed_dict(row: dict[str, Any]) -> dict[str, Any]:
    """One Discord embed dict for a single license key row (compact description)."""
    full = row.get("full_key_plaintext")
    masked = row.get("masked_key") or "???"

    lic = (row.get("license_status") or "active").lower()
    used = bool(row.get("used"))
    exp_cfg = bool(row.get("export_storage_configured", False))

    lines: list[str] = []

    if full:
        lines.append(f"Key: `{full}`")
    elif lic in {"revoked", "expired"}:
        lines.append(f"Key reference (not copyable): {masked}")
    else:
        lines.append(
            "**Full key is not recoverable for copying from the server.** "
            "If this is an older key, export storage was not enabled when it was created."
        )
        lines.append(f"Reference only (not a complete key): {masked}")

    if lic == "revoked":
        lines.append("Status: 🔴 Revoked")
        color = COLOR_STATS_BAD
    elif lic == "expired":
        lines.append("Status: 🔴 Expired")
        color = COLOR_STATS_BAD
    elif used:
        lines.append("Status: Used / Device bound")
        color = COLOR_STATS_USED
    else:
        lines.append("Status: Unused / Ready for first device")
        color = COLOR_STATS_UNUSED

    if used and lic not in {"revoked", "expired"}:
        device = row.get("device_display")
        if device:
            lines.append(f"Device: {device}")
        last_seen = row.get("last_seen_at")
        if last_seen:
            rel = relative_time_ago(last_seen)
            lines.append(f"Last Active: {rel or last_seen[:19]}")
        else:
            lines.append("Last Active: Never")

    if (
        not full
        and lic not in {"revoked", "expired"}
        and not exp_cfg
    ):
        lines.append("Export: full key storage is not enabled on this server.")

    return {
        "description": "\n".join(lines),
        "color": color,
        "footer": {"text": "DENG Tool \u00b7 Key Stats"},
    }


def build_key_stats_embed_dicts(rows_slice: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [build_key_stats_embed_dict(r) for r in rows_slice]


def build_key_stats_empty_embed_dict() -> dict[str, Any]:
    return {
        "description": (
            "You do not have any license keys yet.\n"
            "Click **Generate Key** on the panel to create one."
        ),
        "color": COLOR_STATS_USED,
        "footer": {"text": "DENG Tool \u00b7 Key Stats"},
    }


def format_key_block(row: dict[str, Any]) -> str:
    """Legacy: flatten one key to markdown (tests / introspection only)."""
    d = build_key_stats_embed_dict(row)
    return d.get("description") or ""


def build_key_stats_description(rows_slice: list[dict[str, Any]]) -> str:
    """Legacy: join per-key descriptions (avoid for Discord; use embeds instead)."""
    if not rows_slice:
        return ""
    parts = [format_key_block(r) for r in rows_slice]
    return "\n\n".join(parts)


def build_key_stats_download_body(*, discord_user_id: str, rows: list[dict[str, Any]]) -> str:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S UTC")
    header = [
        f"License Keys For User ID: {discord_user_id}",
        f"Generated: {now}",
        f"Total Keys: {len(rows)}",
        "=" * 43,
        "",
    ]
    body: list[str] = []
    for i, row in enumerate(rows, start=1):
        full = row.get("full_key_plaintext")
        masked = row.get("masked_key") or "???"
        used = bool(row.get("used"))
        lic = (row.get("license_status") or "active").lower()

        if lic == "revoked":
            status_word = "Revoked"
        elif lic == "expired":
            status_word = "Expired"
        elif used:
            status_word = "Used / Device bound"
        else:
            status_word = "Unused / Ready for first device"

        if full:
            body.append(f"{i}. {full} - {status_word}")
        else:
            body.append(f"{i}. [Full key not available for copy] - {status_word}")
            if lic not in {"revoked", "expired"}:
                body.append(
                    "   Full key is not recoverable for this key because export storage "
                    "was not enabled when it was created (or ciphertext is missing)."
                )
            else:
                body.append(f"   Reference: {masked}")

        if device := row.get("device_display"):
            if used and lic not in {"revoked", "expired"}:
                body.append(f"   Device: {device}")

        last_seen = row.get("last_seen_at")
        if used and lic not in {"revoked", "expired"}:
            if last_seen:
                rel = relative_time_ago(last_seen)
                body.append(f"   Last Active: {rel or last_seen[:19]}")
            else:
                body.append("   Last Active: Never")

        body.append("")

    return "\n".join(header + body).rstrip() + "\n"
