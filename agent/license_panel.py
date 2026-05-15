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
BUTTON_RESET_HWID = "license_panel:reset_hwid"
BUTTON_REDEEM     = "license_panel:redeem"
BUTTON_KEY_STATS  = "license_panel:key_stats"
BUTTON_SELECT_VERSION = "license_panel:select_version"

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
    • Title  : "DENG Tool: Rejoin Panel"
    • Color  : 0x2F80ED (brand blue)
    • Fields : 5 instruction cards — Generate, Reset HWID, Redeem, Key Stats, Select Version
    • Footer : "DENG Tool · All responses are private"
    """
    return {
        "title": "DENG Tool: Rejoin Panel",
        "color": 0x2F80ED,
        "description": (
            "Generate or redeem your license key, reset your device binding, "
            "and choose which DENG Tool: Rejoin version to install.\n"
            "All key-related responses are **private** — only you will see them."
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
            {
                "name": "\U0001f4ca Key Stats",
                "value": (
                    "Private summary of keys linked to your Discord account.\n"
                    "**Used / Device bound** or **Unused / Ready for first device**.\n"
                    "**Download Keys** exports a short text list."
                ),
                "inline": True,
            },
        ],
        "footer": {"text": "DENG Tool \u00b7 All responses are private"},
        "timestamp": None,   # Caller should set this to current UTC ISO string
    }


def build_panel_buttons() -> list[dict[str, Any]]:
    """Return an action-row payload with the 4 panel buttons.

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
                    "style": 1,
                    "label": "Generate Key",
                    "custom_id": BUTTON_GENERATE,
                    "emoji": {"name": "\U0001f511"},
                    "disabled": False,
                },
                {
                    "type": 2,
                    "style": 2,
                    "label": "Reset HWID",
                    "custom_id": BUTTON_RESET_HWID,
                    "emoji": {"name": "\u267b\ufe0f"},
                    "disabled": False,
                },
                {
                    "type": 2,
                    "style": 3,
                    "label": "Redeem Key",
                    "custom_id": BUTTON_REDEEM,
                    "emoji": {"name": "\U0001f39f\ufe0f"},
                    "disabled": False,
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


def build_redeem_success_response(display_key: str) -> dict[str, Any]:
    """Ephemeral response after successfully redeeming an existing key."""
    return {
        "ephemeral": True,
        "content": format_copy_license_key_content(display_key),
        "embed": {
            "title": "\U0001f39f\ufe0f Key Redeemed",
            "color": 0x27AE60,
            "description": (
                "This key is now linked to your Discord account.\n"
                "Paste it into **DENG Tool: Rejoin**.\n"
                "Run the tool once to bind this device — that happens on first successful verification."
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
                    f"Reference only (not a complete key): **{ref}**\n"
                    "Use **Recover Full Key** in Key Stats if you still have the key text."
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
            "footer": {"text": "Use Reset HWID to unbind a device when needed."},
        },
    }
    if content:
        out["content"] = content
    return out


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


def build_redeem_already_owned_response(
    message: str | None = None,
    *,
    export_backfilled: bool = False,
    copyable_key: str | None = None,
) -> dict[str, Any]:
    """Ephemeral embed when a user tries to redeem their own already-attached key."""
    if copyable_key:
        desc = "This key is already attached to your account."
        if export_backfilled:
            desc += "\n\n**Full key export has been enabled** for this key in the database."
    elif export_backfilled:
        desc = (
            (message or "This key is already attached to your account.")
            + "\n\n**Full key export has been enabled** for this key."
        )
    else:
        desc = message or "This key is already attached to your account."
    out: dict[str, Any] = {
        "ephemeral": True,
        "embed": {
            "title": "\u2139\ufe0f Key Already Attached",
            "color": 0x2F80ED,
            "description": desc,
        },
    }
    if copyable_key:
        out["content"] = format_copy_license_key_content(copyable_key)
    return out


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


def build_reset_selector_embed(keys_with_state: list[dict]) -> dict[str, Any]:
    """Ephemeral embed shown when the user opens the HWID reset key selector.

    keys_with_state: list of dicts from store.list_user_keys_with_binding_state().
    Legend (above the list): 🟢 no device linked, 🟡 bound to a device.
    Each row is numbered; full key in backticks when recoverable, else masked reference.
    """
    legend = "\U0001f7e2 No device linked\n\U0001f7e1 Bound to a device"
    lines: list[str] = []
    for i, k in enumerate(keys_with_state, start=1):
        bound = bool(k.get("active_binding"))
        # 🟢 = no device linked, 🟡 = bound to a device
        icon = "\U0001f7e1" if bound else "\U0001f7e2"
        fk = k.get("full_key_plaintext")
        mk = k.get("masked_key", "???")
        if fk:
            key_disp = f"`{fk}`"
        else:
            key_disp = f"**{mk}** (reference only)"
        suffix = "Bound to a device" if bound else "No device bound"
        lines.append(f"{i}. {icon} {key_disp} — {suffix}")
    key_list = "\n".join(lines)
    description = (
        "Select which key to reset from the dropdown below, "
        "then click **Confirm Reset**.\n\n"
        f"{legend}\n\n"
        f"{key_list}"
    )
    return {
        "ephemeral": True,
        "embed": {
            "title": "\u267b\ufe0f Reset HWID \u2014 Select Key",
            "color": 0x2F80ED,
            "description": description,
            "footer": {"text": "DENG Tool \u00b7 Limited to 5 resets per 24 hours per key"},
        },
    }


def build_reset_no_keys_response() -> dict[str, Any]:
    """Ephemeral embed when the user has no (non-revoked) license keys to reset."""
    return {
        "ephemeral": True,
        "embed": {
            "title": "\u274c No Keys Found",
            "color": 0xE74C3C,
            "description": (
                "You have no license keys to reset.\n"
                "Use **Generate Key** to create one."
            ),
        },
    }


def build_reset_mixed_summary_embed(results: list[dict]) -> dict[str, Any]:
    """Ephemeral embed listing per-key HWID reset outcomes.

    Each result dict: {display_key (str), success (bool), message (str)}.
    """
    lines: list[str] = []
    for r in results:
        icon = "\u2705" if r.get("success") else "\u274c"  # ✅ / ❌
        dk = r.get("display_key") or r.get("masked_key", "???")
        lines.append(f"{icon} `{dk}` \u2014 {r.get('message', '')}")
    description = "\n".join(lines) if lines else "No keys were processed."
    all_ok = bool(results) and all(r.get("success") for r in results)
    none_ok = bool(results) and not any(r.get("success") for r in results)
    color = 0x27AE60 if all_ok else (0xE74C3C if none_ok else 0xF39C12)
    return {
        "ephemeral": True,
        "embed": {
            "title": "\u267b\ufe0f HWID Reset Results",
            "color": color,
            "description": description,
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
