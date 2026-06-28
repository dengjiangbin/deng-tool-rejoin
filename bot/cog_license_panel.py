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
  Generate Key   (Discord link button -> https://aio.deng.my.id/license)
  Key Stats      (custom_id = "license_panel:key_stats")
  Select Version (custom_id = "license_panel:select_version")
  Guide          (Discord link button -> guide thread)

Removed features (Reset HWID, Redeem Key) are handled gracefully on old,
already-posted panel messages via RemovedFeatureView.

All button flows are EPHEMERAL.  The panel embed itself is public (pinned-style).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from agent.branding import apply_branding_to_embed_dict
from agent.license_owner_recovery import visible_license_rows_for_panel
from agent.key_stats_format import (
    build_license_admin_stats_description,
    build_license_event_log_description,
    filter_active_visible_license_rows,
)
from agent.license_panel import (
    BUTTON_GENERATE,
    BUTTON_KEY_STATS,
    BUTTON_SELECT_VERSION,
    REMOVED_BUTTON_REDEEM,
    REMOVED_BUTTON_RESET_HWID,
    SLASH_GROUP,
    build_guide_thread_url,
    build_key_list_response,
    build_not_owner_response,
    build_panel_embed,
)
from agent.rejoin_versions import (
    NO_PUBLIC_VERSIONS_MESSAGE,
    RejoinVersionInfo,
    format_install_instructions_plain,
    list_public_rejoin_versions,
)
from agent.license_store import (
    BaseLicenseStore,
    get_license_stats_for_discord_user,
)

log = logging.getLogger("deng.rejoin.bot.panel")


# ── Admin status helpers ───────────────────────────────────────────────────────

def _format_discord_ts(raw: "str | None") -> str:
    """Convert an ISO timestamp string to Discord ``<t:UNIX:f>`` format.

    Returns ``"Not set"`` when *raw* is empty, and falls back to a backtick-
    quoted string when the value cannot be parsed as a datetime.
    """
    if not raw or str(raw).strip() in ("—", ""):
        return "Not set"
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return f"<t:{int(dt.timestamp())}:f>"
    except (ValueError, TypeError):
        return f"`{raw}`"


def _format_user_mention(user_id_raw: "str | None") -> str:
    """Return a Discord mention for a user ID, e.g. ``<@110184213604499456>``.

    Falls back to ``<@ID>`` if the ID looks numeric, or a backtick-quoted
    string for non-numeric values.  Returns ``"Not set"`` when empty.
    """
    if not user_id_raw or str(user_id_raw).strip() in ("—", ""):
        return "Not set"
    uid = str(user_id_raw).strip()
    if uid.isdigit():
        return f"<@{uid}>"
    return f"`{uid}`"


# ── Owner helpers (central guard in bot.owner_guard) ──────────────────────────

from bot.owner_guard import (
    OWNER_ENV_VAR,
    is_bot_owner,
    owner_guard_enabled,
    parse_owner_discord_ids,
)

# Back-compat aliases used by tests and command handlers.
_is_owner = is_bot_owner
_owner_ids = parse_owner_discord_ids


def _tester_ids() -> frozenset[int]:
    """Parse REJOIN_TESTER_DISCORD_IDS — Select Version internal picker only."""
    raw = os.environ.get("REJOIN_TESTER_DISCORD_IDS", "")
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return frozenset(ids)


# ── Embed helper ──────────────────────────────────────────────────────────────

def _embed_from_payload(
    payload: dict[str, Any],
    *,
    include_thumbnail: bool = False,
) -> discord.Embed:
    """Convert builder payload dict → discord.Embed (no logo on replies by default)."""
    embed_dict = dict(payload["embed"])
    apply_branding_to_embed_dict(embed_dict, include_thumbnail=include_thumbnail)
    return discord.Embed.from_dict(embed_dict)


def _build_public_panel_embed() -> discord.Embed:
    """Persistent public panel embed — always includes the DENG logo thumbnail."""
    embed_dict = build_panel_embed()
    apply_branding_to_embed_dict(embed_dict, include_thumbnail=True)
    return discord.Embed.from_dict(embed_dict)


async def _respond_ephemeral_payload(
    interaction: discord.Interaction,
    payload: dict[str, Any],
    *,
    followup: bool = False,
) -> None:
    embed = _embed_from_payload(payload)
    send_kw: dict[str, Any] = {"embed": embed, "ephemeral": True}
    if "content" in payload and payload["content"]:
        send_kw["content"] = str(payload["content"])
    if followup:
        await interaction.followup.send(**send_kw)
    else:
        await interaction.response.send_message(**send_kw)


async def _post_license_log(
    guild: discord.Guild,
    store: "BaseLicenseStore",
    *,
    title: str,
    user: discord.User | discord.Member,
    key_serial: str,
    event_type: str,
) -> None:
    """Post a license event log embed to the configured license log channel.

    Silently does nothing if no log channel is configured for this guild or
    if the channel cannot be found / messaged.
    """
    try:
        cfg = store.get_license_log_config(str(guild.id))
        if not cfg:
            return
        channel_id = int(cfg.get("channel_id", 0))
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        uid = str(user.id)
        stats = get_license_stats_for_discord_user(store, uid)

        key_field_name = {
            "generated": "Generated Key",
        }.get(event_type, "Key")

        description = build_license_event_log_description(
            user_mention=f"<@{uid}>",
            key_field_label=key_field_name,
            key_value=key_serial,
            stats=stats,
        )

        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.from_rgb(0, 0, 0),
        )
        embed.set_footer(text="DENG Tool: Rejoin")
        await channel.send(embed=embed)
    except Exception as exc:  # noqa: BLE001
        log.debug("_post_license_log error: %s", exc)


