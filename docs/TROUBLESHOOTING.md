# Troubleshooting

Run doctor first:

```sh
python agent/deng_tool_rejoin.py --doctor
```

## Roblox Package Not Detected

Install the official Roblox Android app or your clone, then run setup again. DENG detects package names that match safe hints such as `roblox`, `rblx`, `blox`, and optional extra fragments you configure for clone naming patterns.

If your clone uses a different prefix, open Roblox Package Setup, choose **Detection hints for cloned package names**, add a safe fragment from the package name, then rescan. You can also set the package manually:

```sh
python agent/deng_tool_rejoin.py --config
```

## Root Not Available

Root is optional. If `su`/`tsu` is unavailable or permission is denied, DENG can still launch Roblox but cannot reliably force-stop it.

## Termux Killed In Background

Disable battery optimization for Termux if your Android image allows it. Cloud phones may also have provider-specific keepalive settings.

## Android 12+ Process Killing

Android 12+ can restrict background process checks and kill Termux. Use foreground Termux, Termux:Boot, and provider keepalive options where available.

## URL Not Opening

Check that the URL is a Roblox `roblox://` deep link or approved `roblox.com` URL. Some Roblox share/private-server URLs are opaque; DENG launches them as Android VIEW intents after validation, but the official Roblox app decides what to do.

## Permission Denied

Run:

```sh
termux-setup-storage
```

For root mode, grant permission in Magisk, KernelSU, Kitsune, or your root manager.

## Signal 4 / Illegal Instruction

This usually points to a Termux/device image ABI mismatch. Reinstall Termux from a current trusted build or use a compatible Android/cloud-phone image.

## Duplicate Agent Lock

Run:

```sh
python agent/deng_tool_rejoin.py --stop
```

If the PID is stale, stop cleans it. If the PID belongs to another process, DENG refuses to kill it.

## Roblox Opens But Does Not Join Server

The official Roblox app controls final routing after Android receives the intent. Verify the private-server URL works when opened manually. Some private links expire, require account access, or are not accepted by the Android app on every build.

## Download Launcher Missing

Run:

```sh
termux-setup-storage
sh ~/.deng-tool/rejoin/scripts/create-launchers.sh
```

DENG checks `/sdcard/Download`, `/sdcard/download`, `/storage/emulated/0/Download`, and `/storage/emulated/0/download`. If none are accessible, use:

```sh
deng-rejoin
```

or:

```sh
python ~/.deng-tool/rejoin/launcher/deng-rejoin.py
```

## Update Fails

Run:

```sh
deng-rejoin-update
```

If Git is unavailable, use the raw installer fallback:

```sh
curl -fsSL https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh -o install.sh && bash install.sh
```
