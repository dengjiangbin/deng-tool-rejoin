"""Safe Discord webhook helpers.

This module never handles Roblox credentials and masks webhook URLs anywhere
they may be displayed.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .url_utils import mask_launch_url

WEBHOOK_MODES = {"new_message", "edit_message"}
MIN_WEBHOOK_INTERVAL_SECONDS = 30
MASK = "***MASKED***"

# ─── Discord embed color constants ────────────────────────────────────────────
EMBED_COLOR_GREEN  = 0x57F287  # online / success
EMBED_COLOR_RED    = 0xED4245  # offline / error
EMBED_COLOR_YELLOW = 0xFEE75C  # warning / captcha alert
EMBED_COLOR_ORANGE = 0xE67E22  # starting / preparing
EMBED_COLOR_GREY   = 0x36393F  # neutral / unknown


class WebhookError(ValueError):
    """Raised for invalid webhook configuration."""


def validate_webhook_url(url: str | None) -> str:
    cleaned = (url or "").strip()
    if not cleaned:
        raise WebhookError("Discord webhook URL is required when webhook is enabled")
    parsed = urllib.parse.urlparse(cleaned)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in {"discord.com", "discordapp.com"}:
        raise WebhookError("Webhook URL must be a Discord https webhook URL")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4 or parts[:2] != ["api", "webhooks"]:
        raise WebhookError("Webhook URL must look like https://discord.com/api/webhooks/...")
    return cleaned


def mask_webhook_url(url: str | None) -> str:
    if not url:
        return ""
    parsed = urllib.parse.urlparse(str(url))
    host = parsed.netloc or "discord.com"
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 4 and parts[:2] == ["api", "webhooks"]:
        webhook_id = parts[2]
        visible_id = webhook_id[:4] + "..." if len(webhook_id) > 4 else webhook_id
        return urllib.parse.urlunparse((parsed.scheme or "https", host, f"/api/webhooks/{visible_id}/{MASK}", "", "", ""))
    return f"{parsed.scheme or 'https'}://{host}/api/webhooks/{MASK}"


def validate_webhook_interval(seconds: int) -> int:
    try:
        value = int(seconds)
    except (TypeError, ValueError) as exc:
        raise WebhookError("Webhook interval must be a number") from exc
    if value < MIN_WEBHOOK_INTERVAL_SECONDS:
        raise WebhookError("Webhook interval must be at least 30 seconds to avoid spam/rate limits")
    return value


def should_send_webhook(config_data: dict[str, Any], *, now: float | None = None) -> bool:
    if not config_data.get("webhook_enabled"):
        return False
    now = time.time() if now is None else now
    last = config_data.get("webhook_last_sent_at") or 0
    try:
        last_value = float(last)
    except (TypeError, ValueError):
        last_value = 0.0
    interval = validate_webhook_interval(config_data.get("webhook_interval_seconds", 300))
    return now - last_value >= interval


# ─── Embed builder helpers ────────────────────────────────────────────────────


def _format_uptime(start_iso: str | None) -> str:
    """Format uptime string like '1h 22m' from an ISO-8601 launch timestamp."""
    if not start_iso:
        return ""
    try:
        start = datetime.fromisoformat(start_iso)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        elapsed = max(0, int((datetime.now(timezone.utc) - start).total_seconds()))
        hours = elapsed // 3600
        minutes = (elapsed % 3600) // 60
        return f"{hours}h {minutes:02d}m"
    except (ValueError, TypeError):
        return ""


# Status category mapping (supervisor STATUS_* → display bucket)
_STATUS_CATEGORY: dict[str, str] = {
    "Online":        "online",
    "Ready":         "ready",
    "Preparing":     "preparing",
    "Launching":     "preparing",
    "Checking":      "preparing",
    "Warning":       "warning",
    "Reviving":      "warning",
    "Reconnecting":  "preparing",
    "Background":    "warning",
    "Unknown":       "warning",
    "Offline":       "offline",
    "Failed":        "failed",
    "Not installed": "failed",
    "Disabled":      "failed",
}


def build_status_embed_payload(
    config_data: dict[str, Any],
    *,
    event: str = "status",
    error: str | None = None,
    app_stats: dict[str, Any] | None = None,
    supervisor_snapshot: list[dict] | None = None,
) -> dict[str, Any]:
    """Build a Discord embed payload for periodic status updates."""
    from .config import mask_license_key
    from .license import get_public_device_model

    raw_packages = config_data.get("roblox_packages") or [config_data.get("roblox_package", "unknown")]
    entries: list[dict[str, str]] = []
    for item in raw_packages:
        if isinstance(item, dict):
            package = str(item.get("package") or "unknown")
            username = str(item.get("account_username") or item.get("label") or "").strip()
        else:
            package = str(item)
            username = ""
        entries.append({"package": package, "username": username})

    app_stats = app_stats or {}

    # Count status categories from supervisor_snapshot or fall back to app_stats
    counts: dict[str, int] = {cat: 0 for cat in ("online", "ready", "preparing", "warning", "offline", "failed")}
    if supervisor_snapshot:
        for snap in supervisor_snapshot:
            category = _STATUS_CATEGORY.get(snap.get("status", ""), "offline")
            counts[category] = counts.get(category, 0) + 1
        total = len(supervisor_snapshot)
    else:
        # Backward-compatible: use app_stats boolean
        for e in entries:
            if app_stats.get(e["package"], {}).get("online"):
                counts["online"] += 1
            else:
                counts["offline"] += 1
        total = len(entries)
    online_count = counts["online"]

    # Embed color
    if error:
        color = EMBED_COLOR_YELLOW
    elif online_count > 0:
        color = EMBED_COLOR_GREEN
    else:
        color = EMBED_COLOR_RED

    # System stats (injected by caller via _mem_info, _cpu_pct, _temp_c keys)
    mem_info: dict[str, int] = config_data.get("_mem_info") or {}
    cpu_pct = config_data.get("_cpu_pct")
    temp_c = config_data.get("_temp_c")
    sys_lines: list[str] = []
    if mem_info.get("free_mb"):
        sys_lines.append(f"💾 RAM: {mem_info['free_mb']}MB free ({mem_info.get('percent_free', 0)}%)")
    if cpu_pct is not None:
        sys_lines.append(f"⚙️ CPU: {cpu_pct:.0f}%")
    if temp_c is not None:
        sys_lines.append(f"🌡️ Temp: {temp_c}°C")
    sys_value = "\n".join(sys_lines) or "N/A"

    # Status overview — full 7-category breakdown
    overview_parts = [
        f"🟢 Online: {counts['online']}",
        f"🟡 Ready: {counts['ready']}",
        f"🔵 Preparing: {counts['preparing']}",
        f"🟠 Warning: {counts['warning']}",
        f"🔴 Offline: {counts['offline']}",
        f"❌ Failed: {counts['failed']}",
        f"🤖 Total: {total}",
    ]
    overview = "\n".join(overview_parts)

    # Per-app application details
    detail_lines: list[str] = []
    for e in entries:
        stats = app_stats.get(e["package"], {})
        indicator = "🟢" if stats.get("online") else "🔴"
        label = (e["username"] or "").strip() or "Unknown"
        detail_lines.append(f"{indicator} {label}")
        sub: list[str] = []
        uptime = _format_uptime(stats.get("uptime_start"))
        if uptime:
            sub.append(f"⏱️ {uptime}")
        mem = stats.get("memory_mb")
        if mem is not None:
            sub.append(f"💾 {int(mem)} MB")
        cpu = stats.get("cpu_pct")
        if cpu is not None:
            sub.append(f"⚡ {cpu:.1f}%")
        if sub:
            detail_lines.append("└ " + " | ".join(sub))
    detail_value = "\n".join(detail_lines) or "No accounts configured"

    # License + tags
    masked_key = mask_license_key(config_data.get("license_key", ""))
    webhook_tags = config_data.get("webhook_tags") or []
    tags_value = f"[{len(webhook_tags)}]"
    phone_type = get_public_device_model()
    host_name = str(config_data.get("device_name", "unknown"))
    device_value = host_name
    if phone_type and phone_type != "Unknown":
        device_value = f"{host_name}\n📱 Type: {phone_type}"

    fields: list[dict[str, Any]] = [
        {"name": "📱 Device",           "value": device_value,                               "inline": True},
        {"name": "🔑 License",          "value": masked_key,                                     "inline": True},
        {"name": "🏷️ Tags",             "value": tags_value,                                     "inline": True},
        {"name": "🖥️ System Stats",     "value": sys_value,                                      "inline": False},
        {"name": "Status Overview",     "value": overview,                                       "inline": False},
        {"name": "Application Details", "value": detail_value,                                   "inline": False},
    ]
    if error:
        fields.append({"name": "⚠️ Last Error", "value": error[:512], "inline": False})

    version = config_data.get("agent_version", "1.0.0")
    return {
        "username": "DENG Tool: Rejoin",
        "embeds": [
            {
                "title": "📊 DENG Status Monitor",
                "description": f"Event: **{event}**",
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "fields": fields,
                "footer": {"text": f"DENG Tool: Rejoin • v{version}"},
            }
        ],
    }


def build_alert_embed_payload(
    config_data: dict[str, Any],
    *,
    account: str,
    reason: str,
    event: str = "alert",
) -> dict[str, Any]:
    """Build an Account Alert embed payload (captcha, failures, etc.)."""
    from .config import mask_license_key
    from .license import get_public_device_model

    masked_key = mask_license_key(config_data.get("license_key", ""))
    webhook_tags = config_data.get("webhook_tags") or []
    tags_value = f"[{len(webhook_tags)}]"
    phone_type = get_public_device_model()
    host_name = str(config_data.get("device_name", "unknown"))
    device_value = host_name
    if phone_type and phone_type != "Unknown":
        device_value = f"{host_name}\n📱 Type: {phone_type}"

    reason_lower = reason.lower()
    if "solved" in reason_lower or "success" in reason_lower or "✅" in reason:
        color = EMBED_COLOR_GREEN
    elif "captcha" in reason_lower or "detect" in reason_lower:
        color = EMBED_COLOR_YELLOW
    else:
        color = EMBED_COLOR_ORANGE

    version = config_data.get("agent_version", "1.0.0")
    return {
        "username": "DENG Tool: Rejoin",
        "embeds": [
            {
                "title": "⚠️ Account Alert",
                "description": "Attention needed immediately.",
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "fields": [
                    {"name": "👤 Account", "value": account or "unknown",                              "inline": False},
                    {"name": "📱 Device",  "value": device_value, "inline": False},
                    {"name": "🔑 License", "value": masked_key,                                     "inline": True},
                    {"name": "🏷️ Tags",    "value": tags_value,                                     "inline": True},
                    {"name": "📝 Reason",  "value": reason[:512],                                    "inline": False},
                ],
                "footer": {"text": f"DENG Tool: Rejoin • v{version}"},
            }
        ],
    }


def build_status_message(config_data: dict[str, Any], *, event: str = "status", error: str | None = None) -> str:
    from .license import get_public_device_model

    raw_packages = config_data.get("roblox_packages") or [config_data.get("roblox_package", "unknown")]
    packages = []
    for entry in raw_packages:
        if isinstance(entry, dict):
            package = str(entry.get("package") or "unknown")
            username = str(entry.get("account_username") or entry.get("label") or "").strip()
            packages.append(f"{username or 'Unknown'} ({package})")
        else:
            packages.append(str(entry))
    launch_url = mask_launch_url(config_data.get("launch_url")) or "not set"
    phone_type = get_public_device_model()
    lines = [
        f"DENG Tool: Rejoin v{config_data.get('agent_version', '1.0.0')}",
        f"Event: {event}",
        f"Device: {config_data.get('device_name', 'unknown')}"
        + (f" | {phone_type}" if phone_type and phone_type != "Unknown" else ""),
        f"Android: {config_data.get('android_release', 'unknown')} / SDK {config_data.get('android_sdk', 'unknown')}",
        f"Roblox packages: {', '.join(packages)}",
        f"Launch link: {launch_url}",
        f"Auto rejoin: {'enabled' if config_data.get('auto_rejoin_enabled') else 'disabled'}",
        f"Root: available={config_data.get('root_available')} enabled={config_data.get('root_mode_enabled')}",
        "Auto resize: automatic",
    ]
    if error:
        lines.append(f"Last error: {error}")
    return "\n".join(lines)


def send_webhook_update(
    config_data: dict[str, Any],
    *,
    event: str = "status",
    snapshot_path: Path | None = None,
    force: bool = False,
    app_stats: dict[str, Any] | None = None,
    alert_account: str | None = None,
    alert_reason: str | None = None,
) -> tuple[bool, str, str | None]:
    """Send a status or alert update via Discord webhook embed.

    Returns ``(success, message, message_id)``.

    Pass ``alert_account`` and ``alert_reason`` to send an Account Alert embed
    instead of the standard Status Monitor embed.
    Pass ``app_stats`` (mapping package → stat-dict) to populate per-app fields.
    """
    if not config_data.get("webhook_enabled"):
        return False, "webhook disabled", None
    if not force and not should_send_webhook(config_data):
        return False, "webhook interval has not elapsed", None
    url = validate_webhook_url(config_data.get("webhook_url"))

    # Choose embed type
    if alert_account and alert_reason:
        payload = build_alert_embed_payload(
            config_data, account=alert_account, reason=alert_reason, event=event
        )
    else:
        payload = build_status_embed_payload(config_data, event=event, app_stats=app_stats)

    data: bytes
    headers: dict[str, str]
    if snapshot_path and snapshot_path.exists():
        boundary = f"deng-{int(time.time())}"
        file_bytes = snapshot_path.read_bytes()
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\nContent-Type: application/json\r\n\r\n{json.dumps(payload)}\r\n".encode("utf-8"),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"snapshot.png\"\r\nContent-Type: image/png\r\n\r\n".encode("utf-8"),
            file_bytes,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
        data = b"".join(parts)
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    else:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}

    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - validated Discord URL
            body = response.read().decode("utf-8", errors="replace")
            message_id = None
            if body:
                try:
                    message_id = json.loads(body).get("id")
                except json.JSONDecodeError:
                    message_id = None
            return 200 <= response.status < 300, f"discord webhook HTTP {response.status}", message_id
    except urllib.error.URLError as exc:
        return False, f"webhook failed: {exc}", None
