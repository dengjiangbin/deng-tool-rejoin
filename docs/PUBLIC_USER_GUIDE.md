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
- Phone Snapshot For Webhook, only when webhook is enabled
- Webhook Info Interval, only when webhook is enabled
- Save And Start

Choose **Setup / Edit Config** to change one section later without redoing everything.

## Roblox Package Setup

The package screen automatically scans Android for Roblox-related packages. It uses safe package-name hints such as `roblox`, `rblx`, `blox`, and `moons`, so cloned packages like `com.moons.*` can be detected even when the package name does not contain `roblox`.

If packages are found, DENG shows them in a numbered list and marks `com.roblox.client` as recommended. Choose one or more packages to use them, including cloned packages.

Each selected package can have a Roblox username/account name such as `deng1629`, `AltAccount1`, or `MyCloud1`. DENG uses this only to make the Start table easy to read. DENG may use a safe Android app label or allowlisted display-name preference key when available, but it never reads Roblox credentials, cookies, tokens, or private session files. If it cannot safely detect a name, type one yourself or leave it blank (shown as `Unknown` in the Start table).

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

Window layout is automatic during Start when more than one package is selected. DENG calculates a layout from the package count and display size/DPI. When App Cloner preference XML is accessible, DENG backs it up and updates only known window position keys. If root/file access is unavailable, DENG warns and continues normal launch.

When only one package is selected, window layout is skipped — but the package still launches normally.

## Private Server URL

Choose web/private-server URL mode and paste your Roblox URL. DENG masks private query values in logs and status, for example:

```text
privateServerLinkCode=***MASKED***
```

## Start

Choose **Start**. If first-time setup is not complete, DENG will guide you into setup first. After setup, Start prints a one-line summary (packages selected, launch mode, webhook state), then launches each selected Roblox package using the best available Android launch command (`am`, `cmd`, or `monkey` as a fallback), and prints a single clean status table:

```
┌───┬──────────────────┬──────────┬─────────┬──────────────────────────────┐
│ # │ Package          │ Username │ Launch  │ Status                       │
├───┼──────────────────┼──────────┼─────────┼──────────────────────────────┤
│ 1 │ com.roblox.client│ Main     │ Started │ Roblox launch command sent   │
└───┴──────────────────┴──────────┴─────────┴──────────────────────────────┘

Final:
1 package launched successfully.
```

**Username** shows the Roblox account name you configured. If no name is configured, it shows **Unknown**. An unknown username does not block launching.

**Android launch fallback**: DENG tries launch methods in order — `am start` (MAIN/LAUNCHER intent), then activity-component from `cmd package resolve-activity`, then `monkey` if available. `monkey` is optional and not required. If all Android launcher commands are unavailable, the table shows `Failed` and the final summary shows `0 packages launched.` with a short reason.

**Single package**: One selected package still launches. Only window layout/auto-resize is skipped when there is only one package.

The final summary shows a clear count:
- `1 package launched successfully.`
- `2 packages launched, 1 failed.`
- `0 packages launched.` followed by a short reason.

After Start, DENG sends a webhook update if enabled, then starts the supervisor loop only if auto rejoin is enabled.

## Auto Rejoin

Direct advanced commands such as `status`, `logs`, `doctor`, `update`, `reset`, and `enable-boot` remain available for testing even though the public menu is simplified.

## Logs

Choose **Logs** or run:

```sh
deng-rejoin-logs
```

Logs include event type, package, root usage, success/failure, and masked URLs.

## Status

Status shows first-time setup state, selected Roblox packages and account names, masked launch link, webhook/snapshot state, automatic layout state, Android release/SDK, root availability, latest heartbeat, latest rejoin attempt, and latest error.
