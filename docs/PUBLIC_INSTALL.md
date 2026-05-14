# Public Install Guide

## Prepare First

1. Android cloud phone or Android device.
2. Termux installed.
3. Roblox installed.
4. Internet connection.
5. Termux storage permission.
6. Optional root permission for stronger restart.
7. Optional Termux:Boot for start after reboot.
8. Optional Roblox private-server URL or normal Roblox game URL.

DENG never asks for Roblox password, cookies, `.ROBLOSECURITY`, session tokens, or 2FA codes.

## Install With curl

```sh
curl -fsSL https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh -o install.sh && bash install.sh
```

## Install With wget

```sh
wget -O install.sh https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh && bash install.sh
```

## Start

```sh
deng-rejoin
```

Choose **Setup or Change Settings**, then choose **One-Time Rejoin Test**.

Setup uses a guided public menu. You will choose the Roblox package, launch mode, optional Roblox link, delay, auto rejoin, and root mode through simple prompts.

## Android 10

Some Android 10 images expose downloads as `/sdcard/download`; others use `/sdcard/Download`. DENG detects both and creates a launcher where possible:

```sh
python /sdcard/download/deng-rejoin.py
```

## Android 12+

Most Android 12+ images use `/sdcard/Download`. Background restrictions may stop Termux, so disable battery optimization when possible:

```sh
python /sdcard/Download/deng-rejoin.py
```

## Root Optional

Non-root mode can open Roblox or a Roblox URL. Root mode can force-stop Roblox before relaunching. Root commands are limited to safe app management and are timeout-protected.

## Termux:Boot Optional

```sh
deng-rejoin enable-boot
```

Install/open Termux:Boot once, disable battery optimization if possible, reboot, then run:

```sh
deng-rejoin-status
```

## Update

```sh
deng-rejoin-update
```

Fallback:

```sh
curl -fsSL https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh -o install.sh && bash install.sh
```

## Reset

```sh
deng-rejoin-reset
```

Reset keeps logs by default and asks before wiping database/logs.

## Uninstall

```sh
sh ~/.deng-tool/rejoin/scripts/uninstall.sh
```

## Troubleshooting

Run:

```sh
deng-rejoin doctor
```

Doctor checks Python, Termux, Android version, SDK, Download path, root, Roblox package, SQLite, logs, and duplicate agent state.
