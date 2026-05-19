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
"""Archived: Old cmd_start smart detection integration.

This file documents the old Start path that called detect_experience_state
from the live supervisor to determine post-launch state.  This integration
caused the SIGSEGV, Joining loop, and URL resend issues.
"""

# ─── BROKEN _post_launch_state (from old supervisor.py) ──────────────────────
#
# The original _post_launch_state method (before Kaeru-style fix in p-f1a4aaafe5):
#
#     def _post_launch_state(self) -> str:
#         """Detect experience state after a URL launch."""
#         try:
#             from .experience_detector import detect_experience_state
#             ev = detect_experience_state(self.package, url_launched=self._url_launched)
#             if ev.is_in_game():
#                 return STATUS_IN_SERVER
#             if ev.is_home_or_lobby():
#                 if self._url_launched and self.has_private_url:
#                     return STATUS_JOIN_UNCONFIRMED  # URL sent but at lobby
#                 return STATUS_LOBBY
#             return STATUS_JOIN_UNCONFIRMED
#         except Exception:
#             return STATUS_JOIN_UNCONFIRMED
#
# PROBLEM: This returned STATUS_JOIN_UNCONFIRMED on almost every launch
# because detect_experience_state() relied on uiautomator (SIGSEGV) and
# logcat (unreliable for App Cloner), which almost always returned weak evidence.
# STATUS_JOIN_UNCONFIRMED then triggered the 120-second URL resend loop.
#
# FIX: _post_launch_state() now returns STATUS_ONLINE immediately.
# Process alive = Online.  No Android UI probing needed or used.
# See agent/supervisor.py for current implementation.

# ─── BROKEN initial_status in cmd_start (before URL canonicalization) ─────────
#
# Old cmd_start code:
#
#     if android.is_process_running(pkg):
#         state = "In Server"  # assumed in-game if process running after URL
#     else:
#         state = "Joining"    # assumed joining if process not detected yet
#
# PROBLEM: "In Server" was incorrect — the process was just launched and
# barely started.  "Joining" triggered the supervisor's Join Unconfirmed timer.
#
# FIX: cmd_start now sets initial state based on URL awareness:
#   - URL configured + process running → "Joining" (immediately resolves to Online)
#   - URL configured, process not yet visible → "Joining" (resolves to Online fast)
#   - No URL → "Launched" or "Launching"
# And the _STATE_DISPLAY_MAP in commands.py maps all these to public states.

# ─── BANNED probes in live Start ─────────────────────────────────────────────
#
# The following Android probes are BANNED from live Start and supervisor paths:
#
#   uiautomator dump     — SIGSEGV on Termux/App Cloner
#   logcat -d            — unreliable for clone package PIDs
#   dumpsys activity     — false lobby detections cause Joining loop
#   accessibility dump   — triggers uiautomator SIGSEGV
#   OCR / screen text    — requires accessibility or screenshot
#
# The live Start path uses ONLY:
#   pidof / am (process alive)     — via android.is_process_running()
#   check_package_health()         — via monitor.py
#   Roblox Presence API            — via agent/roblox_presence.py
#
# No subprocess call inside the supervisor worker may call uiautomator,
# logcat, or dumpsys activity. All subprocess calls go through the
# global subprocess lock in android.py.

# ─── BROKEN detect_experience_state import (old supervisor.py line 13) ───────
#
# Old import that was at the top of agent/supervisor.py:
#
#     from .experience_detector import EvidenceLevel, detect_experience_state
#
# This import meant that even if detect_experience_state was never called,
# the experience_detector module was loaded at import time.  This was harmless
# (no Android probes run at import), but the dead import was confusing.
#
# FIX: This import has been removed from supervisor.py in the stable rebuild.
# The experience_detector module is still present in agent/ for test coverage
# but is no longer imported by the live supervisor path.
