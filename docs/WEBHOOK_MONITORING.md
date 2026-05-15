# Webhook Monitoring

## Overview

DENG Tool sends status updates to a Discord webhook URL at a configurable interval.  Updates are sent as rich embeds built by `agent/webhook.py`.

---

## Status Embed

The primary embed (`build_status_embed_payload`) contains:

| Field | Content |
|---|---|
| 📱 Device | `device_name` from config |
| 🔑 License | Masked license key (`DENG-8F3A...44F0`) |
| 🏷️ Tags | `[N]` count of webhook tags |
| 🖥️ System Stats | RAM free, CPU %, temperature |
| Status Overview | Full 7-category breakdown (see below) |
| Application Details | Per-package status, uptime, RAM, CPU |
| ⚠️ Last Error | Present only when `error` param is set |

### Status Overview — 7 Categories

```
🟢 Online: N      — process confirmed running
🟡 Ready: N       — launched, acknowledged as ready
🔵 Preparing: N   — launching / checking / not yet confirmed
🟠 Warning: N     — reviving / unstable
🔴 Offline: N     — process not found, within grace period
❌ Failed: N      — revive failed / not installed / disabled
🤖 Total: N
```

**Status mapping from supervisor STATUS_*** :

| Supervisor Status | Category |
|---|---|
| `Online` | 🟢 online |
| `Ready` | 🟡 ready |
| `Preparing`, `Launching`, `Reconnecting`, `Checking` | 🔵 preparing |
| `Warning`, `Reviving`, `Background`, `Unknown` | 🟠 warning |
| `Offline` | 🔴 offline |
| `Failed`, `Not installed`, `Disabled` | ❌ failed |

---

## Supervisor Snapshot Integration

Pass `supervisor_snapshot` from `MultiPackageSupervisor.get_status_snapshot()` to get accurate per-category counts:

```python
snapshot = supervisor.get_status_snapshot(entries=enabled_package_entries(cfg))
payload = build_status_embed_payload(cfg, supervisor_snapshot=snapshot)
```

Each snapshot entry:
```python
{
    "package": "com.roblox.client",
    "username": "Main",
    "status": "Online",
    "revive_count": 2,
    "failure_count": 0,
    "last_error": None,
    "online_since": 1715000000.0,   # float or None
    "last_seen_at": 1715000090.0,   # float or None
}
```

Without `supervisor_snapshot`, the function falls back to counting from `app_stats` online booleans (only online/offline split, no other categories).

---

## Alert Embed

`build_alert_embed_payload` is used for captcha events, rejoin failures, and other per-account alerts.  Sent via `send_webhook_update(alert_account=..., alert_reason=...)`.

---

## Configuration

In `config.json`:

```json
{
  "webhook_enabled": true,
  "webhook_url": "https://discord.com/api/webhooks/...",
  "webhook_mode": "new_message",
  "webhook_interval_seconds": 300,
  "webhook_snapshot_enabled": false,
  "webhook_tags": ["prod", "server-1"]
}
```

- `webhook_mode=new_message`: each update posts a new message
- `webhook_mode=edit_message`: edits an existing message by `webhook_last_message_id`
- `webhook_interval_seconds`: minimum 30 seconds (anti-spam)
- `webhook_tags`: user-defined labels shown in embed as `[N]` count

---

## Rate Limiting

`should_send_webhook(config_data)` returns `False` if the configured interval has not elapsed since `webhook_last_sent_at`.  Pass `force=True` to `send_webhook_update` to bypass this check (e.g. on startup or after a rejoin).

Discord rate-limit: avoid intervals below 30 seconds.  The code enforces a minimum of 30 s.

---

## Security

- Webhook URLs are always **masked** in logs, terminal output, and `safe_config_view` using `mask_webhook_url()`
- URLs are validated to be `https://discord.com/api/webhooks/...` before any request is made
- No credentials, keys, or launch URLs are included in the embed in plaintext
- License key is masked to `DENG-8F3A...44F0` format in every embed
