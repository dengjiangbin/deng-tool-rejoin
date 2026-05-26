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
  Generate Key   (Discord link button -> https://tool.deng.my.id)
  Reset HWID     (custom_id = "license_panel:reset_hwid")
  Redeem Key     (custom_id = "license_panel:redeem")
  Select Version (custom_id = "license_panel:select_version")

All button flows are EPHEMERAL.  The panel embed itself is public (pinned-style).
"""

from __future__ import annotations

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
    build_reset_hwid_log_description,
    filter_active_visible_license_rows,
)
from agent.license_panel import (
    BUTTON_GENERATE,
    BUTTON_KEY_STATS,
    BUTTON_REDEEM,
    BUTTON_RESET_HWID,
    BUTTON_SELECT_VERSION,
    SLASH_GROUP,
    build_generate_cooldown_response,
    build_generate_limit_response,
    build_generate_success_response,
    build_key_list_response,
    build_not_owner_response,
    build_panel_embed,
    build_redeem_already_owned_response,
    build_redeem_error_response,
    build_redeem_limit_response,
    build_redeem_success_response,
    build_reset_active_warning_response,
    build_reset_mixed_summary_embed,
    build_reset_no_binding_response,
    build_reset_no_keys_response,
    build_reset_selector_embed,
    build_reset_success_response,
)
from agent.rejoin_versions import (
    NO_PUBLIC_VERSIONS_MESSAGE,
    RejoinVersionInfo,
    format_install_instructions_plain,
    list_public_rejoin_versions,
)
from agent.license_store import (
    ActiveKeyWarning,
    BaseLicenseStore,
    ExpiredKeyError,
    GenerationCooldownError,
    KeyAlreadySelfOwned,
    KeyNotFoundError,
    KeyOwnershipError,
    NoActiveBindingError,
    UserLimitError,
    get_license_stats_for_discord_user,
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
            "redeemed": "Redeemed Key",
            "reset_hwid": "Reset Key",
        }.get(event_type, "Key")

        if event_type == "reset_hwid":
            description = build_reset_hwid_log_description(
                user_mention=f"<@{uid}>",
                reset_key=key_serial,
                stats=stats,
            )
        else:
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


async def _post_max_key_log(
    guild: discord.Guild,
    store: "BaseLicenseStore",
    admin: discord.User | discord.Member,
    scope: str,
    target_user: discord.User | discord.Member | None,
    old_limit: int | str,
    new_limit: int,
) -> None:
    """Post a Key Limit Updated embed to the configured license log channel."""
    try:
        cfg = store.get_license_log_config(str(guild.id))
        if not cfg:
            return
        channel_id = int(cfg.get("channel_id", 0))
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        lines = [
            f"**Admin:** <@{admin.id}>",
            f"**Scope:** {scope}",
        ]
        if target_user is not None:
            lines.append(f"**User:** <@{target_user.id}>")
        lines += [
            f"**Old Limit:** {old_limit}",
            f"**New Limit:** {new_limit}",
        ]
        embed = discord.Embed(
            title="Key Limit Updated",
            description="\n".join(lines),
            color=discord.Color.from_rgb(0, 100, 200),
        )
        embed.set_footer(text="DENG Tool: Rejoin")
        await channel.send(embed=embed)
    except Exception as exc:  # noqa: BLE001
        log.debug("_post_max_key_log error: %s", exc)


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

        from agent.license import normalize_license_key

        await interaction.response.defer(ephemeral=True)

        try:
            self._store.get_or_create_user(uid, username)
            self._store.redeem_key_for_user(uid, raw_key)
            display_key = normalize_license_key(raw_key)
            payload = build_redeem_success_response(display_key)
            if interaction.guild:
                await _post_license_log(
                    interaction.guild, self._store,
                    title="Key Redeemed Log",
                    user=interaction.user,
                    key_serial=display_key,
                    event_type="redeemed",
                )
        except KeyAlreadySelfOwned as exc:
            display_key = normalize_license_key(raw_key)
            payload = build_redeem_already_owned_response(
                export_backfilled=exc.export_backfilled,
                copyable_key=display_key,
            )
        except ExpiredKeyError as exc:
            payload = build_redeem_error_response(str(exc))
        except UserLimitError as exc:
            msg = str(exc)
            import re as _re
            m = _re.search(r"(\d+)\s*/\s*(\d+)", msg)
            if m:
                payload = build_redeem_limit_response(
                    int(m.group(2)), active_count=int(m.group(1))
                )
            else:
                payload = build_redeem_error_response(msg)
        except (KeyNotFoundError, KeyOwnershipError) as exc:
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
            # 🟢 no device linked, 🟡 bound to a device (matches selector embed legend)
            bound = bool(k.get("active_binding"))
            icon = "🟡" if bound else "🟢"
            fk = k.get("full_key_plaintext")
            mk = k.get("masked_key", "???")
            key_str = fk or mk
            # Label: key + device name for bound keys (show actual device, not generic text)
            if bound:
                _dev = (k.get("device_model") or k.get("device_label") or "").strip()
                status_suffix = f" — Bound to {_dev}" if _dev else " — Bound to a device"
            else:
                status_suffix = " — No device linked"
            label = f"{key_str}{status_suffix}"[:100]
            # Description: device model for bound keys; friendly text for unbound
            if bound:
                model = k.get("device_model") or "Unknown device"
                desc = f"Device: {model}"
            else:
                desc = k.get("reason_if_not_resettable") or "No device linked"
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
            fk = state.get("full_key_plaintext")
            mk = state.get("masked_key", "???")
            display_key = fk if fk else f"{mk} (reference only)"
            masked = mk
            if not state.get("can_reset"):
                results.append({
                    "display_key": display_key,
                    "masked_key": masked,
                    "success": False,
                    "message": state.get("reason_if_not_resettable") or "Cannot reset this key.",
                })
                continue
            try:
                self._store.reset_hwid(self._uid, key_id)
                results.append({
                    "display_key": display_key,
                    "masked_key": masked,
                    "success": True,
                    "message": "Device binding cleared.",
                })
                if interaction.guild and fk:
                    import asyncio
                    asyncio.ensure_future(
                        _post_license_log(
                            interaction.guild, self._store,
                            title="Reset HWID Log",
                            user=interaction.user,
                            key_serial=fk,
                            event_type="reset_hwid",
                        )
                    )
            except NoActiveBindingError:
                results.append({
                    "display_key": display_key,
                    "masked_key": masked,
                    "success": False,
                    "message": "No device binding to clear.",
                })
            except ActiveKeyWarning as exc:
                # ActiveKeyWarning is no longer raised (cooldown is based on reset history only),
                # but kept for safety in case of legacy store implementations.
                results.append({
                    "display_key": display_key,
                    "masked_key": masked,
                    "success": False,
                    "message": str(exc),
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
        """discord.py decorator buttons are added before __init__; put the link first."""
        generate = discord.ui.Button(
            style=discord.ButtonStyle.link,
            label="Generate Key",
            emoji="🔑",
            url="https://tool.deng.my.id",
            row=0,
        )
        existing = [child for child in self.children if getattr(child, "label", "") != "Generate Key"]
        self.clear_items()
        self.add_item(generate)
        for child in existing:
            self.add_item(child)

    # ── Reset HWID ────────────────────────────────────────────────────────────

    @discord.ui.button(
        label="Reset HWID",
        style=discord.ButtonStyle.danger,
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

        self._panel_group = app_commands.Group(
            name=SLASH_GROUP,
            description="DENG Tool license panel management.",
        )
        self._register_commands()
        bot.tree.add_command(self._panel_group)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _owner_denied(self) -> discord.Embed:
        return _embed_from_payload(build_not_owner_response(), include_thumbnail=False)

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

        # /license_log_channel set ────────────────────────────────────────────

        _log_group = app_commands.Group(
            name="license_log_channel",
            description="Configure the channel where license events are logged.",
        )
        bot.tree.add_command(_log_group)

        @_log_group.command(
            name="set",
            description="Set the channel for license event logs (generate/redeem/reset).",
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

        # /license max_key ────────────────────────────────────────────────────

        @self._panel_group.command(
            name="max_key",
            description="Set the maximum active keys a user can have (owner/admin only).",
        )
        @app_commands.describe(
            scope="global = change the default for all users; user = set a specific user's limit",
            max_keys="Maximum number of active keys (0 = block all key generation/redemption)",
            user="Target Discord user (only required when scope = user)",
        )
        @app_commands.rename(max_keys="max")
        @app_commands.choices(scope=[
            app_commands.Choice(name="global", value="global"),
            app_commands.Choice(name="user", value="user"),
        ])
        async def cmd_max_key(
            interaction: discord.Interaction,
            scope: str,
            max_keys: int,
            user: Optional[discord.User] = None,
        ) -> None:
            if not _is_owner(interaction.user):
                await interaction.response.send_message(
                    embed=self._owner_denied(), ephemeral=True
                )
                return

            if max_keys < 0:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="\u274c Invalid Value",
                        description="`max` must be 0 or higher.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)
            actor = str(interaction.user.id)

            try:
                if scope == "global":
                    old_max = store.get_global_max_keys()
                    store.set_global_max_keys(max_keys, updated_by=actor)
                    if interaction.guild:
                        await _post_max_key_log(
                            interaction.guild, store,
                            interaction.user, "Global", None,
                            old_max, max_keys,
                        )
                    store.audit_admin_action(
                        actor, "set_global_max_keys",
                        metadata={"old": old_max, "new": max_keys},
                    )
                    embed = discord.Embed(
                        title="\u2705 Global Key Limit Updated",
                        description=(
                            f"Global default max active keys:\n"
                            f"**{old_max}** \u2192 **{max_keys}**"
                        ),
                        color=discord.Color.green(),
                    )
                    embed.set_footer(text="DENG Tool: Rejoin")
                    await interaction.followup.send(embed=embed, ephemeral=True)

                elif scope == "user":
                    if user is None:
                        await interaction.followup.send(
                            embed=discord.Embed(
                                title="\u274c Missing User",
                                description="Scope `user` requires specifying a Discord user.",
                                color=discord.Color.red(),
                            ),
                            ephemeral=True,
                        )
                        return
                    uid = str(user.id)
                    global_max = store.get_global_max_keys()
                    old_limit = store.get_user_key_limit(uid)
                    old_display: str | int = (
                        f"Global {global_max}" if old_limit is None else old_limit
                    )
                    store.set_user_key_limit(uid, max_keys, updated_by=actor)
                    if interaction.guild:
                        await _post_max_key_log(
                            interaction.guild, store,
                            interaction.user, "User", user,
                            old_display, max_keys,
                        )
                    store.audit_admin_action(
                        actor, "set_user_key_limit",
                        target_type="user", target_id=uid,
                        metadata={"old": str(old_display), "new": max_keys},
                    )
                    embed = discord.Embed(
                        title="\u2705 Per-User Key Limit Updated",
                        description=(
                            f"User: <@{uid}>\n"
                            f"Limit: **{old_display}** \u2192 **{max_keys}**"
                        ),
                        color=discord.Color.green(),
                    )
                    embed.set_footer(text="DENG Tool: Rejoin")
                    await interaction.followup.send(embed=embed, ephemeral=True)

            except Exception as exc:  # noqa: BLE001
                log.error("cmd_max_key error: %s", exc)
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="\u274c Error",
                        description=f"Failed to update key limit: {exc}",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )

        # /license <user> ─────────────────────────────────────────────────────

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
            # Resolve limit info for admin display
            try:
                effective_max = store.get_effective_max_keys(uid)
                user_override = store.get_user_key_limit(uid)
                max_keys_source = "user" if user_override is not None else "global"
            except Exception:
                effective_max = None
                max_keys_source = None
            description = build_license_admin_stats_description(
                user_label=f"<@{uid}> ({uid})",
                stats=stats,
                active_rows=active_rows,
                effective_max_keys=effective_max,
                max_keys_source=max_keys_source,
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
                channel = await self._get_panel_channel(guild, str(cfg.get("channel_id", "")))
                if channel is not None:
                    try:
                        msg = await channel.fetch_message(msg_id)
                        embed_dict = build_panel_embed()
                        await msg.edit(embed=discord.Embed.from_dict(embed_dict), view=view)
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
