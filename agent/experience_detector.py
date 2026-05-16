"""Best-effort Android experience-state detector for Roblox.

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

from . import android
from .config import validate_package_name

# ─── Evidence Levels ──────────────────────────────────────────────────────────
#
# Ordered from weakest to strongest.  The detector picks the highest level that
# any available signal can support.


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
        """True when evidence strongly suggests an experience/game is loaded."""
        return self.level == EvidenceLevel.EXPERIENCE_LIKELY_LOADED

    def is_home_or_lobby(self) -> bool:
        """True when evidence indicates the app is at home/lobby, not in-game."""
        return self.level in (
            EvidenceLevel.ROBLOX_HOME_OR_LOBBY,
            EvidenceLevel.JOIN_FAILED_OR_HOME,
        )


# ─── Logcat patterns ──────────────────────────────────────────────────────────
#
# Conservative patterns; only fire when clearly distinguishable from other noise.

# Signals that indicate Roblox has loaded an experience/game.
# Sorted by specificity — more specific first.
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

# Signals that indicate Roblox is at home screen / lobby / menu.
_LOGCAT_HOME_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(HomeScene|HomeMenu|HomeScreen|LuaHome)\b", re.I),
    re.compile(r"\bRomarkApp\b.*\b(home|ready|launch)\b", re.I),
    re.compile(r"\b(LaunchingToHome|ReturnToHome|BackToHome)\b", re.I),
    re.compile(r"\b(BootstrapScene|LandingScene|UniverseScene)\b", re.I),
    re.compile(r"\bAppBootstrap\b.*\b(home|ready)\b", re.I),
]

# ─── Dumpsys / Activity patterns ──────────────────────────────────────────────

# Activity class name fragments → in-game
_ACTIVITY_INGAME_FRAGMENTS = (
    "gameactivity",
    "robloxgame",
    "gameclient",
    "experienceactivity",
    "placejoin",
)

# Activity class name fragments → home / lobby
_ACTIVITY_HOME_FRAGMENTS = (
    "splashactivity",
    "mainactivity",
    "launchactivity",
    "launcheractivity",
    "homeactivity",
    "landingactivity",
    "bootstrapactivity",
)

# ─── UIAutomator patterns ─────────────────────────────────────────────────────

# Text nodes in the home/lobby UI.  If multiple of these are visible at once,
# it strongly suggests the home screen (not an experience).
_UIA_HOME_TEXT = re.compile(
    r'text="(Home|Discover|Play|Friends|Avatar|Robux|Shop|Activity Feed|Profile)"',
    re.I,
)

# Text that indicates game/experience HUD is active.
_UIA_INGAME_TEXT = re.compile(
    r'text="(Leave Game|Back to Home|Respawn|Report Abuse|Settings|Reset Character)"',
    re.I,
)


# ─── Individual probes ────────────────────────────────────────────────────────


def _pid_for_package(package: str) -> Optional[str]:
    res = android.run_command(["pidof", package], timeout=4)
    if res.ok and res.stdout.strip():
        return res.stdout.strip().split()[0]
    return None


def _probe_logcat(package: str, pid: Optional[str] = None) -> Optional[ExperienceEvidence]:
    """Read a short bounded logcat window and look for game/home signals."""
    try:
        # Prefer PID-scoped logcat to avoid noise from other packages.
        if pid:
            res = android.run_command(["logcat", "-d", "-t", "80", "--pid", pid], timeout=5)
        else:
            res = android.run_command(["logcat", "-d", "-t", "100"], timeout=5)

        if not res.ok or not res.stdout.strip():
            return None

        blob = res.stdout[-6000:]  # cap to last ~6 KB

        # Check for in-game signals first (higher specificity).
        for rx in _LOGCAT_INGAME_PATTERNS:
            m = rx.search(blob)
            if m:
                snip = m.group(0)[:80]
                return ExperienceEvidence(
                    level=EvidenceLevel.EXPERIENCE_LIKELY_LOADED,
                    detail="logcat: in-game signal detected",
                    source="logcat",
                    raw_snippet=snip,
                )

        # Check for home/lobby signals.
        for rx in _LOGCAT_HOME_PATTERNS:
            m = rx.search(blob)
            if m:
                snip = m.group(0)[:80]
                return ExperienceEvidence(
                    level=EvidenceLevel.ROBLOX_HOME_OR_LOBBY,
                    detail="logcat: home/lobby signal detected",
                    source="logcat",
                    raw_snippet=snip,
                )

        return None  # logcat available but no specific signal
    except Exception:  # noqa: BLE001
        return None


def _probe_dumpsys_activity(package: str) -> Optional[ExperienceEvidence]:
    """Read dumpsys activity to determine the top resumed activity class."""
    try:
        # `dumpsys activity activities` gives task/activity stack.
        res = android.run_command(["dumpsys", "activity", "activities"], timeout=6)
        if not res.ok or not res.stdout.strip():
            return None

        # Find lines that reference the target package and contain an activity component.
        pkg_lines: list[str] = []
        for line in res.stdout.splitlines():
            if package in line:
                pkg_lines.append(line)
            if len(pkg_lines) > 60:
                break

        blob = "\n".join(pkg_lines)
        if not blob.strip():
            return None

        # Extract activity class name fragments from component strings like
        # "com.roblox.client/.SplashActivity" or "com.roblox.client/GameActivity".
        component_rx = re.compile(
            r"(?:" + re.escape(package) + r"/|component=" + re.escape(package) + r"/)"
            r"([A-Za-z0-9._$]+)",
        )
        found_activities: list[str] = [
            m.group(1).lower() for m in component_rx.finditer(blob)
        ]

        for act in found_activities:
            for fragment in _ACTIVITY_INGAME_FRAGMENTS:
                if fragment in act:
                    return ExperienceEvidence(
                        level=EvidenceLevel.EXPERIENCE_LIKELY_LOADED,
                        detail=f"dumpsys: in-game activity ({act})",
                        source="dumpsys_activity",
                        raw_snippet=act[:80],
                    )
            for fragment in _ACTIVITY_HOME_FRAGMENTS:
                if fragment in act:
                    return ExperienceEvidence(
                        level=EvidenceLevel.ROBLOX_HOME_OR_LOBBY,
                        detail=f"dumpsys: home/lobby activity ({act})",
                        source="dumpsys_activity",
                        raw_snippet=act[:80],
                    )

        # Also try `dumpsys activity top` for a faster single-activity snapshot.
        top_res = android.run_command(["dumpsys", "activity", "top"], timeout=5)
        if top_res.ok and package in top_res.stdout:
            top_lines = [ln for ln in top_res.stdout.splitlines() if package in ln][:20]
            top_blob = "\n".join(top_lines).lower()
            for fragment in _ACTIVITY_INGAME_FRAGMENTS:
                if fragment in top_blob:
                    return ExperienceEvidence(
                        level=EvidenceLevel.EXPERIENCE_LIKELY_LOADED,
                        detail=f"dumpsys_top: in-game activity fragment ({fragment})",
                        source="dumpsys_activity",
                        raw_snippet=fragment,
                    )
            for fragment in _ACTIVITY_HOME_FRAGMENTS:
                if fragment in top_blob:
                    return ExperienceEvidence(
                        level=EvidenceLevel.ROBLOX_HOME_OR_LOBBY,
                        detail=f"dumpsys_top: home activity fragment ({fragment})",
                        source="dumpsys_activity",
                        raw_snippet=fragment,
                    )

        return None  # dumpsys ran, package found, but no specific activity signals
    except Exception:  # noqa: BLE001
        return None


def _probe_uiautomator(package: str) -> Optional[ExperienceEvidence]:
    """Parse uiautomator dump to look for home vs in-game UI signals.

    Gracefully returns None if uiautomator is unavailable or fails.
    Reads /dev/stdout output only — never writes files.
    """
    try:
        # Try uiautomator dump to stdout.  Some devices support --compressed,
        # others do not — try both and fall back.
        for extra in (["--compressed"], []):
            res = android.run_command(
                ["uiautomator", "dump", "/dev/stdout"] + extra,
                timeout=8,
            )
            if res.ok and "<hierarchy" in res.stdout:
                break
        else:
            return None

        xml = res.stdout[:32000]  # cap to 32 KB to avoid huge parse

        # Only consider nodes that belong to the target package.
        if package not in xml:
            return None

        # Check for in-game HUD text (e.g. "Leave Game", "Reset Character").
        ingame_hits = len(_UIA_INGAME_TEXT.findall(xml))
        home_hits   = len(_UIA_HOME_TEXT.findall(xml))

        if ingame_hits >= 1:
            return ExperienceEvidence(
                level=EvidenceLevel.EXPERIENCE_LIKELY_LOADED,
                detail=f"uiautomator: {ingame_hits} in-game HUD element(s) found",
                source="uiautomator",
                raw_snippet=f"ingame_hits={ingame_hits}",
            )

        if home_hits >= 2:
            # Seeing at least 2 known lobby-nav items → likely home screen.
            return ExperienceEvidence(
                level=EvidenceLevel.ROBLOX_HOME_OR_LOBBY,
                detail=f"uiautomator: {home_hits} home navigation element(s) found",
                source="uiautomator",
                raw_snippet=f"home_hits={home_hits}",
            )

        return None  # uiautomator ran but no clear signal
    except Exception:  # noqa: BLE001
        return None


# ─── Main public API ──────────────────────────────────────────────────────────


def detect_experience_state(
    package: str,
    *,
    url_launched: bool = False,
) -> ExperienceEvidence:
    """Return the best available evidence about the current Roblox state.

    Probes dumpsys activity, logcat, and optionally uiautomator in order of
    reliability/speed.  Never raises.  Returns a weak ``FOREGROUND_APP``
    evidence record if no Android tools are available (e.g. on Windows during
    unit tests).

    Args:
        package: The Roblox Android package name.
        url_launched: True when a private URL intent was the last launch method.
                      Influences interpretation but not the raw detection.
    """
    try:
        package = validate_package_name(package)
    except Exception:  # noqa: BLE001
        return ExperienceEvidence(
            level=EvidenceLevel.PROCESS_ONLY,
            detail="invalid package name",
            source="none",
        )

    # ── 1. dumpsys activity (fastest, most reliable signal) ───────────────────
    dumpsys_ev = _probe_dumpsys_activity(package)
    if dumpsys_ev is not None and dumpsys_ev.level >= EvidenceLevel.ROBLOX_HOME_OR_LOBBY:
        # Upgrade JOIN_FAILED_OR_HOME when a URL was recently used and we see home evidence.
        if url_launched and dumpsys_ev.level == EvidenceLevel.ROBLOX_HOME_OR_LOBBY:
            return ExperienceEvidence(
                level=EvidenceLevel.JOIN_FAILED_OR_HOME,
                detail=dumpsys_ev.detail + " [after url launch]",
                source=dumpsys_ev.source,
                raw_snippet=dumpsys_ev.raw_snippet,
            )
        return dumpsys_ev

    # ── 2. Logcat (PID-scoped preferred) ──────────────────────────────────────
    pid = _pid_for_package(package)
    logcat_ev = _probe_logcat(package, pid=pid)
    if logcat_ev is not None and logcat_ev.level >= EvidenceLevel.ROBLOX_HOME_OR_LOBBY:
        if url_launched and logcat_ev.level == EvidenceLevel.ROBLOX_HOME_OR_LOBBY:
            return ExperienceEvidence(
                level=EvidenceLevel.JOIN_FAILED_OR_HOME,
                detail=logcat_ev.detail + " [after url launch]",
                source=logcat_ev.source,
                raw_snippet=logcat_ev.raw_snippet,
            )
        return logcat_ev

    # ── 3. UIAutomator (optional, slower — last resort) ───────────────────────
    uia_ev = _probe_uiautomator(package)
    if uia_ev is not None and uia_ev.level >= EvidenceLevel.ROBLOX_HOME_OR_LOBBY:
        if url_launched and uia_ev.level == EvidenceLevel.ROBLOX_HOME_OR_LOBBY:
            return ExperienceEvidence(
                level=EvidenceLevel.JOIN_FAILED_OR_HOME,
                detail=uia_ev.detail + " [after url launch]",
                source=uia_ev.source,
                raw_snippet=uia_ev.raw_snippet,
            )
        return uia_ev

    # Merge partial positive evidence from the three probes.
    best_partial: Optional[ExperienceEvidence] = None
    for ev in (dumpsys_ev, logcat_ev, uia_ev):
        if ev is not None:
            if best_partial is None or ev.level > best_partial.level:
                best_partial = ev
    if best_partial is not None:
        return best_partial

    # ── 4. Fallback: process running but nothing specific detected ────────────
    #
    # The health check already confirmed the package is running/foreground,
    # so we know at minimum FOREGROUND_APP.  We cannot claim in-game without
    # evidence, especially after a URL launch.
    return ExperienceEvidence(
        level=EvidenceLevel.FOREGROUND_APP,
        detail="app is running; no specific experience signal detected",
        source="none",
    )
