"""Safe Discord webhook helpers.

This module never handles Roblox credentials and masks webhook URLs anywhere
they may be displayed.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .url_utils import mask_launch_url
from . import safe_http
from .constants import CONFIG_PATH, DATA_DIR
from .runtime_format import format_lifecycle_dead_runtime, format_runtime_compact

WEBHOOK_MODES = {"edit", "new_post", "none"}
MIN_WEBHOOK_INTERVAL_MINUTES = 5
MAX_WEBHOOK_INTERVAL_MINUTES = 1_440
MASK = "***MASKED***"

# ─── Discord embed color constants ────────────────────────────────────────────
EMBED_COLOR_GREEN  = 0x57F287  # online / success
EMBED_COLOR_RED    = 0xED4245  # offline / error
EMBED_COLOR_YELLOW = 0xFEE75C  # warning / captcha alert
EMBED_COLOR_ORANGE = 0xE67E22  # starting / preparing
EMBED_COLOR_GREY   = 0x36393F  # neutral / unknown
WEBHOOK_USERNAME = "DENG Tool Rejoin"
WEBHOOK_AVATAR_URL = "https://aio.deng.my.id/public/img/deng-logo.png"
EMBED_TITLE = "📊 DENG Tool: Rejoin Status Monitor"
EMBED_URL = "https://aio.deng.my.id"
EMBED_FOOTER_TEXT = "DENG Tool: Rejoin"


def record_webhook_trace(**fields: Any) -> None:
    """Persist redacted installed-flow markers for probe consumption."""
    record = {"timestamp": time.time(), "pid": os.getpid(), **fields}
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / "webhook-trace.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass


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


def _webhook_url_fingerprint(url: str | None) -> str:
    cleaned = str(url or "").strip()
    if not cleaned:
        return ""
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]


def _redact_message_id(message_id: Any) -> str:
    text = str(message_id or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}...{text[-4:]}"


def _sync_edit_state_for_url(config_data: dict[str, Any], url: str) -> None:
    fingerprint = _webhook_url_fingerprint(url)
    old = str(config_data.get("webhook_url_fingerprint") or "")
    if old and old != fingerprint:
        config_data["webhook_last_message_id"] = ""
        config_data["webhook_message_id"] = ""
    config_data["webhook_url_fingerprint"] = fingerprint


def _persist_webhook_edit_state(config_data: dict[str, Any], *, url: str, message_id: str | None = None) -> bool:
    """Persist edit-mode message state to the installed config file."""
    _sync_edit_state_for_url(config_data, url)
    if message_id:
        config_data["webhook_last_message_id"] = str(message_id)
        config_data["webhook_message_id"] = str(message_id)
    record_webhook_trace(source="send_periodic_status", state_write_path=str(CONFIG_PATH))
    try:
        from .config import save_config

        saved = save_config(config_data, CONFIG_PATH)
        config_data.update(saved)
        ok = True
    except Exception as exc:  # noqa: BLE001 - tracing must explain persistence failures
        ok = False
        record_webhook_trace(
            source="send_periodic_status",
            state_write_ok=False,
            last_exception_type=type(exc).__name__,
            last_exception_message_redacted=_redact_exception(exc),
        )
    else:
        record_webhook_trace(
            source="send_periodic_status",
            state_write_ok=True,
            state_message_id_present=bool(config_data.get("webhook_last_message_id")),
            state_message_id_redacted=_redact_message_id(config_data.get("webhook_last_message_id")),
        )
    return ok


def validate_webhook_interval(minutes: int) -> int:
    try:
        if isinstance(minutes, str) and not minutes.strip().isdigit():
            raise ValueError
        value = int(minutes)
    except (TypeError, ValueError) as exc:
        raise WebhookError("Webhook interval must be whole minutes") from exc
    if not MIN_WEBHOOK_INTERVAL_MINUTES <= value <= MAX_WEBHOOK_INTERVAL_MINUTES:
        raise WebhookError("Webhook interval must be 5 to 1440 minutes")
    return value


def should_send_webhook(config_data: dict[str, Any], *, now: float | None = None) -> bool:
    if str(config_data.get("webhook_mode") or "none") == "none":
        return False
    now = time.time() if now is None else now
    last = config_data.get("webhook_last_sent_at") or 0
    try:
        last_value = float(last)
    except (TypeError, ValueError):
        last_value = 0.0
    interval = validate_webhook_interval(config_data.get("webhook_interval_minutes", 5))
    return now - last_value >= interval * 60


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
        return format_runtime_compact(elapsed)
    except (ValueError, TypeError):
        return ""


def _coerce_float(value: Any) -> float | None:
    """Best-effort numeric coercion for raw telemetry values."""
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"n/a", "na", "none", "null", "unknown", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _memory_mb_value(value: Any) -> float | None:
    """Return a RAM value in MB from raw numbers or display strings.

    Real Termux probes showed per-package RAM can arrive as formatted text such
    as ``"1.2 GB"``.  Webhook payload rendering must treat that as optional
    telemetry, not as a reason to skip Discord delivery.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    parts = text.replace(",", "").split()
    number = _coerce_float(parts[0] if parts else text)
    if number is None:
        return None
    unit = (parts[1] if len(parts) > 1 else "mb").lower()
    if unit.startswith("g"):
        return number * 1024
    if unit.startswith("k"):
        return number / 1024
    return number


