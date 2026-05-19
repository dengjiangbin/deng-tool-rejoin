# BROKEN LEGACY CODE — DO NOT USE IN LIVE START PATH.
#
# Archived after live probes p-f1a4aaafe5 / p-8b025e8c3c because this smart
# Android/UI detection caused SIGSEGV, false Joining/Join Unconfirmed states,
# private URL relaunch failures, and endless restart loops.
#
# This code is kept only for historical reference.
# Live Start must not import or call this module.
# Future real state detection must use Roblox Presence API, not
# uiautomator/logcat/UI dump.
#
# Specific issues:
#   - _probe_uiautomator: triggered SIGSEGV on Termux via Android Accessibility Service
#   - _probe_logcat: unreliable for App Cloner packages (truncated process names)
#   - _probe_dumpsys_activity: produced false Lobby detection, caused Joining loop
#   - detect_experience_state: used by old supervisor for post-launch state classification,
#     triggering the "stuck in Joining" loop and URL resend storms
#
"""Best-effort Android experience-state detector for Roblox. (ARCHIVED — BROKEN)

Classifies what Roblox is currently doing by combining evidence from:
  1. dumpsys activity (top/resumed activity name and task stack)
  2. logcat (bounded recent entries for this PID)
  3. uiautomator dump (optional XML UI hierarchy, graceful failure)

Design goals:
  * Never raises — all paths silently degrade to weak evidence.
  * Never reads secrets (cookies, tokens, sessions, account files).
  * Bounded execution — each probe has an explicit short timeout.
  * All side-effects via android.run_command which is already testable.
  * Runs only when transitioning FROM Launching/Joining states, never on
    steady-state healthy polls (no performance cost in normal operation).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

# Archived: import removed to prevent accidental live use
# from . import android
# from .config import validate_package_name


class EvidenceLevel(IntEnum):
    """Strength of evidence about the current Roblox runtime state."""

    PROCESS_ONLY             = 1  # Process exists; foreground/activity unknown
    FOREGROUND_APP           = 2  # Package confirmed as foreground; activity unclear
    ROBLOX_HOME_OR_LOBBY     = 3  # Signal indicates home screen / lobby / menu
    JOINING_PRIVATE_URL      = 4  # URL intent dispatched; waiting for join
    EXPERIENCE_LIKELY_LOADED = 5  # Strong signal: game / experience screen active
    JOIN_FAILED_OR_HOME      = 6  # After URL: evidence shows home/lobby; join likely failed


@dataclass(frozen=True)
class ExperienceEvidence:
    """Evidence record returned by ``detect_experience_state``."""

    level: EvidenceLevel
    detail: str
    source: str           # "logcat", "dumpsys_activity", "uiautomator", "foreground", "none"
    raw_snippet: str = "" # Brief safe excerpt for debug logging (no secrets)

    def is_in_game(self) -> bool:
        return self.level == EvidenceLevel.EXPERIENCE_LIKELY_LOADED

    def is_home_or_lobby(self) -> bool:
        return self.level in (
            EvidenceLevel.ROBLOX_HOME_OR_LOBBY,
            EvidenceLevel.JOIN_FAILED_OR_HOME,
        )


# ─── Logcat patterns ──────────────────────────────────────────────────────────
# ARCHIVED: These patterns were used by _probe_logcat (BROKEN).

_LOGCAT_INGAME_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bGameLoaded\b", re.I),
    re.compile(r"\bJoinedGame\b|\bJoinedPlace\b", re.I),
    re.compile(r"\bPlaceLauncher\b.*\b(join|joined|connected)\b", re.I),
    re.compile(r"\bGameJoin(Received|Success)\b", re.I),
    re.compile(r"\b(DataModel|LuaGameScript|GameClient)\b.*\b(ready|loaded|start)\b", re.I),
    re.compile(r"\bReplicationInfo\b", re.I),
    re.compile(r"\bClientDataModel\b.*\b(creat|ready|loaded)\b", re.I),
    re.compile(r"\bRoblox.*Experience.*load", re.I),
]

_LOGCAT_HOME_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(HomeScene|HomeMenu|HomeScreen|LuaHome)\b", re.I),
    re.compile(r"\bRomarkApp\b.*\b(home|ready|launch)\b", re.I),
    re.compile(r"\b(LaunchingToHome|ReturnToHome|BackToHome)\b", re.I),
    re.compile(r"\b(BootstrapScene|LandingScene|UniverseScene)\b", re.I),
    re.compile(r"\bAppBootstrap\b.*\b(home|ready)\b", re.I),
]

# ─── Activity class name fragments ────────────────────────────────────────────
# ARCHIVED: Used by _probe_dumpsys_activity (BROKEN — caused false Lobby detections).

_ACTIVITY_INGAME_FRAGMENTS = (
    "gameactivity", "robloxgame", "gameclient",
    "experienceactivity", "placejoin",
)

_ACTIVITY_HOME_FRAGMENTS = (
    "splashactivity", "mainactivity", "launchactivity",
    "launcheractivity", "homeactivity", "landingactivity",
    "bootstrapactivity",
)

# ─── UIAutomator patterns ─────────────────────────────────────────────────────
# ARCHIVED: Used by _probe_uiautomator (BROKEN — SIGSEGV on Termux/App Cloner).

_UIA_HOME_TEXT = re.compile(
    r'text="(Home|Discover|Play|Friends|Avatar|Robux|Shop|Activity Feed|Profile)"',
    re.I,
)
_UIA_INGAME_TEXT = re.compile(
    r'text="(Leave Game|Back to Home|Respawn|Report Abuse|Settings|Reset Character)"',
    re.I,
)


# ─── BROKEN probes (archived — never call from live Start) ───────────────────


def _probe_logcat_broken(package: str, pid: Optional[str] = None) -> Optional[ExperienceEvidence]:
    """BROKEN: logcat probe. Archived for historical reference.

    Issues:
    - App Cloner packages have truncated process names; pidof returns wrong PIDs.
    - Logcat output is device-dependent; patterns miss many Roblox client versions.
    - Called from live Start in old versions, causing false Joining loop.
    """
    raise RuntimeError(
        "BROKEN LEGACY CODE: _probe_logcat_broken must not be called from live Start."
    )


def _probe_dumpsys_activity_broken(package: str) -> Optional[ExperienceEvidence]:
    """BROKEN: dumpsys activity probe. Archived for historical reference.

    Issues:
    - Activity class names vary across Roblox versions and clone apps.
    - Produced false ROBLOX_HOME_OR_LOBBY evidence after URL launch.
    - When combined with Join Unconfirmed logic, caused 120s URL resend loop.
    """
    raise RuntimeError(
        "BROKEN LEGACY CODE: _probe_dumpsys_activity_broken must not be called from live Start."
    )


def _probe_uiautomator_broken(package: str) -> Optional[ExperienceEvidence]:
    """BROKEN: uiautomator probe. Archived for historical reference.

    Issues:
    - `uiautomator dump` triggers Android Accessibility Service callback.
    - On Termux with App Cloner packages, this causes SIGSEGV in Python.
    - SIGSEGV is a hard C-level segfault; faulthandler cannot intercept it cleanly.
    - Removed in probe p-f1a4aaafe5.
    """
    raise RuntimeError(
        "BROKEN LEGACY CODE: _probe_uiautomator_broken must not be called from live Start."
    )


def detect_experience_state_broken(
    package: str,
    *,
    url_launched: bool = False,
) -> ExperienceEvidence:
    """BROKEN: Old main API. Archived for historical reference.

    The old supervisor called this after every URL launch to check if the
    account was "in-game" or "at lobby." When detection was inconclusive
    (which it always was for App Cloner packages), the package would be stuck
    in Join Unconfirmed state and the URL would be resent every 120 seconds.

    Replaced by: Roblox Presence API + process-alive check.
    """
    raise RuntimeError(
        "BROKEN LEGACY CODE: detect_experience_state_broken must not be called from live Start."
    )
