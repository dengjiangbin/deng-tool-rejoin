# Public User Guide

## Open The Menu

```sh
deng-rejoin
```

The pink DENG banner appears, then the local Termux menu.

## Configure Roblox

Choose **First Time Setup Config** for a new device. Setup is a guided menu, not a code or JSON editor. It walks through:

- Roblox Package Setup
- Roblox Public / Private Server Link
- Discord Webhook Setup
- Phone Snapshot For Webhook
- Webhook Info Interval
- Post-Launch Action
- Auto Resize / Window Layout Setup
- Save And Start

Choose **Setup / Edit Config** to change one section later without redoing everything.

## Roblox Package Setup

The package screen automatically scans Android for Roblox-related packages. It uses safe package-name hints such as `roblox`, `rblx`, `blox`, and `moons`, so cloned packages like `com.moons.*` can be detected even when the package name does not contain `roblox`.

If packages are found, DENG shows them in a numbered list and marks `com.roblox.client` as recommended. Choose one or more packages to use them, including cloned packages.

If your clone uses another prefix, open **Detection hints for cloned package names** and add a safe fragment from the package name. For example, add `moons` for `com.moons.myroblox` or `com.moons.` for a whole prefix.

You can also choose **Enter package name manually**. Manual package names are validated and must look like a normal Android package, for example:

```text
com.roblox.client
```

If no package is detected, install Roblox, add a clone detection hint, reopen DENG, and rescan. Android package names do not always reveal the original app identity, so manual entry remains available for unusual clone tools.

## Discord Webhook

Discord webhook updates are optional. DENG masks the webhook URL in status/config/logs and only sends safe device/rejoin information. It never sends Roblox cookies, tokens, passwords, or credentials.

Snapshots are optional and may show private screen information. Enable snapshots only on your own device/cloud phone.

## Window Layout

Auto resize can preview a grid for multiple Roblox packages. When App Cloner preference XML is accessible, DENG backs it up and updates only known window position keys. If root/file access is unavailable, DENG warns and continues normal launch.

## Post-Launch Actions

Safe actions are: none, open Roblox, open configured Roblox link, send webhook update, or show a running status table. DENG does not run Roblox scripts, executors, anti-AFK, farming, macro, captcha bypass, memory, packet, or exploit logic.

## Private Server URL

Choose web/private-server URL mode and paste your Roblox URL. DENG masks private query values in logs and status, for example:

```text
privateServerLinkCode=***MASKED***
```

## Start

Choose **Start**. If first-time setup is not complete, DENG will guide you into setup first. After setup, Start applies any enabled window layout, opens selected Roblox packages, opens the configured link when selected, sends webhook status when enabled, and starts the supervisor only if auto rejoin is enabled.

## Auto Rejoin

Direct advanced commands such as `status`, `logs`, `doctor`, `update`, `reset`, and `enable-boot` remain available for testing even though the public menu is simplified.

## Logs

Choose **Logs** or run:

```sh
deng-rejoin-logs
```

Logs include event type, package, root usage, success/failure, and masked URLs.

## Status

Status shows config, running state, latest heartbeat, latest rejoin attempt, latest error, Android release/SDK, root availability, Roblox package, and detected Download path.
