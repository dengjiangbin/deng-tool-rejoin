"""DENG Tool: Rejoin — Discord license panel builders.

This module provides *reusable builders* for the Discord license key panel.
It is NOT a runtime bot — it has no discord.py/disnake client, no event loop,
and no network calls.  Wire these embed/button specs into your existing Discord
bot cog or command tree.

Panel life-cycle
────────────────
1. Admin runs /license_panel set_channel #channel
2. Admin runs /license_panel post           → creates a persistent embed message
3. Members click buttons (ephemeral flows)
4. Admin runs /license_panel refresh        → edits the embed in place
5. Admin runs /license_panel clear          → removes panel config (not the message)

Button custom IDs (use these as constants in your button handler):
    Generate Key is a URL button and has no custom_id.
    BUTTON_KEY_STATS  = "license_panel:key_stats"
    BUTTON_SELECT_VERSION = "license_panel:select_version"

All button response flows are EPHEMERAL — only the clicking user sees the result.

Required bot permissions: Send Messages, Embed Links, Read Message History.
"""

from __future__ import annotations

from typing import Any


def format_copy_license_key_content(full_key: str) -> str:
    """First line labels; second line is backticks-only for easy Discord copying."""
    key = (full_key or "").strip()
    return f"Copy License Key:\n`{key}`"


def format_copy_license_keys_lines(keys: list[str]) -> str:
    """One or many keys: plain lines with minimal extra text inside copy lines."""
    cleaned = [k.strip() for k in keys if k.strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return format_copy_license_key_content(cleaned[0])
    lines = ["Copy License Keys:"]
    for i, k in enumerate(cleaned, start=1):
        lines.append(f"{i}. `{k}`")
    return "\n".join(lines)


# ── Button custom ID constants ─────────────────────────────────────────────────

BUTTON_GENERATE   = "license_panel:generate"
BUTTON_KEY_STATS  = "license_panel:key_stats"
BUTTON_SELECT_VERSION = "license_panel:select_version"

# Legacy custom_ids for features removed during the license-system rebuild.
# Old already-posted panel messages may still carry these; the bot registers a
# RemovedFeatureView so clicks respond gracefully instead of "interaction failed".
REMOVED_BUTTON_RESET_HWID = "license_panel:reset_hwid"
REMOVED_BUTTON_REDEEM     = "license_panel:redeem"

PANEL_LOGO_URL = "https://aio.deng.my.id/public/img/deng-logo.png"

# ── Slash command names ────────────────────────────────────────────────────────

SLASH_GROUP       = "license_panel"
SLASH_SET_CHANNEL = "set_channel"
SLASH_POST        = "post"
SLASH_REFRESH     = "refresh"
SLASH_STATUS        = "status"
SLASH_CLEAR         = "clear"
SLASH_ADMIN_STATUS  = "admin_status"

# Top-level slash command names owned by DENG Tool Rejoin (for deploy/cleanup).
DENG_SLASH_ROOT_COMMANDS: tuple[str, ...] = (
    SLASH_GROUP,
    "license_log_channel",
    "license",
)


# ── Panel embed builder ────────────────────────────────────────────────────────

def build_panel_embed() -> dict[str, Any]:
    """Return a Discord embed payload dict for the persistent license panel.

    The returned structure is framework-agnostic JSON.  Convert it to your
    discord.py / disnake Embed with ``discord.Embed.from_dict(payload)``.

    Structure
    ─────────
    • Title       : "DENG Tool: Rejoin Panel"
    • Description : compact mobile-friendly blockquote button guide
    • Footer      : "DENG Tool • https://aio.deng.my.id • Secure & Automated"
    """
    return {
        "title": "DENG Tool: Rejoin Panel",
        "color": 0x2F80ED,
        "description": (
            "Manage your key and package version seamlessly with our automated system.\n"
            "Select an option below to get started:\n\n"
            "> \U0001f511 Generate Key\n"
            "> Take you to our portal to generate the keys.\n\n"
            "> \U0001f4ca Key Stats\n"
            "> View status and export keys.\n\n"
            "> \U0001f4e6 Select Version\n"
            "> Choose which package version to install."
        ),
        "footer": {"text": "DENG Tool \u2022 https://aio.deng.my.id \u2022 Secure & Automated"},
        "thumbnail": {"url": PANEL_LOGO_URL},
    }


def build_panel_buttons() -> list[dict[str, Any]]:
    """Return an action-row payload with the panel buttons.

    Structure mirrors the Discord components v2 JSON shape::

        [
          {
            "type": 1,   # ACTION_ROW
            "components": [
              {"type": 2, "style": 1, "label": "...", "custom_id": "...", "emoji": {...}},
              ...
            ]
          }
        ]

    Convert to discord.py Button objects in your bot code.
    All buttons are non-disabled by default.
    """
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 5,
                    "label": "Generate Key",
                    "url": "https://aio.deng.my.id/license",
                    "emoji": {"name": "\U0001f511"},
                },
                {
                    "type": 2,
                    "style": 2,
                    "label": "Key Stats",
                    "custom_id": BUTTON_KEY_STATS,
                    "emoji": {"name": "\U0001f4ca"},
                    "disabled": False,
                },
                {
                    "type": 2,
                    "style": 1,
                    "label": "Select Version",
                    "custom_id": BUTTON_SELECT_VERSION,
                    "emoji": {"name": "\U0001f4e6"},
                    "disabled": False,
                },
            ],
        }
    ]


