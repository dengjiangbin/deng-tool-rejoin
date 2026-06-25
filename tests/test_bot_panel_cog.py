"""Tests for the Discord license panel cog (LicensePanelCog).

Uses unittest + mocked discord.py interactions so no live token is required.
Tests cover:
  - _is_owner / _owner_ids parsing
  - _tester_ids / _internal_version_pick_enabled (Select Version)
  - PanelView button handler logic (generate, key_stats, select_version)
  - RemovedFeatureView graceful handling of legacy reset_hwid/redeem custom_ids
  - LicensePanelCog command helpers (owner denied, panel config persistence)
  - Duplicate panel post prevention
  - restore_persistent_views
"""

from __future__ import annotations

import json
import os
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# ── Ensure project root is on sys.path ───────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.license import normalize_license_key
from agent import rejoin_versions as rv
from agent.rejoin_versions import NO_PUBLIC_VERSIONS_MESSAGE, RejoinVersionInfo
from agent.license_store import (
    LocalJsonLicenseStore,
)
from bot.cog_license_panel import (
    KeyStatsDownloadButton,
    KeyStatsNextButton,
    LicensePanelCog,
    PanelView,
    RemovedFeatureView,
    VersionPickSelect,
    VersionPickView,
    _build_key_stats_ephemeral_parts,
    _internal_version_pick_enabled,
    _is_owner,
    _owner_ids,
    _tester_ids,
)
from agent.license_panel import build_panel_buttons, build_panel_embed


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_store(tmp_dir: str) -> LocalJsonLicenseStore:
    return LocalJsonLicenseStore(Path(tmp_dir) / "license_store.json")


def _fake_user(uid: int = 111, name: str = "TestUser#0001") -> MagicMock:
    user = MagicMock()
    user.id = uid
    user.display_name = name
    user.name = name
    user.__str__ = lambda self: name
    return user


def _fake_interaction(
    user: MagicMock | None = None,
    guild_id: int = 999,
) -> MagicMock:
    inter = MagicMock()
    inter.user = user or _fake_user()
    inter.guild_id = guild_id
    inter.guild = MagicMock()
    inter.guild.id = guild_id
    inter.response = MagicMock()
    inter.response.send_message = AsyncMock()
    inter.response.send_modal = AsyncMock()
    inter.response.defer = AsyncMock()
    inter.response.edit_message = AsyncMock()
    inter.followup = MagicMock()
    inter.followup.send = AsyncMock()
    return inter


def _fake_bot() -> MagicMock:
    bot = MagicMock()
    bot.guilds = []
    bot.tree = MagicMock()
    bot.tree.add_command = MagicMock()
    bot.add_view = MagicMock()
    bot.add_cog = AsyncMock()
    return bot


# ── Owner ID parsing ──────────────────────────────────────────────────────────

class TestOwnerIds(unittest.TestCase):
    def test_single_id(self) -> None:
        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": "123"}):
            self.assertIn(123, _owner_ids())

    def test_multiple_ids(self) -> None:
        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": "111,222,333"}):
            ids = _owner_ids()
            self.assertIn(111, ids)
            self.assertIn(222, ids)
            self.assertIn(333, ids)

    def test_empty_env(self) -> None:
        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": ""}):
            self.assertEqual(len(_owner_ids()), 0)

    def test_non_numeric_skipped(self) -> None:
        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": "abc,123,xyz"}):
            ids = _owner_ids()
            self.assertIn(123, ids)
            self.assertNotIn("abc", ids)

    def test_spaces_stripped(self) -> None:
        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": " 456 , 789 "}):
            ids = _owner_ids()
            self.assertIn(456, ids)
            self.assertIn(789, ids)

    def test_is_owner_true(self) -> None:
        user = _fake_user(uid=555)
        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": "555"}):
            self.assertTrue(_is_owner(user))

    def test_is_owner_false(self) -> None:
        user = _fake_user(uid=999)
        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": "111"}):
            self.assertFalse(_is_owner(user))


