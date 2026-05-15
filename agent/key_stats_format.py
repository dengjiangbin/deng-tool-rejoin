"""Formatting helpers for Discord Key Stats (no Discord imports)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def relative_time_ago(iso_str: str | None) -> str | None:
    """Human-readable relative time like '3 days ago', or None if unparseable."""
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


def format_stats_embed_title(*, total: int, page: int, total_pages: int) -> str:
    return f"Your License Keys (Total: {total} | Page {page + 1}/{total_pages})"


def format_key_block(row: dict[str, Any]) -> str:
    """One key section for embed description (markdown)."""
    lines: list[str] = ["**License Key**", ""]

    full = row.get("full_key_plaintext")
    masked = row.get("masked_key") or "???"
    has_stored_ciphertext = bool(row.get("has_stored_ciphertext"))

    if full:
        lines.append(f"**Key:** `{full}`")
    else:
        lines.append(f"**Key:** `{masked}`")
        lines.append("**Full Key:** Not Available For Old Hashed Key")
        if not has_stored_ciphertext:
            lines.append(
                "_Full key export is available only for keys generated after export storage was enabled "
                "and when LICENSE_KEY_EXPORT_SECRET is configured._"
            )

    lic = (row.get("license_status") or "active").lower()
    used = bool(row.get("used"))
    if lic == "revoked":
        lines.append("**Status:** Revoked")
    elif lic == "expired":
        lines.append("**Status:** Expired")
    else:
        lines.append("**Status:** Used" if used else "**Status:** Unused")

    created = row.get("created_at")
    rel_c = relative_time_ago(created) if created else None
    if rel_c:
        lines.append(f"**Created:** {rel_c}")
    elif created:
        lines.append(f"**Created:** {created[:10]}")

    device = row.get("device_display")
    show_device = device and used and lic not in {"revoked", "expired"}
    if show_device:
        lines.append(f"**Device:** {device}")

    last_seen = row.get("last_seen_at")
    if used and lic not in {"revoked", "expired"}:
        if last_seen:
            rel = relative_time_ago(last_seen)
            lines.append(f"**Last Active:** {rel or last_seen[:19]}")
        else:
            lines.append("**Last Active:** Never")

    tags = row.get("tags_label")
    if tags:
        lines.append(f"**Tags:** {tags}")

    return "\n".join(lines)


def build_key_stats_description(rows_slice: list[dict[str, Any]]) -> str:
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
        status_word = "Revoked" if lic == "revoked" else ("Expired" if lic == "expired" else ("Used" if used else "Unused"))

        if full:
            body.append(f"{i}. {full} - {status_word}")
        else:
            body.append(f"{i}. {masked} - {status_word}")
            body.append("   Full Key: Not Available For Old Hashed Key")

        created = row.get("created_at")
        if created:
            rel_c = relative_time_ago(created)
            body.append(f"   Created: {rel_c or created[:10]}")

        device = row.get("device_display")
        if device and used and lic not in {"revoked", "expired"}:
            body.append(f"   Device: {device}")

        last_seen = row.get("last_seen_at")
        if used and lic not in {"revoked", "expired"}:
            if last_seen:
                rel = relative_time_ago(last_seen)
                body.append(f"   Last Active: {rel or last_seen[:19]}")
            else:
                body.append("   Last Active: Never")

        tags = row.get("tags_label")
        if tags:
            body.append(f"   Tags: {tags}")

        body.append("")

    return "\n".join(header + body).rstrip() + "\n"
