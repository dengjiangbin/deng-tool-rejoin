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
from pathlib import Path
from typing import Any

from .url_utils import mask_launch_url

WEBHOOK_MODES = {"new_message", "edit_message"}
MIN_WEBHOOK_INTERVAL_SECONDS = 30
MASK = "***MASKED***"


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


def build_status_message(config_data: dict[str, Any], *, event: str = "status", error: str | None = None) -> str:
    raw_packages = config_data.get("roblox_packages") or [config_data.get("roblox_package", "unknown")]
    packages = []
    for entry in raw_packages:
        if isinstance(entry, dict):
            package = str(entry.get("package") or "unknown")
            username = str(entry.get("account_username") or entry.get("label") or "").strip()
            packages.append(f"{username} ({package})" if username else f"Username not set ({package})")
        else:
            packages.append(str(entry))
    launch_url = mask_launch_url(config_data.get("launch_url")) or "not set"
    lines = [
        f"DENG Tool: Rejoin v{config_data.get('agent_version', '1.0.0')}",
        f"Event: {event}",
        f"Device: {config_data.get('device_name', 'unknown')}",
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


def send_webhook_update(config_data: dict[str, Any], *, event: str = "status", snapshot_path: Path | None = None, force: bool = False) -> tuple[bool, str, str | None]:
    """Send a safe status update. Returns (success, message, message_id)."""
    if not config_data.get("webhook_enabled"):
        return False, "webhook disabled", None
    if not force and not should_send_webhook(config_data):
        return False, "webhook interval has not elapsed", None
    url = validate_webhook_url(config_data.get("webhook_url"))
    content = build_status_message(config_data, event=event)
    payload = {"content": content}
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
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - user-configured Discord URL is validated.
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
