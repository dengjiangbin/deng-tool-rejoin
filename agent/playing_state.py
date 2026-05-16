"""Heartbeat-based playing-state detection for Roblox / App Cloner clones.

Why this exists
───────────────
The previous state machine flipped to "Offline" the instant ANY one evidence
source went silent — even though the user could *see* the clone playing.
This module fixes that by:

* Combining MULTIPLE evidence sources (process, window, surface, foreground,
  task, UI) per package.
* Tracking the LAST time each source was positive.
* Only declaring a package Offline when *no* source has been positive for a
  grace period (``offline_grace_s``, default 60s).
* Mapping the evidence + recent history to one of:
  ``Playing``, ``Online``, ``Lobby``, ``Background``, ``Join Unconfirmed``,
  ``Recovering``, ``Failed``, ``Offline``, ``Unknown``.

Public API
──────────
``StateTracker``                       — heartbeat tracker per package.
``StateTracker.observe(package, evidence)`` — feed fresh evidence in.
``StateTracker.decide(package, prev, evidence, *, url_launched, attempt_count)``
                                       — returns the public-facing state label.
``classify_ui_signal(uiautomator_dump)`` — classifies a UI dump as "lobby" /
                                       "in_game" / "unknown" (best-effort
                                       string scan, never raises).

The tracker is intentionally a plain in-memory store; supervisor instances
hold one tracker for the lifetime of a Start session.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping

# Public state labels.  These mirror the symbols already defined in
# agent.supervisor / agent.types so the supervisor can pass them straight
# into the table renderer without translation.
STATE_PLAYING = "Playing"
STATE_ONLINE = "Online"
STATE_IN_SERVER = "In Server"
STATE_LOBBY = "Lobby"
STATE_BACKGROUND = "Background"
STATE_JOIN_UNCONFIRMED = "Join Unconfirmed"
STATE_RECOVERING = "Recovering"
STATE_FAILED = "Failed"
STATE_OFFLINE = "Offline"
STATE_UNKNOWN = "Unknown"

# UI classification thresholds.
_LOBBY_TOKENS: tuple[str, ...] = (
    "Recommended For You", "Add Friends", "Friends", "Discover",
    "Home", "Profile", "More",
)
_IN_GAME_TOKENS: tuple[str, ...] = (
    "Leave Game", "Reset Character", "Roblox menu", "Settings", "Players",
    "Backpack", "Chat",
)


def classify_ui_signal(text: str | None) -> str:
    """Best-effort classifier for a uiautomator dump.

    Returns ``"in_game"``, ``"lobby"``, or ``"unknown"``.  Never raises.
    The matcher is intentionally simple: it counts how many tokens of each
    class appear and picks the higher; ties resolve to ``"unknown"``.
    """
    if not text:
        return "unknown"
    try:
        s = str(text)
    except Exception:  # noqa: BLE001
        return "unknown"
    lobby_hits = sum(1 for t in _LOBBY_TOKENS if t in s)
    game_hits = sum(1 for t in _IN_GAME_TOKENS if t in s)
    if game_hits > lobby_hits and game_hits >= 1:
        return "in_game"
    if lobby_hits > game_hits and lobby_hits >= 1:
        return "lobby"
    return "unknown"


@dataclass
class _Heartbeat:
    """Last-positive timestamps for each evidence source."""

    last_process: float = 0.0
    last_window: float = 0.0
    last_surface: float = 0.0
    last_foreground: float = 0.0
    last_task: float = 0.0
    last_in_game_ui: float = 0.0
    last_lobby_ui: float = 0.0
    last_url_launch: float = 0.0
    last_any: float = 0.0

    def freshest(self) -> float:
        return max(
            self.last_process, self.last_window, self.last_surface,
            self.last_foreground, self.last_task, self.last_in_game_ui,
            self.last_lobby_ui, self.last_url_launch,
        )


@dataclass
class StateDecision:
    """Result of one decide() call — state + reason for the diagnostic log."""

    state: str
    reason: str
    evidence_used: Mapping[str, Any] = field(default_factory=dict)


class StateTracker:
    """Per-package heartbeat tracker.  Thread-safe for a single supervisor.

    ``offline_grace_s`` is how long we wait after the LAST positive signal
    before flipping a package to Offline.  60s default keeps a 5–10s
    transient process restart from being misreported as Offline.

    ``stale_task_grace_s`` is how long we tolerate a "task only" state
    (task record exists but no process / no surface) before treating it as
    Offline.  20s default — generous but not forever.
    """

    def __init__(
        self,
        *,
        offline_grace_s: float = 60.0,
        stale_task_grace_s: float = 20.0,
        join_unconfirmed_grace_s: float = 25.0,
    ) -> None:
        self._beats: dict[str, _Heartbeat] = {}
        self.offline_grace_s = float(offline_grace_s)
        self.stale_task_grace_s = float(stale_task_grace_s)
        self.join_unconfirmed_grace_s = float(join_unconfirmed_grace_s)

    def _hb(self, package: str) -> _Heartbeat:
        hb = self._beats.get(package)
        if hb is None:
            hb = _Heartbeat()
            self._beats[package] = hb
        return hb

    def reset(self, package: str) -> None:
        self._beats.pop(package, None)

    def note_url_launch(self, package: str, now: float | None = None) -> None:
        """Call right after ``am start ... <url>`` to start the join window."""
        ts = float(now) if now is not None else time.monotonic()
        self._hb(package).last_url_launch = ts

    def observe(
        self,
        package: str,
        evidence: Mapping[str, Any],
        *,
        ui_signal: str | None = None,
        now: float | None = None,
    ) -> _Heartbeat:
        """Update heartbeats from an :func:`android.get_package_alive_evidence` dict."""
        ts = float(now) if now is not None else time.monotonic()
        hb = self._hb(package)
        if bool(evidence.get("running")) or bool(evidence.get("root_running")):
            hb.last_process = ts
        if bool(evidence.get("window")):
            hb.last_window = ts
        if bool(evidence.get("surface")):
            hb.last_surface = ts
        if bool(evidence.get("foreground")):
            hb.last_foreground = ts
        if bool(evidence.get("task")):
            hb.last_task = ts
        if ui_signal == "in_game":
            hb.last_in_game_ui = ts
        elif ui_signal == "lobby":
            hb.last_lobby_ui = ts
        hb.last_any = hb.freshest()
        return hb

    def decide(
        self,
        package: str,
        prev_state: str | None,
        evidence: Mapping[str, Any],
        *,
        url_launched: bool = False,
        ui_signal: str | None = None,
        attempt_count: int = 0,
        max_attempts: int = 5,
        now: float | None = None,
    ) -> StateDecision:
        """Map current evidence + heartbeat history to a public state label.

        Decision tree (in order):

        1. UI says in-game            → ``Playing``
        2. Process AND (window OR surface OR foreground) → ``Online``
        3. Window OR surface OR foreground (but no process detection)
                                        → ``Online`` (visual evidence wins;
                                          process detection on cloud phones
                                          is unreliable for long pkg names)
        4. UI says lobby AND process   → ``Lobby``
        5. Process but no visual yet + recent URL launch → ``Join Unconfirmed``
        6. Process only (background)   → ``Background``
        7. Task only, within grace      → ``Background`` (waiting)
        8. No evidence at all:
             - within offline_grace_s of last positive → keep ``prev_state``
               or ``Recovering`` if prev was healthy
             - after grace                              → ``Offline``
        9. After many failed attempts → ``Failed`` (but supervisor keeps
                                          checking; this is a UI label only).
        """
        ts = float(now) if now is not None else time.monotonic()
        hb = self.observe(package, evidence, ui_signal=ui_signal, now=ts)
        running = bool(evidence.get("running") or evidence.get("root_running"))
        window = bool(evidence.get("window"))
        surface = bool(evidence.get("surface"))
        foreground = bool(evidence.get("foreground"))
        task = bool(evidence.get("task"))
        visual = window or surface or foreground

        ev_used = {
            "running": running, "window": window, "surface": surface,
            "foreground": foreground, "task": task,
            "ui_signal": ui_signal or "",
            "url_launched": bool(url_launched),
            "attempt_count": int(attempt_count),
        }

        # 1. Strong in-game UI signal — beats everything else.
        if ui_signal == "in_game":
            return StateDecision(
                STATE_PLAYING, "ui_in_game_tokens_detected", ev_used,
            )

        # 2. Real running process + any visual evidence = Online.
        if running and visual:
            # If UI explicitly says lobby, prefer that.
            if ui_signal == "lobby":
                return StateDecision(
                    STATE_LOBBY, "process+visual+lobby_ui", ev_used,
                )
            return StateDecision(
                STATE_ONLINE, "process+visual_evidence", ev_used,
            )

        # 3. Visual evidence alone (process detection unreliable on cloud
        # phones with long clone names).  Trust the visible window/surface.
        if visual:
            if ui_signal == "lobby":
                return StateDecision(
                    STATE_LOBBY, "visual_with_lobby_ui", ev_used,
                )
            return StateDecision(
                STATE_ONLINE, "visual_evidence_only", ev_used,
            )

        # 4. Recent URL launch with weak evidence = Join Unconfirmed.
        if url_launched or (
            hb.last_url_launch
            and (ts - hb.last_url_launch) < self.join_unconfirmed_grace_s
        ):
            if running or task:
                return StateDecision(
                    STATE_JOIN_UNCONFIRMED,
                    "url_launched_recently",
                    ev_used,
                )

        # 5. Process exists but no visible window/surface (yet) = Background.
        if running:
            return StateDecision(
                STATE_BACKGROUND, "process_only_no_visual", ev_used,
            )

        # 6. Task-only — within grace window, treat as Background; after,
        # let it fall through to Offline logic.
        if task and (ts - hb.last_task) < self.stale_task_grace_s:
            return StateDecision(
                STATE_BACKGROUND, "task_only_within_grace", ev_used,
            )

        # 7. No evidence at all.  Did we have evidence recently?
        freshest = hb.freshest()
        if freshest > 0 and (ts - freshest) < self.offline_grace_s:
            # Just lost evidence — assume Recovering, keep the supervisor
            # from showing a hard Offline.
            return StateDecision(
                STATE_RECOVERING,
                f"no_evidence_within_grace_age={int(ts - freshest)}s",
                ev_used,
            )

        # 8. Cold offline.  Show Failed only after attempt budget exhausted.
        if max_attempts > 0 and attempt_count >= max_attempts:
            return StateDecision(
                STATE_FAILED,
                f"no_evidence_attempts={attempt_count}/{max_attempts}",
                ev_used,
            )
        return StateDecision(
            STATE_OFFLINE,
            "no_evidence_beyond_grace" if freshest > 0 else "never_observed",
            ev_used,
        )
