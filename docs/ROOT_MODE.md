# Root Mode

Root mode is optional. DENG Tool: Rejoin works in limited non-root mode.

## What Root Is Used For

When `root_mode_enabled` is true and root is available, the agent may use:

- `su -c id` or `tsu -c id` to verify root access with a short timeout
- `su -c 'am force-stop <package>'` or `tsu -c 'am force-stop <package>'` to stop the configured Roblox package

The package name is validated before use.

## What Root Is Not Used For

Root is not used for hidden behavior, credential access, gameplay automation, memory editing, packet manipulation, script injection, bypassing Roblox systems, or remote shell access.

## Command Categories

Root commands are centralized in `agent/android.py` and limited to Android app-management operations:

- Root identity check
- Force-stop configured package

Launch intents normally run without root:

- `monkey -p <package> -c android.intent.category.LAUNCHER 1`
- `am start -a android.intent.action.VIEW -d <url> <package>`

## Limitations

If the user denies root permission, DENG continues in non-root mode. Some Android/cloud-phone images restrict `dumpsys`, `pidof`, `am`, or `monkey`; doctor reports these conditions when it can.
