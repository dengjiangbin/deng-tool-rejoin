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
    ConfirmResetButton,
    KeyStatsDownloadButton,
    KeyStatsNextButton,
    LicensePanelCog,
    PanelView,
    RedeemModal,
    ResetHwidSelect,
    ResetHwidSelectView,
    _build_key_stats_discord_embed,
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


class TestPanelViewFourButtons(unittest.TestCase):
    """Panel exposes Generate, Reset HWID, Redeem, Key Stats (order)."""

    def test_panel_view_has_four_children(self) -> None:
        with TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            view = PanelView(store)
            self.assertEqual(len(view.children), 4)


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
        """No keys → send_message (ephemeral, not followup) with 'No Keys' embed."""
        inter = _fake_interaction(user=_fake_user(uid=77))
        view = PanelView(self.store)
        await view.btn_reset_hwid.callback(inter)
        inter.response.send_message.assert_called_once()
        _, kwargs = inter.response.send_message.call_args
        self.assertIn("No Keys", kwargs["embed"].title)
        self.assertTrue(kwargs.get("ephemeral"))

    async def test_reset_opens_selector_when_key_exists(self) -> None:
        """Having an unbound key → selector view is opened via send_message."""
        uid = 88
        inter_gen = _fake_interaction(user=_fake_user(uid=uid))
        inter_reset = _fake_interaction(user=_fake_user(uid=uid))
        view = PanelView(self.store)
        await view.btn_generate.callback(inter_gen)
        await view.btn_reset_hwid.callback(inter_reset)
        inter_reset.response.send_message.assert_called_once()
        _, kwargs = inter_reset.response.send_message.call_args
        # Must send a ResetHwidSelectView, not a plain embed
        self.assertIsInstance(kwargs.get("view"), ResetHwidSelectView)
        self.assertTrue(kwargs.get("ephemeral"))

    async def test_confirm_reset_success_with_bound_device(self) -> None:
        """ConfirmResetButton with a resettable key emits 'HWID Reset Results' embed."""
        uid_str = "880"
        self.store.get_or_create_user(uid_str, "TestUser")
        raw_key = self.store.create_key_for_user(uid_str)
        from agent.license import hash_license_key, normalize_license_key
        key_id = hash_license_key(normalize_license_key(raw_key))
        # Use a key state dict that says can_reset=True (binding exists, old heartbeat)
        key_state = {
            "key_id": key_id,
            "masked_key": "DENG-????...????",
            "status": "active",
            "active_binding": True,
            "device_model": "Test Device",
            "device_label": "",
            "last_seen_at": "2000-01-01T00:00:00+00:00",
            "reset_count_24h": 0,
            "can_reset": True,
            "reason_if_not_resettable": None,
        }
        keys_with_state = [key_state]
        sel_view = ResetHwidSelectView(self.store, uid_str, keys_with_state)
        select = next(c for c in sel_view.children if isinstance(c, ResetHwidSelect))
        select._values = [key_id]  # values is a read-only property backed by _values
        confirm_btn = next(c for c in sel_view.children if isinstance(c, ConfirmResetButton))
        inter = _fake_interaction(user=_fake_user(uid=880))
        # Patch reset_hwid to succeed without needing a real binding in the store
        with patch.object(self.store, "reset_hwid", return_value=None):
            await confirm_btn.callback(inter)
        inter.response.edit_message.assert_called_once()
        _, kwargs = inter.response.edit_message.call_args
        self.assertIn("HWID", kwargs["embed"].title)

    async def test_confirm_reset_active_warning(self) -> None:
        """ConfirmResetButton with can_reset=False (recently active) shows reason."""
        uid_str = "99"
        self.store.get_or_create_user(uid_str, "TestUser")
        raw_key = self.store.create_key_for_user(uid_str)
        from agent.license import hash_license_key, normalize_license_key
        key_id = hash_license_key(normalize_license_key(raw_key))
        key_state = {
            "key_id": key_id,
            "masked_key": "DENG-????...????",
            "status": "active",
            "active_binding": True,
            "device_model": "Phone",
            "device_label": "",
            "last_seen_at": None,
            "reset_count_24h": 0,
            "can_reset": False,
            "reason_if_not_resettable": "Key active 1m 0s ago — wait 5 min",
        }
        sel_view = ResetHwidSelectView(self.store, uid_str, [key_state])
        select = next(c for c in sel_view.children if isinstance(c, ResetHwidSelect))
        select._values = [key_id]  # values is a read-only property backed by _values
        confirm_btn = next(c for c in sel_view.children if isinstance(c, ConfirmResetButton))
        inter = _fake_interaction(user=_fake_user(uid=99))
        await confirm_btn.callback(inter)
        inter.response.edit_message.assert_called_once()
        _, kwargs = inter.response.edit_message.call_args
        embed = kwargs["embed"]
        self.assertIn("HWID", embed.title)
        # Reason text should appear in description
        self.assertIn("wait 5 min", embed.description)

    async def test_confirm_reset_limit_exceeded(self) -> None:
        """ConfirmResetButton with can_reset=False (limit) shows limit reason."""
        uid_str = "101"
        self.store.get_or_create_user(uid_str, "TestUser")
        raw_key = self.store.create_key_for_user(uid_str)
        from agent.license import hash_license_key, normalize_license_key
        key_id = hash_license_key(normalize_license_key(raw_key))
        reason = f"Reset limit reached ({MAX_HWID_RESETS_PER_24H}/{MAX_HWID_RESETS_PER_24H} today)"
        key_state = {
            "key_id": key_id,
            "masked_key": "DENG-????...????",
            "status": "active",
            "active_binding": True,
            "device_model": "Phone",
            "device_label": "",
            "last_seen_at": None,
            "reset_count_24h": MAX_HWID_RESETS_PER_24H,
            "can_reset": False,
            "reason_if_not_resettable": reason,
        }
        sel_view = ResetHwidSelectView(self.store, uid_str, [key_state])
        select = next(c for c in sel_view.children if isinstance(c, ResetHwidSelect))
        select._values = [key_id]  # values is a read-only property backed by _values
        confirm_btn = next(c for c in sel_view.children if isinstance(c, ConfirmResetButton))
        inter = _fake_interaction(user=_fake_user(uid=101))
        await confirm_btn.callback(inter)
        inter.response.edit_message.assert_called_once()
        _, kwargs = inter.response.edit_message.call_args
        embed = kwargs["embed"]
        self.assertIn("HWID", embed.title)
        self.assertIn("Reset limit", embed.description)


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
        self.assertIn("any license keys", kwargs["embed"].description.lower())

    async def test_key_stats_shows_unused(self) -> None:
        inter_g = _fake_interaction(user=_fake_user(uid=502))
        inter_s = _fake_interaction(user=_fake_user(uid=502))
        view = PanelView(self.store)
        await view.btn_generate.callback(inter_g)
        await view.btn_key_stats.callback(inter_s)
        _, kwargs = inter_s.followup.send.call_args
        self.assertIn("Unused", kwargs["embed"].description)

    async def test_key_stats_pagination_title(self) -> None:
        uid = 503
        self.store.get_or_create_user(str(uid))
        self.store.set_user_max_keys(str(uid), 10)
        gen_view = PanelView(self.store)
        for _ in range(6):
            inter = _fake_interaction(user=_fake_user(uid=uid))
            await gen_view.btn_generate.callback(inter)
        inter_s = _fake_interaction(user=_fake_user(uid=uid))
        await gen_view.btn_key_stats.callback(inter_s)
        _, kwargs = inter_s.followup.send.call_args
        self.assertIn("Page 1/2", kwargs["embed"].title)

    async def test_key_stats_next_page(self) -> None:
        uid = 504
        self.store.get_or_create_user(str(uid))
        self.store.set_user_max_keys(str(uid), 10)
        gen_view = PanelView(self.store)
        for _ in range(6):
            await gen_view.btn_generate.callback(_fake_interaction(user=_fake_user(uid=uid)))
        inter_s = _fake_interaction(user=_fake_user(uid=uid))
        await gen_view.btn_key_stats.callback(inter_s)
        stats_view = inter_s.followup.send.call_args[1]["view"]
        nxt = next(c for c in stats_view.children if isinstance(c, KeyStatsNextButton))
        inter2 = _fake_interaction(user=_fake_user(uid=uid))
        await nxt.callback(inter2)
        inter2.response.edit_message.assert_called_once()
        _, ek = inter2.response.edit_message.call_args
        self.assertIn("Page 2/2", ek["embed"].title)

    async def test_key_stats_wrong_user_blocked(self) -> None:
        uid = 600
        self.store.get_or_create_user(str(uid))
        self.store.set_user_max_keys(str(uid), 10)
        gen_view = PanelView(self.store)
        for _ in range(6):
            await gen_view.btn_generate.callback(_fake_interaction(user=_fake_user(uid=uid)))
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
        self.assertEqual(dk["file"].filename, f"my_keys_{uid}.txt")


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
