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


def format_stats_copy_block_for_slice(rows_slice: list[dict[str, Any]]) -> str:
    from agent.license_panel import format_copy_license_keys_lines

    keys = [str(r["full_key_plaintext"]) for r in rows_slice if r.get("full_key_plaintext")]
    return format_copy_license_keys_lines(keys)


def format_stats_page_content_header(
    rows_slice: list[dict[str, Any]], *, total: int, page: int, total_pages: int
) -> str:
    """Message content: just the page header. Keys are shown inside the embed (no top copy block)."""
    return format_stats_header_plain(total=total, page=page, total_pages=total_pages)


def format_stats_embed_title(*, total: int, page: int, total_pages: int) -> str:
    """Deprecated: use :func:`format_stats_header_plain` for Key Stats."""
    return format_stats_header_plain(total=total, page=page, total_pages=total_pages)


def build_key_stats_embed_dict(row: dict[str, Any], *, number: int = 0) -> dict[str, Any]:
    """One Discord embed dict for a single license key row.

    The key itself is shown as a numbered line inside the embed description.
    Device is combined into the status line. No separate top 'Copy License Key:' block.
    """
    full = row.get("full_key_plaintext")
    masked = row.get("masked_key") or "???"

    lic = (row.get("license_status") or "active").lower()
    used = bool(row.get("used"))

    lines: list[str] = []

    # --- Key display (numbered) ---
    prefix = f"{number}. " if number > 0 else ""
    if full:
        lines.append(f"{prefix}`{full}`")
    elif lic in {"revoked", "expired"}:
        lines.append(f"{prefix}**{masked}** *(reference only, not copyable)*")
    else:
        # Show masked reference and note that the full key is not recoverable
        lines.append(f"{prefix}**{masked}** *(full key not recoverable)*")

    # --- Status line (device combined) ---
    device = row.get("device_display") if used and lic not in {"revoked", "expired"} else None

    if lic == "revoked":
        lines.append("Status: 🔴 Revoked")
        color = COLOR_STATS_BAD
    elif lic == "expired":
        lines.append("Status: 🔴 Expired")
        color = COLOR_STATS_BAD
    elif used:
        if device:
            lines.append(f"Status: Used / Device bound on {device}")
        else:
            lines.append("Status: Used / Device bound")
        color = COLOR_STATS_USED
    else:
        lines.append("Status: Unused / No device linked")
        color = COLOR_STATS_UNUSED

    # --- Last active ---
    if used and lic not in {"revoked", "expired"}:
        last_seen = row.get("last_seen_at")
        if last_seen:
            rel = relative_time_ago(last_seen)
            lines.append(f"Last Active: {rel or last_seen[:19]}")
        else:
            lines.append("Last Active: Never")

    return {
        "description": "\n".join(lines),
        "color": color,
        "footer": {"text": "DENG Tool \u00b7 Key Stats"},
    }


def build_key_stats_embed_dicts(rows_slice: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [build_key_stats_embed_dict(r, number=i) for i, r in enumerate(rows_slice, start=1)]


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


def _iso_expired(iso_str: str | None) -> bool:
    if not iso_str:
        return False
    try:
        normalized = str(iso_str).replace("Z", "+00:00")
        exp_dt = datetime.fromisoformat(normalized)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > exp_dt
    except (ValueError, TypeError):
        return False


def _license_row_status(row: dict[str, Any]) -> str:
    return str(row.get("license_status") or row.get("status") or "active").lower()


def _license_row_is_bound(row: dict[str, Any]) -> bool:
    if row.get("used") or row.get("active_binding"):
        return True
    device = (row.get("device_display") or row.get("bound_device") or "").strip()
    return bool(device and device != "(unbound)")


def is_active_visible_license_row(row: dict[str, Any]) -> bool:
    """Return True when a key belongs in authorized admin Keys lists."""
    status = _license_row_status(row)
    if status in {"revoked", "expired", "deleted", "disabled"}:
        return False
    if row.get("is_deleted") or row.get("deleted"):
        return False
    if row.get("is_disabled") or row.get("disabled"):
        return False
    if row.get("is_hidden") or row.get("archived") or row.get("hidden"):
        return False
    if row.get("redeemed_at") or _license_row_is_bound(row):
        return True
    if status != "active":
        return False
    if _iso_expired(row.get("expires_at")):
        return False
    return True


def filter_active_visible_license_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if is_active_visible_license_row(row)]


