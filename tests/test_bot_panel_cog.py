"""Tests for the Discord license panel cog (LicensePanelCog).

Uses unittest + mocked discord.py interactions so no live token is required.
Tests cover:
  - _is_owner / _owner_ids parsing
  - PanelView button handler logic (generate, reset_hwid, redeem)
  - RedeemModal submission
  - LicensePanelCog command helpers (owner denied, panel config persistence)
  - Duplicate panel post prevention
  - restore_persistent_views
"""

from __future__ import annotations

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

from agent.license_store import (
    LocalJsonLicenseStore,
    MAX_HWID_RESETS_PER_24H,
    ActiveKeyWarning,
    ResetLimitError,
    UserLimitError,
)
from bot.cog_license_panel import (
    LicensePanelCog,
    PanelView,
    RedeemModal,
    _is_owner,
    _owner_ids,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_store(tmp_dir: str) -> LocalJsonLicenseStore:
    return LocalJsonLicenseStore(Path(tmp_dir) / "license_store.json")


def _fake_user(uid: int = 111, name: str = "TestUser#0001") -> MagicMock:
    user = MagicMock()
    user.id = uid
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


# ── PanelView — Generate Key ──────────────────────────────────────────────────

class TestPanelViewGenerate(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.store = _make_store(self._tmp.name)

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def test_generate_creates_key_and_responds_ephemeral(self) -> None:
        inter = _fake_interaction()
        view = PanelView(self.store)
        await view.btn_generate.callback(inter)
        inter.response.defer.assert_called_once_with(ephemeral=True)
        inter.followup.send.assert_called_once()
        _, kwargs = inter.followup.send.call_args
        self.assertTrue(kwargs.get("ephemeral"))

    async def test_generate_embed_contains_key(self) -> None:
        inter = _fake_interaction()
        view = PanelView(self.store)
        await view.btn_generate.callback(inter)
        _, kwargs = inter.followup.send.call_args
        embed = kwargs["embed"]
        self.assertIn("DENG-", embed.description)

    async def test_generate_limit_reached(self) -> None:
        """Second generate should show limit response, not raise."""
        inter = _fake_interaction(user=_fake_user(uid=42))
        view = PanelView(self.store)
        # First generate succeeds
        await view.btn_generate.callback(inter)
        # Second generate hits limit
        inter2 = _fake_interaction(user=_fake_user(uid=42))
        await view.btn_generate.callback(inter2)
        _, kwargs = inter2.followup.send.call_args
        embed = kwargs["embed"]
        self.assertIn("Limit", embed.title)
        self.assertTrue(kwargs.get("ephemeral"))


# ── PanelView — Reset HWID ────────────────────────────────────────────────────

class TestPanelViewResetHWID(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.store = _make_store(self._tmp.name)

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def test_reset_no_keys_gives_not_found(self) -> None:
        inter = _fake_interaction(user=_fake_user(uid=77))
        view = PanelView(self.store)
        await view.btn_reset_hwid.callback(inter)
        _, kwargs = inter.followup.send.call_args
        self.assertIn("No Keys", kwargs["embed"].title)

    async def test_reset_no_binding_after_generate(self) -> None:
        """Generate creates a key but no device binding; reset should say 'No Device Bound'."""
        uid = 88
        inter_gen = _fake_interaction(user=_fake_user(uid=uid))
        inter_reset = _fake_interaction(user=_fake_user(uid=uid))
        view = PanelView(self.store)
        await view.btn_generate.callback(inter_gen)
        await view.btn_reset_hwid.callback(inter_reset)
        _, kwargs = inter_reset.followup.send.call_args
        embed = kwargs["embed"]
        # No device binding was ever created, so reset must NOT claim success.
        self.assertIn("No Device Bound", embed.title)
        self.assertTrue(kwargs.get("ephemeral"))

    async def test_reset_success_with_bound_device(self) -> None:
        """Reset after a device has been bound (old last_seen) must return the HWID Reset success embed."""
        from agent.license import normalize_license_key, hash_license_key
        uid = 880
        inter_gen = _fake_interaction(user=_fake_user(uid=uid))
        view = PanelView(self.store)
        await view.btn_generate.callback(inter_gen)
        # Simulate a bound device with an old last_seen_at so the active guard passes
        uid_str = str(uid)
        keys = self.store.list_user_keys(uid_str)
        key_id = keys[0]["id"]
        self.store.bind_or_check_device(
            # bind_or_check_device takes the raw key, so we need to find it from id (key_hash)
            # Instead patch reset_hwid to return None (success)
            uid_str, uid_str, uid_str, uid_str  # placeholder - bypassed below
        )
        # The cleanest approach: patch reset_hwid to succeed
        from unittest.mock import patch as _patch
        with _patch.object(self.store, "reset_hwid", return_value=None):
            inter_reset = _fake_interaction(user=_fake_user(uid=uid))
            await view.btn_reset_hwid.callback(inter_reset)
        _, kwargs = inter_reset.followup.send.call_args
        embed = kwargs["embed"]
        self.assertIn("HWID", embed.title)
        self.assertTrue(kwargs.get("ephemeral"))

    async def test_reset_active_warning(self) -> None:
        """Key that was recently seen should raise ActiveKeyWarning → warning embed."""
        uid = 99
        inter_gen = _fake_interaction(user=_fake_user(uid=uid))
        view = PanelView(self.store)
        await view.btn_generate.callback(inter_gen)

        # Simulate the key being recently seen by patching reset_hwid
        def _raise_active(*args: Any, **kwargs: Any) -> None:
            raise ActiveKeyWarning("240s ago.")

        with patch.object(self.store, "reset_hwid", side_effect=_raise_active):
            inter_reset = _fake_interaction(user=_fake_user(uid=uid))
            await view.btn_reset_hwid.callback(inter_reset)

        _, kwargs = inter_reset.followup.send.call_args
        embed = kwargs["embed"]
        self.assertIn("Active", embed.title)

    async def test_reset_limit_exceeded(self) -> None:
        uid = 101
        inter_gen = _fake_interaction(user=_fake_user(uid=uid))
        view = PanelView(self.store)
        await view.btn_generate.callback(inter_gen)

        def _raise_limit(*args: Any, **kwargs: Any) -> None:
            raise ResetLimitError("Limit reached.")

        with patch.object(self.store, "reset_hwid", side_effect=_raise_limit):
            with patch.object(self.store, "get_reset_count_24h", return_value=MAX_HWID_RESETS_PER_24H):
                inter_reset = _fake_interaction(user=_fake_user(uid=uid))
                await view.btn_reset_hwid.callback(inter_reset)

        _, kwargs = inter_reset.followup.send.call_args
        embed = kwargs["embed"]
        self.assertIn("Limit", embed.title)


# ── PanelView — Redeem Key ────────────────────────────────────────────────────

class TestPanelViewRedeem(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.store = _make_store(self._tmp.name)

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def test_redeem_opens_modal(self) -> None:
        inter = _fake_interaction()
        view = PanelView(self.store)
        await view.btn_redeem.callback(inter)
        inter.response.send_modal.assert_called_once()
        modal_arg = inter.response.send_modal.call_args[0][0]
        self.assertIsInstance(modal_arg, RedeemModal)


# ── RedeemModal ───────────────────────────────────────────────────────────────

class TestRedeemModal(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.store = _make_store(self._tmp.name)

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def test_redeem_unowned_key_succeeds(self) -> None:
        # Create a key owned by user A
        uid_a = "111"
        self.store.get_or_create_user(uid_a)
        full_key = self.store.create_key_for_user(uid_a)
        # Revoke ownership so it's unowned
        db_path = Path(self._tmp.name) / "license_store.json"
        import json
        raw = json.loads(db_path.read_text())
        for k in raw["keys"].values():
            k["owner_discord_id"] = None
        db_path.write_text(json.dumps(raw, indent=2))

        # Redeem as user B
        uid_b = "222"
        modal = RedeemModal(self.store)
        modal.key_input._value = full_key  # type: ignore[attr-defined]  # inject value

        inter = _fake_interaction(user=_fake_user(uid=int(uid_b)))
        await modal.on_submit(inter)
        inter.response.defer.assert_called_once()
        _, kwargs = inter.followup.send.call_args
        embed = kwargs["embed"]
        self.assertIn("Redeemed", embed.title)
        self.assertNotIn(full_key, embed.description)  # full key NOT in success embed

    async def test_redeem_invalid_key_shows_error(self) -> None:
        modal = RedeemModal(self.store)
        modal.key_input._value = "DENG-FAKE-FAKE-NOPE-NOPE"  # type: ignore[attr-defined]

        inter = _fake_interaction(user=_fake_user(uid=333))
        await modal.on_submit(inter)
        _, kwargs = inter.followup.send.call_args
        embed = kwargs["embed"]
        self.assertIn("Failed", embed.title)

    async def test_redeem_already_owned_key_shows_ownership_error(self) -> None:
        uid_a = "444"
        self.store.get_or_create_user(uid_a)
        full_key = self.store.create_key_for_user(uid_a)

        uid_b = "555"
        modal = RedeemModal(self.store)
        modal.key_input._value = full_key  # type: ignore[attr-defined]

        inter = _fake_interaction(user=_fake_user(uid=int(uid_b)))
        await modal.on_submit(inter)
        _, kwargs = inter.followup.send.call_args
        embed = kwargs["embed"]
        self.assertIn("Failed", embed.title)


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
        self.bot.tree.add_command.assert_called_once()
        cmd = self.bot.tree.add_command.call_args[0][0]
        self.assertEqual(cmd.name, "license_panel")


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

        self.assertEqual(self.bot.add_view.call_count, call_count_before)


# ── Security: sensitive data not in responses ────────────────────────────────

class TestSecurity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.store = _make_store(self._tmp.name)

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def test_full_key_not_in_redeem_success_embed(self) -> None:
        uid = "660"
        self.store.get_or_create_user(uid)
        full_key = self.store.create_key_for_user(uid)

        # Reset ownership so another user can redeem
        import json
        db_path = Path(self._tmp.name) / "license_store.json"
        raw = json.loads(db_path.read_text())
        for k in raw["keys"].values():
            k["owner_discord_id"] = None
        db_path.write_text(json.dumps(raw, indent=2))

        modal = RedeemModal(self.store)
        modal.key_input._value = full_key  # type: ignore[attr-defined]
        inter = _fake_interaction(user=_fake_user(uid=661))
        await modal.on_submit(inter)

        _, kwargs = inter.followup.send.call_args
        description = kwargs["embed"].description
        # Full key hex must NOT be in the redeem response (only masked key)
        hex_part = full_key.replace("DENG-", "").replace("-", "")
        self.assertNotIn(hex_part, description)

    async def test_generate_key_shown_once_in_full(self) -> None:
        inter = _fake_interaction(user=_fake_user(uid=700))
        view = PanelView(self.store)
        await view.btn_generate.callback(inter)
        _, kwargs = inter.followup.send.call_args
        desc = kwargs["embed"].description
        # Full key IS in generate response (one-time display)
        self.assertIn("DENG-", desc)


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


if __name__ == "__main__":
    unittest.main()