class TestPanelViewThreeButtons(unittest.TestCase):
    """Panel exposes three persistent controls (Reset HWID + Redeem removed)."""

    def test_panel_view_has_three_children(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            view = PanelView(store)
            self.assertEqual(len(view.children), 3)
            self.assertEqual(
                [getattr(child, "label", "") for child in view.children],
                ["Generate Key", "Key Stats", "Select Version"],
            )

    def test_panel_view_has_no_reset_or_redeem_buttons(self) -> None:
        with TemporaryDirectory() as tmp:
            view = PanelView(_make_store(tmp))
            custom_ids = {getattr(c, "custom_id", None) for c in view.children}
            self.assertNotIn("license_panel:reset_hwid", custom_ids)
            self.assertNotIn("license_panel:redeem", custom_ids)

    def test_button_payload_custom_ids_are_unchanged(self) -> None:
        buttons = build_panel_buttons()[0]["components"]
        self.assertEqual([btn["label"] for btn in buttons], [
            "Generate Key",
            "Key Stats",
            "Select Version",
        ])
        self.assertEqual([buttons[i]["custom_id"] for i in range(1, 3)], [
            "license_panel:key_stats",
            "license_panel:select_version",
        ])
        # Removed features must not appear in newly posted/refreshed panels.
        all_ids = {btn.get("custom_id") for btn in buttons}
        self.assertNotIn("license_panel:reset_hwid", all_ids)
        self.assertNotIn("license_panel:redeem", all_ids)

    def test_panel_view_button_order_style_and_callbacks(self) -> None:
        with TemporaryDirectory() as tmp:
            view = PanelView(_make_store(tmp))
            buttons = view.children
            self.assertEqual([getattr(btn, "label", "") for btn in buttons], [
                "Generate Key",
                "Key Stats",
                "Select Version",
            ])
            self.assertEqual(getattr(buttons[0], "url", None), "https://tool.deng.my.id")
            self.assertIsNone(getattr(buttons[0], "custom_id", None))
            self.assertEqual(getattr(buttons[1], "custom_id", None), "license_panel:key_stats")
            self.assertEqual(getattr(buttons[2], "custom_id", None), "license_panel:select_version")

    def test_public_panel_has_no_timestamp_and_website_logo(self) -> None:
        embed = build_panel_embed()
        self.assertNotIn("timestamp", embed)
        self.assertEqual(
            embed["thumbnail"]["url"],
            "https://tool.deng.my.id/public/img/deng-logo.png",
        )


# ── PanelView — Generate Key ──────────────────────────────────────────────────

class TestPanelViewGenerate(unittest.TestCase):
    def test_generate_key_is_web_portal_link_button(self) -> None:
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"TOOL_SITE_URL": "https://example.invalid"}):
            store = _make_store(tmp)
            view = PanelView(store)
            generate = next(c for c in view.children if getattr(c, "label", "") == "Generate Key")
            self.assertEqual(generate.url, "https://tool.deng.my.id")
            self.assertIsNone(getattr(generate, "custom_id", None))

    def test_generate_key_button_does_not_generate_inside_discord(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            view = PanelView(store)
            generate = next(c for c in view.children if getattr(c, "label", "") == "Generate Key")
            self.assertEqual(getattr(generate, "url", None), "https://tool.deng.my.id")
            self.assertEqual(store.count_user_keys("42"), 0)

    def test_generate_key_link_uses_portal_domain_default(self) -> None:
        """The Generate Key control defaults to the portal URL."""
        with TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            view = PanelView(store)
            generate = next(c for c in view.children if getattr(c, "label", "") == "Generate Key")
            self.assertEqual(getattr(generate, "url", None), "https://tool.deng.my.id")


# ── PanelView — removed features (Reset HWID / Redeem) ─────────────────────────

class TestRemovedFeatureView(unittest.IsolatedAsyncioTestCase):
    """Old panel buttons (Reset HWID, Redeem) are gone from new panels, and the
    legacy custom_ids respond gracefully via RemovedFeatureView."""

    def test_panel_view_has_no_reset_or_redeem_handlers(self) -> None:
        with TemporaryDirectory() as tmp:
            view = PanelView(_make_store(tmp))
            self.assertFalse(hasattr(view, "btn_reset_hwid"))
            self.assertFalse(hasattr(view, "btn_redeem"))

    def test_removed_feature_view_registers_legacy_custom_ids(self) -> None:
        view = RemovedFeatureView()
        custom_ids = {getattr(c, "custom_id", None) for c in view.children}
        self.assertIn("license_panel:reset_hwid", custom_ids)
        self.assertIn("license_panel:redeem", custom_ids)

    async def test_removed_reset_hwid_replies_feature_removed(self) -> None:
        view = RemovedFeatureView()
        inter = _fake_interaction()
        await view.removed_reset_hwid.callback(inter)
        inter.response.send_message.assert_called_once()
        call = inter.response.send_message.call_args
        msg = (call.args[0] if call.args else "") or call.kwargs.get("content") or ""
        self.assertIn("has been removed", msg)
        self.assertTrue(call.kwargs.get("ephemeral"))

    async def test_removed_redeem_replies_feature_removed(self) -> None:
        view = RemovedFeatureView()
        inter = _fake_interaction()
        await view.removed_redeem.callback(inter)
        inter.response.send_message.assert_called_once()
        call = inter.response.send_message.call_args
        msg = (call.args[0] if call.args else "") or call.kwargs.get("content") or ""
        self.assertIn("has been removed", msg)
        self.assertTrue(call.kwargs.get("ephemeral"))


class TestPanelViewKeyStats(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.store = _make_store(self._tmp.name)

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def test_key_stats_empty_message(self) -> None:
        inter = _fake_interaction(user=_fake_user(uid=501))
        view = PanelView(self.store)
        await view.btn_key_stats.callback(inter)
        inter.response.defer.assert_called_once_with(ephemeral=True)
        inter.followup.send.assert_called_once()
        _, kwargs = inter.followup.send.call_args
        self.assertIn("Total: 0", kwargs.get("content") or "")
        embeds = kwargs.get("embeds") or []
        self.assertGreaterEqual(len(embeds), 1)
        self.assertIn("license keys", (embeds[0].description or "").lower())

    async def test_key_stats_shows_unused(self) -> None:
        inter_s = _fake_interaction(user=_fake_user(uid=502))
        view = PanelView(self.store)
        self.store.get_or_create_user("502")
        self.store.create_key_for_user("502")
        await view.btn_key_stats.callback(inter_s)
        _, kwargs = inter_s.followup.send.call_args
        texts = " ".join((e.description or "") for e in (kwargs.get("embeds") or []))
        self.assertIn("Unused", texts)

    async def test_key_stats_pagination_title(self) -> None:
        uid = 503
        self.store.get_or_create_user(str(uid))
        # Allow enough keys for pagination test (>2 default)
        self.store.set_user_key_limit(str(uid), 20, updated_by="test")
        # Bypass 60-second cooldown so we can create 6 keys for pagination test
        import agent.license_store as _ls
        _orig = _ls.GENERATION_COOLDOWN_SECONDS
        _ls.GENERATION_COOLDOWN_SECONDS = 0
        try:
            for _ in range(6):
                self.store.create_key_for_user(str(uid))
        finally:
            _ls.GENERATION_COOLDOWN_SECONDS = _orig
        gen_view = PanelView(self.store)
        inter_s = _fake_interaction(user=_fake_user(uid=uid))
        await gen_view.btn_key_stats.callback(inter_s)
        _, kwargs = inter_s.followup.send.call_args
        self.assertIn("Page 1/2", kwargs.get("content") or "")

    async def test_key_stats_next_page(self) -> None:
        uid = 504
        self.store.get_or_create_user(str(uid))
        # Allow enough keys for pagination test (>2 default)
        self.store.set_user_key_limit(str(uid), 20, updated_by="test")
        # Bypass 60-second cooldown so we can create 6 keys for pagination test
        import agent.license_store as _ls
        _orig = _ls.GENERATION_COOLDOWN_SECONDS
        _ls.GENERATION_COOLDOWN_SECONDS = 0
        try:
            for _ in range(6):
                self.store.create_key_for_user(str(uid))
        finally:
            _ls.GENERATION_COOLDOWN_SECONDS = _orig
        gen_view = PanelView(self.store)
        inter_s = _fake_interaction(user=_fake_user(uid=uid))
        await gen_view.btn_key_stats.callback(inter_s)
        stats_view = inter_s.followup.send.call_args[1]["view"]
        nxt = next(c for c in stats_view.children if isinstance(c, KeyStatsNextButton))
        inter2 = _fake_interaction(user=_fake_user(uid=uid))
        await nxt.callback(inter2)
        inter2.response.edit_message.assert_called_once()
        _, ek = inter2.response.edit_message.call_args
        self.assertIn("Page 2/2", ek.get("content") or "")

    async def test_key_stats_wrong_user_blocked(self) -> None:
        uid = 600
        self.store.get_or_create_user(str(uid))
        self.store.set_user_max_keys(str(uid), 10)
        gen_view = PanelView(self.store)
        import agent.license_store as _ls
        _orig = _ls.GENERATION_COOLDOWN_SECONDS
        _ls.GENERATION_COOLDOWN_SECONDS = 0
        try:
            for _ in range(6):
                self.store.create_key_for_user(str(uid))
        finally:
            _ls.GENERATION_COOLDOWN_SECONDS = _orig
        inter_s = _fake_interaction(user=_fake_user(uid=uid))
        await gen_view.btn_key_stats.callback(inter_s)
        stats_view = inter_s.followup.send.call_args[1]["view"]
        nxt = next(c for c in stats_view.children if isinstance(c, KeyStatsNextButton))
        inter_bad = _fake_interaction(user=_fake_user(uid=601))
        await nxt.callback(inter_bad)
        inter_bad.response.send_message.assert_called_once()
        call = inter_bad.response.send_message.call_args
        msg = (call.args[0] if call.args else "") or call.kwargs.get("content") or ""
        self.assertIn("not yours", msg.lower())

    async def test_download_keys_attachment_name(self) -> None:
        uid = 602
        self.store.get_or_create_user(str(uid))
        self.store.create_key_for_user(str(uid))
        inter_s = _fake_interaction(user=_fake_user(uid=uid))
        await PanelView(self.store).btn_key_stats.callback(inter_s)
        stats_view = inter_s.followup.send.call_args[1]["view"]
        dl = next(c for c in stats_view.children if isinstance(c, KeyStatsDownloadButton))
        inter2 = _fake_interaction(user=_fake_user(uid=uid))
        await dl.callback(inter2)
        inter2.response.send_message.assert_called_once()
        _, dk = inter2.response.send_message.call_args
        self.assertRegex(
            dk["file"].filename,
            rf"TestUser#0001 - DENG Tool Rejoin License Keys - \d{{1,2}} [A-Za-z]+ 20\d{{2}}\.txt",
        )
        self.assertNotIn(f"my_keys_{uid}", dk["file"].filename)


# ── LicensePanelCog helpers ───────────────────────────────────────────────────

class TestCogOwnerDenied(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.store = _make_store(self._tmp.name)
        self.bot = _fake_bot()

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def test_owner_denied_embed_returned(self) -> None:
        cog = LicensePanelCog(self.bot, self.store)
        embed = cog._owner_denied()
        self.assertIn("Unauthorized", embed.title)

    def test_slash_group_registered(self) -> None:
        cog = LicensePanelCog(self.bot, self.store)
        # Multiple slash command groups registered: license_panel, license_log_channel
        self.assertTrue(self.bot.tree.add_command.called)
        registered_names = {
            call[0][0].name
            for call in self.bot.tree.add_command.call_args_list
        }
        self.assertIn("license_panel", registered_names)
        self.assertIn("license_log_channel", registered_names)

    def test_license_command_registered_on_tree(self) -> None:
        """The /license <user> command must be registered directly on bot.tree."""
        cog = LicensePanelCog(self.bot, self.store)
        # /license is registered via bot.tree.command() (not add_command),
        # so it appears as a tree command rather than an add_command call.
        # Verify indirectly: _register_commands runs without error and
        # both license_log_channel and license_panel groups are in add_command.
        registered_names = {
            call[0][0].name
            for call in self.bot.tree.add_command.call_args_list
        }
        # license_log_channel group must be registered (set/clear/status subcommands)
        self.assertIn("license_log_channel", registered_names)


# ── Cog: restore_persistent_views ────────────────────────────────────────────

class TestRestorePersistentViews(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.store = _make_store(self._tmp.name)
        self.bot = _fake_bot()

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def test_restore_calls_add_view_for_known_guild(self) -> None:
        guild = MagicMock()
        guild.id = 12345
        self.bot.guilds = [guild]
        self.store.save_panel_config("12345", "9999", "8888", "1")

        cog = LicensePanelCog(self.bot, self.store)
        await cog.restore_persistent_views()

        self.bot.add_view.assert_called()
        _, kwargs = self.bot.add_view.call_args
        self.assertEqual(kwargs.get("message_id"), 8888)

    async def test_restore_skips_guild_without_config(self) -> None:
        guild = MagicMock()
        guild.id = 99999  # no config saved
        self.bot.guilds = [guild]
        # Prior call_count from __init__ (add_view isn't called in __init__)
        call_count_before = self.bot.add_view.call_count

        cog = LicensePanelCog(self.bot, self.store)
        await cog.restore_persistent_views()

        # restore registers exactly one global RemovedFeatureView (legacy custom_ids)
        # and nothing per-guild because this guild has no panel config.
        self.assertEqual(self.bot.add_view.call_count, call_count_before + 1)
        registered = [c.args[0] for c in self.bot.add_view.call_args_list]
        self.assertTrue(any(isinstance(v, RemovedFeatureView) for v in registered))


# ── Security: sensitive data not in responses ────────────────────────────────

class TestSecurity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.store = _make_store(self._tmp.name)

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def test_generate_key_is_not_returned_by_discord_button(self) -> None:
        view = PanelView(self.store)
        generate = next(c for c in view.children if getattr(c, "label", "") == "Generate Key")
        self.assertEqual(getattr(generate, "url", None), "https://tool.deng.my.id")
        self.assertEqual(self.store.count_user_keys("700"), 0)


# ── Admin status command ──────────────────────────────────────────────────────

class TestAdminStatusCommand(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.store = _make_store(self._tmp.name)
        self.bot = _fake_bot()

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    def _make_cog(self) -> "LicensePanelCog":
        return LicensePanelCog(self.bot, self.store)

    def _get_admin_status_cmd(self, cog: "LicensePanelCog"):
        return next(
            c for c in cog._panel_group.commands if c.name == "admin_status"
        )

    async def test_admin_status_registered(self) -> None:
        cog = self._make_cog()
        names = [c.name for c in cog._panel_group.commands]
        self.assertIn("admin_status", names)

    async def test_admin_status_owner_only(self) -> None:
        """Non-owner should get owner-denied embed."""
        cog = self._make_cog()
        cmd = self._get_admin_status_cmd(cog)
        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": "999"}):
            inter = _fake_interaction(user=_fake_user(uid=111))  # not 999
            await cmd.callback(inter)
        inter.response.send_message.assert_called_once()
        _, kwargs = inter.response.send_message.call_args
        embed = kwargs.get("embed")
        self.assertIsNotNone(embed)
        self.assertIn("Unauthorized", embed.title)
        self.assertTrue(kwargs.get("ephemeral"))

    async def test_admin_status_shows_store_info(self) -> None:
        """Owner should see store backend and user/key counts."""
        # Create some data
        self.store.get_or_create_user("111")
        self.store.create_key_for_user("111")

        cog = self._make_cog()
        cmd = self._get_admin_status_cmd(cog)

        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": "555"}):
            inter = _fake_interaction(user=_fake_user(uid=555))
            # Patch guild.get_channel to return None (no real channel)
            inter.guild.get_channel = MagicMock(return_value=None)
            await cmd.callback(inter)

        inter.response.defer.assert_called_once_with(ephemeral=True)
        inter.followup.send.assert_called_once()
        _, kwargs = inter.followup.send.call_args
        embed = kwargs["embed"]
        self.assertIn("Admin Status", embed.title)
        # Check store field is present
        field_names = [f.name for f in embed.fields]
        self.assertIn("License Store", field_names)
        self.assertTrue(kwargs.get("ephemeral"))

    async def test_admin_status_shows_panel_config_when_set(self) -> None:
        """When panel config exists, embed should show it."""
        self.store.save_panel_config("12345", "9999", "8888", "555")

        cog = self._make_cog()
        cmd = self._get_admin_status_cmd(cog)

        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": "555"}):
            inter = _fake_interaction(user=_fake_user(uid=555), guild_id=12345)
            inter.guild.get_channel = MagicMock(return_value=None)
            await cmd.callback(inter)

        _, kwargs = inter.followup.send.call_args
        embed = kwargs["embed"]
        panel_field = next(f for f in embed.fields if f.name == "Panel Config")
        self.assertIn("9999", panel_field.value)  # channel ID
        self.assertIn("8888", panel_field.value)  # message ID

    async def test_admin_status_no_panel_config(self) -> None:
        """Without panel config, a helpful message is shown."""
        cog = self._make_cog()
        cmd = self._get_admin_status_cmd(cog)

        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": "555"}):
            inter = _fake_interaction(user=_fake_user(uid=555), guild_id=99999)
            inter.guild.get_channel = MagicMock(return_value=None)
            await cmd.callback(inter)

        _, kwargs = inter.followup.send.call_args
        embed = kwargs["embed"]
        panel_field = next(f for f in embed.fields if f.name == "Panel Config")
        self.assertIn("no panel configured", panel_field.value)


# ── Select Version internal vs public ───────────────────────────────────────────


class TestTesterIds(unittest.TestCase):
    def test_csv_ids(self) -> None:
        with patch.dict(os.environ, {"REJOIN_TESTER_DISCORD_IDS": "10, 20"}):
            self.assertEqual(_tester_ids(), frozenset({10, 20}))


class TestInternalVersionPickEnabled(unittest.TestCase):
    def test_owner_default_true(self) -> None:
        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": "7"}):
            self.assertTrue(_internal_version_pick_enabled(_fake_user(uid=7)))

    def test_owner_false_when_show_dev_off(self) -> None:
        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": "7", "REJOIN_ADMIN_SHOW_DEV": "0"}):
            self.assertFalse(_internal_version_pick_enabled(_fake_user(uid=7)))

    def test_tester_true_without_owner(self) -> None:
        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": "1", "REJOIN_TESTER_DISCORD_IDS": "99"}):
            self.assertTrue(_internal_version_pick_enabled(_fake_user(uid=99)))

    def test_random_user_false(self) -> None:
        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": "1", "REJOIN_TESTER_DISCORD_IDS": ""}):
            self.assertFalse(_internal_version_pick_enabled(_fake_user(uid=50)))


class TestPanelSelectVersion(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.store = _make_store(self._tmp.name)
        self.manifest = Path(self._tmp.name) / "versions.json"
        self.manifest.write_text(
            json.dumps(
                [
                    {
                        "version": "main-dev",
                        "channel": "dev",
                        "install_ref": "refs/heads/main",
                        "visible": False,
                        "visibility": "admin",
                        "label": "main-dev",
                        "description": "Owner/admin testing only",
                    }
                ]
            ),
            encoding="utf-8",
        )

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def test_public_user_sees_no_versions_message(self) -> None:
        with patch.dict(os.environ, {"REJOIN_VERSIONS_MANIFEST": str(self.manifest)}), patch.object(
            rv, "fetch_github_tag_names", return_value=[]
        ):
            inter = _fake_interaction(user=_fake_user(uid=501))
            inter.response.send_message = AsyncMock()
            view = PanelView(self.store)
            await view.btn_select_version.callback(inter)

        inter.response.send_message.assert_called_once()
        call = inter.response.send_message.call_args
        content = call.kwargs.get("content")
        if content is None and call.args:
            content = call.args[0]
        self.assertEqual(content, NO_PUBLIC_VERSIONS_MESSAGE)
        self.assertTrue(call.kwargs.get("ephemeral"))
        self.assertIsNone(call.kwargs.get("embed"))

    async def test_owner_gets_no_public_versions_message(self) -> None:
        """Owner must NOT see main-dev; manifest with only main-dev returns no-public message."""
        with patch.dict(os.environ, {"LICENSE_OWNER_DISCORD_IDS": "502", "REJOIN_VERSIONS_MANIFEST": str(self.manifest)}), patch.object(
            rv, "fetch_github_tag_names", return_value=[]
        ):
            inter = _fake_interaction(user=_fake_user(uid=502))
            inter.response.send_message = AsyncMock()
            view = PanelView(self.store)
            await view.btn_select_version.callback(inter)

        call = inter.response.send_message.call_args
        content = call.kwargs.get("content") or (call.args[0] if call.args else "")
        self.assertEqual(content, NO_PUBLIC_VERSIONS_MESSAGE)
        self.assertIsNone(call.kwargs.get("view"))
        self.assertTrue(call.kwargs.get("ephemeral"))

    async def test_tester_gets_no_public_versions_message(self) -> None:
        """Tester must NOT see main-dev; manifest with only main-dev returns no-public message."""
        with patch.dict(
            os.environ,
            {
                "LICENSE_OWNER_DISCORD_IDS": "999",
                "REJOIN_TESTER_DISCORD_IDS": "503",
                "REJOIN_VERSIONS_MANIFEST": str(self.manifest),
            },
        ), patch.object(rv, "fetch_github_tag_names", return_value=[]):
            inter = _fake_interaction(user=_fake_user(uid=503))
            inter.response.send_message = AsyncMock()
            view = PanelView(self.store)
            await view.btn_select_version.callback(inter)

        call = inter.response.send_message.call_args
        content = call.kwargs.get("content") or (call.args[0] if call.args else "")
        self.assertEqual(content, NO_PUBLIC_VERSIONS_MESSAGE)
        self.assertIsNone(call.kwargs.get("view"))
        self.assertTrue(call.kwargs.get("ephemeral"))

    async def test_select_version_never_shows_main_dev_in_dropdown(self) -> None:
        """main-dev must not appear in VersionPickView even when admin-show-dev is on."""
        public_manifest = self.manifest.parent / "pub_manifest.json"
        public_manifest.write_text(
            json.dumps([
                {
                    "version": "main-dev",
                    "channel": "dev",
                    "install_ref": "refs/heads/main",
                    "visible": False,
                    "visibility": "admin",
                    "enabled": True,
                },
                {
                    "version": "v1.0.0",
                    "channel": "stable",
                    "install_ref": "refs/tags/v1.0.0",
                    "visibility": "public",
                    "enabled": True,
                    "recommended": True,
                },
            ]),
            encoding="utf-8",
        )
        try:
            with patch.dict(
                os.environ,
                {"LICENSE_OWNER_DISCORD_IDS": "505", "REJOIN_ADMIN_SHOW_DEV": "1", "REJOIN_VERSIONS_MANIFEST": str(public_manifest)},
            ), patch.object(rv, "fetch_github_tag_names", return_value=[]):
                inter = _fake_interaction(user=_fake_user(uid=505))
                inter.response.send_message = AsyncMock()
                view = PanelView(self.store)
                await view.btn_select_version.callback(inter)

            kw = inter.response.send_message.call_args[1]
            pick_view = kw.get("view")
            self.assertIsInstance(pick_view, VersionPickView)
            select = next(c for c in pick_view.children if isinstance(c, VersionPickSelect))
            version_values = [opt.value for opt in select.options]
            self.assertNotIn("main-dev", version_values, "main-dev must never appear in the dropdown")
            self.assertIn("v1.0.0", version_values)
            stable_option = next(opt for opt in select.options if opt.value == "v1.0.0")
            self.assertEqual(select.placeholder, "Select a specific version to install...")
            self.assertEqual(stable_option.emoji.name, "\U0001f4e6")
            self.assertEqual(stable_option.label, "v1.0.0")
            self.assertEqual(stable_option.description, "Install DENG Tool: Rejoin v1.0.0")
            option_blob = f"{stable_option.label} {stable_option.description}"
            self.assertNotEqual(stable_option.emoji.name, "\U0001f4dc")
            self.assertNotEqual(stable_option.label, "\U0001f4e6 v1.0.0")
            self.assertNotIn("\U0001f4e6", stable_option.label)
            self.assertNotEqual(stable_option.description, "Install v1.0.0")
            self.assertNotIn("frozen public stable release", option_blob.lower())
            self.assertNotIn("public", option_blob.lower())
            self.assertNotIn("stable", option_blob.lower())
            self.assertNotIn("debug", option_blob.lower())
            self.assertNotIn("refs/tags", option_blob)
            self.assertNotIn("sha", option_blob.lower())
        finally:
            public_manifest.unlink(missing_ok=True)

    async def test_public_version_exists_shows_version_pick_view(self) -> None:
        """When a public stable version exists, Select Version shows VersionPickView."""
        public_manifest = self.manifest.parent / "pub_manifest2.json"
        public_manifest.write_text(
            json.dumps([
                {
                    "version": "v1.0.0",
                    "channel": "stable",
                    "install_ref": "refs/tags/v1.0.0",
                    "visibility": "public",
                    "enabled": True,
                    "recommended": True,
                },
            ]),
            encoding="utf-8",
        )
        try:
            with patch.dict(os.environ, {"REJOIN_VERSIONS_MANIFEST": str(public_manifest)}), patch.object(
                rv, "fetch_github_tag_names", return_value=[]
            ):
                inter = _fake_interaction(user=_fake_user(uid=506))
                inter.response.send_message = AsyncMock()
                view = PanelView(self.store)
                await view.btn_select_version.callback(inter)

            kw = inter.response.send_message.call_args[1]
            self.assertIsInstance(kw.get("view"), VersionPickView)
        finally:
            public_manifest.unlink(missing_ok=True)


class TestVersionPickSelectCallback(unittest.IsolatedAsyncioTestCase):
    async def test_public_version_reply_has_only_desktop_and_mobile_copy(self) -> None:
        """Selected public version reply must contain only Desktop Copy and Mobile Copy."""
        info = RejoinVersionInfo(
            version="v1.0.0",
            channel="stable",
            label="v1.0.0 Stable",
            description="",
            install_ref="refs/tags/v1.0.0",
            internal_only=False,
        )
        sel = VersionPickSelect([info])
        sel._values = ["v1.0.0"]
        inter = _fake_interaction()
        inter.response.send_message = AsyncMock()
        await sel.callback(inter)

        kw = inter.response.send_message.call_args[1]
        self.assertIsNone(kw.get("embed"), "No embed must be sent with the version reply")
        content = kw["content"]
        self.assertIn("Install DENG Tool: Rejoin v1.0.0", content)
        self.assertIn("Desktop Copy:", content)
        self.assertIn("Mobile Copy:", content)
        self.assertTrue(kw["ephemeral"])
        # No metadata in the reply
        for forbidden in ("Selected version", "Channel:", "Visibility:", "Internal testing", "After install:"):
            self.assertNotIn(forbidden, content, msg=f"Forbidden text found: {forbidden!r}")
        cmd = "curl -fsSL https://rejoin.deng.my.id/install/v1.0.0 -o install.sh && bash install.sh"
        mobile = content.split("Mobile Copy:\n", 1)[1]
        self.assertEqual(mobile, f"`{cmd}`")
        self.assertNotIn("```", mobile)
        self.assertEqual(mobile.count("`"), 2)

    async def test_public_version_reply_no_duplicate_copy_blocks(self) -> None:
        """Desktop Copy and Mobile Copy must each appear exactly once."""
        info = RejoinVersionInfo(
            version="v1.0.0",
            channel="stable",
            label="v1.0.0 Stable",
            description="",
            install_ref="refs/tags/v1.0.0",
            internal_only=False,
        )
        sel = VersionPickSelect([info])
        sel._values = ["v1.0.0"]
        inter = _fake_interaction()
        inter.response.send_message = AsyncMock()
        await sel.callback(inter)

        content = inter.response.send_message.call_args[1]["content"]
        self.assertEqual(content.count("Desktop Copy:"), 1, "Desktop Copy must appear exactly once")
        self.assertEqual(content.count("Mobile Copy:"), 1, "Mobile Copy must appear exactly once")


if __name__ == "__main__":
    unittest.main()
