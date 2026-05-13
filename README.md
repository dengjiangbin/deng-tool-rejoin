# DENG Tool: Rejoin v1.0.0

**DENG** means **Device Engine for Networked Game Rejoin**.

DENG Tool: Rejoin is a Termux-based Android/cloud-phone utility that helps you open, reopen, and reconnect the official Roblox Android app on your own device. It is a local phone agent, not a gameplay bot.

## Quick Install

1. Install Termux.
2. Open Termux.
3. Paste:

```sh
curl -fsSL https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh -o install.sh && bash install.sh
```

Wget alternative:

```sh
wget -O install.sh https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh && bash install.sh
```

4. Start:

```sh
deng-rejoin
```

5. Choose **Setup / Edit Config**.
6. Choose **One-Time Rejoin Test**.

## What To Prepare

- Android cloud phone or Android device
- Termux installed
- Roblox installed
- Internet connection
- Termux storage permission
- Optional root permission for stronger restart
- Optional Termux:Boot for auto-start after reboot
- Optional Roblox game/private-server URL for direct join

Root is optional. Without root, DENG can open Roblox but may not force-stop it first. With root enabled, DENG can force-stop the configured Roblox package before relaunching.

## Start Menu

```sh
deng-rejoin
```

Menu options:

- Setup / Edit Config
- Start Auto Rejoin
- Stop Auto Rejoin
- One-Time Rejoin Test
- Status
- Logs
- Doctor / Fix Problems
- Enable Termux:Boot
- Update
- Reset

## Manual Commands

```sh
deng-rejoin-setup
deng-rejoin-status
deng-rejoin-start
deng-rejoin-stop
deng-rejoin-logs
deng-rejoin-update
deng-rejoin-reset
```

Advanced direct commands:

```sh
python ~/.deng-tool/rejoin/agent/deng_tool_rejoin.py --once
python ~/.deng-tool/rejoin/agent/deng_tool_rejoin.py enable-boot
python ~/.deng-tool/rejoin/agent/deng_tool_rejoin.py update
```

## Market-Style Launcher

Recommended:

```sh
deng-rejoin
```

Android 12+ / common:

```sh
python /sdcard/Download/deng-rejoin.py
```

Android 10 / fallback:

```sh
python /sdcard/download/deng-rejoin.py
```

If public storage is unavailable, DENG also creates:

```sh
~/.deng-tool/rejoin/launcher/deng-rejoin.py
```

## What DENG Does

- Opens Roblox
- Opens Roblox deep links or Roblox web/private-server URLs
- Optionally force-stops Roblox first when root is enabled
- Runs a local auto-rejoin supervisor
- Records rejoin attempts, heartbeats, and events in SQLite
- Writes readable local logs
- Diagnoses Android/Termux/root/package/path problems

## What DENG Does Not Do

DENG Tool: Rejoin does not ask for Roblox login, cookies, `.ROBLOSECURITY`, browser cookies, session tokens, two-factor codes, or account credentials.

It does not automate gameplay, auto farm, run gameplay macros, bypass AFK systems, solve captchas, bypass anti-cheat, edit memory, manipulate packets, inject scripts, execute exploits, fake user activity, provide a hidden remote shell, or allow arbitrary remote command execution.

## Runtime Paths

- App directory: `~/.deng-tool/rejoin`
- Config: `~/.deng-tool/rejoin/config.json`
- SQLite DB: `~/.deng-tool/rejoin/data/rejoin.sqlite3`
- Logs: `~/.deng-tool/rejoin/logs/agent.log`
- PID: `~/.deng-tool/rejoin/run/agent.pid`
- Lock: `~/.deng-tool/rejoin/run/agent.lock`

## GitHub

- Repo: https://github.com/dengjiangbin/deng-tool-rejoin
- Raw installer: https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh

## More Docs

- [Public install guide](docs/PUBLIC_INSTALL.md)
- [Public user guide](docs/PUBLIC_USER_GUIDE.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Root mode](docs/ROOT_MODE.md)
- [Termux:Boot](docs/TERMUX_BOOT_PUBLIC.md)
- [Android paths](docs/ANDROID_VERSION_PATHS.md)
- [Security](SECURITY.md)
