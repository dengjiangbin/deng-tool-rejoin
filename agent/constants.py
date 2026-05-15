"""Shared constants for DENG Tool: Rejoin."""

from __future__ import annotations

import os
from pathlib import Path

PRODUCT_NAME = "DENG Tool: Rejoin"
PRODUCT_FAMILY = "DENG Tool"
PRODUCT_MODULE = "Rejoin"
ABBREVIATION_MEANING = "Device Engine for Networked Game Rejoin"
VERSION = "1.0.0"

GITHUB_OWNER = "dengjiangbin"
GITHUB_REPO = "deng-tool-rejoin"
GITHUB_BRANCH = "main"
GITHUB_REMOTE = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}.git"
RAW_INSTALL_URL = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/install.sh"

DEFAULT_ROBLOX_PACKAGE = "com.roblox.client"
DEFAULT_ROBLOX_PACKAGE_HINTS = ("roblox", "rblx", "blox", "moons")
DEFAULT_DEVICE_NAME = "termux-android"

APP_HOME = Path(os.environ.get("DENG_REJOIN_HOME", Path.home() / ".deng-tool" / "rejoin")).expanduser()
CONFIG_PATH = APP_HOME / "config.json"
DATA_DIR = APP_HOME / "data"
DB_PATH = DATA_DIR / "rejoin.sqlite3"
LOG_DIR = APP_HOME / "logs"
LOG_PATH = LOG_DIR / "agent.log"
RUN_DIR = APP_HOME / "run"
LOCK_PATH = RUN_DIR / "agent.lock"
PID_PATH = RUN_DIR / "agent.pid"
LAUNCHER_DIR = APP_HOME / "launcher"
CACHE_DIR = APP_HOME / "cache"
SNAPSHOT_DIR = CACHE_DIR / "snapshots"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = PROJECT_ROOT / "agent"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DIST_DIR = PROJECT_ROOT / "dist"
RELEASES_DIR = DIST_DIR / "releases"

# Release channels
CHANNEL_STABLE = "stable"
CHANNEL_BETA = "beta"
CHANNEL_DEV = "dev"
VALID_CHANNELS: frozenset[str] = frozenset({CHANNEL_STABLE, CHANNEL_BETA, CHANNEL_DEV})

LAUNCH_MODES = {"app", "deeplink", "web_url"}

MIN_RECONNECT_DELAY_SECONDS = 5
MIN_HEALTH_CHECK_INTERVAL_SECONDS = 10
MIN_FOREGROUND_GRACE_SECONDS = 10
MIN_BACKOFF_SECONDS = 10
MAX_BACKOFF_SECONDS = 3600
DEFAULT_RECONNECT_DELAY_SECONDS = 8
DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS = 30
DEFAULT_FOREGROUND_GRACE_SECONDS = 30
DEFAULT_MAX_FAST_FAILURES = 3
DEFAULT_BACKOFF_MIN_SECONDS = 10
DEFAULT_BACKOFF_MAX_SECONDS = 300

PROCESS_TIMEOUT_SECONDS = 8
ROOT_TIMEOUT_SECONDS = 6
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3

PACKAGE_NAME_REGEX = r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$"

TERMUX_BOOT_SCRIPT = Path.home() / ".termux" / "boot" / "deng-tool-rejoin.sh"

LICENSE_KEY_PREFIX = "DENG"
# Canonical display: DENG-XXXX-XXXX-XXXX-XXXX (16 uppercase hex, dashed groups).
LICENSE_KEY_PATTERN = r"^DENG-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$"

# Public beta license + download API (HTTPS only for production clients).
DEFAULT_LICENSE_SERVER_URL = "https://rejoin.deng.my.id"
YESCAPTCHA_API_BASE = "https://api.yescaptcha.com"

SENSITIVE_URL_PARAM_NAMES = {
    "accesscode",
    "auth",
    "authentication",
    "code",
    "cookie",
    "invite",
    "invitation",
    "linkcode",
    "privateserverlinkcode",
    "rcc",
    "session",
    "sharecode",
    "sharelinkid",
    "ticket",
    "token",
}

APP_DIRS = (APP_HOME, DATA_DIR, LOG_DIR, RUN_DIR, LAUNCHER_DIR, CACHE_DIR, SNAPSHOT_DIR)
