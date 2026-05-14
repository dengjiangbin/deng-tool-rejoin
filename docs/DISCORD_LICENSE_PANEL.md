# Discord License Panel

## Overview

The license panel is a **persistent Discord embed** with 3 interactive buttons, posted in a designated channel.  It is self-service: users can generate, reset, or redeem keys without pinging an admin.

All button response flows are **ephemeral** — only the clicking user sees the response.

---

## Panel Embed

**Title**: `DENG Tool — License Key Panel`  
**Color**: Brand blue (`#2F80ED`)

| Field | Purpose |
|---|---|
| 🔑 Generate Key | Create a new license key (shown once) |
| ♻️ Reset HWID | Unbind current device (max 5/24h) |
| 🎟️ Redeem Key | Attach an existing key to your account |

---

## Button Custom IDs

```python
BUTTON_GENERATE   = "license_panel:generate"
BUTTON_RESET_HWID = "license_panel:reset_hwid"
BUTTON_REDEEM     = "license_panel:redeem"
```

Use these constants in your `interaction.custom_id` match in your Discord bot cog.

---

## Slash Commands

All commands are under the `/license_panel` group.

| Command | Scope | Description |
|---|---|---|
| `/license_panel set_channel #channel` | Admin | Set where the panel embed will be posted |
| `/license_panel post` | Admin | Create or re-post the panel embed |
| `/license_panel refresh` | Admin | Edit the existing message in place |
| `/license_panel status` | Anyone | View your own key list (ephemeral) |
| `/license_panel clear` | Admin | Remove saved panel config (not message) |

"Admin" means the user's Discord ID is in `LICENSE_OWNER_DISCORD_IDS`.

---

## Response Flows

### 🔑 Generate Key

1. User clicks **Generate Key**
2. Bot checks: is user blocked? has user reached `max_keys` limit?
3. If blocked → ephemeral error
4. If at limit → ephemeral "Key Limit Reached" embed
5. If OK → call `store.create_key_for_user(discord_user_id)`
6. Send ephemeral embed with full key in a code block
7. **The full key is never stored in plaintext** — only the SHA-256 hash

### ♻️ Reset HWID

1. User clicks **Reset HWID**
2. If user has no keys → ephemeral "No keys found"
3. If user has one key → reset directly
4. If user has multiple keys → ephemeral select menu to choose
5. Check reset count: ≥ 5 in last 24h → ephemeral "Reset Limit Reached"
6. Check last heartbeat: < 5 minutes ago → ephemeral "Key Recently Active" warning
7. If all checks pass → call `store.reset_hwid(discord_user_id, key_id)`
8. Send ephemeral "HWID Reset" success embed

### 🎟️ Redeem Key

1. User clicks **Redeem Key**
2. Bot sends ephemeral modal with a text input for the key
3. User pastes key (accepts: with or without inner dashes, any case)
4. Bot normalizes key: `deng-8f3a-b3c4-d5e6-44f0` → `DENG-8F3A-B3C4-D5E6-44F0`
5. Validates format; if invalid → ephemeral "Redemption Failed"
6. Calls `store.redeem_key_for_user(discord_user_id, raw_key)`
7. On success → ephemeral "Key Redeemed" with masked key

---

## Panel Config Storage

Panel channel and message ID are stored per guild in the license store:

```python
store.save_panel_config(guild_id, channel_id, message_id, updated_by)
store.get_panel_config(guild_id)    # → {channel_id, message_id, updated_by, updated_at}
store.clear_panel_config(guild_id)
```

For **local mode** (`LocalJsonLicenseStore`): stored in `panel_configs` key of `license_store.json`.  
For **remote mode** (`SupabaseLicenseStore`): stored in the `license_panel_config` table.

---

## Bot Setup Checklist

Required bot permissions:
- `Send Messages`
- `Embed Links`
- `Read Message History`
- `Use Application Commands`

Required intents:
- No privileged intents needed for the panel itself

Register the `/license_panel` command tree once on startup.  Store `guild_id` consistently (do not mix int/str).

---

## Builders (agent/license_panel.py)

The module exports framework-agnostic dict payloads:

```python
from agent.license_panel import (
    build_panel_embed,          # → embed dict for discord.Embed.from_dict()
    build_panel_buttons,        # → components list (action row + 3 buttons)
    build_generate_success_response,
    build_generate_limit_response,
    build_reset_success_response,
    build_reset_limit_response,
    build_reset_active_warning_response,
    build_redeem_success_response,
    build_redeem_error_response,
    build_key_list_response,
    build_not_owner_response,
    get_slash_command_specs,
    BUTTON_GENERATE,
    BUTTON_RESET_HWID,
    BUTTON_REDEEM,
)
```

No discord.py/disnake import — wire into your cog of choice.
