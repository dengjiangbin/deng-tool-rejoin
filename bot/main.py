#!/usr/bin/env python3
"""DENG Tool: Rejoin — Discord License Panel Bot.

Run from the project root:
    python -m bot.main

Required environment variables (set in .env or shell):
    DISCORD_BOT_TOKEN           Bot token from Discord Developer Portal.
    LICENSE_OWNER_DISCORD_IDS   Comma-separated Discord user IDs for panel admins.

Optional:
    DENG_REJOIN_HOME            Override default data directory (default: ~/.deng-tool/rejoin).
    DENG_DEV                    Set to any non-empty value to skip license verification.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# ── dotenv ────────────────────────────────────────────────────────────────────
# Load .env from project root if present — do this BEFORE any agent imports so
# that DENG_REJOIN_HOME is in the environment when constants are evaluated.
try:
    from dotenv import load_dotenv as _load_dotenv

    _env_path = Path(__file__).resolve().parents[1] / ".env"
    if _env_path.exists():
        _load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv optional; rely on shell environment

import discord
from discord.ext import commands

from agent.license_store import get_default_store
from bot.cog_license_panel import LicensePanelCog

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("deng.rejoin.bot")

# Suppress discord.py's own verbose logger slightly
logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("discord.gateway").setLevel(logging.INFO)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        log.critical("Required environment variable %s is not set — exiting.", name)
        sys.exit(1)
    return value


# ── Bot setup ─────────────────────────────────────────────────────────────────

async def _build_and_run(token: str) -> None:
    intents = discord.Intents.default()
    intents.guilds = True
    intents.guild_messages = False  # no message content needed

    bot = commands.Bot(
        command_prefix="!",   # unused but required by commands.Bot
        intents=intents,
        help_command=None,
    )

    store = get_default_store()
    cog = LicensePanelCog(bot, store)
    await bot.add_cog(cog)

    # Start optional license API server in background thread
    from bot.license_api import maybe_start_api_thread
    maybe_start_api_thread()

    @bot.event
    async def on_ready() -> None:
        log.info("Logged in as %s (id=%s)", bot.user, bot.user.id)

        # Restore persistent views so buttons work after restart
        await cog.restore_persistent_views()

        # NOTE: This bot is GUILD-ONLY. Global command sync is intentionally
        # disabled here. Commands are registered once via:
        #   python -m bot.deploy_commands --guild 1435142398647734396
        log.info(
            "Command sync skipped on startup — "
            "managed by deploy_commands.py (guild-only policy)."
        )

    @bot.event
    async def on_error(event: str, *args: object, **kwargs: object) -> None:
        log.exception("Unhandled error in event %s", event)

    log.info("Starting DENG Tool: Rejoin license panel bot…")
    # log_handler=None → use our structured setup above; discord.py won't add its own
    await bot.start(token, reconnect=True)


def main() -> None:
    # Validate required env before starting the event loop
    token = _require_env("DISCORD_BOT_TOKEN")

    owner_ids_raw = os.environ.get("LICENSE_OWNER_DISCORD_IDS", "").strip()
    if not owner_ids_raw:
        log.warning(
            "LICENSE_OWNER_DISCORD_IDS is not set — no one will be able to run "
            "owner-only /license_panel commands."
        )

    asyncio.run(_build_and_run(token))


if __name__ == "__main__":
    main()
