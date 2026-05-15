"""LicensePanelCog — discord.py 2.x cog for DENG Tool Rejoin license panel.

Slash commands
--------------
  /license_panel set_channel   — owner-only; sets channel for the panel embed
  /license_panel post          — owner-only; posts the embed + buttons
  /license_panel refresh       — owner-only; edits the embed in-place
  /license_panel status        — any user; shows own key status (ephemeral)
  /license_panel clear         — owner-only; clears saved panel config
  /license_panel admin_status  — owner-only; shows panel config + store stats

Button handlers
---------------
  Generate Key   (custom_id = "license_panel:generate")
  Reset HWID     (custom_id = "license_panel:reset_hwid")
  Redeem Key     (custom_id = "license_panel:redeem")
  Key Stats      (custom_id = "license_panel:key_stats")

All button flows are EPHEMERAL.  The panel embed itself is public (pinned-style).
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from agent.branding import apply_branding_to_embed_dict
from agent.license_panel import (
    BUTTON_GENERATE,
    BUTTON_KEY_STATS,
    BUTTON_REDEEM,
    BUTTON_RESET_HWID,
    SLASH_GROUP,
    build_generate_limit_response,
    build_generate_success_response,
    build_key_list_response,
    build_not_owner_response,
    build_panel_embed,
    build_redeem_already_owned_response,
    build_redeem_error_response,
    build_redeem_success_response,
    build_reset_active_warning_response,
    build_reset_limit_response,
    build_reset_mixed_summary_embed,
    build_reset_no_binding_response,
    build_reset_no_keys_response,
    build_reset_selector_embed,
    build_reset_success_response,
)
from agent.license_store import (
    MAX_HWID_RESETS_PER_24H,
    ActiveKeyWarning,
    BaseLicenseStore,
    KeyAlreadySelfOwned,
    KeyNotFoundError,
    KeyOwnershipError,
    NoActiveBindingError,
    ResetLimitError,
    UserLimitError,
)

log = logging.getLogger("deng.rejoin.bot.panel")


# ── Owner helpers ─────────────────────────────────────────────────────────────

def _owner_ids() -> frozenset[int]:
    """Parse LICENSE_OWNER_DISCORD_IDS env var; evaluated each call so live reload works."""
    raw = os.environ.get("LICENSE_OWNER_DISCORD_IDS", "")
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return frozenset(ids)


def _is_owner(user: discord.User | discord.Member) -> bool:
    return user.id in _owner_ids()


# ── Embed helper ──────────────────────────────────────────────────────────────

def _embed_from_payload(payload: dict[str, Any]) -> discord.Embed:
    """Convert builder payload dict → discord.Embed."""
    embed_dict = dict(payload["embed"])
    apply_branding_to_embed_dict(embed_dict)
    return discord.Embed.from_dict(embed_dict)


async def _respond_ephemeral_payload(
    interaction: discord.Interaction,
    payload: dict[str, Any],
    *,
    followup: bool = False,
) -> None:
    embed = _embed_from_payload(payload)
    if followup:
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Redeem modal ──────────────────────────────────────────────────────────────

class RedeemModal(discord.ui.Modal, title="Redeem License Key"):
    """Text-input modal that collects the raw DENG-XXXX-XXXX-XXXX-XXXX key."""

    key_input: discord.ui.TextInput = discord.ui.TextInput(
        label="License Key",
        placeholder="DENG-XXXX-XXXX-XXXX-XXXX",
        min_length=19,
        max_length=24,
        style=discord.TextStyle.short,
    )

    def __init__(self, store: BaseLicenseStore) -> None:
        super().__init__()
        self._store = store

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_key = self.key_input.value.strip()
        uid = str(interaction.user.id)
        username = str(interaction.user)

        await interaction.response.defer(ephemeral=True)

        try:
            self._store.get_or_create_user(uid, username)
            masked = self._store.redeem_key_for_user(uid, raw_key)
            payload = build_redeem_success_response(masked)
        except KeyAlreadySelfOwned as exc:
            payload = build_redeem_already_owned_response(str(exc))
        except (KeyNotFoundError, KeyOwnershipError, UserLimitError) as exc:
            payload = build_redeem_error_response(str(exc))

        await _respond_ephemeral_payload(interaction, payload, followup=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("RedeemModal error for user %s: %s", interaction.user.id, error)
        try:
            await interaction.followup.send(
                "❌ An unexpected error occurred. Please try again.", ephemeral=True
            )
        except discord.HTTPException:
            pass


# ── HWID reset selector views ─────────────────────────────────────────────────

class ResetHwidSelect(discord.ui.Select):
    """Dropdown listing the user's keys with 🟢/🟡 binding state indicators."""

    def __init__(self, keys_with_state: list[dict]) -> None:
        options: list[discord.SelectOption] = []
        for k in keys_with_state:
            icon = "🟢" if k.get("active_binding") else "🟡"
            label = k.get("masked_key", "???")
            if k.get("active_binding"):
                model = k.get("device_model") or "Unknown device"
                last = k.get("last_seen_at")
                desc = f"Device: {model}" + (f" · Last seen: {last[:10]}" if last else "")
            else:
                desc = k.get("reason_if_not_resettable") or "No device bound"
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=k["key_id"],
                    description=desc[:100],
                    emoji=icon,
                )
            )
        super().__init__(
            placeholder="Select a key to reset...",
            min_values=1,
            max_values=min(25, len(options)),
            options=options,
            custom_id="reset_hwid:select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        # Silently acknowledge; selection is read by ConfirmResetButton
        await interaction.response.defer()


class ConfirmResetButton(discord.ui.Button):
    """Processes the selected key(s) from the dropdown and runs HWID reset."""

    def __init__(
        self,
        store: BaseLicenseStore,
        uid: str,
        keys_with_state: list[dict],
    ) -> None:
        super().__init__(
            label="Confirm Reset",
            style=discord.ButtonStyle.danger,
            custom_id="reset_hwid:confirm",
            emoji="♻️",
        )
        self._store = store
        self._uid = uid
        self._key_map: dict[str, dict] = {k["key_id"]: k for k in keys_with_state}

    async def callback(self, interaction: discord.Interaction) -> None:
        select: ResetHwidSelect | None = next(
            (c for c in self.view.children if isinstance(c, ResetHwidSelect)), None
        )
        selected_ids: list[str] = select.values if select and select.values else []

        if not selected_ids:
            await interaction.response.send_message(
                "⚠️ Please choose at least one key from the dropdown first.",
                ephemeral=True,
            )
            return

        results: list[dict] = []
        for key_id in selected_ids:
            state = self._key_map.get(key_id, {})
            masked = state.get("masked_key", "???")
            if not state.get("can_reset"):
                results.append({
                    "masked_key": masked,
                    "success": False,
                    "message": state.get("reason_if_not_resettable") or "Cannot reset this key.",
                })
                continue
            try:
                self._store.reset_hwid(self._uid, key_id)
                results.append({
                    "masked_key": masked,
                    "success": True,
                    "message": "Device binding cleared.",
                })
            except NoActiveBindingError:
                results.append({
                    "masked_key": masked,
                    "success": False,
                    "message": "No device binding to clear.",
                })
            except ResetLimitError:
                count = self._store.get_reset_count_24h(key_id)
                results.append({
                    "masked_key": masked,
                    "success": False,
                    "message": f"Reset limit reached ({count}/{MAX_HWID_RESETS_PER_24H} today).",
                })
            except ActiveKeyWarning as exc:
                m = re.search(r"(\d+)s ago", str(exc))
                elapsed = int(m.group(1)) if m else 0
                mins, secs = elapsed // 60, elapsed % 60
                results.append({
                    "masked_key": masked,
                    "success": False,
                    "message": f"Key active {mins}m {secs}s ago — wait 5 min first.",
                })

        for child in self.view.children:
            child.disabled = True

        embed = _embed_from_payload(build_reset_mixed_summary_embed(results))
        try:
            await interaction.response.edit_message(embed=embed, view=self.view)
        except discord.HTTPException:
            await interaction.response.send_message(embed=embed, ephemeral=True)


class CancelResetButton(discord.ui.Button):
    """Cancels the HWID reset flow and disables all selector components."""

    def __init__(self) -> None:
        super().__init__(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id="reset_hwid:cancel",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        for child in self.view.children:
            child.disabled = True
        embed_dict = {
            "title": "\u2716 Reset Cancelled",
            "description": "HWID reset was cancelled. No changes were made.",
            "color": 0x95A5A6,
        }
        apply_branding_to_embed_dict(embed_dict)
        embed = discord.Embed.from_dict(embed_dict)
        try:
            await interaction.response.edit_message(embed=embed, view=self.view)
        except discord.HTTPException:
            await interaction.response.send_message(embed=embed, ephemeral=True)


class ResetHwidSelectView(discord.ui.View):
    """Ephemeral, non-persistent view shown when a user clicks Reset HWID.

    Contains: dropdown key selector, Confirm Reset button, Cancel button.
    Times out after 120 seconds; components are disabled on timeout.
    """

    def __init__(
        self,
        store: BaseLicenseStore,
        uid: str,
        keys_with_state: list[dict],
    ) -> None:
        super().__init__(timeout=120)
        self.add_item(ResetHwidSelect(keys_with_state))
        self.add_item(ConfirmResetButton(store, uid, keys_with_state))
        self.add_item(CancelResetButton())

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


KEY_STATS_PAGE_SIZE = 5


def _build_key_stats_ephemeral_parts(
    rows_all: list[dict], page: int
) -> tuple[str, list[dict[str, Any]], int, int, int]:
    """Return plain-text header, embed dicts (branded), clamped page, total_pages, row count."""
    from agent.key_stats_format import (
        build_key_stats_embed_dicts,
        build_key_stats_empty_embed_dict,
        format_stats_header_plain,
    )

    n = len(rows_all)
    total_pages = max(1, (n + KEY_STATS_PAGE_SIZE - 1) // KEY_STATS_PAGE_SIZE) if n else 1
    page = max(0, min(page, total_pages - 1))
    header = format_stats_header_plain(total=n, page=page, total_pages=total_pages)
    if n == 0:
        embed_dicts = [build_key_stats_empty_embed_dict()]
    else:
        sl = rows_all[page * KEY_STATS_PAGE_SIZE : (page + 1) * KEY_STATS_PAGE_SIZE]
        embed_dicts = build_key_stats_embed_dicts(sl)
    for ed in embed_dicts:
        apply_branding_to_embed_dict(ed)
    return header, embed_dicts, page, total_pages, n


class KeyStatsCloseButton(discord.ui.Button):
    def __init__(self, host: "KeyStatsView") -> None:
        super().__init__(
            label="Close",
            style=discord.ButtonStyle.secondary,
            custom_id="license_panel:ks_close",
        )
        self._host = host

    async def callback(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self._host._owner_id:
            await interaction.response.send_message(
                "This key stats view is not yours.", ephemeral=True
            )
            return
        await interaction.response.edit_message(content="Closed.", embeds=[], view=None)


class KeyStatsDownloadButton(discord.ui.Button):
    def __init__(self, host: "KeyStatsView", *, disabled: bool) -> None:
        super().__init__(
            label="Download Keys",
            style=discord.ButtonStyle.primary,
            custom_id="license_panel:ks_dl",
            disabled=disabled,
        )
        self._host = host

    async def callback(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self._host._owner_id:
            await interaction.response.send_message(
                "This key stats view is not yours.", ephemeral=True
            )
            return
        from io import BytesIO

        from agent.key_stats_format import build_key_stats_download_body

        rows = self._host._store.get_user_key_export_rows(self._host._owner_id)
        body = build_key_stats_download_body(
            discord_user_id=self._host._owner_id,
            rows=rows,
        )
        buf = BytesIO(body.encode("utf-8"))
        buf.seek(0)
        filename = f"my_keys_{self._host._owner_id}.txt"
        file = discord.File(buf, filename=filename)
        dl_embed_dict: dict[str, Any] = {
            "title": "Your Keys Download",
            "description": (
                f"Total: {len(rows)} keys\n\n"
                "Download the attached file to view your keys."
            ),
            "color": 0x2F80ED,
            "footer": {"text": "DENG Tool \u00b7 Key Stats"},
        }
        apply_branding_to_embed_dict(dl_embed_dict)
        dl_embed = discord.Embed.from_dict(dl_embed_dict)
        await interaction.response.send_message(embed=dl_embed, file=file, ephemeral=True)


class KeyStatsPrevButton(discord.ui.Button):
    def __init__(self, host: "KeyStatsView", *, disabled: bool) -> None:
        super().__init__(
            label="Previous",
            style=discord.ButtonStyle.secondary,
            custom_id="license_panel:ks_prev",
            disabled=disabled,
        )
        self._host = host

    async def callback(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self._host._owner_id:
            await interaction.response.send_message(
                "This key stats view is not yours.", ephemeral=True
            )
            return
        rows = self._host._store.list_user_keys_for_stats(self._host._owner_id)
        new_page = self._host._page - 1
        content, embed_dicts, new_page, _, _ = _build_key_stats_ephemeral_parts(rows, new_page)
        embeds = [discord.Embed.from_dict(d) for d in embed_dicts]
        new_view = KeyStatsView(self._host._store, self._host._owner_id, new_page)
        await interaction.response.edit_message(content=content, embeds=embeds, view=new_view)


class KeyStatsNextButton(discord.ui.Button):
    def __init__(self, host: "KeyStatsView", *, disabled: bool) -> None:
        super().__init__(
            label="Next",
            style=discord.ButtonStyle.secondary,
            custom_id="license_panel:ks_next",
            disabled=disabled,
        )
        self._host = host

    async def callback(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self._host._owner_id:
            await interaction.response.send_message(
                "This key stats view is not yours.", ephemeral=True
            )
            return
        rows = self._host._store.list_user_keys_for_stats(self._host._owner_id)
        new_page = self._host._page + 1
        content, embed_dicts, new_page, _, _ = _build_key_stats_ephemeral_parts(rows, new_page)
        embeds = [discord.Embed.from_dict(d) for d in embed_dicts]
        new_view = KeyStatsView(self._host._store, self._host._owner_id, new_page)
        await interaction.response.edit_message(content=content, embeds=embeds, view=new_view)


class KeyStatsView(discord.ui.View):
    """Ephemeral paginated Key Stats + download (not persistent)."""

    def __init__(self, store: BaseLicenseStore, owner_id: str, page: int = 0) -> None:
        super().__init__(timeout=600)
        self._store = store
        self._owner_id = owner_id
        rows = store.list_user_keys_for_stats(owner_id)
        n = len(rows)
        total_pages = max(1, (n + KEY_STATS_PAGE_SIZE - 1) // KEY_STATS_PAGE_SIZE) if n else 1
        self._page = max(0, min(page, total_pages - 1))
        self.add_item(KeyStatsPrevButton(self, disabled=self._page <= 0 or n == 0))
        self.add_item(
            KeyStatsNextButton(self, disabled=self._page >= total_pages - 1 or n == 0)
        )
        self.add_item(KeyStatsDownloadButton(self, disabled=n == 0))
        self.add_item(KeyStatsCloseButton(self))

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


# ── Persistent panel view ─────────────────────────────────────────────────────

class PanelView(discord.ui.View):
    """Persistent view with Generate / Reset HWID / Redeem / Key Stats buttons.

    timeout=None keeps the view alive across bot restarts when registered
    via ``bot.add_view(view, message_id=<id>)``.
    """

    def __init__(self, store: BaseLicenseStore) -> None:
        super().__init__(timeout=None)
        self._store = store

    # ── Generate Key ──────────────────────────────────────────────────────────

    @discord.ui.button(
        label="Generate Key",
        style=discord.ButtonStyle.primary,
        custom_id=BUTTON_GENERATE,
        emoji="🔑",
    )
    async def btn_generate(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        uid = str(interaction.user.id)
        username = str(interaction.user)

        await interaction.response.defer(ephemeral=True)

        user = self._store.get_or_create_user(uid, username)
        max_keys = user.get("max_keys", 1)

        try:
            full_key = self._store.create_key_for_user(uid, created_by=uid)
            payload = build_generate_success_response(full_key)
        except UserLimitError:
            payload = build_generate_limit_response(max_keys)

        await _respond_ephemeral_payload(interaction, payload, followup=True)

    # ── Reset HWID ────────────────────────────────────────────────────────────

    @discord.ui.button(
        label="Reset HWID",
        style=discord.ButtonStyle.secondary,
        custom_id=BUTTON_RESET_HWID,
        emoji="♻️",
    )
    async def btn_reset_hwid(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        uid = str(interaction.user.id)
        username = str(interaction.user)

        self._store.get_or_create_user(uid, username)
        keys_with_state = self._store.list_user_keys_with_binding_state(uid)

        if not keys_with_state:
            embed = _embed_from_payload(build_reset_no_keys_response())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = _embed_from_payload(build_reset_selector_embed(keys_with_state))
        view = ResetHwidSelectView(self._store, uid, keys_with_state)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ── Redeem Key ────────────────────────────────────────────────────────────

    @discord.ui.button(
        label="Redeem Key",
        style=discord.ButtonStyle.success,
        custom_id=BUTTON_REDEEM,
        emoji="🎟️",
    )
    async def btn_redeem(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(RedeemModal(self._store))

    # ── Key Stats ─────────────────────────────────────────────────────────────

    @discord.ui.button(
        label="Key Stats",
        style=discord.ButtonStyle.secondary,
        custom_id=BUTTON_KEY_STATS,
        emoji="\U0001f4ca",
    )
    async def btn_key_stats(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        uid = str(interaction.user.id)
        username = str(interaction.user)
        self._store.get_or_create_user(uid, username)
        await interaction.response.defer(ephemeral=True)
        rows = self._store.list_user_keys_for_stats(uid)
        content, embed_dicts, page, _, _ = _build_key_stats_ephemeral_parts(rows, 0)
        embeds = [discord.Embed.from_dict(d) for d in embed_dicts]
        view = KeyStatsView(self._store, uid, page)
        await interaction.followup.send(
            content=content, embeds=embeds, view=view, ephemeral=True
        )


# ── Cog ───────────────────────────────────────────────────────────────────────

class LicensePanelCog(commands.Cog, name="LicensePanel"):
    """Hosts the /license_panel command group and wires all button + modal logic."""

    def __init__(self, bot: commands.Bot, store: BaseLicenseStore) -> None:
        self.bot = bot
        self._store = store

        self._panel_group = app_commands.Group(
            name=SLASH_GROUP,
            description="DENG Tool license panel management.",
        )
        self._register_commands()
        bot.tree.add_command(self._panel_group)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _owner_denied(self) -> discord.Embed:
        return _embed_from_payload(build_not_owner_response())

    async def _get_panel_channel(
        self, guild: discord.Guild, channel_id: str
    ) -> discord.TextChannel | None:
        try:
            ch = guild.get_channel(int(channel_id))
            return ch if isinstance(ch, discord.TextChannel) else None
        except (ValueError, TypeError):
            return None

    # ── Command registration ──────────────────────────────────────────────────

    def _register_commands(self) -> None:  # noqa: C901 (complex but linear)
        store = self._store
        bot = self.bot

        # /license_panel set_channel ─────────────────────────────────────────

        @self._panel_group.command(
            name="set_channel",
            description="Set the channel where the license panel embed will be posted.",
        )
        @app_commands.describe(channel="Target text channel")
        async def cmd_set_channel(
            interaction: discord.Interaction,
            channel: discord.TextChannel,
        ) -> None:
            if not _is_owner(interaction.user):
                await interaction.response.send_message(
                    embed=self._owner_denied(), ephemeral=True
                )
                return

            guild_id = str(interaction.guild_id)
            cfg = store.get_panel_config(guild_id)
            existing_msg_id = cfg["message_id"] if cfg else ""

            store.save_panel_config(
                guild_id,
                str(channel.id),
                existing_msg_id,
                str(interaction.user.id),
            )
            store.audit_admin_action(
                str(interaction.user.id),
                "set_panel_channel",
                target_type="channel",
                target_id=str(channel.id),
            )
            await interaction.response.send_message(
                f"✅ License panel channel set to {channel.mention}.\n"
                "Run `/license_panel post` to post the embed.",
                ephemeral=True,
            )

        # /license_panel post ────────────────────────────────────────────────

        @self._panel_group.command(
            name="post",
            description="Post or re-post the license panel embed in the configured channel.",
        )
        async def cmd_post(interaction: discord.Interaction) -> None:
            if not _is_owner(interaction.user):
                await interaction.response.send_message(
                    embed=self._owner_denied(), ephemeral=True
                )
                return

            guild_id = str(interaction.guild_id)
            cfg = store.get_panel_config(guild_id)

            if not cfg or not cfg.get("channel_id"):
                await interaction.response.send_message(
                    "❌ No channel configured. Run `/license_panel set_channel` first.",
                    ephemeral=True,
                )
                return

            # Prevent duplicate panel posts
            if cfg.get("message_id"):
                await interaction.response.send_message(
                    "⚠️ A panel already exists in this server.\n"
                    "• Use `/license_panel refresh` to update the embed.\n"
                    "• Use `/license_panel clear` then re-post to replace it.",
                    ephemeral=True,
                )
                return

            channel = await self._get_panel_channel(
                interaction.guild, cfg["channel_id"]
            )
            if channel is None:
                await interaction.response.send_message(
                    "❌ Configured channel not found. Re-run `/license_panel set_channel`.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)

            embed_dict = build_panel_embed()
            embed_dict["timestamp"] = datetime.now(timezone.utc).isoformat()
            apply_branding_to_embed_dict(embed_dict)
            embed = discord.Embed.from_dict(embed_dict)
            view = PanelView(store)
            msg = await channel.send(embed=embed, view=view)

            # Register as persistent so buttons survive restarts
            bot.add_view(view, message_id=msg.id)

            store.save_panel_config(
                guild_id,
                str(channel.id),
                str(msg.id),
                str(interaction.user.id),
            )
            store.audit_admin_action(
                str(interaction.user.id),
                "post_panel",
                target_type="message",
                target_id=str(msg.id),
            )
            await interaction.followup.send(
                f"✅ Panel posted in {channel.mention} — [jump to message]({msg.jump_url}).",
                ephemeral=True,
            )

        # /license_panel refresh ─────────────────────────────────────────────

        @self._panel_group.command(
            name="refresh",
            description="Edit the existing panel message in place (update embed content).",
        )
        async def cmd_refresh(interaction: discord.Interaction) -> None:
            if not _is_owner(interaction.user):
                await interaction.response.send_message(
                    embed=self._owner_denied(), ephemeral=True
                )
                return

            guild_id = str(interaction.guild_id)
            cfg = store.get_panel_config(guild_id)

            if not cfg or not cfg.get("message_id"):
                await interaction.response.send_message(
                    "❌ No panel message found. Use `/license_panel post` first.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)

            channel = await self._get_panel_channel(
                interaction.guild, cfg["channel_id"]
            )
            if channel is None:
                await interaction.followup.send(
                    "❌ Panel channel not found. Re-post the panel.", ephemeral=True
                )
                return

            try:
                msg = await channel.fetch_message(int(cfg["message_id"]))
            except discord.NotFound:
                await interaction.followup.send(
                    "❌ Panel message not found (deleted?). "
                    "Clear config with `/license_panel clear` then re-post.",
                    ephemeral=True,
                )
                return

            embed_dict = build_panel_embed()
            embed_dict["timestamp"] = datetime.now(timezone.utc).isoformat()
            apply_branding_to_embed_dict(embed_dict)
            embed = discord.Embed.from_dict(embed_dict)
            view = PanelView(store)
            await msg.edit(embed=embed, view=view)
            bot.add_view(view, message_id=msg.id)

            store.audit_admin_action(
                str(interaction.user.id),
                "refresh_panel",
                target_type="message",
                target_id=str(msg.id),
            )
            await interaction.followup.send("✅ Panel embed refreshed.", ephemeral=True)

        # /license_panel status ──────────────────────────────────────────────

        @self._panel_group.command(
            name="status",
            description="Show your own license key status (ephemeral).",
        )
        async def cmd_status(interaction: discord.Interaction) -> None:
            uid = str(interaction.user.id)
            username = str(interaction.user)
            store.get_or_create_user(uid, username)
            keys = store.list_user_keys(uid)
            payload = build_key_list_response(keys)
            await _respond_ephemeral_payload(interaction, payload)

        # /license_panel clear ───────────────────────────────────────────────

        @self._panel_group.command(
            name="clear",
            description="Remove the saved panel config (does NOT delete the panel message).",
        )
        async def cmd_clear(interaction: discord.Interaction) -> None:
            if not _is_owner(interaction.user):
                await interaction.response.send_message(
                    embed=self._owner_denied(), ephemeral=True
                )
                return

            guild_id = str(interaction.guild_id)
            store.clear_panel_config(guild_id)
            store.audit_admin_action(
                str(interaction.user.id),
                "clear_panel_config",
                target_type="guild",
                target_id=guild_id,
            )
            await interaction.response.send_message(
                "✅ Panel config cleared. The embed message (if any) was NOT deleted.\n"
                "Run `/license_panel set_channel` + `/license_panel post` to recreate.",
                ephemeral=True,
            )

        # /license_panel admin_status ────────────────────────────────────────

        @self._panel_group.command(
            name="admin_status",
            description="Show panel config and store stats (owner only).",
        )
        async def cmd_admin_status(interaction: discord.Interaction) -> None:
            if not _is_owner(interaction.user):
                await interaction.response.send_message(
                    embed=self._owner_denied(), ephemeral=True
                )
                return

            await interaction.response.defer(ephemeral=True)

            guild_id = str(interaction.guild_id)
            cfg = store.get_panel_config(guild_id)

            # ── Panel config section ──
            if cfg:
                ch_id = cfg.get("channel_id", "—")
                msg_id = cfg.get("message_id", "—")
                updated_by = cfg.get("updated_by", "—")
                updated_at = cfg.get("updated_at", "—")

                # Verify message still reachable
                msg_exists = "unknown"
                if ch_id and msg_id and ch_id != "—" and msg_id != "—":
                    try:
                        ch = interaction.guild.get_channel(int(ch_id))
                        if ch and isinstance(ch, discord.TextChannel):
                            try:
                                await ch.fetch_message(int(msg_id))
                                msg_exists = "✅ reachable"
                            except discord.NotFound:
                                msg_exists = "❌ deleted"
                            except discord.Forbidden:
                                msg_exists = "⚠️ no access"
                        else:
                            msg_exists = "❌ channel not found"
                    except (ValueError, TypeError):
                        msg_exists = "⚠️ bad ID"

                panel_lines = (
                    f"**Channel:** <#{ch_id}> (`{ch_id}`)\n"
                    f"**Message ID:** `{msg_id}`\n"
                    f"**Message:** {msg_exists}\n"
                    f"**Set by:** `{updated_by}`\n"
                    f"**Updated:** `{updated_at}`"
                )
            else:
                panel_lines = "*(no panel configured — run `/license_panel set_channel`)*"

            # ── Store stats section ──
            store_type = type(store).__name__
            try:
                db = store._load()  # type: ignore[attr-defined]
                total_users = len(db.get("users", {}))
                all_keys = db.get("keys", {})
                active_keys = sum(
                    1 for k in all_keys.values() if k.get("status") == "active"
                )
                total_keys = len(all_keys)
                audit_entries = len(db.get("audit_logs", []))
                store_path = str(store._path)  # type: ignore[attr-defined]
                store_lines = (
                    f"**Backend:** `{store_type}`\n"
                    f"**File:** `{store_path}`\n"
                    f"**Users:** {total_users}\n"
                    f"**Keys (active / total):** {active_keys} / {total_keys}\n"
                    f"**Audit log entries:** {audit_entries}"
                )
            except Exception as exc:  # noqa: BLE001
                store_lines = f"**Backend:** `{store_type}`\n⚠️ Could not read store: {exc}"

            embed = discord.Embed(
                title="🛠️ License Panel — Admin Status",
                color=0x2F80ED,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Panel Config", value=panel_lines, inline=False)
            embed.add_field(name="License Store", value=store_lines, inline=False)
            embed.set_footer(text=f"Guild: {guild_id}")

            await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Persistent view restoration ───────────────────────────────────────────

    async def restore_persistent_views(self) -> None:
        """Re-attach PanelView for every guild so buttons survive restarts."""
        for guild in self.bot.guilds:
            try:
                cfg = self._store.get_panel_config(str(guild.id))
            except Exception as exc:
                log.warning(
                    "Could not read panel config for guild %s (store error: %s). "
                    "If using Supabase, apply the migration first.",
                    guild.id,
                    exc,
                )
                continue
            if not cfg or not cfg.get("message_id"):
                continue
            try:
                msg_id = int(cfg["message_id"])
                view = PanelView(self._store)
                self.bot.add_view(view, message_id=msg_id)
                log.info(
                    "Restored persistent panel view: guild=%s message=%s",
                    guild.id,
                    msg_id,
                )
            except (ValueError, TypeError) as exc:
                log.warning(
                    "Could not restore panel view for guild %s: %s",
                    guild.id,
                    exc,
                )