def _format_memory_mb(value: Any) -> str | None:
    mb = _memory_mb_value(value)
    if mb is None:
        return None
    return f"{int(round(max(0.0, mb)))} MB"


def _format_cpu_pct(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip().rstrip("%")
    pct = _coerce_float(value)
    if pct is None:
        return None
    return f"{pct:.1f}%"


def _format_system_ram(mem_info: Any) -> str | None:
    if not isinstance(mem_info, dict):
        return None
    free = _format_memory_mb(mem_info.get("free_mb"))
    if not free:
        free = _format_memory_mb(mem_info.get("available_mb"))
    if not free:
        return None
    pct = _coerce_float(mem_info.get("percent_free"))
    pct_text = f" ({pct:.0f}%)" if pct is not None else ""
    return f"💾 RAM: {free} free{pct_text}"


def _redact_exception(exc: BaseException) -> str:
    return str(exc).replace("\n", " ")[:200]


def _spoiler(value: Any) -> str:
    text = str(value or "").strip() or "unknown"
    if text.startswith("||") and text.endswith("||"):
        return text
    return f"||{text.replace('||', '')}||"


def _public_device_label(config_data: dict[str, Any], phone_type: str | None) -> str:
    model = str(phone_type or "").strip()
    if model and model.lower() != "unknown":
        return model
    fallback = str(config_data.get("device_model") or config_data.get("android_model") or config_data.get("device_name") or "").strip()
    if fallback.lower() in {"localhost", "unknown", ""}:
        return "Unknown device"
    return fallback


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
    mem_info = config_data.get("_mem_info") or {}
    if isinstance(mem_info, dict):
        _free_mb = _memory_mb_value(mem_info.get("free_mb") or mem_info.get("available_mb"))
        mem_info = {
            **mem_info,
            "free_mb": int(round(_free_mb)) if _free_mb is not None else 0,
            "percent_free": int(round(_coerce_float(mem_info.get("percent_free")) or 0)),
        }
    else:
        mem_info = {}
    cpu_pct = _coerce_float(config_data.get("_cpu_pct"))
    temp_c = _coerce_float(config_data.get("_temp_c"))
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
    snapshot_by_package = {str(row.get("package") or ""): row for row in (supervisor_snapshot or [])}
    for e in entries:
        stats = app_stats.get(e["package"], {})
        snap = snapshot_by_package.get(e["package"], {})
        indicator = "🟢" if stats.get("online") else "🔴"
        label = str(snap.get("username") or e["username"] or e["package"]).strip()
        detail_lines.append(f"{indicator} {label}")
        sub: list[str] = []
        if snap.get("online_since") and stats.get("online"):
            try:
                sub.append("\u23f1\ufe0f " + format_runtime_compact(time.time() - float(snap["online_since"])))
            except (TypeError, ValueError):
                pass
        uptime = _format_uptime(stats.get("uptime_start"))
        if uptime:
            sub.append(f"⏱️ {uptime}")
        mem = _memory_mb_value(stats.get("memory_mb"))
        if mem is not None:
            sub.append(f"💾 {int(mem)} MB")
        cpu = _coerce_float(stats.get("cpu_pct"))
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
        "username": WEBHOOK_USERNAME,
        "avatar_url": WEBHOOK_AVATAR_URL,
        "allowed_mentions": {"parse": []},
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
        "username": WEBHOOK_USERNAME,
        "avatar_url": WEBHOOK_AVATAR_URL,
        "allowed_mentions": {"parse": []},
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


_PACKAGE_LIFECYCLE_STATE_PATH = DATA_DIR / "package-lifecycle-webhook-state.json"
_PACKAGE_LIFECYCLE_PRELAUNCH = frozenset({
    "Launching", "Pending", "Checking", "Waiting", "Reopening", "Relaunching",
})


def validate_discord_tag_user_id(value: Any) -> str:
    """Validate a Discord user ID for Package Dead tagging (17–20 digits)."""
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{17,20}", text):
        raise ValueError("Discord user ID must be 17-20 digits only")
    return text


def _discord_tag_user_id_from_config(config_data: dict[str, Any]) -> str | None:
    enabled, uid = _lifecycle_tag_settings(config_data)
    if not enabled or not uid:
        return None
    return uid


def _lifecycle_tag_settings(config_data: dict[str, Any]) -> tuple[bool, str | None]:
    """Resolve Tag Discord settings for Package Dead (disk wins when fully configured)."""
    enabled = bool(config_data.get("webhook_tag_enabled"))
    uid_raw = config_data.get("webhook_tag_user_id")
    try:
        from .config import load_config

        fresh = load_config()
        disk_enabled = bool(fresh.get("webhook_tag_enabled"))
        disk_uid = str(fresh.get("webhook_tag_user_id") or "").strip()
        if disk_enabled and disk_uid:
            enabled = True
            uid_raw = disk_uid
    except Exception:
        pass
    if not enabled:
        return False, None
    try:
        return True, validate_discord_tag_user_id(uid_raw)
    except ValueError:
        record_webhook_trace(
            source="lifecycle_tag",
            send_result="skipped",
            skip_reason="tag_user_id_invalid",
        )
        return False, None


def record_package_lifecycle_alive(package: str, alive_since: float | None = None) -> None:
    """Persist alive_since when a package becomes online (survives supervisor restart)."""
    pkg = str(package or "").strip()
    if not pkg:
        return
    ts = float(alive_since if alive_since is not None else time.time())
    state = _load_package_lifecycle_state()
    packages = state.setdefault("packages", {})
    row = dict(packages.get(pkg) or {})
    row["alive_since"] = ts
    row["updated_at"] = time.time()
    packages[pkg] = row
    _save_package_lifecycle_state(state)


def lifecycle_dead_runtime_seconds(
    package: str,
    dead_at: float | None = None,
    *,
    fallback_alive_since: float | None = None,
) -> float | None:
    """Seconds the package was alive before confirmed dead."""
    pkg = str(package or "").strip()
    if not pkg:
        return None
    row = _load_package_lifecycle_state().get("packages", {}).get(pkg, {})
    alive_raw = row.get("alive_since") if isinstance(row, dict) else None
    if alive_raw is None and fallback_alive_since is not None:
        alive_raw = fallback_alive_since
    try:
        alive = float(alive_raw)
    except (TypeError, ValueError):
        return None
    end = float(dead_at if dead_at is not None else time.time())
    if end < alive:
        return None
    return end - alive


def _load_package_lifecycle_state() -> dict[str, Any]:
    try:
        if _PACKAGE_LIFECYCLE_STATE_PATH.is_file():
            parsed = json.loads(_PACKAGE_LIFECYCLE_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                packages = parsed.get("packages")
                if isinstance(packages, dict):
                    return {"packages": packages}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return {"packages": {}}


def _save_package_lifecycle_state(state: dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _PACKAGE_LIFECYCLE_STATE_PATH.write_text(
            json.dumps({"packages": state.get("packages") or {}}, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def package_lifecycle_dead_already_notified(package: str) -> bool:
    pkg = str(package or "").strip()
    if not pkg:
        return True
    row = _load_package_lifecycle_state().get("packages", {}).get(pkg, {})
    return bool(row.get("dead_notified"))


def mark_package_lifecycle_dead_notified(package: str, username: str | None = None) -> None:
    pkg = str(package or "").strip()
    if not pkg:
        return
    state = _load_package_lifecycle_state()
    packages = state.setdefault("packages", {})
    row = dict(packages.get(pkg) or {})
    row.update({
        "dead_notified": True,
        "dead_active": True,
        "updated_at": time.time(),
        "username_resolution_failed": False,
    })
    clean = str(username or "").strip()
    if clean:
        row["last_username"] = clean
    packages[pkg] = row
    _save_package_lifecycle_state(state)


def record_package_lifecycle_username_failure(package: str) -> None:
    """Defer lifecycle webhook until username can be resolved."""
    pkg = str(package or "").strip()
    if not pkg:
        return
    state = _load_package_lifecycle_state()
    packages = state.setdefault("packages", {})
    row = dict(packages.get(pkg) or {})
    row.update({
        "username_resolution_failed": True,
        "username_resolution_failed_at": time.time(),
        "updated_at": time.time(),
    })
    packages[pkg] = row
    _save_package_lifecycle_state(state)


def clear_package_lifecycle_username_failure(package: str) -> None:
    pkg = str(package or "").strip()
    if not pkg:
        return
    state = _load_package_lifecycle_state()
    packages = state.setdefault("packages", {})
    row = dict(packages.get(pkg) or {})
    row.pop("username_resolution_failed", None)
    row.pop("username_resolution_failed_at", None)
    if row:
        packages[pkg] = row
        _save_package_lifecycle_state(state)


def package_lifecycle_recover_pending(package: str) -> bool:
    pkg = str(package or "").strip()
    if not pkg:
        return False
    row = _load_package_lifecycle_state().get("packages", {}).get(pkg, {})
    return bool(row.get("dead_active") and row.get("dead_notified"))


def mark_package_lifecycle_recovered(package: str, username: str | None = None) -> None:
    pkg = str(package or "").strip()
    if not pkg:
        return
    state = _load_package_lifecycle_state()
    packages = state.setdefault("packages", {})
    row = {
        "dead_notified": False,
        "dead_active": False,
        "updated_at": time.time(),
        "username_resolution_failed": False,
        "recovered_at": time.time(),
    }
    clean = str(username or "").strip()
    if clean:
        row["last_username"] = clean
    row.pop("alive_since", None)
    packages[pkg] = row
    _save_package_lifecycle_state(state)


def build_package_lifecycle_embed_payload(
    config_data: dict[str, Any],
    *,
    event: str,
    package: str,
    username: str,
    runtime_seconds: float | None = None,
) -> dict[str, Any]:
    """Build a minimal Package Dead / Package Recovered embed."""
    from .license import get_public_device_model
    from .package_identity import format_discord_username_spoiler

    normalized = str(event or "").strip().lower()
    if normalized == "package_dead":
        title = "Package Dead"
        color = EMBED_COLOR_RED
    else:
        title = "Package Recovered"
        color = EMBED_COLOR_GREEN

    device = _public_device_label(config_data, get_public_device_model()) or "Unknown"
    pkg_value = str(package or "").strip() or "Unknown"
    user_value = format_discord_username_spoiler(username)
    if not user_value:
        raise ValueError("username_resolution_failed")

    fields: list[dict[str, Any]] = [
        {"name": "Device", "value": device[:256], "inline": True},
        {"name": "Package", "value": pkg_value[:256], "inline": True},
        {"name": "Username", "value": user_value[:256], "inline": True},
    ]
    if normalized == "package_dead" and runtime_seconds is not None:
        runtime_display = format_lifecycle_dead_runtime(runtime_seconds)
        if runtime_display:
            fields.append({"name": "Runtime", "value": runtime_display[:256], "inline": True})

    return {
        "username": WEBHOOK_USERNAME,
        "avatar_url": WEBHOOK_AVATAR_URL,
        "embeds": [{
            "title": title,
            "color": color,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fields": fields,
        }],
    }


def _lifecycle_allowed_mentions(
    config_data: dict[str, Any],
    event: str,
) -> dict[str, Any]:
    normalized = str(event or "").strip().lower()
    if normalized != "package_dead":
        return {"parse": []}
    uid = _discord_tag_user_id_from_config(config_data)
    if uid:
        return {"parse": [], "users": [uid]}
    return {"parse": []}


def _lifecycle_content(config_data: dict[str, Any], event: str) -> str | None:
    normalized = str(event or "").strip().lower()
    if normalized != "package_dead":
        return None
    uid = _discord_tag_user_id_from_config(config_data)
    if not uid:
        return None
    return f"<@{uid}>"


def send_package_lifecycle_alert(
    config_data: dict[str, Any],
    *,
    event: str,
    package: str,
    username: str,
    runtime_seconds: float | None = None,
) -> tuple[bool, str]:
    """Send one Package Dead / Package Recovered embed without blocking relaunch."""
    from .package_identity import format_discord_username_spoiler

    spoiler = format_discord_username_spoiler(username)
    if not spoiler:
        record_webhook_trace(
            source="send_package_lifecycle_alert",
            event=event,
            send_attempted=False,
            send_result="skipped",
            skip_reason="username_resolution_failed",
        )
        return False, "username_resolution_failed"

    mode = str(config_data.get("webhook_mode") or "none")
    if mode == "none" or not config_data.get("webhook_enabled", mode != "none"):
        record_webhook_trace(
            source="send_package_lifecycle_alert",
            event=event,
            send_attempted=False,
            send_result="skipped",
            skip_reason="webhook_disabled",
        )
        return False, "webhook disabled"
    try:
        url = validate_webhook_url(config_data.get("webhook_url"))
    except WebhookError as exc:
        record_webhook_trace(
            source="send_package_lifecycle_alert",
            event=event,
            send_attempted=False,
            send_result="failure",
            last_exception_type=type(exc).__name__,
        )
        return False, f"webhook config error: {type(exc).__name__}"

    payload = build_package_lifecycle_embed_payload(
        config_data,
        event=event,
        package=package,
        username=username,
        runtime_seconds=runtime_seconds,
    )
    payload["allowed_mentions"] = _lifecycle_allowed_mentions(config_data, event)
    content = _lifecycle_content(config_data, event)
    if content:
        payload["content"] = content
    post_url = url + ("&" if "?" in url else "?") + "wait=true"
    record_webhook_trace(
        source="send_package_lifecycle_alert",
        event=event,
        webhook_mode=mode,
        send_attempted=True,
        http_method="POST",
    )
    ok, message, _message_id = _discord_json_request(post_url, payload, "POST")
    record_webhook_trace(
        source="send_package_lifecycle_alert",
        event=event,
        send_result="success" if ok else "failure",
        http_status=_http_status_from_message(message),
    )
    return ok, message


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


def build_status_embed_payload(
    config_data: dict[str, Any],
    *,
    event: str = "status",
    error: str | None = None,
    app_stats: dict[str, Any] | None = None,
    supervisor_snapshot: list[dict] | None = None,
) -> dict[str, Any]:
    """Build the simplified mobile Discord status embed."""
    from .config import mask_license_key
    from .license import get_public_device_model

    raw_packages = config_data.get("roblox_packages") or [config_data.get("roblox_package", "unknown")]
    entries: list[dict[str, str]] = []
    for item in raw_packages:
        if isinstance(item, dict):
            entries.append({
                "package": str(item.get("package") or "unknown"),
                "username": str(item.get("account_username") or item.get("label") or "").strip(),
            })
        else:
            entries.append({"package": str(item), "username": ""})

    app_stats = app_stats or {}
    snapshot = supervisor_snapshot or []
    if snapshot:
        total = len(snapshot)
        online_count = sum(1 for row in snapshot if str(row.get("status") or "") == "Online")
    else:
        total = len(entries)
        online_count = sum(1 for row in entries if app_stats.get(row["package"], {}).get("online"))
    offline_count = max(0, total - online_count)

    if error:
        color = EMBED_COLOR_YELLOW
    elif online_count > 0:
        color = EMBED_COLOR_GREEN
    else:
        color = EMBED_COLOR_RED

    mem_info = config_data.get("_mem_info") or {}
    sys_lines: list[str] = []
    if isinstance(mem_info, dict):
        free_mb = _memory_mb_value(mem_info.get("free_mb") or mem_info.get("available_mb"))
        pct = _coerce_float(mem_info.get("percent_free"))
        if free_mb is not None:
            pct_text = f" ({pct:.0f}%)" if pct is not None else ""
            sys_lines.append(f"💾 RAM: {int(round(max(0.0, free_mb)))}MB free{pct_text}")
    cpu_pct = _coerce_float(config_data.get("_cpu_pct"))
    if cpu_pct is not None:
        sys_lines.append(f"⚙️ CPU: {cpu_pct:.0f}%")
    temp_c = _coerce_float(config_data.get("_temp_c"))
    if temp_c is not None:
        sys_lines.append(f"🌡️ Temp: {temp_c:g}°C")
    sys_value = "\n".join(sys_lines) or "Telemetry unavailable"

    overview = "\n".join([
        f"🟢 Online: {online_count}",
        f"🔴 Offline: {offline_count}",
        f"🤖 Total: {total}",
    ])

    snapshot_by_package = {str(row.get("package") or ""): row for row in snapshot}
    detail_lines: list[str] = []
    for entry in entries:
        pkg = entry["package"]
        stats = app_stats.get(pkg, {})
        snap = snapshot_by_package.get(pkg, {})
        online = bool(stats.get("online")) or str(snap.get("status") or "") == "Online"
        indicator = "🟢" if online else "🔴"
        detail_lines.append(f"{indicator} {_spoiler(snap.get('username') or entry['username'] or pkg)}")
        sub: list[str] = []
        if snap.get("online_since") and online:
            try:
                sub.append("⏱️ " + format_runtime_compact(time.time() - float(snap["online_since"])))
            except (TypeError, ValueError):
                pass
        uptime = _format_uptime(stats.get("uptime_start"))
        if uptime:
            sub.append(f"⏱️ {uptime}")
        memory = _format_memory_mb(stats.get("memory_mb") if "memory_mb" in stats else snap.get("ram_mb"))
        if memory:
            sub.append(f"💾 {memory}")
        cpu = _format_cpu_pct(stats.get("cpu_pct"))
        if cpu:
            sub.append(f"⚡ {cpu}")
        if sub:
            detail_lines.append("└ " + " | ".join(sub))
    detail_value = "\n".join(detail_lines) or "No accounts configured"

    fields: list[dict[str, Any]] = [
        {"name": "📱 Device", "value": _public_device_label(config_data, get_public_device_model()), "inline": True},
        {"name": "🔑 License", "value": mask_license_key(config_data.get("license_key", "")), "inline": True},
        {"name": "🖥️ System Stats", "value": sys_value, "inline": False},
        {"name": "Status Overview", "value": overview, "inline": False},
        {"name": "Application Details", "value": detail_value, "inline": False},
    ]
    if error:
        fields.append({"name": "⚠️ Last Error", "value": error[:512], "inline": False})

    return {
        "username": WEBHOOK_USERNAME,
        "avatar_url": WEBHOOK_AVATAR_URL,
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": EMBED_TITLE,
            "url": EMBED_URL,
            "description": "",
            "color": color,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fields": fields,
            "footer": {"text": EMBED_FOOTER_TEXT},
        }],
    }


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

    # On Termux, route JSON-only webhook through safe_http/curl to prevent
    # Python ssl SIGSEGV.  Multipart (snapshot) falls back to urllib since
    # snapshots are rarely used on cloud phones, and the request is not
    # part of the license-critical path.
    on_termux = bool(os.environ.get("TERMUX_VERSION")) or os.environ.get("DENG_HTTP_BACKEND") == "curl"
    if on_termux and not (snapshot_path and snapshot_path.exists()):
        try:
            resp_dict = safe_http.post_json(url, payload, timeout=10)
            message_id = resp_dict.get("id") if isinstance(resp_dict, dict) else None
            return True, "discord webhook OK (curl)", message_id
        except safe_http.SafeHttpStatusError as exc:
            return False, f"webhook HTTP {exc.status_code}", None
        except safe_http.SafeHttpError as exc:
            return False, f"webhook failed: {exc}", None
        except Exception as exc:  # noqa: BLE001
            return False, f"webhook error: {exc}", None

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


def _discord_json_request(url: str, payload: dict[str, Any], method: str) -> tuple[bool, str, str | None]:
    """Issue one bounded Discord request; HTTP failures are returned, never raised."""
    body = json.dumps(payload).encode("utf-8")
    status: int | str = ""
    response: Any = b""
    try:
        if method == "POST":
            record_webhook_trace(source="discord_request", exception_stage="post_with_response_started", response_body_type="")
            status, _headers, response = safe_http.post_with_response(url, body, timeout=10)
        elif safe_http._http_backend() == "curl":  # curl keeps Termux TLS outside Python.
            headers = safe_http._build_curl_header_args({"Content-Type": "application/json"})
            record_webhook_trace(source="discord_request", exception_stage="curl_patch_started", response_body_type="")
            status, _headers, response = safe_http._run_curl_with_headers(
                ["-X", method, "--data-binary", "@-"] + headers + [url], stdin_bytes=body, timeout=10,
            )
        else:
            request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method=method)
            record_webhook_trace(source="discord_request", exception_stage="urllib_patch_started", response_body_type="")
            try:
                with urllib.request.urlopen(request, timeout=10) as result:  # noqa: S310 - validated webhook URL
                    status, response = int(result.status), result.read()
            except urllib.error.HTTPError as exc:
                status, response = int(exc.code), exc.read()
    except Exception as exc:  # network errors must not affect the watchdog
        record_webhook_trace(
            source="discord_request",
            exception_type=type(exc).__name__,
            exception_message_redacted=_redact_exception(exc),
            exception_stage="http_request",
            response_type=type(response).__name__,
            response_status_raw=str(status),
            response_body_type=type(response).__name__,
            response_body_redacted=_redacted_body_preview(response),
            last_exception_type=type(exc).__name__,
            last_exception_message_redacted=_redact_exception(exc),
        )
        return False, f"webhook request failed: {type(exc).__name__}", None
    record_webhook_trace(
        source="discord_request",
        response_type=type(response).__name__,
        response_status_raw=str(status),
        response_body_type=type(response).__name__,
        response_body_redacted=_redacted_body_preview(response),
    )
    if not 200 <= status < 300:
        return False, f"webhook HTTP {status}", None
    try:
        message_id = _parse_discord_message_id(response)
    except Exception as exc:  # noqa: BLE001
        record_webhook_trace(
            source="discord_request",
            exception_type=type(exc).__name__,
            exception_message_redacted=_redact_exception(exc),
            exception_stage="parse_discord_message_id",
            response_type=type(response).__name__,
            response_status_raw=str(status),
            response_body_type=type(response).__name__,
            response_body_redacted=_redacted_body_preview(response),
            last_exception_type=type(exc).__name__,
            last_exception_message_redacted=_redact_exception(exc),
        )
        message_id = None
    return True, f"webhook HTTP {status}", message_id


def _http_status_from_message(message: str | None) -> int | None:
    if not message:
        return None
    for token in str(message).replace(":", " ").split():
        if token.isdigit():
            value = int(token)
            if 100 <= value <= 599:
                return value
    return None


def _redacted_body_preview(value: Any, *, limit: int = 500) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    return text.replace("\n", " ")[:limit]


def _parse_discord_message_id(response: Any) -> str | None:
    if not response:
        return None
    if isinstance(response, dict):
        return str(response.get("id") or "") or None
    if isinstance(response, bytes):
        text = response.decode("utf-8", errors="replace")
    else:
        text = str(response)
    if not text.strip():
        return None
    parsed = json.loads(text)
    if isinstance(parsed, dict):
        return str(parsed.get("id") or "") or None
    return None


def _minimal_status_payload(config_data: dict[str, Any], *, event: str, error: str | None = None) -> dict[str, Any]:
    """Fallback Discord payload used when optional telemetry formatting fails."""
    title = "📊 DENG Status Monitor"
    description = f"Event: **{event}**"
    fields = [
        {"name": "📱 Device", "value": str(config_data.get("device_name") or "unknown"), "inline": True},
        {"name": "Telemetry", "value": "telemetry_unavailable", "inline": False},
    ]
    if error:
        fields.append({"name": "⚠️ Payload warning", "value": error[:512], "inline": False})
    return {
        "username": WEBHOOK_USERNAME,
        "avatar_url": WEBHOOK_AVATAR_URL,
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": EMBED_COLOR_YELLOW,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "fields": fields,
                "footer": {"text": f"DENG Tool: Rejoin • v{config_data.get('agent_version', '1.0.0')}"},
            }
        ],
    }