def compute_active_visible_stats(active_rows: list[dict[str, Any]]) -> dict[str, int]:
    generated = len(active_rows)
    bound = sum(1 for row in active_rows if _license_row_is_bound(row))
    redeemed = sum(
        1 for row in active_rows if row.get("redeemed_at") or _license_row_is_bound(row)
    )
    return {
        "key_generated_count": generated,
        "key_redeemed_count": redeemed,
        "unbound_key_count": max(0, generated - bound),
        "bound_key_count": bound,
    }


def authorized_full_license_key(row: dict[str, Any]) -> str | None:
    """Return full key for authorized Discord admin output, never masked."""
    full = row.get("full_key_plaintext") or row.get("full_key")
    if not full:
        return None
    text = str(full).strip()
    if "..." in text or "…" in text:
        return None
    return text


def format_authorized_active_key_line(row: dict[str, Any]) -> str:
    full = authorized_full_license_key(row)
    if not full:
        raise ValueError("authorized admin output requires a full license key")
    if _license_row_is_bound(row):
        device = (row.get("device_display") or row.get("bound_device") or "").strip()
        device_label = device if device and device != "(unbound)" else "(bound)"
    else:
        device_label = "(unbound)"
    return f"{full} — active — {device_label}"


def build_reset_hwid_log_description(
    *,
    user_mention: str,
    reset_key: str,
    stats: dict[str, Any],
) -> str:
    return "\n".join(
        [
            f"**User:** {user_mention}",
            f"**Reset Key:** {reset_key}",
            f"**Current Key Generated:** {stats['key_generated_count']}",
            f"**Current Key Redeemed:** {stats['key_redeemed_count']}",
            f"**Current Unbound Key:** {stats['unbound_key_count']}",
            f"**Current Bound Key:** {stats['bound_key_count']}",
            f"**Current Reset HWID:** {stats['reset_hwid_count']} times",
        ]
    )


def build_license_event_log_description(
    *,
    user_mention: str,
    key_field_label: str,
    key_value: str,
    stats: dict[str, Any],
) -> str:
    return "\n".join(
        [
            f"**User:** {user_mention}",
            f"**{key_field_label}:** {key_value}",
            f"**Current Key Generated:** {stats['key_generated_count']}",
            f"**Current Key Redeemed:** {stats['key_redeemed_count']}",
            f"**Current Unbound Key:** {stats['unbound_key_count']}",
            f"**Current Bound Key:** {stats['bound_key_count']}",
            f"**Current Reset HWID:** {stats['reset_hwid_count']} times",
        ]
    )


def build_license_admin_stats_description(
    *,
    user_label: str,
    stats: dict[str, Any],
    active_rows: list[dict[str, Any]],
) -> str:
    lines = [
        f"**User:** {user_label}",
        f"**Generated (Active):** {stats['key_generated_count']}",
        f"**Redeemed:** {stats['key_redeemed_count']}",
        f"**Unbound:** {stats['unbound_key_count']}",
        f"**Bound:** {stats['bound_key_count']}",
        f"**HWID Resets:** {stats['reset_hwid_count']} times",
        f"**Key Executed (Public):** {stats.get('key_executed_count', 0)}",
        "",
        f"**Keys ({len(active_rows)})**",
    ]
    if active_rows:
        lines.extend(format_authorized_active_key_line(row) for row in active_rows)
    else:
        lines.append("No active keys.")
    return "\n".join(lines)


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
