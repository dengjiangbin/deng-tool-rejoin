# agent/legacy — Archived Broken Smart Detection Code

**BROKEN LEGACY CODE — DO NOT USE IN LIVE START PATH.**

Archived after live probes p-f1a4aaafe5 / p-8b025e8c3c because this smart
Android/UI detection caused SIGSEGV, false Joining/Join Unconfirmed states,
private URL relaunch failures, and endless restart loops.

This code is kept only for historical reference.
Live Start must not import or call this module.
Future real state detection must use Roblox Presence API, not
uiautomator/logcat/UI dump.

## Files

| File | Contents |
|------|----------|
| `experience_detector_broken.py` | Full Android experience detector: logcat, dumpsys, uiautomator probes. The uiautomator probe caused SIGSEGV on Termux/App Cloner. |
| `join_state_machine_broken.py` | Old Joining/Join Unconfirmed/In Server/Lobby state machine. Caused endless URL resend loops and false state transitions. |
| `start_smart_detection_broken.py` | Old Start path smart detection code. Called detect_experience_state from live supervisor, triggered crashes. |

## Why These Were Archived

### uiautomator (SIGSEGV trigger)
`uiautomator dump` calls the Android Accessibility Service under the hood.
On Termux with App Cloner packages, the Accessibility Service callback
triggers a SIGSEGV in the calling Python process — not a Python exception,
but a hard C-level segfault that faulthandler cannot intercept cleanly.

### logcat (unreliable for clone packages)
`logcat --pid` requires the correct PID. App Cloner packages truncate their
process names, making `pidof` return wrong PIDs or nothing. This caused
false-negative detections.

### Join Unconfirmed loop
The old code resent the private URL every 120 seconds if the package was
"healthy but not confirmed in-game." This triggered on every launch because
in-game detection was unreliable, creating an infinite URL resend loop.

### Lobby / In Server detection
Detecting lobby vs in-server was only possible via the now-banned probes.
Without them, these states could not be determined reliably.

## Replacement

The live Start path now uses:
1. **Process-alive check** — primary reliability layer
2. **Roblox Presence API** — safe confirmation layer (no device probing)

Public states are: Layout, Launching, Online, Reopening, Failed.
