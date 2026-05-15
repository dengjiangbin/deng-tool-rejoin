# Discord License Panel

## Overview

The license panel is a **persistent Discord embed** with 4 interactive buttons, posted in a designated channel.  It is self-service: users can generate, reset, redeem keys, or open **Key Stats** without pinging an admin.

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
| 📊 Key Stats | Private list of your keys, pagination, **Download Keys** (text file) |

---

## Button Custom IDs

```python
BUTTON_GENERATE   = "license_panel:generate"
BUTTON_RESET_HWID = "license_panel:reset_hwid"
BUTTON_REDEEM     = "license_panel:redeem"
BUTTON_KEY_STATS  = "license_panel:key_stats"
```

Ephemeral **Key Stats** navigation uses these `custom_id`s (not persistent views):

`license_panel:ks_prev` · `license_panel:ks_next` · `license_panel:ks_dl` · `license_panel:ks_close`

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

### ♻️ Reset HWID — Dropdown Selector Flow

1. User clicks **Reset HWID**
2. Bot calls `store.list_user_keys_with_binding_state(discord_user_id)`
3. If user has no (non-revoked) keys → ephemeral "No Keys Found" embed
4. If user has keys → send ephemeral message with:
   - **Header embed** listing all keys with 🟢/🟡 state indicators
   - **Dropdown** (`discord.ui.Select`) — one option per key
   - **Confirm Reset** button (red) and **Cancel** button (grey)
5. User selects one or more keys from the dropdown
6. User clicks **Confirm Reset**:
   - For each selected key: run per-key reset logic
   - If key has `can_reset=False`: show reason (no binding, limit, recently active)
   - If key can reset: call `store.reset_hwid(discord_user_id, key_id)`
7. Message updates in-place with per-key result summary. Components disabled.
8. Clicking **Cancel** disables components and shows "Reset Cancelled."

**Key state indicators:**

| Indicator | Meaning |
|-----------|---------|
| 🟢 | Key is bound to a device (HWID is active) |
| 🟡 | No device bound — key has never been used or binding was cleared |

**🟡 keys cannot be reset** — there is nothing to clear. The reason is shown in the dropdown description.

**Timeout:** The selector view expires after 120 seconds; components auto-disable.

### 🎟️ Redeem Key

1. User clicks **Redeem Key**
2. Bot sends ephemeral modal with a text input for the key
3. User pastes key (accepts: with or without inner dashes, any case)
4. Bot normalizes key: `deng-8f3a-b3c4-d5e6-44f0` → `DENG-8F3A-B3C4-D5E6-44F0`
5. Validates format; if invalid → ephemeral "Redemption Failed"
6. Calls `store.redeem_key_for_user(discord_user_id, raw_key)`
7. On success → ephemeral "Key Redeemed" with masked key

### 📊 Key Stats

1. User clicks **Key Stats**
2. Bot defers ephemeral, calls `store.list_user_keys_for_stats(discord_user_id)`
3. Bot sends an ephemeral message: **plain-text header** `Your License Keys (Total: N | Page X/Y)` plus **one embed per key** (max 5 per page).
4. **Used** = key has an **active** device binding. **Unused** = no active binding (free for a new device after reset).
5. **Previous** / **Next** edit the same message. Only the opening user may interact; others get "This key stats view is not yours."
6. **Download Keys** sends another ephemeral message with `my_keys_<discord_user_id>.txt` listing **all** keys for that user (short format: Used/Unused, device when bound).
7. **Close** edits the stats message to `Closed.` and removes the view.

**Limits:** By default **one Discord user → one license key → one device**. If the tool says the key is bound elsewhere, use **Reset HWID** in Discord, wait if recently active, then bind again.

**Logo on embeds:** Set environment variable `DENG_BRANDING_LOGO_URL` to a public HTTPS image URL (for example a hosted `D_96px.png`). If unset, embeds work without a thumbnail.

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
    build_panel_embed,                  # → embed dict for discord.Embed.from_dict()
    build_panel_buttons,                # → components list (action row + 4 buttons)
    build_generate_success_response,
    build_generate_limit_response,
    build_reset_selector_embed,         # → embed for the HWID key selector
    build_reset_no_keys_response,       # → embed when user has no keys
    build_reset_mixed_summary_embed,    # → per-key result summary embed
    build_reset_success_response,       # kept for direct resets in tests/admin flows
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
    BUTTON_KEY_STATS,
)
```

No discord.py/disnake import — wire into your cog of choice.
