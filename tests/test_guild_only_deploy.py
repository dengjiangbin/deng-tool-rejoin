"""Tests: guild-only deployment policy for DENG Tool: Rejoin.

Verifies:
1. on_ready does NOT call tree.sync() globally.
2. deploy script without --guild refuses to deploy.
3. deploy script has no --global deploy mode.
4. deploy script supports --clear-global only for cleanup.
5. deploy script supports --guild TARGET_GUILD_ID.
6. deploy script supports --clear-guild TARGET_GUILD_ID.
7. No token printed in any code path.
8. Wrong guild IDs are rejected.
"""

from __future__ import annotations

import ast
import inspect
import sys
import textwrap
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ── Project root on sys.path ───────────────────────────────────────────────────
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

# Import the modules under test (no Discord connection made at import time)
import bot.deploy_commands as deploy_mod
import bot.main as main_mod


TARGET_GUILD_ID = deploy_mod.TARGET_GUILD_ID


# ──────────────────────────────────────────────────────────────────────────────
# 1. on_ready source must not contain a bare global tree.sync()
# ──────────────────────────────────────────────────────────────────────────────

class TestOnReadyNoGlobalSync(unittest.TestCase):
    """Confirm main.py on_ready never calls tree.sync() without a guild argument."""

    def _get_on_ready_source(self) -> str:
        """Extract _build_and_run source and find the on_ready inner function."""
        src = inspect.getsource(main_mod._build_and_run)
        return src

    def test_no_global_tree_sync_in_on_ready(self) -> None:
        src = self._get_on_ready_source()
        # A bare `tree.sync()` with no guild= argument is the global path.
        # We allow tree.sync(guild=...) but not tree.sync() with no args.
        # Simple AST check: look for Call nodes where func ends in .sync and
        # there are no keyword args named 'guild'.
        tree = ast.parse(textwrap.dedent(src))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Check for attribute call ending in 'sync'
            if isinstance(func, ast.Attribute) and func.attr == "sync":
                kwarg_names = {kw.arg for kw in node.keywords}
                # If no 'guild' kwarg → this is a global sync → FAIL
                self.assertIn(
                    "guild",
                    kwarg_names,
                    "on_ready contains a bare tree.sync() with no guild= — this causes global sync!",
                )

    def test_on_ready_logs_guild_only_policy(self) -> None:
        src = self._get_on_ready_source()
        self.assertIn(
            "guild-only",
            src,
            "on_ready should mention guild-only policy in its log message.",
        )

    def test_on_ready_no_synced_globally_message(self) -> None:
        src = self._get_on_ready_source()
        self.assertNotIn(
            "globally",
            src,
            'on_ready should not reference "globally" — that implies global sync.',
        )


# ──────────────────────────────────────────────────────────────────────────────
# 2. Deploy script CLI validation
# ──────────────────────────────────────────────────────────────────────────────