def _minimal_status_payload(config_data: dict[str, Any], *, event: str, error: str | None = None) -> dict[str, Any]:
    """Fallback Discord payload used when optional telemetry formatting fails."""
    fields = [
        {"name": "📱 Device", "value": str(config_data.get("device_name") or "Unknown device"), "inline": True},
        {"name": "Telemetry", "value": "telemetry_unavailable", "inline": False},
    ]
    if error:
        fields.append({"name": "⚠️ Payload warning", "value": error[:512], "inline": False})
    return {
        "username": WEBHOOK_USERNAME,
        "avatar_url": WEBHOOK_AVATAR_URL,
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": EMBED_TITLE,
            "url": EMBED_URL,
            "description": "",
            "color": EMBED_COLOR_YELLOW,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fields": fields,
            "footer": {"text": EMBED_FOOTER_TEXT},
        }],
    }


def send_periodic_status(
    config_data: dict[str, Any], *, supervisor_snapshot: list[dict[str, Any]], app_stats: dict[str, Any]
) -> tuple[bool, str]:
    """Send/update one monitor embed according to the configured user mode."""
    record_webhook_trace(
        source="send_periodic_status",
        send_periodic_status_entered=True,
        webhook_mode=str(config_data.get("webhook_mode") or "none"),
        config_path_read=str(CONFIG_PATH),
        config_read_path=str(CONFIG_PATH),
        state_read_path=str(CONFIG_PATH),
        edit_message_id_present=bool(config_data.get("webhook_last_message_id")),
    )
    mode = str(config_data.get("webhook_mode") or "none")
    if mode == "none":
        record_webhook_trace(source="send_periodic_status", webhook_send_attempted=False, send_attempted=False, http_method="", http_status="", send_result="skipped", skip_reason="webhook_disabled")
        return False, "webhook disabled"
    try:
        url = validate_webhook_url(config_data.get("webhook_url"))
    except Exception as exc:
        record_webhook_trace(source="send_periodic_status", send_attempted=False, send_result="failure", last_exception_type=type(exc).__name__, last_exception_message_redacted=_redact_exception(exc))
        return False, f"webhook config error: {type(exc).__name__}"
    _sync_edit_state_for_url(config_data, url)
    record_webhook_trace(
        source="send_periodic_status",
        edit_mode_selected=(mode == "edit"),
        webhook_url_present_redacted=bool(url),
        state_message_id_present=bool(config_data.get("webhook_last_message_id")),
        state_message_id_redacted=_redact_message_id(config_data.get("webhook_last_message_id")),
    )
    record_webhook_trace(source="send_periodic_status", payload_build_started=True)
    try:
        payload = build_status_embed_payload(config_data, event="monitor", app_stats=app_stats, supervisor_snapshot=supervisor_snapshot)
        record_webhook_trace(source="send_periodic_status", payload_build_result="success")
    except Exception as exc:  # optional telemetry must never block Discord delivery
        payload = _minimal_status_payload(config_data, event="monitor", error="telemetry_unavailable")
        record_webhook_trace(
            source="send_periodic_status",
            payload_build_result="failure",
            last_exception_type=type(exc).__name__,
            last_exception_message_redacted=_redact_exception(exc),
        )
    if mode == "edit" and config_data.get("webhook_last_message_id"):
        edit_url = f"{url.rstrip('/')}/messages/{config_data['webhook_last_message_id']}?wait=true"
        used_id = str(config_data.get("webhook_last_message_id") or "")
        record_webhook_trace(
            source="send_periodic_status",
            edit_patch_started=True,
            edit_patch_message_id_used=_redact_message_id(used_id),
            webhook_message_id_present=True,
            webhook_send_attempted=True,
            send_attempted=True,
            http_method="PATCH",
            last_http_method="PATCH",
            last_http_url_kind="PATCH_EDIT",
            message_id_saved_or_used=True,
        )
        ok, message, _message_id = _discord_json_request(edit_url, payload, "PATCH")
        patch_status = _http_status_from_message(message)
        record_webhook_trace(source="send_periodic_status", edit_patch_http_status=patch_status or message, http_method="PATCH", http_status=patch_status or message, last_http_method="PATCH", last_http_status=patch_status or message, discord_response_body_redacted=message[:200], edit_rebootstrap_required=(not ok and patch_status == 404), send_result="success" if ok else "failure")
        if ok:
            config_data["webhook_last_sent_at"] = time.time()
            if not _persist_webhook_edit_state(config_data, url=url):
                return False, "webhook state save failed"
            return True, "edited monitor message"
        if patch_status != 404:
            return False, message
        record_webhook_trace(source="send_periodic_status", edit_rebootstrap_started=True, edit_rebootstrap_reason="discord_404")
    post_url = url + ("&" if "?" in url else "?") + "wait=true"
    record_webhook_trace(source="send_periodic_status", edit_bootstrap_post_started=(mode == "edit"), webhook_message_id_present=False, edit_bootstrap_required=(mode == "edit"), webhook_wait=True, webhook_send_attempted=True, send_attempted=True, http_method="POST", last_http_method="POST", last_http_url_kind="POST_BOOTSTRAP" if mode == "edit" else "POST_NEW", message_id_saved_or_used=False)
    ok, message, message_id = _discord_json_request(post_url, payload, "POST")
    post_status = _http_status_from_message(message)
    record_webhook_trace(source="send_periodic_status", edit_bootstrap_post_http_status=post_status or message if mode == "edit" else "", http_method="POST", http_status=post_status or message, last_http_method="POST", last_http_status=post_status or message, discord_response_body_redacted=message[:200], returned_message_id_present=bool(message_id), last_discord_message_id_redacted=_redact_message_id(message_id), saved_message_id=bool(mode == "edit" and message_id), message_id_saved_or_used=bool(mode == "edit" and message_id), send_result="success" if ok else "failure")
    if ok:
        config_data["webhook_last_sent_at"] = time.time()
        if mode == "edit" and not message_id:
            record_webhook_trace(
                source="send_periodic_status",
                edit_bootstrap_post_succeeded_but_id_missing=True,
                send_result="failure",
                last_exception_type="MissingDiscordMessageId",
                last_exception_message_redacted="Discord webhook POST succeeded but no message id was returned or parsed",
            )
            return False, "webhook edit bootstrap failed: missing Discord message id"
        if mode == "edit" and message_id:
            persisted = _persist_webhook_edit_state(config_data, url=url, message_id=message_id)
            record_webhook_trace(source="send_periodic_status", edit_bootstrap_message_id_saved=persisted)
            if not persisted:
                return False, "webhook state save failed"
    return ok, message


