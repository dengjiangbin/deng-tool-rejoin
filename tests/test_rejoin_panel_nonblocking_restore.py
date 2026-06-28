"""Regression tests for the rejoin (license) panel staying alive when the
license store (Supabase) is slow or unreachable.

Root cause of the "panel dead in Discord" incident:
  * _health_loop and restore_persistent_views made SYNCHRONOUS Supabase calls
    directly on the asyncio event loop. When Supabase was slow those calls froze
    the Discord gateway heartbeat, the bot dropped, on_ready re-fired and spawned
    yet another health loop -> reconnect storm -> the panel went dead.

These tests lock in the fix:
  * the blocking panel-config read is offloaded to a worker thread,
  * restore is idempotent (safe for the background retry / every reconnect),
  * a store failure marks the guild pending (so it is retried) instead of
    crashing or being silently dropped forever.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.license_store import LocalJsonLicenseStore
from bot.cog_license_panel import LicensePanelCog


def _make_store(tmp_dir: str) -> LocalJsonLicenseStore:
    return LocalJsonLicenseStore(Path(tmp_dir) / "license_store.json")


def _fake_bot() -> MagicMock:
    bot = MagicMock()
    bot.guilds = []
    bot.tree = MagicMock()
    bot.tree.add_command = MagicMock()
    bot.add_view = MagicMock()
    return bot


class TestNonblockingRestore(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.store = _make_store(self._tmp.name)
        self.bot = _fake_bot()

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    def _cog_with_guild(self, guild_id: int = 555, message_id: str = "123") -> LicensePanelCog:
        guild = MagicMock()
        guild.id = guild_id
        self.bot.guilds = [guild]
        self.store.save_panel_config(str(guild_id), "9999", message_id, "1")
        cog = LicensePanelCog(self.bot, self.store)
        # Avoid real Discord HTTP during the cosmetic message refresh.
        cog._get_panel_channel = AsyncMock(return_value=None)
        return cog

    async def test_panel_config_read_runs_off_event_loop(self) -> None:
        cog = self._cog_with_guild()
        with patch(
            "bot.cog_license_panel.asyncio.to_thread", wraps=asyncio.to_thread
        ) as spy:
            pending = await cog.restore_persistent_views()
        self.assertEqual(pending, 0, "configured guild should restore on first pass")
        self.assertTrue(
            spy.called,
            "blocking get_panel_config must be offloaded to a worker thread "
            "(asyncio.to_thread) so it never blocks the gateway heartbeat",
        )

    async def test_restore_is_idempotent_no_duplicate_views(self) -> None:
        cog = self._cog_with_guild()
        await cog.restore_persistent_views()
        calls_after_first = self.bot.add_view.call_count
        pending = await cog.restore_persistent_views()
        self.assertEqual(pending, 0)
        self.assertEqual(
            self.bot.add_view.call_count,
            calls_after_first,
            "second restore pass must not re-register views (idempotent for the "
            "background retry / every on_ready reconnect)",
        )

    async def test_store_failure_marks_guild_pending_for_retry(self) -> None:
        guild = MagicMock()
        guild.id = 777
        self.bot.guilds = [guild]
        cog = LicensePanelCog(self.bot, self.store)
        cog._store = MagicMock()
        cog._store.get_panel_config = MagicMock(side_effect=RuntimeError("supabase down"))

        pending = await cog.restore_persistent_views()

        self.assertEqual(pending, 1, "a failed store read must count as pending")
        self.assertNotIn(
            "777", cog._restored_guild_ids,
            "a failed guild must NOT be marked restored, so the background retry "
            "re-attempts it once the store recovers",
        )

    async def test_schedule_does_not_spawn_duplicate_restore_tasks(self) -> None:
        cog = self._cog_with_guild()
        cog.schedule_persistent_view_restore()
        first = cog._restore_task
        cog.schedule_persistent_view_restore()
        self.assertIs(
            cog._restore_task, first,
            "scheduling again while a restore task is live must reuse it (on_ready "
            "fires on every reconnect — duplicates caused the original storm)",
        )
        if first is not None:
            await first


if __name__ == "__main__":
    unittest.main()
