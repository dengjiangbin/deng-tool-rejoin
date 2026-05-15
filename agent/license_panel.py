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
    BUTTON_GENERATE   = "license_panel:generate"
    BUTTON_RESET_HWID = "license_panel:reset_hwid"
    BUTTON_REDEEM     = "license_panel:redeem"

All button response flows are EPHEMERAL — only the clicking user sees the result.

Required bot permissions: Send Messages, Embed Links, Read Message History.
"""

from __future__ import annotations

from typing import Any


# ── Button custom ID constants ─────────────────────────────────────────────────

BUTTON_GENERATE   = "license_panel:generate"
BUTTON_RESET_HWID = "license_panel:reset_hwid"
BUTTON_REDEEM     = "license_panel:redeem"

# ── Slash command names ────────────────────────────────────────────────────────

SLASH_GROUP       = "license_panel"
SLASH_SET_CHANNEL = "set_channel"
SLASH_POST        = "post"
SLASH_REFRESH     = "refresh"
SLASH_STATUS        = "status"
SLASH_CLEAR         = "clear"
SLASH_ADMIN_STATUS  = "admin_status"


# ── Panel embed builder ────────────────────────────────────────────────────────

def build_panel_embed() -> dict[str, Any]:
    """Return a Discord embed payload dict for the persistent license panel.

    The returned structure is framework-agnostic JSON.  Convert it to your
    discord.py / disnake Embed with ``discord.Embed.from_dict(payload)``.

    Structure
    ─────────
    • Title  : "DENG Tool — License Key Panel"
    • Color  : 0x2F80ED (brand blue)
    • Fields : 3 instruction cards — Generate, Reset HWID, Redeem
    • Footer : "DENG Tool · All responses are private"
    """
    return {
        "title": "DENG Tool \u2014 License Key Panel",
        "color": 0x2F80ED,
        "description": (
            "Use the buttons below to manage your license key.\n"
            "All responses are **private** — only you will see them."
        ),
        "fields": [
            {
                "name": "\U0001f511 Generate Key",
                "value": (
                    "Create a new license key.\n"
                    "Each account is allowed **1 key** by default.\n"
                    "Store it somewhere safe — it is only shown once."
                ),
                "inline": True,
            },
            {
                "name": "\u267b\ufe0f Reset HWID",
                "value": (
                    "Unbind your current device so you can move your key to a new install.\n"
                    "Limited to **5 resets every 24 hours**.\n"
                    "Wait at least 5 minutes after your last session."
                ),
                "inline": True,
            },
            {
                "name": "\U0001f39f\ufe0f Redeem Key",
                "value": (
                    "Attach an existing key to your Discord account.\n"
                    "Paste the full key (e.g. `DENG-XXXX-XXXX-XXXX-XXXX`)."
                ),
                "inline": True,
            },
        ],
        "footer": {"text": "DENG Tool \u00b7 All responses are private"},
        "timestamp": None,   # Caller should set this to current UTC ISO string
    }


def build_panel_buttons() -> list[dict[str, Any]]:
    """Return an action-row payload with the 3 panel buttons.

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
            "type": 1,       # ACTION_ROW
            "components": [
                {
                    "type": 2,           # BUTTON
                    "style": 1,          # PRIMARY (blurple)
                    "label": "Generate Key",
                    "custom_id": BUTTON_GENERATE,
                    "emoji": {"name": "\U0001f511"},
                    "disabled": False,
                },
                {
                    "type": 2,
                    "style": 2,          # SECONDARY (grey)
                    "label": "Reset HWID",
                    "custom_id": BUTTON_RESET_HWID,
                    "emoji": {"name": "\u267b\ufe0f"},
                    "disabled": False,
                },
                {
                    "type": 2,
                    "style": 3,          # SUCCESS (green)
                    "label": "Redeem Key",
                    "custom_id": BUTTON_REDEEM,
                    "emoji": {"name": "\U0001f39f\ufe0f"},
                    "disabled": False,
                },
            ],
        }
    ]


# ── Ephemeral response builders ────────────────────────────────────────────────

def build_generate_success_response(full_key: str) -> dict[str, Any]:
    """Build the ephemeral embed shown to a user after key generation.

    full_key: the complete DENG-XXXX-XXXX-XXXX-XXXX key string.
    This response is shown ONCE; the key is not stored in plaintext.
    """
    return {
        "ephemeral": True,
        "embed": {
            "title": "\U0001f511 Your License Key",
            "color": 0x27AE60,
            "description": (
                f"```\n{full_key}\n```\n"
                "\u26a0\ufe0f **Save this key now.** It will not be shown again.\n"
                "Keep it private — anyone with your key can bind it to their device."
            ),
            "footer": {"text": "DENG Tool \u00b7 Key generated"},
        },
    }