# ── Removed-feature graceful fallback view ─────────────────────────────────────

class RemovedFeatureView(discord.ui.View):
    """Persistent catch-view for buttons removed during the license rebuild.

    Old, already-posted panel messages may still carry the legacy
    ``license_panel:reset_hwid`` and ``license_panel:redeem`` custom_ids. Without
    a registered handler those clicks fail with "interaction failed". Registering
    this view (timeout=None) with the same custom_ids makes them respond with a
    clean ephemeral notice instead. Newly posted panels never include these.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Reset HWID",
        style=discord.ButtonStyle.danger,
        custom_id=REMOVED_BUTTON_RESET_HWID,
    )
    async def removed_reset_hwid(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_message(
            "This feature has been removed.", ephemeral=True
        )

    @discord.ui.button(
        label="Redeem Key",
        style=discord.ButtonStyle.success,
        custom_id=REMOVED_BUTTON_REDEEM,
    )
    async def removed_redeem(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_message(
            "This feature has been removed.", ephemeral=True
        )


KEY_STATS_PAGE_SIZE = 5


def _build_key_stats_ephemeral_parts(
    rows_all: list[dict], page: int
) -> tuple[str, list[dict[str, Any]], int, int, int]:
    """Return plain-text header, embed dicts (branded), clamped page, total_pages, row count."""
    from agent.key_stats_format import (
        build_key_stats_embed_dicts,
        build_key_stats_empty_embed_dict,
        format_stats_page_content_header,
    )

    n = len(rows_all)
    total_pages = max(1, (n + KEY_STATS_PAGE_SIZE - 1) // KEY_STATS_PAGE_SIZE) if n else 1
    page = max(0, min(page, total_pages - 1))
    if n == 0:
        sl: list[dict[str, Any]] = []
        embed_dicts = [build_key_stats_empty_embed_dict()]
    else:
        sl = rows_all[page * KEY_STATS_PAGE_SIZE : (page + 1) * KEY_STATS_PAGE_SIZE]
        embed_dicts = build_key_stats_embed_dicts(sl)
    header = format_stats_page_content_header(sl, total=n, page=page, total_pages=total_pages)
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

        from agent.key_stats_format import build_key_stats_download_body, license_export_filename

        def _display_name(user: Any) -> str:
            for attr in ("display_name", "global_name", "name"):
                value = getattr(user, attr, None)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return ""

        username = _display_name(interaction.user)
        rows = visible_license_rows_for_panel(
            self._host._store.get_user_key_export_rows(self._host._owner_id)
        )
        body = build_key_stats_download_body(
            discord_user_id=self._host._owner_id,
            rows=rows,
            username=username,
        )
        buf = BytesIO(body.encode("utf-8"))
        buf.seek(0)
        filename = license_export_filename(username, self._host._owner_id)
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
        rows = visible_license_rows_for_panel(
            self._host._store.list_user_keys_for_stats(self._host._owner_id)
        )
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
        rows = visible_license_rows_for_panel(
            self._host._store.list_user_keys_for_stats(self._host._owner_id)
        )
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
        rows = visible_license_rows_for_panel(store.list_user_keys_for_stats(owner_id))
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


def _internal_version_pick_enabled(user: discord.User | discord.Member) -> bool:
    """True → Select Version lists manifest dev/beta/internal rows (not public-only).

    Owners respect ``REJOIN_ADMIN_SHOW_DEV`` (default on). Tester IDs always see
    internal picks when listed in ``REJOIN_TESTER_DISCORD_IDS``.
    """
    if user.id in _tester_ids():
        return True
    if not _is_owner(user):
        return False
    raw = (os.environ.get("REJOIN_ADMIN_SHOW_DEV") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


# ── Select Version (install from tagged ref) ────────────────────────────────


class VersionPickSelect(discord.ui.Select):
    """Dropdown of public Rejoin versions; sends copy/paste install command."""

    def __init__(self, versions: list[RejoinVersionInfo]) -> None:
        self._by_ver = {v.version: v for v in versions}
        opts: list[discord.SelectOption] = []
        for v in versions[:25]:
            desc = f"Install DENG Tool: Rejoin {v.version}"[:100]
            opts.append(
                discord.SelectOption(
                    label=v.label[:100],
                    value=v.version[:100],
                    description=desc,
                    emoji="\U0001f4e6",
                )
            )
        super().__init__(
            placeholder="Select a specific version to install...",
            min_values=1,
            max_values=1,
            options=opts,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        ver = self.values[0]
        info = self._by_ver[ver]
        text = format_install_instructions_plain(info)
        await interaction.response.send_message(content=text, ephemeral=True)


class VersionPickView(discord.ui.View):
    def __init__(self, versions: list[RejoinVersionInfo]) -> None:
        super().__init__(timeout=300)
        self.add_item(VersionPickSelect(versions))


# ── Persistent panel view ─────────────────────────────────────────────────────

class PanelView(discord.ui.View):
    """Persistent view: license buttons + Select Version (tagged install).

    timeout=None keeps the view alive across bot restarts when registered
    via ``bot.add_view(view, message_id=<id>)``.
    """

    def __init__(self, store: BaseLicenseStore) -> None:
        super().__init__(timeout=None)
        self._store = store
        self._move_generate_button_first()

    def _move_generate_button_first(self) -> None:
        """discord.py decorator buttons are added before __init__; pin link buttons first/last."""
        generate = discord.ui.Button(
            style=discord.ButtonStyle.link,
            label="Generate Key",
            emoji="🔑",
            url="https://aio.deng.my.id/license",
            row=0,
        )
        guide = discord.ui.Button(
            style=discord.ButtonStyle.link,
            label="Guide",
            emoji="\U0001f4d6",
            url=build_guide_thread_url(),
            row=0,
        )
        existing = [
            child for child in self.children
            if getattr(child, "label", "") not in ("Generate Key", "Guide")
        ]
        self.clear_items()
        self.add_item(generate)
        for child in existing:
            self.add_item(child)
        self.add_item(guide)

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
        rows = visible_license_rows_for_panel(self._store.list_user_keys_for_stats(uid))
        content, embed_dicts, page, _, _ = _build_key_stats_ephemeral_parts(rows, 0)
        embeds = [discord.Embed.from_dict(d) for d in embed_dicts]
        view = KeyStatsView(self._store, uid, page)
        await interaction.followup.send(
            content=content, embeds=embeds, view=view, ephemeral=True
        )

    @discord.ui.button(
        label="Select Version",
        style=discord.ButtonStyle.primary,
        custom_id=BUTTON_SELECT_VERSION,
        emoji="\U0001f4e6",
    )
    async def btn_select_version(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        versions = list_public_rejoin_versions(include_internal_channels=False)
        if not versions:
            await interaction.response.send_message(
                NO_PUBLIC_VERSIONS_MESSAGE,
                ephemeral=True,
            )
            return
        view = VersionPickView(versions)
        await interaction.response.send_message(
            "Pick a version, then copy the install command into Termux:",
            view=view,
            ephemeral=True,
        )


# ── Cog ───────────────────────────────────────────────────────────────────────

class LicensePanelCog(commands.Cog, name="LicensePanel"):
    """Hosts the /license_panel command group and wires all button + modal logic."""

    def __init__(self, bot: commands.Bot, store: BaseLicenseStore) -> None:
        self.bot = bot
        self._store = store

        # Persistent-view restoration state. Restoration needs a (sometimes very
        # slow) Supabase read; we track which guilds are already restored so a
        # background retry can finish the rest without re-doing successes or
        # registering duplicate views.
        self._restored_guild_ids: set[str] = set()
        self._removed_feature_view_added = False
        self._restore_task: "asyncio.Task | None" = None

        self._panel_group = app_commands.Group(
            name=SLASH_GROUP,
            description="DENG Tool license panel management.",
        )
        self._register_commands()
        bot.tree.add_command(self._panel_group)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _owner_denied(self) -> discord.Embed:
        return _embed_from_payload(build_not_owner_response(), include_thumbnail=False)

    async def _require_owner(self, interaction: discord.Interaction) -> bool:
        """Return True when the caller is an owner; otherwise send ephemeral denial."""
        if _is_owner(interaction.user):
            return True
        await interaction.response.send_message(
            embed=self._owner_denied(), ephemeral=True
        )
        return False

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

            embed = _build_public_panel_embed()
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

            embed = _build_public_panel_embed()
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
                ch_id = cfg.get("channel_id") or ""
                msg_id = cfg.get("message_id") or ""
                updated_by = cfg.get("updated_by") or ""
                updated_at = cfg.get("updated_at") or ""

                # Verify message still reachable
                if not msg_id:
                    msg_status = "Not set"
                else:
                    msg_status = "⚠️ unknown"
                    if ch_id:
                        try:
                            ch = interaction.guild.get_channel(int(ch_id))
                            if ch and isinstance(ch, discord.TextChannel):
                                try:
                                    await ch.fetch_message(int(msg_id))
                                    msg_status = "✅ reachable"
                                except discord.NotFound:
                                    msg_status = (
                                        "❌ deleted\n"
                                        "> Run `/license_panel post` to repost the panel"
                                    )
                                except discord.Forbidden:
                                    msg_status = "⚠️ no access"
                            else:
                                msg_status = "❌ channel not found"
                        except (ValueError, TypeError):
                            msg_status = "⚠️ bad ID"

                ch_display = (
                    f"<#{ch_id}> (`{ch_id}`)" if ch_id else "Not set"
                )
                msg_id_display = f"`{msg_id}`" if msg_id else "Not set"
                set_by_display = _format_user_mention(updated_by)
                updated_display = _format_discord_ts(updated_at)

                panel_lines = (
                    f"**Channel:** {ch_display}\n"
                    f"**Message ID:** {msg_id_display}\n"
                    f"**Message:** {msg_status}\n"
                    f"**Set by:** {set_by_display}\n"
                    f"**Updated:** {updated_display}"
                )
            else:
                panel_lines = "*(no panel configured — run `/license_panel set_channel`)*"

            # ── Store status section ──
            store_info = store.get_store_status()
            status_icon = "✅ Ready" if store_info["status"] == "ready" else "⚠️ Error"
            store_lines = (
                f"**Backend:** `{store_info['backend']}`\n"
                f"**Status:** {status_icon}"
            )
            if store_info.get("detail"):
                if store_info["status"] == "ready":
                    store_lines += f"\n{store_info['detail']}"
                else:
                    safe_detail = str(store_info["detail"])[:80]
                    store_lines += f"\n**Detail:** `{safe_detail}`"

            embed = discord.Embed(
                title="🛠️ License Panel — Admin Status",
                color=0x2F80ED,
                timestamp=datetime.now(timezone.utc),
            )
            guard_status = "Enabled" if owner_guard_enabled() else "Disabled (no owners configured)"
            registration_lines = (
                "**Mode:** Global\n"
                f"**Owner Guard:** {guard_status}\n"
                f"**Owner Source:** `{OWNER_ENV_VAR}`"
            )
            db_scope_lines = (
                "**License Data:** Global\n"
                "**Panel Config:** Per Guild\n"
                "**Log Config:** Per Guild"
            )
            guild_name = interaction.guild.name if interaction.guild else "Unknown"
            guild_lines = f"**Current Guild:** {guild_name} (`{guild_id}`)"

            embed.add_field(
                name="Global Command Registration",
                value=registration_lines,
                inline=False,
            )
            embed.add_field(name="Database Scope", value=db_scope_lines, inline=False)
            embed.add_field(name="Guild", value=guild_lines, inline=False)
            embed.add_field(name="Panel Config", value=panel_lines, inline=False)
            embed.add_field(name="License Store", value=store_lines, inline=False)
            embed.set_footer(text="DENG Tool: Rejoin")

            await interaction.followup.send(embed=embed, ephemeral=True)

        # /license_log_channel set ────────────────────────────────────────────

        _log_group = app_commands.Group(
            name="license_log_channel",
            description="Configure the channel where license events are logged.",
        )
        bot.tree.add_command(_log_group)

        @_log_group.command(
            name="set",
            description="Set the channel for license event logs.",
        )
        @app_commands.describe(channel="Target text channel for license logs")
        async def cmd_log_set(
            interaction: discord.Interaction,
            channel: discord.TextChannel,
        ) -> None:
            if not _is_owner(interaction.user):
                await interaction.response.send_message(
                    embed=self._owner_denied(), ephemeral=True
                )
                return
            guild_id = str(interaction.guild_id)
            store.save_license_log_config(guild_id, str(channel.id), str(interaction.user.id))
            store.audit_admin_action(
                str(interaction.user.id), "set_license_log_channel",
                target_type="channel", target_id=str(channel.id),
            )
            await interaction.response.send_message(
                f"✅ License log channel set to {channel.mention}.", ephemeral=True
            )

        @_log_group.command(
            name="clear",
            description="Remove the configured license log channel.",
        )
        async def cmd_log_clear(interaction: discord.Interaction) -> None:
            if not _is_owner(interaction.user):
                await interaction.response.send_message(
                    embed=self._owner_denied(), ephemeral=True
                )
                return
            guild_id = str(interaction.guild_id)
            store.clear_license_log_config(guild_id)
            store.audit_admin_action(str(interaction.user.id), "clear_license_log_channel")
            await interaction.response.send_message(
                "✅ License log channel cleared.", ephemeral=True
            )

        @_log_group.command(
            name="status",
            description="Show the currently configured license log channel.",
        )
        async def cmd_log_status(interaction: discord.Interaction) -> None:
            if not _is_owner(interaction.user):
                await interaction.response.send_message(
                    embed=self._owner_denied(), ephemeral=True
                )
                return
            guild_id = str(interaction.guild_id)
            cfg = store.get_license_log_config(guild_id)
            if cfg:
                ch_id = cfg.get("channel_id", "?")
                msg = f"✅ License logs → <#{ch_id}> (set by `{cfg.get('updated_by', '?')}` at `{cfg.get('updated_at', '?')}`)"
            else:
                msg = "❌ No license log channel configured. Use `/license_log_channel set`."
            await interaction.response.send_message(msg, ephemeral=True)

        # /license <user> ──────────────────────────────────────────────────────

        @bot.tree.command(
            name="license",
            description="View license key stats for a Discord user (owner/admin only).",
        )
        @app_commands.describe(user="The Discord user to look up")
        async def cmd_license_user(
            interaction: discord.Interaction,
            user: discord.User,
        ) -> None:
            if not _is_owner(interaction.user):
                await interaction.response.send_message(
                    embed=self._owner_denied(), ephemeral=True
                )
                return

            await interaction.response.defer(ephemeral=True)
            uid = str(user.id)
            store.get_or_create_user(uid, str(user))
            stats = get_license_stats_for_discord_user(store, uid)
            active_rows = filter_active_visible_license_rows(
                store.list_user_keys_for_stats(uid)
            )
            description = build_license_admin_stats_description(
                user_label=f"<@{uid}> ({uid})",
                stats=stats,
                active_rows=active_rows,
            )

            embed = discord.Embed(
                title=f"License Stats — {user.display_name}",
                description=description,
                color=discord.Color.from_rgb(0, 0, 0),
            )
            embed.set_footer(text="DENG Tool: Rejoin")
            store.audit_admin_action(
                str(interaction.user.id), "admin_view_license",
                target_type="user", target_id=uid,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Persistent view restoration ───────────────────────────────────────────

    def schedule_persistent_view_restore(self) -> None:
        """Start the (idempotent) background restore-with-retry task once.

        Safe to call from every ``on_ready`` (which fires on every gateway
        reconnect): a single task is kept alive and reused, so reconnect storms
        never pile up duplicate restore loops.
        """
        existing = self._restore_task
        if existing is not None and not existing.done():
            return
        self._restore_task = asyncio.create_task(self._restore_persistent_views_with_retry())

    async def _restore_persistent_views_with_retry(self) -> None:
        """Keep restoring panel views in the background until all guilds succeed.

        Restoration depends on a Supabase read that can be slow or briefly
        unreachable. Rather than give up after a single pass (which left the
        panel buttons dead until the next reconnect), retry with backoff so the
        views attach automatically the moment the store responds — without ever
        blocking the event loop / Discord heartbeat.
        """
        delays = [5, 10, 20, 30, 60, 60, 120, 120, 300]
        attempt = 0
        while True:
            pending = await self.restore_persistent_views()
            if pending <= 0:
                if attempt:
                    log.info("All persistent panel views restored after %d retr%s.",
                             attempt, "y" if attempt == 1 else "ies")
                return
            delay = delays[min(attempt, len(delays) - 1)]
            attempt += 1
            log.warning(
                "Persistent panel view restore incomplete (%d guild(s) pending); "
                "retrying in %ds (attempt %d).",
                pending, delay, attempt,
            )
            await asyncio.sleep(delay)

    async def restore_persistent_views(self) -> int:
        """Re-attach PanelView for every guild so buttons survive restarts.

        Also registers a global :class:`RemovedFeatureView` so that clicks on the
        legacy Reset HWID / Redeem Key buttons of OLD already-posted panels respond
        with a clean "This feature has been removed." notice instead of failing.

        Idempotent: guilds already restored are skipped, so it is safe to call
        repeatedly (the background retry relies on this). Returns the number of
        guilds whose views could NOT be restored on this pass (0 == all done).
        """
        # Global catch-view for removed buttons on old panel messages (no message_id
        # → matches the legacy custom_ids on any message that still carries them).
        # Add exactly once — re-adding on every retry/reconnect would duplicate it.
        if not self._removed_feature_view_added:
            self.bot.add_view(RemovedFeatureView())
            self._removed_feature_view_added = True

        pending = 0
        for guild in self.bot.guilds:
            guild_id = str(guild.id)
            if guild_id in self._restored_guild_ids:
                continue
            try:
                # get_panel_config is a SYNCHRONOUS store call (blocking Supabase
                # round-trip). on_ready runs on the event loop and fires on every
                # gateway reconnect, so calling it inline blocked the Discord
                # heartbeat per-guild whenever Supabase was slow — a key reason
                # the panel went dead. Run it in a worker thread with a timeout.
                cfg = await asyncio.wait_for(
                    asyncio.to_thread(self._store.get_panel_config, guild_id),
                    timeout=15,
                )
            except Exception as exc:
                pending += 1
                log.warning(
                    "Could not read panel config for guild %s (store error: %s). "
                    "If using Supabase, apply the migration first.",
                    guild.id,
                    exc,
                )
                continue
            if not cfg or not cfg.get("message_id"):
                # No panel configured for this guild — nothing to restore, and it
                # is not a failure, so mark it done to stop retrying.
                self._restored_guild_ids.add(guild_id)
                continue
            try:
                msg_id = int(cfg["message_id"])
                view = PanelView(self._store)
                self.bot.add_view(view, message_id=msg_id)
                channel = await self._get_panel_channel(guild, str(cfg.get("channel_id", "")))
                if channel is not None:
                    try:
                        msg = await channel.fetch_message(msg_id)
                        await msg.edit(embed=_build_public_panel_embed(), view=view)
                        log.info(
                            "Refreshed persistent panel message: guild=%s message=%s",
                            guild.id,
                            msg_id,
                        )
                    except (discord.Forbidden, discord.HTTPException, discord.NotFound) as exc:
                        log.warning(
                            "Could not refresh persistent panel message for guild %s message %s: %s",
                            guild.id,
                            msg_id,
                            exc,
                        )
                # add_view succeeded → buttons are live for this guild even if the
                # cosmetic message refresh above failed. Mark done so we stop
                # retrying it.
                self._restored_guild_ids.add(guild_id)
                log.info(
                    "Restored persistent panel view: guild=%s message=%s",
                    guild.id,
                    msg_id,
                )
            except (ValueError, TypeError) as exc:
                pending += 1
                log.warning(
                    "Could not restore panel view for guild %s: %s",
                    guild.id,
                    exc,
                )
        return pending
