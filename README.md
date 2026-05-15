# DENG Tool: Rejoin v1.0.0

**DENG** means **Device Engine for Networked Game Rejoin**.

DENG Tool: Rejoin is a Termux-based Android/cloud-phone utility that helps you open, reopen, and reconnect the official Roblox Android app on your own device. It is a local phone agent, not a gameplay bot.

## Quick install for Termux

1. Open **Termux**.
2. Update packages (recommended before first install):

```sh
pkg update -y && pkg upgrade -y
```

3. Install tools the installer expects (optional if you go straight to step 4 — the installer also runs `pkg` and installs `python`, `sqlite`, `curl`, `git`, and tries `android-tools`):

```sh
pkg install -y curl git python sqlite
```

4. Download and run the **official installer**:

```sh
curl -fsSL https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh -o install.sh && bash install.sh
```

Wget alternative:

```sh
wget -O install.sh https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh && bash install.sh
```

5. Start the menu:

```sh
deng-rejoin
```

6. Enter your **license key** when prompted, then **First Time Setup Config**, then **Start**.

**Beginner walkthrough (recommended):** [docs/NEW_USER_TERMUX_GUIDE.md](docs/NEW_USER_TERMUX_GUIDE.md)

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

- First Time Setup Config
- Setup / Edit Config
- Start

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

## Setup Experience

Setup is a guided Termux menu, not a raw JSON editor. First-time setup walks through Roblox packages/account names, Roblox public/private link, optional Discord webhook, optional snapshot and webhook interval only when webhook is enabled, and Save And Start.

The Roblox Package screen scans Android for Roblox-related packages, marks `com.roblox.client` as recommended when found, lets you choose detected packages, and supports **manual package entry** for clones. Detection uses **hint fragments** (e.g. `roblox`, `rblx`, `blox`, plus optional extra hints you add for your clone’s naming pattern). Account names like `Main`, `Alt 1`, or a Roblox username are safe display names; **Unknown** is shown if you skip a name — launch still works. DENG never reads Roblox cookies, tokens, passwords, or session files.

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
- Launches one or more configured Roblox packages
- Optionally sends safe Discord webhook status updates
- Optionally attaches phone snapshots when explicitly enabled
- Optionally previews/applies App Cloner window layout values when accessible
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

- **[New user Termux setup (step-by-step)](docs/NEW_USER_TERMUX_GUIDE.md)**
- [Public install guide](docs/PUBLIC_INSTALL.md)
- [Public user guide](docs/PUBLIC_USER_GUIDE.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Root mode](docs/ROOT_MODE.md)
- [Termux:Boot](docs/TERMUX_BOOT_PUBLIC.md)
- [Android paths](docs/ANDROID_VERSION_PATHS.md)
- [Security](SECURITY.md)