def build_generate_limit_response(max_keys: int) -> dict[str, Any]:
    """Ephemeral embed when a user has reached their key limit."""
    return {
        "ephemeral": True,
        "embed": {
            "title": "\u274c Key Limit Reached",
            "color": 0xE74C3C,
            "description": (
                f"You already have the maximum number of license keys (**{max_keys}**).\n"
                "Contact an admin if you need additional keys."
            ),
        },
    }


def build_reset_success_response() -> dict[str, Any]:
    """Ephemeral embed after a successful HWID reset."""
    return {
        "ephemeral": True,
        "embed": {
            "title": "\u267b\ufe0f HWID Reset",
            "color": 0x27AE60,
            "description": (
                "Your device binding has been cleared.\n"
                "You can now start the tool on a different device.\n\n"
                "The new device will be bound automatically on first use."
            ),
        },
    }


def build_reset_limit_response(resets_used: int, max_resets: int) -> dict[str, Any]:
    """Ephemeral embed when HWID reset limit is reached."""
    return {
        "ephemeral": True,
        "embed": {
            "title": "\u26d4 Reset Limit Reached",
            "color": 0xE74C3C,
            "description": (
                f"You have used **{resets_used}/{max_resets}** HWID resets in the last 24 hours.\n"
                "Please wait before trying again."
            ),
        },
    }


def build_reset_active_warning_response(elapsed_seconds: int) -> dict[str, Any]:
    """Ephemeral embed when the key was recently active."""
    minutes = elapsed_seconds // 60
    seconds = elapsed_seconds % 60
    elapsed_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    return {
        "ephemeral": True,
        "embed": {
            "title": "\u26a0\ufe0f Key Recently Active",
            "color": 0xF39C12,
            "description": (
                f"Your key was last seen **{elapsed_str} ago**.\n"
                "Stop the tool first and wait at least **5 minutes** before resetting HWID to avoid data loss."
            ),
        },
    }


def build_redeem_success_response(masked_key: str) -> dict[str, Any]:
    """Ephemeral embed after successfully redeeming an existing key."""
    return {
        "ephemeral": True,
        "embed": {
            "title": "\U0001f39f\ufe0f Key Redeemed",
            "color": 0x27AE60,
            "description": (
                f"Key **{masked_key}** has been attached to your account.\n"
                "Start the tool to activate your device binding."
            ),
        },
    }


def build_redeem_error_response(reason: str) -> dict[str, Any]:
    """Ephemeral embed when key redemption fails."""
    return {
        "ephemeral": True,
        "embed": {
            "title": "\u274c Redemption Failed",
            "color": 0xE74C3C,
            "description": reason,
        },
    }


def build_key_list_response(key_records: list[dict]) -> dict[str, Any]:
    """Ephemeral embed listing a user's active keys (for /license_panel status)."""
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
            lines.append(
                f"{status_icon} `{rec.get('masked_key', '???')}` "
                f"— {rec.get('status', 'unknown')}\n"
                f"   {bound_icon} Device: {device}{last_seen_str}"
            )
        description = "\n\n".join(lines)
    return {
        "ephemeral": True,
        "embed": {
            "title": "\U0001f4cb Your License Keys",
            "color": 0x2F80ED,
            "description": description,
            "footer": {"text": "Use Reset HWID to unbind a device. Keys shown are masked for security."},
        },
    }


def build_reset_no_binding_response() -> dict[str, Any]:
    """Ephemeral embed when Reset HWID is attempted but no device is bound."""
    return {
        "ephemeral": True,
        "embed": {
            "title": "\u26a0\ufe0f No Device Bound",
            "color": 0xF39C12,
            "description": (
                "No device is currently bound to your key.\n"
                "Start the tool once on your device to activate the binding, "
                "then you can reset it here if needed.\n\n"
                "_This does not count against your 5 daily HWID resets._"
            ),
        },
    }


def build_redeem_already_owned_response(message: str) -> dict[str, Any]:
    """Ephemeral embed when a user tries to redeem their own already-attached key."""
    return {
        "ephemeral": True,
        "embed": {
            "title": "\u2139\ufe0f Key Already Attached",
            "color": 0x2F80ED,
            "description": message,
        },
    }


def build_not_owner_response() -> dict[str, Any]:
    """Ephemeral embed when a non-owner tries an admin-only panel operation."""
    return {
        "ephemeral": True,
        "embed": {
            "title": "\U0001f6ab Unauthorized",
            "color": 0xE74C3C,
            "description": "You do not have permission to manage the license panel.",
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