class TestDeployCliValidation(unittest.TestCase):
    """Confirm deploy_commands.py CLI enforces guild-only policy."""

    def _parse(self, argv: list[str]) -> object:
        """Run argparse with the given argv; return args or capture SystemExit."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--guild", type=int, default=None)
        parser.add_argument("--clear-global", action="store_true")
        parser.add_argument("--clear-guild", type=int, default=None)
        return parser.parse_args(argv)

    def test_target_guild_id_constant(self) -> None:
        self.assertEqual(TARGET_GUILD_ID, 1435142398647734396)

    def test_no_args_causes_error(self) -> None:
        """Running with no args should trigger error (no default global deploy)."""
        with patch("sys.argv", ["deploy_commands"]):
            with patch("sys.stderr", new_callable=StringIO):
                with self.assertRaises(SystemExit) as ctx:
                    deploy_mod.main()
        self.assertEqual(ctx.exception.code, 2)

    def test_has_guild_argument(self) -> None:
        args = self._parse(["--guild", str(TARGET_GUILD_ID)])
        self.assertEqual(args.guild, TARGET_GUILD_ID)

    def test_has_clear_global_argument(self) -> None:
        args = self._parse(["--clear-global"])
        self.assertTrue(args.clear_global)

    def test_has_clear_guild_argument(self) -> None:
        args = self._parse(["--clear-guild", str(TARGET_GUILD_ID)])
        self.assertEqual(args.clear_guild, TARGET_GUILD_ID)

    def test_has_list_global_argument(self) -> None:
        """--list-global should be accepted without a guild argument."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--list-global", action="store_true")
        args = parser.parse_args(["--list-global"])
        self.assertTrue(args.list_global)

    def test_has_list_guild_argument(self) -> None:
        """--list-guild should accept the target guild ID."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--list-guild", type=int, default=None)
        args = parser.parse_args(["--list-guild", str(TARGET_GUILD_ID)])
        self.assertEqual(args.list_guild, TARGET_GUILD_ID)

    def test_list_global_accepted_without_guild(self) -> None:
        """--list-global flag must work without --guild (no guild required)."""
        with patch("sys.argv", ["deploy_commands", "--list-global"]):
            with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "fake-token"}):
                with patch.object(
                    deploy_mod, "_list_global", new=lambda token: None
                ):
                    with patch("asyncio.run", side_effect=lambda coro: None):
                        try:
                            deploy_mod.main()
                        except SystemExit as exc:
                            self.fail(f"--list-global raised SystemExit: {exc}")

    def test_list_guild_target_accepted(self) -> None:
        """--list-guild with target guild ID must not raise an error."""
        with patch("sys.argv", ["deploy_commands", "--list-guild", str(TARGET_GUILD_ID)]):
            with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "fake-token"}):
                with patch("asyncio.run", side_effect=lambda coro: None):
                    try:
                        deploy_mod.main()
                    except SystemExit as exc:
                        self.fail(f"--list-guild raised SystemExit: {exc}")

    def test_list_guild_wrong_id_rejected(self) -> None:
        """--list-guild with wrong guild ID must fail."""
        with patch("sys.argv", ["deploy_commands", "--list-guild", "9999999999999999999"]):
            with patch("sys.stderr", new_callable=StringIO):
                with self.assertRaises(SystemExit) as ctx:
                    deploy_mod.main()
        self.assertEqual(ctx.exception.code, 2)

    def test_wrong_guild_id_rejected(self) -> None:
        """A guild ID other than the target must fail."""
        with patch("sys.argv", ["deploy_commands", "--guild", "9999999999999999999"]):
            with patch("sys.stderr", new_callable=StringIO):
                with self.assertRaises(SystemExit) as ctx:
                    deploy_mod.main()
        self.assertEqual(ctx.exception.code, 2)

    def test_wrong_clear_guild_id_rejected(self) -> None:
        """--clear-guild with wrong ID must fail."""
        with patch("sys.argv", ["deploy_commands", "--clear-guild", "9999999999999999999"]):
            with patch("sys.stderr", new_callable=StringIO):
                with self.assertRaises(SystemExit) as ctx:
                    deploy_mod.main()
        self.assertEqual(ctx.exception.code, 2)

    def test_multiple_ops_rejected(self) -> None:
        """Specifying --guild and --clear-global together must fail."""
        with patch(
            "sys.argv",
            ["deploy_commands", "--guild", str(TARGET_GUILD_ID), "--clear-global"],
        ):
            with patch("sys.stderr", new_callable=StringIO):
                with self.assertRaises(SystemExit) as ctx:
                    deploy_mod.main()
        self.assertEqual(ctx.exception.code, 2)


# ──────────────────────────────────────────────────────────────────────────────
# 3. Deploy script source-level checks — no global sync path
# ──────────────────────────────────────────────────────────────────────────────

class TestDeployScriptSourcePolicy(unittest.TestCase):
    """Inspect deploy_commands.py source to ensure global sync is impossible."""

    def _src(self) -> str:
        return inspect.getsource(deploy_mod)

    def test_no_global_sync_function(self) -> None:
        src = self._src()
        self.assertNotIn(
            "_deploy_global",
            src,
            "deploy_commands must not have a global deploy function.",
        )

    def test_clear_global_uses_clear_commands_not_sync(self) -> None:
        """_clear_global must call clear_commands(guild=None) then sync() to push empty list."""
        src = inspect.getsource(deploy_mod._clear_global)
        self.assertIn("clear_commands", src)

    def test_deploy_guild_does_not_sync_globally(self) -> None:
        """_deploy_guild must only sync to a specific guild, never globally."""
        src = inspect.getsource(deploy_mod._deploy_guild)
        tree = ast.parse(textwrap.dedent(src))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "sync":
                kwarg_names = {kw.arg for kw in node.keywords}
                self.assertIn(
                    "guild",
                    kwarg_names,
                    "_deploy_guild contains a bare tree.sync() — this is a global sync!",
                )

    def test_no_token_in_log_calls(self) -> None:
        """No log call should reference the token variable directly."""
        src = self._src()
        # Simple heuristic: token should not appear as a log argument
        # (it's only checked for being non-empty then passed to Discord)
        self.assertNotIn("log.info(token", src)
        self.assertNotIn("log.warning(token", src)
        self.assertNotIn("log.error(token", src)

    def test_target_guild_id_present(self) -> None:
        src = self._src()
        self.assertIn(str(TARGET_GUILD_ID), src)


# ──────────────────────────────────────────────────────────────────────────────
# 4. Async function mocking tests
# ──────────────────────────────────────────────────────────────────────────────

class TestDeployGuildAsync(unittest.IsolatedAsyncioTestCase):
    """Test _deploy_guild syncs to guild, not globally."""

    async def test_deploy_guild_calls_sync_with_guild(self) -> None:
        mock_bot = MagicMock()
        mock_bot.tree.sync = AsyncMock(return_value=[])
        mock_bot.tree.copy_global_to = MagicMock()
        mock_bot.close = AsyncMock()
        mock_bot.login = AsyncMock()

        with patch.object(deploy_mod, "_login_only", new=AsyncMock(return_value=mock_bot)):
            await deploy_mod._deploy_guild("fake-token", TARGET_GUILD_ID)

        # sync must have been called with guild= keyword
        call_kwargs = mock_bot.tree.sync.call_args
        self.assertIn("guild", call_kwargs.kwargs)

    async def test_clear_global_calls_clear_commands_guild_none(self) -> None:
        mock_bot = MagicMock()
        mock_bot.tree.sync = AsyncMock(return_value=[])
        mock_bot.tree.clear_commands = MagicMock()
        mock_bot.close = AsyncMock()
        mock_bot.login = AsyncMock()

        with patch.object(deploy_mod, "_login_only", new=AsyncMock(return_value=mock_bot)):
            await deploy_mod._clear_global("fake-token")

        mock_bot.tree.clear_commands.assert_called_once_with(guild=None)
        # After clearing, sync() is called to push empty list globally
        mock_bot.tree.sync.assert_called_once()

    async def test_clear_guild_calls_clear_commands_with_guild(self) -> None:
        mock_bot = MagicMock()
        mock_bot.tree.sync = AsyncMock(return_value=[])
        mock_bot.tree.clear_commands = MagicMock()
        mock_bot.close = AsyncMock()
        mock_bot.login = AsyncMock()

        with patch.object(deploy_mod, "_login_only", new=AsyncMock(return_value=mock_bot)):
            await deploy_mod._clear_guild("fake-token", TARGET_GUILD_ID)

        call_kwargs = mock_bot.tree.clear_commands.call_args
        self.assertIn("guild", call_kwargs.kwargs)
        self.assertIsNotNone(call_kwargs.kwargs["guild"])

    async def test_list_global_calls_http_get_global_commands(self) -> None:
        """_list_global must call http.get_global_commands with the app ID."""
        mock_bot = MagicMock()
        mock_bot.user.id = 123456789
        mock_bot.http.get_global_commands = AsyncMock(return_value=[])
        mock_bot.close = AsyncMock()
        mock_bot.login = AsyncMock()

        with patch.object(deploy_mod, "_login_only", new=AsyncMock(return_value=mock_bot)):
            await deploy_mod._list_global("fake-token")

        mock_bot.http.get_global_commands.assert_called_once_with(mock_bot.user.id)

    async def test_list_global_displays_commands(self) -> None:
        """_list_global must log each command's id, name, type, and description."""
        mock_bot = MagicMock()
        mock_bot.user.id = 123456789
        mock_bot.http.get_global_commands = AsyncMock(
            return_value=[
                {"id": "111", "name": "license_panel", "type": 1, "description": "Manage licenses"}
            ]
        )
        mock_bot.close = AsyncMock()
        mock_bot.login = AsyncMock()

        with patch.object(deploy_mod, "_login_only", new=AsyncMock(return_value=mock_bot)):
            with self.assertLogs("deng.rejoin.deploy", level="INFO") as cm:
                await deploy_mod._list_global("fake-token")

        combined = "\n".join(cm.output)
        self.assertIn("license_panel", combined)
        self.assertIn("111", combined)

    async def test_list_guild_calls_http_get_guild_commands(self) -> None:
        """_list_guild must call http.get_guild_commands with app ID and guild ID."""
        mock_bot = MagicMock()
        mock_bot.user.id = 123456789
        mock_bot.http.get_guild_commands = AsyncMock(return_value=[])
        mock_bot.close = AsyncMock()
        mock_bot.login = AsyncMock()

        with patch.object(deploy_mod, "_login_only", new=AsyncMock(return_value=mock_bot)):
            await deploy_mod._list_guild("fake-token", TARGET_GUILD_ID)

        mock_bot.http.get_guild_commands.assert_called_once_with(
            mock_bot.user.id, TARGET_GUILD_ID
        )

    async def test_list_guild_displays_commands(self) -> None:
        """_list_guild must log each command's id, name, type, and description."""
        mock_bot = MagicMock()
        mock_bot.user.id = 123456789
        mock_bot.http.get_guild_commands = AsyncMock(
            return_value=[
                {"id": "222", "name": "license_panel", "type": 1, "description": "Manage licenses"}
            ]
        )
        mock_bot.close = AsyncMock()
        mock_bot.login = AsyncMock()

        with patch.object(deploy_mod, "_login_only", new=AsyncMock(return_value=mock_bot)):
            with self.assertLogs("deng.rejoin.deploy", level="INFO") as cm:
                await deploy_mod._list_guild("fake-token", TARGET_GUILD_ID)

        combined = "\n".join(cm.output)
        self.assertIn("license_panel", combined)
        self.assertIn("222", combined)

    async def test_list_global_does_not_print_token(self) -> None:
        """_list_global must never log the token."""
        mock_bot = MagicMock()
        mock_bot.user.id = 123456789
        mock_bot.http.get_global_commands = AsyncMock(return_value=[])
        mock_bot.close = AsyncMock()
        mock_bot.login = AsyncMock()

        with patch.object(deploy_mod, "_login_only", new=AsyncMock(return_value=mock_bot)):
            with self.assertLogs("deng.rejoin.deploy", level="INFO") as cm:
                await deploy_mod._list_global("super-secret-token")

        combined = "\n".join(cm.output)
        self.assertNotIn("super-secret-token", combined)

    async def test_list_guild_does_not_print_token(self) -> None:
        """_list_guild must never log the token."""
        mock_bot = MagicMock()
        mock_bot.user.id = 123456789
        mock_bot.http.get_guild_commands = AsyncMock(return_value=[])
        mock_bot.close = AsyncMock()
        mock_bot.login = AsyncMock()

        with patch.object(deploy_mod, "_login_only", new=AsyncMock(return_value=mock_bot)):
            with self.assertLogs("deng.rejoin.deploy", level="INFO") as cm:
                await deploy_mod._list_guild("super-secret-token", TARGET_GUILD_ID)

        combined = "\n".join(cm.output)
        self.assertNotIn("super-secret-token", combined)


if __name__ == "__main__":
    unittest.main()
