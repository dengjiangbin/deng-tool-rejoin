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
"""Archived: Old Joining / Join Unconfirmed / In Server / Lobby state machine.

This file documents the state constants and logic that caused endless
Joining loops and URL resend storms.  None of this runs in the live Start path.

THE OLD BROKEN FLOW (what used to happen):
==========================================

1. User pressed Start.
2. Tool sent private_server_url to each Roblox package via `am start`.
3. Package was marked STATUS_JOINING ("Joining" in public table).
4. Supervisor called detect_experience_state() every health check cycle.
5. detect_experience_state() used dumpsys/logcat/uiautomator.
6. These probes often returned ROBLOX_HOME_OR_LOBBY (false positive lobby).
7. Supervisor set STATUS_JOIN_UNCONFIRMED ("Join Unconfirmed").
8. After 120 seconds in Join Unconfirmed, supervisor resent the private URL.
9. The URL resend marked the package STATUS_JOINING again.
10. Goto step 4. → Infinite loop.

WHY IT FAILED:
==============
- App Cloner packages have truncated process names, breaking pidof/logcat --pid.
- dumpsys activity class names vary across Roblox app versions and clone APKs.
- uiautomator dump crashes with SIGSEGV on Termux/App Cloner (Accessibility Service).
- The account could actually be playing (visible in Roblox Presence API as InGame)
  while all local probes returned "lobby" — the tool kept resending the URL
  and interrupting the active game session.

THE FIX (Kaeru-style):
======================
- After URL launch, if process is alive → STATUS_ONLINE ("Online").
- No Joining state, no Join Unconfirmed state, no URL resend loop.
- Roblox Presence API is used as a safe CONFIRMATION layer:
  * If InGame → Online (confirmed).
  * If not in game → don't resend; just note it internally.
  * Process dead → relaunch (regardless of Presence API).
"""

# ─── BROKEN state constants (archived — do not use in live Start) ─────────────

# These constants were set by the old supervisor on URL launch.
# They are kept here for documentation only.  The live supervisor
# maps them to STATUS_ONLINE or STATUS_LAUNCHING for public display.

STATUS_JOINING_BROKEN = "Joining"
# Set when `am start <private_server_url>` was sent.
# The old supervisor waited for detect_experience_state() to confirm in-game.
# Without confirmation, it fell into STATUS_JOIN_UNCONFIRMED after grace period.

STATUS_JOIN_UNCONFIRMED_BROKEN = "Join Unconfirmed"
# Set when: URL was sent, process is alive, but no in-game evidence detected.
# OLD BEHAVIOR: after _JOIN_UNCONFIRMED_RELAUNCH_SECONDS (120s), resend URL.
# PROBLEM: caused infinite URL resend loop on every launch.

STATUS_IN_SERVER_BROKEN = "In Server"
# Set when detect_experience_state() returned EXPERIENCE_LIKELY_LOADED.
# PROBLEM: required uiautomator (SIGSEGV) or logcat (unreliable for clones).
# Almost never reached correctly.

STATUS_LOBBY_BROKEN = "Lobby"
# Set when detect_experience_state() returned ROBLOX_HOME_OR_LOBBY.
# PROBLEM: frequently triggered false positives → led to STATUS_JOIN_UNCONFIRMED.

# ─── BROKEN 120-second resend timeout ─────────────────────────────────────────

_JOIN_UNCONFIRMED_RELAUNCH_SECONDS_BROKEN = 120
# After 120 seconds in Join Unconfirmed, the old supervisor resent the URL.
# This was the root cause of the endless relaunch loop.
#
# Changed to 3600 (1 hour, effectively disabled) in probe p-f1a4aaafe5.
# Now dead code — _post_launch_state() returns STATUS_ONLINE immediately.

# ─── BROKEN presence.is_lobby branch (from old supervisor.py) ─────────────────
#
# The old presence detection in supervisor.py (before stable rebuild):
#
#     if presence.is_lobby:
#         if (
#             self.launching_since is not None
#             and self.has_private_url
#             and prev_before_check in (
#                 STATUS_LAUNCHING, STATUS_JOINING, STATUS_LAUNCHED,
#             )
#         ):
#             # URL was sent and Roblox says the account is in the app lobby.
#             # The old code called this "Joining", not "Online".
#             self._set_status(STATUS_JOINING, "Roblox presence: Online (lobby)")
#         else:
#             self._set_status(STATUS_LOBBY, "Roblox presence: Online (lobby)")
#         self._last_presence_label = presence.presence_type.label
#         self.last_seen_at = time.time()
#         self._sleep(interval)
#         continue
#
# PROBLEM: This path set STATUS_JOINING when the account was at the lobby,
# which was almost always the case briefly after launch.  The Joining state
# then triggered the 120-second URL resend timer, creating an endless loop.
#
# FIX: The live supervisor now records presence state internally but does NOT
# change the visible state based on lobby detection.  The process-alive check
# below determines the public Online/Reopening/Failed state.

# ─── BROKEN _label_map (from old supervisor.py StateTracker path) ─────────────
#
# The old label map in supervisor.py:
#
#     _label_map = {
#         "Playing":          STATUS_IN_SERVER,     # → now STATUS_ONLINE
#         "In Server":        STATUS_IN_SERVER,     # → now STATUS_ONLINE
#         "Online":           STATUS_ONLINE,
#         "Lobby":            STATUS_LOBBY,         # → now STATUS_ONLINE
#         "Background":       STATUS_BACKGROUND,    # → now STATUS_ONLINE
#         "Join Unconfirmed": STATUS_JOIN_UNCONFIRMED,  # → now STATUS_ONLINE
#         "Recovering":       STATUS_RECONNECTING,
#         "Unknown":          STATUS_BACKGROUND,    # → now STATUS_ONLINE
#     }
#
# FIX: All old states now map to STATUS_ONLINE for display.
# The _STATE_DISPLAY_MAP in commands.py also handles any residual old states.
