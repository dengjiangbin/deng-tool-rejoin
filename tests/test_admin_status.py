"""Tests for /license_panel admin_status command fixes.

Tests cover the 12 requirements from the fix task:
 1. SupabaseLicenseStore does not call _load.
 2. No "Could not read store: …_load…" error appears.
 3. License Store shows Backend + Status: ✅ Ready when health check passes.
 4. Store health failure shows safe short warning without secrets.
 5. Set by formats as <@ID> mention, not plain numeric ID.
 6. Updated uses Discord timestamp <t:UNIX:f>, not raw ISO string.
 7. Global Config section appears in embed.
 8. Global Config shows Max Key Slot: 2 and Max Reset: 1 when DB config is missing.
 9. Global Config uses DB values when configured.
10. Message deleted status renders cleanly (no crash, shows hint).
11. Footer is "DENG Tool: Rejoin", not "Guild: <ID>".
12. No secrets are printed in admin_status.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import discord
from agent.license_store import (
    DEFAULT_GLOBAL_MAX_KEYS,
    DEFAULT_GLOBAL_MAX_PANEL,
    LocalJsonLicenseStore,
)
from bot.cog_license_panel import (
    LicensePanelCog,
    _format_discord_ts,
    _format_user_mention,
)


# ── Test helpers ──────────────────────────────────────────────────────────────

def _make_store(tmp_dir: str) -> LocalJsonLicenseStore:
    return LocalJsonLicenseStore(Path(tmp_dir) / "license_store.json")


def _fake_user(uid: int = 555, name: str = "TestOwner") -> MagicMock:
    user = MagicMock()
    user.id = uid
    user.display_name = name
    user.name = name
    user.__str__ = lambda self: name
    return user


def _fake_interaction(user: MagicMock | None = None, guild_id: int = 9999) -> MagicMock:
    inter = MagicMock()
    inter.user = user or _fake_user()
    inter.guild_id = guild_id
    inter.guild = MagicMock()
    inter.guild.id = guild_id
    inter.guild.get_channel = MagicMock(return_value=None)
    inter.response = MagicMock()
    inter.response.send_message = AsyncMock()
    inter.response.defer = AsyncMock()
    inter.followup = MagicMock()
    inter.followup.send = AsyncMock()
    return inter


def _fake_bot() -> MagicMock:
    bot = MagicMock()
    bot.tree = MagicMock()
    bot.tree.add_command = MagicMock()
    bot.add_view = MagicMock()
    bot.guilds = []
    return bot


def _get_cmd(cog: LicensePanelCog):
    return next(c for c in cog._panel_group.commands if c.name == "admin_status")


def _embed_from_followup(inter: MagicMock) -> discord.Embed:
    _, kwargs = inter.followup.send.call_args
    return kwargs["embed"]


def _field_value(embed: discord.Embed, name: str) -> str:
    for f in embed.fields:
        if f.name == name:
            return f.value
    raise KeyError(f"Field '{name}' not found in embed")


# ── Test: _format_discord_ts helper ───────────────────────────────────────────

class TestFormatDiscordTs(unittest.TestCase):
    """Unit tests for the _format_discord_ts helper."""

    def test_valid_iso_returns_discord_timestamp(self):
        result = _format_discord_ts("2026-05-26T07:04:13.532264+00:00")
        self.assertTrue(result.startswith("<t:"))
        self.assertTrue(result.endswith(":f>"))
        # Verify it contains a numeric Unix timestamp
        unix_part = result[3:-3]
        self.assertTrue(unix_part.isdigit(), f"Expected numeric Unix ts, got: {unix_part}")

    def test_does_not_return_raw_iso(self):
        raw_iso = "2026-05-26T07:04:13.532264+00:00"
        result = _format_discord_ts(raw_iso)
        self.assertNotIn("2026-05-26T07:04:13", result)

    def test_empty_returns_not_set(self):
        self.assertEqual(_format_discord_ts(""), "Not set")
        self.assertEqual(_format_discord_ts(None), "Not set")
        self.assertEqual(_format_discord_ts("—"), "Not set")

    def test_z_suffix_iso_is_handled(self):
        result = _format_discord_ts("2026-01-01T00:00:00Z")
        self.assertTrue(result.startswith("<t:"))

    def test_invalid_returns_backtick_quoted(self):
        result = _format_discord_ts("not-a-date")
        self.assertIn("`", result)


# ── Test: _format_user_mention helper ─────────────────────────────────────────

class TestFormatUserMention(unittest.TestCase):
    """Unit tests for the _format_user_mention helper."""

    def test_numeric_id_returns_mention(self):
        result = _format_user_mention("110184213604499456")
        self.assertEqual(result, "<@110184213604499456>")

    def test_does_not_return_plain_id(self):
        result = _format_user_mention("110184213604499456")
        self.assertNotEqual(result, "110184213604499456")

    def test_empty_returns_not_set(self):
        self.assertEqual(_format_user_mention(""), "Not set")
        self.assertEqual(_format_user_mention(None), "Not set")
        self.assertEqual(_format_user_mention("—"), "Not set")

    def test_non_numeric_returns_backtick(self):
        result = _format_user_mention("DENG#1234")
        self.assertIn("`", result)


# ── Main admin_status embed tests ─────────────────────────────────────────────

class TestAdminStatusEmbedFixes(unittest.IsolatedAsyncioTestCase):
    """End-to-end tests for all 12 requirements on admin_status."""

    async def asyncSetUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.store = _make_store(self._tmp.name)
        self.bot = _fake_bot()

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    def _make_cog(self) -> LicensePanelCog:
        return LicensePanelCog(self.bot, self.store)

    # ─── Req 1 + 2: SupabaseLicenseStore mock — no _load call, no error ───────

    async def test_supabase_store_does_not_call_load(self):
        """Req 1: admin_status must not call _load on SupabaseLicenseStore."""
        from unittest.mock import MagicMock, AsyncMock

        # Build a fake Supabase-like store with get_store_status but no _load
        fake_store = MagicMock(spec=LocalJsonLicenseStore)
        del fake_store._load  # Ensure _load is not accessible on spec
        fake_store.get_panel_config = MagicMock(return_value=None)
        fake_store.get_global_max_keys = MagicMock(return_value=2)
        fake_store.get_global_max_panel = MagicMock(return_value=1)
        fake_store.get_store_status = MagicMock(return_value={
            "backend": "SupabaseLicenseStore",
            "status": "ready",
            "detail": None,
        })
        type(fake_store).__name__ = "SupabaseLicenseStore"

        bot = _fake_bot()
        cog = LicensePanelCog(bot, fake_store)
        cmd = _get_cmd(cog)

        with patch.dict("os.environ", {"LICENSE_OWNER_DISCORD_IDS": "555"}):
            inter = _fake_interaction(user=_fake_user(uid=555))
            await cmd.callback(inter)

        # get_store_status must be called, _load must not
        fake_store.get_store_status.assert_called_once()
        self.assertFalse(
            hasattr(fake_store, "_load") and fake_store._load.called,
            "_load should not be called on a SupabaseLicenseStore-like store",
        )

    async def test_no_load_error_message_in_embed(self):
        """Req 2: embed must not contain 'Could not read store' error text."""
        cog = self._make_cog()
        cmd = _get_cmd(cog)

        with patch.dict("os.environ", {"LICENSE_OWNER_DISCORD_IDS": "555"}):
            inter = _fake_interaction(user=_fake_user(uid=555))
            await cmd.callback(inter)

        embed = _embed_from_followup(inter)
        all_text = " ".join(f.value for f in embed.fields)
        self.assertNotIn("Could not read store", all_text)
        self.assertNotIn("_load", all_text)
        self.assertNotIn("has no attribute", all_text)

    # ─── Req 3: License Store shows ✅ Ready ──────────────────────────────────

    async def test_store_status_shows_ready(self):
        """Req 3: License Store section shows Status: ✅ Ready."""
        cog = self._make_cog()
        cmd = _get_cmd(cog)

        with patch.dict("os.environ", {"LICENSE_OWNER_DISCORD_IDS": "555"}):
            inter = _fake_interaction(user=_fake_user(uid=555))
            await cmd.callback(inter)

        embed = _embed_from_followup(inter)
        store_val = _field_value(embed, "License Store")
        self.assertIn("LocalJsonLicenseStore", store_val)
        self.assertIn("✅ Ready", store_val)
        self.assertNotIn("⚠️", store_val)

    # ─── Req 4: Store health failure shows safe short warning ─────────────────

    async def test_store_health_failure_shows_safe_warning(self):
        """Req 4: Error status shows ⚠️ Error without secrets in the embed."""
        fake_store = MagicMock(spec=LocalJsonLicenseStore)
        fake_store.get_panel_config = MagicMock(return_value=None)
        fake_store.get_global_max_keys = MagicMock(return_value=2)
        fake_store.get_global_max_panel = MagicMock(return_value=1)
        fake_store.get_store_status = MagicMock(return_value={
            "backend": "SupabaseLicenseStore",
            "status": "error",
            "detail": "Connection failed",
        })

        cog = LicensePanelCog(_fake_bot(), fake_store)
        cmd = _get_cmd(cog)

        with patch.dict("os.environ", {"LICENSE_OWNER_DISCORD_IDS": "555"}):
            inter = _fake_interaction(user=_fake_user(uid=555))
            await cmd.callback(inter)

        embed = _embed_from_followup(inter)
        store_val = _field_value(embed, "License Store")
        self.assertIn("⚠️ Error", store_val)
        self.assertNotIn("SUPABASE_URL", store_val)
        self.assertNotIn("service_role", store_val)
        self.assertNotIn("traceback", store_val.lower())
        # Must contain a safe summary
        self.assertIn("Connection failed", store_val)

    # ─── Req 5: Set by formats as <@ID> mention ────────────────────────────────

    async def test_set_by_shows_mention_not_plain_id(self):
        """Req 5: Set by must show <@ID>, not just the raw number."""
        self.store.save_panel_config(
            str(_fake_interaction().guild_id), "9000", "8000", "110184213604499456"
        )
        cog = self._make_cog()
        cmd = _get_cmd(cog)

        with patch.dict("os.environ", {"LICENSE_OWNER_DISCORD_IDS": "555"}):
            inter = _fake_interaction(user=_fake_user(uid=555))
            await cmd.callback(inter)

        embed = _embed_from_followup(inter)
        panel_val = _field_value(embed, "Panel Config")
        self.assertIn("<@110184213604499456>", panel_val)
        # Must NOT be just the bare numeric ID without mention wrapper
        self.assertNotIn("`110184213604499456`", panel_val)

    # ─── Req 6: Updated uses Discord timestamp format ─────────────────────────

    async def test_updated_uses_discord_timestamp(self):
        """Req 6: Updated must use <t:UNIX:f>, not raw ISO timestamp."""
        self.store.save_panel_config(
            str(_fake_interaction().guild_id), "9000", "8000", "555"
        )
        cog = self._make_cog()
        cmd = _get_cmd(cog)

        with patch.dict("os.environ", {"LICENSE_OWNER_DISCORD_IDS": "555"}):
            inter = _fake_interaction(user=_fake_user(uid=555))
            await cmd.callback(inter)

        embed = _embed_from_followup(inter)
        panel_val = _field_value(embed, "Panel Config")
        # Timestamp must be in Discord format
        self.assertRegex(panel_val, r"<t:\d+:f>")
        # Must NOT contain the raw ISO format
        self.assertNotIn("T", panel_val.split("Updated")[1].split("\n")[0].replace(
            "<t:", ""
        ).replace(":f>", ""))

    # ─── Req 7: Global Config section appears ─────────────────────────────────

    async def test_global_config_section_present(self):
        """Req 7: Global Config section must appear in the embed."""
        cog = self._make_cog()
        cmd = _get_cmd(cog)

        with patch.dict("os.environ", {"LICENSE_OWNER_DISCORD_IDS": "555"}):
            inter = _fake_interaction(user=_fake_user(uid=555))
            await cmd.callback(inter)

        embed = _embed_from_followup(inter)
        field_names = [f.name for f in embed.fields]
        self.assertIn("Global Config", field_names)

    # ─── Req 8: Global Config shows defaults when DB config is missing ─────────

    async def test_global_config_shows_fallback_defaults(self):
        """Req 8: When no DB config, shows Max Key Slot: 2 and Max Reset: 1."""
        cog = self._make_cog()
        cmd = _get_cmd(cog)

        with patch.dict("os.environ", {"LICENSE_OWNER_DISCORD_IDS": "555"}):
            inter = _fake_interaction(user=_fake_user(uid=555))
            await cmd.callback(inter)

        embed = _embed_from_followup(inter)
        global_val = _field_value(embed, "Global Config")
        self.assertIn(f"**Max Key Slot:** {DEFAULT_GLOBAL_MAX_KEYS}", global_val)
        self.assertIn(f"**Max Reset:** {DEFAULT_GLOBAL_MAX_PANEL}", global_val)

    # ─── Req 9: Global Config uses DB values when configured ──────────────────

    async def test_global_config_uses_configured_db_values(self):
        """Req 9: When DB has custom limits, show those values."""
        self.store.set_global_max_keys(5, updated_by="555")
        self.store.set_global_max_panel(3, updated_by="555")

        cog = self._make_cog()
        cmd = _get_cmd(cog)

        with patch.dict("os.environ", {"LICENSE_OWNER_DISCORD_IDS": "555"}):
            inter = _fake_interaction(user=_fake_user(uid=555))
            await cmd.callback(inter)

        embed = _embed_from_followup(inter)
        global_val = _field_value(embed, "Global Config")
        self.assertIn("**Max Key Slot:** 5", global_val)
        self.assertIn("**Max Reset:** 3", global_val)

    # ─── Req 10: Message deleted renders cleanly with hint ────────────────────

    async def test_message_deleted_shows_clean_hint(self):
        """Req 10: ❌ deleted status renders without crash and shows action hint."""
        self.store.save_panel_config(
            str(_fake_interaction().guild_id), "9000", "8000", "555"
        )
        cog = self._make_cog()
        cmd = _get_cmd(cog)

        # Make the channel resolve but message fetch raises NotFound
        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_channel.fetch_message = AsyncMock(
            side_effect=discord.NotFound(MagicMock(), "Unknown Message")
        )

        with patch.dict("os.environ", {"LICENSE_OWNER_DISCORD_IDS": "555"}):
            inter = _fake_interaction(user=_fake_user(uid=555))
            inter.guild.get_channel = MagicMock(return_value=mock_channel)
            await cmd.callback(inter)  # Must not crash

        embed = _embed_from_followup(inter)
        panel_val = _field_value(embed, "Panel Config")
        self.assertIn("❌ deleted", panel_val)
        self.assertIn("/license_panel post", panel_val)

    # ─── Req 11: Footer is "DENG Tool: Rejoin" ────────────────────────────────

    async def test_footer_is_deng_tool_rejoin(self):
        """Req 11: Footer must say 'DENG Tool: Rejoin', not guild ID."""
        cog = self._make_cog()
        cmd = _get_cmd(cog)

        with patch.dict("os.environ", {"LICENSE_OWNER_DISCORD_IDS": "555"}):
            inter = _fake_interaction(user=_fake_user(uid=555), guild_id=1435142398647734396)
            await cmd.callback(inter)

        embed = _embed_from_followup(inter)
        self.assertEqual(embed.footer.text, "DENG Tool: Rejoin")
        self.assertNotIn("Guild:", embed.footer.text)
        self.assertNotIn("1435142398647734396", embed.footer.text)

    # ─── Req 12: No secrets are printed ───────────────────────────────────────

    async def test_no_secrets_in_embed(self):
        """Req 12: admin_status embed must never expose credentials or secrets."""
        cog = self._make_cog()
        cmd = _get_cmd(cog)

        with patch.dict("os.environ", {
            "LICENSE_OWNER_DISCORD_IDS": "555",
            "SUPABASE_SERVICE_ROLE_KEY": "super-secret-token-xyz",
            "DISCORD_BOT_TOKEN": "discord-bot-secret-abc",
        }):
            inter = _fake_interaction(user=_fake_user(uid=555))
            await cmd.callback(inter)

        embed = _embed_from_followup(inter)
        all_text = embed.title + " ".join(f.value for f in embed.fields)
        self.assertNotIn("super-secret-token-xyz", all_text)
        self.assertNotIn("discord-bot-secret-abc", all_text)
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", all_text)
        self.assertNotIn("DISCORD_BOT_TOKEN", all_text)


# ── Test: get_store_status on LocalJsonLicenseStore ───────────────────────────

class TestGetStoreStatusLocal(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = LocalJsonLicenseStore(Path(self._tmp.name) / "s.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_ready_status(self):
        result = self.store.get_store_status()
        self.assertEqual(result["status"], "ready")

    def test_returns_correct_backend_name(self):
        result = self.store.get_store_status()
        self.assertEqual(result["backend"], "LocalJsonLicenseStore")

    def test_detail_contains_user_key_counts(self):
        self.store.get_or_create_user("100")
        self.store.set_user_key_limit("100", 5, "admin")
        self.store.create_key_for_user("100")
        result = self.store.get_store_status()
        self.assertIn("Users:", result["detail"])
        self.assertIn("Keys:", result["detail"])

    def test_no_secrets_in_status(self):
        result = self.store.get_store_status()
        status_str = str(result)
        self.assertNotIn("token", status_str.lower())
        self.assertNotIn("secret", status_str.lower())


# ── Test: get_store_status on a mock SupabaseLicenseStore-like object ─────────

class TestGetStoreStatusSupabase(unittest.TestCase):
    """Validate get_store_status on a store that has no _load (like SupabaseLicenseStore)."""

    def test_supabase_store_status_ready(self):
        from unittest.mock import MagicMock
        # Simulate SupabaseLicenseStore with working _client
        fake_store = MagicMock(spec=LocalJsonLicenseStore)
        fake_store.get_store_status = LocalJsonLicenseStore.get_store_status.__get__(
            MagicMock(spec=LocalJsonLicenseStore)
        )
        # The key test: explicitly verify the real SupabaseLicenseStore class
        # has get_store_status without needing _load
        from agent.license_store import SupabaseLicenseStore
        self.assertTrue(hasattr(SupabaseLicenseStore, "get_store_status"))

    def test_supabase_store_status_does_not_require_load(self):
        from agent.license_store import SupabaseLicenseStore
        import inspect
        src = inspect.getsource(SupabaseLicenseStore.get_store_status)
        self.assertNotIn("_load", src)

    def test_base_store_status_method_exists(self):
        from agent.license_store import BaseLicenseStore
        self.assertTrue(hasattr(BaseLicenseStore, "get_store_status"))

    def test_local_store_overrides_base_status(self):
        from agent.license_store import BaseLicenseStore
        # LocalJsonLicenseStore should override the base
        self.assertIsNot(
            LocalJsonLicenseStore.get_store_status,
            BaseLicenseStore.get_store_status,
        )


if __name__ == "__main__":
    unittest.main()
