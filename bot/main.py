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
import datetime
import json
import logging
import os
import sys
import time
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

        # Restore persistent views so buttons work after restart. This is a
        # background, self-retrying task (restoration needs a sometimes-slow
        # Supabase read) so it neither blocks on_ready nor the gateway heartbeat,
        # and it self-heals the moment the store responds.
        cog.schedule_persistent_view_restore()

        # NOTE: This bot is GUILD-ONLY. Global command sync is intentionally
        # disabled here. Commands are registered once via:
        #   python -m bot.deploy_commands --guild 1435142398647734396
        log.info(
            "Command sync skipped on startup — "
            "managed by deploy_commands.py (guild-only policy)."
        )

        # Write initial health snapshot and start heartbeat loop.
        #
        # on_ready fires on EVERY gateway (re)connect, not just the first. The
        # old code spawned a brand-new _health_loop task on every reconnect, so
        # a single reconnect storm piled up dozens of concurrent loops each
        # hammering the DB probe — which starved the loop further. Start it
        # exactly once and reuse the task across reconnects.
        cmds_loaded = len(bot.tree.get_commands()) if hasattr(bot, 'tree') else 0
        _write_health(bot, discord_ready=True, db_ok=True, commands_loaded=cmds_loaded)
        existing = getattr(bot, "_deng_health_task", None)
        if existing is None or existing.done():
            bot._deng_health_task = asyncio.create_task(_health_loop(bot, store))

    @bot.event
    async def on_error(event: str, *args: object, **kwargs: object) -> None:
        log.exception("Unhandled error in event %s", event)

    log.info("Starting DENG Tool: Rejoin license panel bot…")
    # log_handler=None → use our structured setup above; discord.py won't add its own
    await bot.start(token, reconnect=True)


_HEALTH_PATH = Path(__file__).resolve().parents[1] / "data" / "health.json"
_PID_LOCK_PATH = Path(__file__).resolve().parents[1] / "data" / "bot.pid"
_BOT_START_TIME = time.monotonic()


def _acquire_single_instance() -> None:
    """Ensure only one bot instance runs by killing the previous PID from the lock file.

    Called once at startup.  On Windows, PM2 sometimes starts the new process
    before the old one has fully exited, leaving duplicate instances that share
    the same Discord token and trigger mutual Gateway disconnects.  Reading the
    PID lock lets the new instance cleanly evict only the previous instance
    without accidentally killing unrelated processes.
    """
    import subprocess

    own_pid = os.getpid()
    lock = _PID_LOCK_PATH
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        if lock.exists():
            old_pid_str = lock.read_text().strip()
            if old_pid_str.isdigit():
                old_pid = int(old_pid_str)
                if old_pid and old_pid != own_pid:
                    log.info("Evicting previous instance (pid=%d).", old_pid)
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", str(old_pid)],
                            capture_output=True, timeout=5,
                        )
                        time.sleep(0.5)  # allow socket TIME_WAIT to settle
                    except Exception as exc:  # noqa: BLE001
                        log.debug("Could not evict pid=%d: %s", old_pid, exc)
        lock.write_text(str(own_pid))
    except Exception as exc:  # noqa: BLE001
        log.debug("PID lock error: %s", exc)


def _write_health(bot: commands.Bot | None, *, discord_ready: bool, db_ok: bool, commands_loaded: int) -> None:
    """Write a health.json compatible with ecosystemPingStatus.js."""
    try:
        _HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": "ok" if discord_ready and db_ok else "warning",
            "lastHeartbeatAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pid": os.getpid(),
            "discordReady": discord_ready,
            "databaseOk": db_ok,
            "commandsLoaded": commands_loaded,
            "uptimeSeconds": int(time.monotonic() - _BOT_START_TIME),
        }
        tmp = _HEALTH_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(_HEALTH_PATH)
    except Exception as exc:  # noqa: BLE001
        log.debug("health.json write failed: %s", exc)


async def _health_loop(bot: commands.Bot, store: object) -> None:
    """Background task: refresh health.json every 30 seconds."""
    while True:
        try:
            db_ok = True
            try:
                # Lightweight DB check — just probe the users table.
                #
                # CRITICAL: the supabase/postgrest client is SYNCHRONOUS and
                # blocks the calling thread until the HTTP round-trip finishes.
                # Calling it directly on the event loop froze the entire bot
                # (and the Discord gateway heartbeat) whenever Supabase was slow
                # or unreachable — the gateway then dropped the connection, which
                # made the rejoin panel go dead. Run the blocking probe in a
                # worker thread with a hard timeout so the event loop is never
                # blocked; a failed/slow probe only flips db_ok, it never stalls
                # the heartbeat.
                if hasattr(store, '_client'):
                    def _probe_db() -> None:
                        store._client.table("license_users").select("id").limit(1).execute()
                    await asyncio.wait_for(asyncio.to_thread(_probe_db), timeout=10)
                elif hasattr(store, '_load'):
                    await asyncio.wait_for(asyncio.to_thread(store._load), timeout=10)
            except Exception:  # noqa: BLE001
                db_ok = False
            _write_health(
                bot,
                discord_ready=not bot.is_closed() and bot.is_ready(),
                db_ok=db_ok,
                commands_loaded=len(bot.tree.get_commands()) if hasattr(bot, 'tree') else 0,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("health loop error: %s", exc)
        await asyncio.sleep(30)


def main() -> None:
    # Validate required env before starting the event loop
    token = _require_env("DISCORD_BOT_TOKEN")

    owner_ids_raw = os.environ.get("LICENSE_OWNER_DISCORD_IDS", "").strip()
    if not owner_ids_raw:
        log.warning(
            "LICENSE_OWNER_DISCORD_IDS is not set — no one will be able to run "
            "owner-only /license_panel commands."
        )

    # Write our PID to the lock file so the ecosystem knows which instance is
    # current.  We do NOT evict a running instance here because on Windows,
    # PM2's restart sequence (old kill → new start) is not atomic.  If the new
    # process kills the old one, PM2 sees the kill as an unexpected exit and
    # starts yet another instance, creating a restart loop.  Instead, rely on
    # PM2's kill_timeout to ensure only one instance is active at a time.
    try:
        _PID_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PID_LOCK_PATH.write_text(str(os.getpid()))
    except Exception as exc:  # noqa: BLE001
        log.debug("PID lock write failed: %s", exc)

    # On Windows, PM2 uses TerminateProcess() which doesn't trigger Python
    # signal handlers.  As a best-effort, also register SIGTERM/SIGINT so that
    # graceful restarts triggered by other means exit quickly.
    import signal

    def _sigterm_handler(signum: int, frame: object) -> None:  # noqa: ARG001
        log.info("Received signal %d — exiting immediately.", signum)
        os._exit(0)

    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_sig, _sigterm_handler)
        except (OSError, ValueError):
            pass

    # Windows: PM2 may send CTRL_BREAK_EVENT to console processes.
    if hasattr(signal, "CTRL_BREAK_EVENT"):
        try:
            signal.signal(signal.CTRL_BREAK_EVENT, _sigterm_handler)
        except (OSError, ValueError):
            pass

    try:
        asyncio.run(_build_and_run(token))
        log.warning("asyncio.run returned normally — bot.start() exited. PM2 will restart.")
    except Exception as exc:
        log.exception("Unhandled exception in main event loop: %s", exc)
        raise


if __name__ == "__main__":
    main()
