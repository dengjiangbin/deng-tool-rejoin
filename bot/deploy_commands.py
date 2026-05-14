#!/usr/bin/env python3
"""Deploy (register) slash commands to Discord.

This bot is GUILD-ONLY. Global command deployment is intentionally disabled.

Usage
-----
    # (Required) Deploy guild commands instantly:
    python -m bot.deploy_commands --guild 1435142398647734396

    # Remove all global commands (one-time cleanup for accidental global syncs):
    python -m bot.deploy_commands --clear-global

    # Clear all guild commands without re-registering:
    python -m bot.deploy_commands --clear-guild 1435142398647734396

    # Diagnostics — list registered commands:
    python -m bot.deploy_commands --list-global
    python -m bot.deploy_commands --list-guild 1435142398647734396

Running without any flag is an error. Global deployment is not supported.

Environment variables
---------------------
    DISCORD_BOT_TOKEN   Required.

A .env file in the project root is loaded automatically if present.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Load .env before anything else
try:
    from dotenv import load_dotenv as _load_dotenv

    _env_path = Path(__file__).resolve().parents[1] / ".env"
    if _env_path.exists():
        _load_dotenv(_env_path)
except ImportError:
    pass

import discord
from discord.ext import commands

from agent.license_store import get_default_store
from bot.cog_license_panel import LicensePanelCog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("deng.rejoin.deploy")

# ── Target guild constant ─────────────────────────────────────────────────────
# This is the only guild this bot may deploy commands to.
TARGET_GUILD_ID = 1435142398647734396


async def _login_only(token: str) -> commands.Bot:
    """Log in over HTTP only (REST-based sync; no gateway connection needed)."""
    intents = discord.Intents.default()
    intents.guilds = True
    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
    store = get_default_store()
    cog = LicensePanelCog(bot, store)
    await bot.add_cog(cog)
    await bot.login(token)
    return bot


async def _deploy_guild(token: str, guild_id: int) -> None:
    """Register all slash commands to the specified guild (instant propagation)."""
    bot = await _login_only(token)
    try:
        target = discord.Object(id=guild_id)
        # Copy the global command tree into the guild scope, then sync guild-only.
        bot.tree.copy_global_to(guild=target)
        synced = await bot.tree.sync(guild=target)
        log.info("Synced %d command(s) to guild %s.", len(synced), guild_id)
        for cmd in synced:
            log.info("  /%s — %s", cmd.name, cmd.description)
    finally:
        await bot.close()


async def _clear_global(token: str) -> None:
    """Remove all globally registered slash commands (one-time cleanup)."""
    bot = await _login_only(token)
    try:
        bot.tree.clear_commands(guild=None)
        synced = await bot.tree.sync()
        log.info(
            "Cleared global commands. %d command(s) now registered globally (expected: 0).",
            len(synced),
        )
    finally:
        await bot.close()


async def _clear_guild(token: str, guild_id: int) -> None:
    """Remove all slash commands from the specified guild."""
    bot = await _login_only(token)
    try:
        target = discord.Object(id=guild_id)
        bot.tree.clear_commands(guild=target)
        synced = await bot.tree.sync(guild=target)
        log.info(
            "Cleared guild %s commands. %d command(s) remaining (expected: 0).",
            guild_id,
            len(synced),
        )
    finally:
        await bot.close()


async def _list_global(token: str) -> None:
    """List all globally registered slash commands (diagnostic)."""
    bot = await _login_only(token)
    try:
        app_id = bot.user.id
        log.info("Application ID: %s", app_id)
        cmds = await bot.http.get_global_commands(app_id)
        log.info("Global commands (%d):", len(cmds))
        if cmds:
            for cmd in cmds:
                log.info(
                    "  id=%-20s  name=%-20s  type=%s  desc=%s",
                    cmd.get("id", "?"),
                    cmd.get("name", "?"),
                    cmd.get("type", "?"),
                    cmd.get("description", "")[:80],
                )
        else:
            log.info("  (none — global command list is empty, as expected)")
    finally:
        await bot.close()


async def _list_guild(token: str, guild_id: int) -> None:
    """List all guild-specific slash commands (diagnostic)."""
    bot = await _login_only(token)
    try:
        app_id = bot.user.id
        log.info("Application ID: %s", app_id)
        cmds = await bot.http.get_guild_commands(app_id, guild_id)
        log.info("Guild %s commands (%d):", guild_id, len(cmds))
        if cmds:
            for cmd in cmds:
                log.info(
                    "  id=%-20s  name=%-20s  type=%s  desc=%s",
                    cmd.get("id", "?"),
                    cmd.get("name", "?"),
                    cmd.get("type", "?"),
                    cmd.get("description", "")[:80],
                )
        else:
            log.info("  (none — guild command list is empty)")
    finally:
        await bot.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Deploy DENG Tool: Rejoin slash commands. "
            "This bot is GUILD-ONLY; global deployment is not supported."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            f"  python -m bot.deploy_commands --guild {TARGET_GUILD_ID}\n"
            "  python -m bot.deploy_commands --clear-global\n"
            f"  python -m bot.deploy_commands --clear-guild {TARGET_GUILD_ID}\n"
            "  python -m bot.deploy_commands --list-global\n"
            f"  python -m bot.deploy_commands --list-guild {TARGET_GUILD_ID}"
        ),
    )
    parser.add_argument(
        "--guild",
        type=int,
        default=None,
        metavar="GUILD_ID",
        help=f"Deploy to this guild (must be {TARGET_GUILD_ID}).",
    )
    parser.add_argument(
        "--clear-global",
        action="store_true",
        help="Remove all globally registered commands (cleanup only).",
    )
    parser.add_argument(
        "--clear-guild",
        type=int,
        default=None,
        metavar="GUILD_ID",
        help=f"Clear all commands from this guild (must be {TARGET_GUILD_ID}).",
    )
    parser.add_argument(
        "--list-global",
        action="store_true",
        help="List all globally registered commands (diagnostic).",
    )
    parser.add_argument(
        "--list-guild",
        type=int,
        default=None,
        metavar="GUILD_ID",
        help=f"List all commands for this guild (must be {TARGET_GUILD_ID}).",
    )
    args = parser.parse_args()

    # ── Validate: exactly one operation must be specified ────────────────────
    ops = [
        args.guild is not None,
        args.clear_global,
        args.clear_guild is not None,
        args.list_global,
        args.list_guild is not None,
    ]
    if not any(ops):
        parser.error(
            "This bot is guild-only. Specify an operation:\n"
            f"  --guild {TARGET_GUILD_ID}            (deploy commands)\n"
            "  --clear-global                       (remove accidental global commands)\n"
            f"  --clear-guild {TARGET_GUILD_ID}      (clear guild commands)\n"
            "  --list-global                        (list global commands — diagnostic)\n"
            f"  --list-guild {TARGET_GUILD_ID}       (list guild commands — diagnostic)"
        )

    if sum(ops) > 1:
        parser.error(
            "Specify only one of --guild, --clear-global, --clear-guild, "
            "--list-global, or --list-guild."
        )

    # ── Validate guild IDs against the allowed target ────────────────────────
    if args.guild is not None and args.guild != TARGET_GUILD_ID:
        parser.error(
            f"Global deployment is not allowed. "
            f"Use --guild {TARGET_GUILD_ID}."
        )
    if args.clear_guild is not None and args.clear_guild != TARGET_GUILD_ID:
        parser.error(
            f"Unknown guild {args.clear_guild}. "
            f"Use --clear-guild {TARGET_GUILD_ID}."
        )
    if args.list_guild is not None and args.list_guild != TARGET_GUILD_ID:
        parser.error(
            f"Unknown guild {args.list_guild}. "
            f"Use --list-guild {TARGET_GUILD_ID}."
        )

    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        log.critical("DISCORD_BOT_TOKEN is not set — exiting.")
        sys.exit(1)

    if args.guild is not None:
        asyncio.run(_deploy_guild(token, args.guild))
    elif args.clear_global:
        asyncio.run(_clear_global(token))
    elif args.clear_guild is not None:
        asyncio.run(_clear_guild(token, args.clear_guild))
    elif args.list_global:
        asyncio.run(_list_global(token))
    elif args.list_guild is not None:
        asyncio.run(_list_guild(token, args.list_guild))


if __name__ == "__main__":
    main()
