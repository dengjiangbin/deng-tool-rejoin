#!/usr/bin/env python3
"""Deploy (register) slash commands to Discord.

Usage
-----
    python bot/deploy_commands.py               # deploy globally (up to 1 hour to propagate)
    python bot/deploy_commands.py --guild GUILD_ID  # deploy to a single guild instantly

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


async def _deploy(token: str, guild_id: int | None) -> None:
    intents = discord.Intents.default()
    intents.guilds = True

    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
    store = get_default_store()
    cog = LicensePanelCog(bot, store)
    await bot.add_cog(cog)

    # Login over HTTP (sets bot.application_id).
    # Command syncing uses REST only — no gateway connection needed.
    await bot.login(token)
    try:
        if guild_id is not None:
            target = discord.Object(id=guild_id)
            bot.tree.copy_global_to(guild=target)
            synced = await bot.tree.sync(guild=target)
            log.info(
                "Synced %d command(s) to guild %s.", len(synced), guild_id
            )
        else:
            synced = await bot.tree.sync()
            log.info(
                "Synced %d command(s) globally (may take up to 1 hour to propagate).",
                len(synced),
            )

        for cmd in synced:
            log.info("  /%s — %s", cmd.name, cmd.description)
    finally:
        await bot.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy DENG Tool: Rejoin slash commands to Discord."
    )
    parser.add_argument(
        "--guild",
        type=int,
        default=None,
        metavar="GUILD_ID",
        help="Deploy to a specific guild ID (instant). Omit for global deployment.",
    )
    args = parser.parse_args()

    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        log.critical("DISCORD_BOT_TOKEN is not set — exiting.")
        sys.exit(1)

    asyncio.run(_deploy(token, args.guild))


if __name__ == "__main__":
    main()