# ── Ephemeral response builders ────────────────────────────────────────────────

def build_generate_success_response(full_key: str) -> dict[str, Any]:
    """Build the ephemeral embed shown to a user after key generation.

    full_key: the complete DENG-XXXX-XXXX-XXXX-XXXX key string.
    """
    return {
        "ephemeral": True,
        "content": format_copy_license_key_content(full_key),
        "embed": {
            "title": "\U0001f511 Your License Key",
            "color": 0x27AE60,
            "description": (
                "This key is linked to your Discord account.\n"
                "Paste it into **DENG Tool: Rejoin**.\n\n"
                "\u26a0\ufe0f **Save this key now.** It will not be shown again.\n"
                "Keep it private — do not share it."
            ),
            "footer": {"text": "DENG Tool \u00b7 Key generated"},
        },
    }


def build_generate_cooldown_response(remaining_seconds: int) -> dict[str, Any]:
    """Ephemeral embed when a user tries to generate a key too soon."""
    return {
        "ephemeral": True,
        "embed": {
            "title": "\u23f3 Please Wait",
            "color": 0xF39C12,
            "description": (
                f"You can generate another key in **{remaining_seconds} second(s)**.\n"
                "Key generation has a 1-minute cooldown after the first key."
            ),
            "footer": {"text": "DENG Tool: Rejoin"},
        },
    }


def build_key_list_response(key_records: list[dict]) -> dict[str, Any]:
    """Ephemeral response listing a user's active keys (for /license_panel status)."""
    copy_keys: list[str] = []
    for rec in key_records:
        fk = rec.get("full_key_plaintext")
        if fk:
            copy_keys.append(str(fk).strip())

    content = format_copy_license_keys_lines(copy_keys) if copy_keys else ""

    if not key_records:
        description = "You have no license keys yet. Click **Generate Key** to create one."
    else:
        lines: list[str] = []
        for rec in key_records:
            status_icon = {
                "active": "\U0001f7e2",
                "expired": "\U0001f534",
                "revoked": "\u26ab",
            }.get(rec.get("status", ""), "\u26aa")
            device = rec.get("bound_device") or "(unbound)"
            last_seen = rec.get("last_seen_at")
            last_seen_str = f"\n   \u23f1 Last seen: `{last_seen}`" if last_seen else ""
            bound_icon = "\U0001f4f1" if device != "(unbound)" else "\U0001f534"
            full = rec.get("full_key_plaintext")
            if full:
                key_block = "**Key:** copy block above (full key not repeated here)."
            else:
                ref = rec.get("masked_key", "???")
                key_block = (
                    "**Full key is not recoverable for copying from the server.** "
                    "If this is an older key, export storage may not have been enabled when it was created. "
                    f"Reference only (not a complete key): **{ref}**"
                )
            lines.append(
                f"{status_icon} {key_block}\n"
                f"   — {rec.get('status', 'unknown')}\n"
                f"   {bound_icon} Device: {device}{last_seen_str}"
            )
        description = "\n\n".join(lines)
    out: dict[str, Any] = {
        "ephemeral": True,
        "embed": {
            "title": "\U0001f4cb Your License Keys",
            "color": 0x2F80ED,
            "description": description,
            "footer": {"text": "DENG Tool: Rejoin"},
        },
    }
    if content:
        out["content"] = content
    return out


def build_not_owner_response() -> dict[str, Any]:
    """Ephemeral embed when a non-owner tries an owner-only slash command."""
    return {
        "ephemeral": True,
        "embed": {
            "title": "\u274c Owner Only",
            "color": 0xE74C3C,
            "description": "\u274c This command is owner-only.",
        },
    }


# ── Admin command spec (documentation / registration helper) ───────────────────

def get_slash_command_specs() -> list[dict[str, Any]]:
    """Return a list of slash command spec dicts for /license_panel sub-commands.

    Use this to generate discord.py app_commands or disnake SlashCommand trees.
    Each dict: {name, description, options?, owner_only}
    """
    return [
        {
            "group": SLASH_GROUP,
            "name": SLASH_SET_CHANNEL,
            "description": "Set the channel where the license panel embed will be posted.",
            "owner_only": True,
            "options": [
                {"name": "channel", "description": "Target text channel", "required": True, "type": "CHANNEL"},
            ],
        },
        {
            "group": SLASH_GROUP,
            "name": SLASH_POST,
            "description": "Post or re-post the license panel embed in the configured channel.",
            "owner_only": True,
            "options": [],
        },
        {
            "group": SLASH_GROUP,
            "name": SLASH_REFRESH,
            "description": "Edit the existing panel message in place (update embed content).",
            "owner_only": True,
            "options": [],
        },
        {
            "group": SLASH_GROUP,
            "name": SLASH_STATUS,
            "description": "Show your own license key status (ephemeral).",
            "owner_only": False,
            "options": [],
        },
        {
            "group": SLASH_GROUP,
            "name": SLASH_CLEAR,
            "description": "Remove the saved panel config without deleting the message.",
            "owner_only": True,
            "options": [],
        },
        {
            "group": SLASH_GROUP,
            "name": SLASH_ADMIN_STATUS,
            "description": "Show panel config and store stats (owner only).",
            "owner_only": True,
            "options": [],
        },
    ]