class WebhookStatusReporter:
    """Daemon reporter started only by Start; it never controls package state."""

    def __init__(self, config_data: dict[str, Any], supervisor: Any, entries: list[dict[str, Any]], save_callback: Any) -> None:
        self.config_data, self.supervisor, self.entries, self.save_callback = config_data, supervisor, entries, save_callback
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.loop_count = 0
        self.debug: dict[str, Any] = {
            "mode": str(config_data.get("webhook_mode") or "none"),
            "interval_minutes": config_data.get("webhook_interval_minutes", 5),
            "url_present": bool(config_data.get("webhook_url")),
            "url_masked": mask_webhook_url(config_data.get("webhook_url")),
            "raw_url_never_included": True,
            "edit_message_id_present": bool(config_data.get("webhook_last_message_id")),
            "scheduler_enabled": False, "scheduler_running": False, "scheduler_loop_count": 0,
            "started_by_command": "Start", "last_send_result": "not_started",
        }

    def _record(self, **changes: Any) -> None:
        self.debug.update(changes)
        self.debug["scheduler_loop_count"] = self.loop_count
        self.debug["last_message_id_present"] = bool(self.config_data.get("webhook_last_message_id"))
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            (DATA_DIR / "webhook-debug.json").write_text(json.dumps(self.debug, sort_keys=True), encoding="utf-8")
        except OSError:
            pass

    def start(self) -> None:
        if str(self.config_data.get("webhook_mode") or "none") == "none":
            self._record(reason_skipped="mode_none", scheduler_enabled=False)
            return
        self._record(scheduler_enabled=True, timer_armed=True, start_pressed_at=time.time())
        record_webhook_trace(source="WebhookStatusReporter.start", timer_armed=True, webhook_mode=self.config_data.get("webhook_mode"), config_path_read=str(CONFIG_PATH))
        self.thread = threading.Thread(target=self._run, name="deng-webhook-status", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1.0)
        self._record(scheduler_running=False, scheduler_enabled=False, reason_skipped="stopped")

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.loop_count += 1
                self._record(scheduler_running=True, reporter_tick_started=True, last_webhook_tick_at=time.time(), last_send_mode=self.config_data.get("webhook_mode"), last_send_attempt_at=time.time())
                record_webhook_trace(source="WebhookStatusReporter._run", reporter_tick=True, reporter_tick_started=True, telemetry_build_started=True)
                try:
                    from . import android
                    snapshot = self.supervisor.get_status_snapshot(self.entries)
                    mem = android.get_memory_info()
                    self.config_data["_mem_info"] = mem
                    self.config_data["_cpu_pct"] = android.get_cpu_usage()
                    self.config_data["_temp_c"] = android.get_temperature()
                    record_webhook_trace(source="WebhookStatusReporter._run", telemetry_build_result="success", telemetry_result="success")
                except Exception as telemetry_exc:  # telemetry must never suppress delivery
                    snapshot = []
                    self._record(telemetry_error_redacted=type(telemetry_exc).__name__)
                    record_webhook_trace(source="WebhookStatusReporter._run", telemetry_build_result="failure", telemetry_result="failure", error=type(telemetry_exc).__name__)
                app_stats = {
                    str(row.get("package") or ""): {
                        "online": row.get("status") == "Online",
                        "memory_mb": row.get("ram_mb"),
                        "cpu_pct": self.config_data["_cpu_pct"],
                    }
                    for row in snapshot
                }
                ok, message = send_periodic_status(self.config_data, supervisor_snapshot=snapshot, app_stats=app_stats)
                if ok:
                    self.save_callback(self.config_data)
                    self._record(last_send_result="success", last_http_error_redacted="", last_exception_type="", next_scheduled_send_at=time.time() + validate_webhook_interval(self.config_data.get("webhook_interval_minutes", 5)) * 60)
                else:
                    self._record(last_send_result="failure", last_http_error_redacted=message[:200], last_exception_type="", next_scheduled_send_at=time.time() + validate_webhook_interval(self.config_data.get("webhook_interval_minutes", 5)) * 60)
                    import logging
                    logging.getLogger(__name__).warning("webhook monitor update skipped: %s", message)
            except Exception as exc:  # reporting is strictly best-effort
                self._record(last_send_result="failure", last_exception_type=type(exc).__name__, last_exception_message_redacted=str(exc)[:200])
                import logging
                logging.getLogger(__name__).warning("webhook monitor update failed: %s", type(exc).__name__)
            finally:
                record_webhook_trace(source="WebhookStatusReporter._run", reporter_tick_completed=True)
            interval = validate_webhook_interval(self.config_data.get("webhook_interval_minutes", 5)) * 60
            if self.stop_event.wait(interval):
                return
