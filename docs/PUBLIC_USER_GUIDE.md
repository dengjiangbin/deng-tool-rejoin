# Public User Guide

## Open The Menu

```sh
deng-rejoin
```

The pink DENG banner appears, then the local Termux menu.

## Configure Roblox

Choose **First Time Setup Config** for a new device. Setup is a guided menu, not a code or JSON editor. It walks through:

- Roblox Package Setup
- Roblox Public / Private Server Link (Optional — leave blank to skip)
- Discord Webhook Setup
- Phone Snapshot For Webhook, only when webhook is enabled
- Webhook Info Interval, only when webhook is enabled
- Save And Start

Choose **Setup / Edit Config** to change one section later without redoing everything.

## Setup / Edit Config Menu

The **Setup / Edit Config** menu has four sections:

```
1. Package
2. Roblox Launch Link
3. Webhook
4. YesCaptcha
0. Back
```

### 1. Package

Manage Roblox packages (the main app and any clones):

- **Add Package** — Auto-detect from Android or enter manually. DENG detects a safe display username when possible (app label, readable app data with **optional root read-only scan**). If nothing is found, the Start table shows **Unknown** (launch still works).
- **Remove Package** — Select by number, confirm removal. Only the selected package is removed.
- **Auto Detect Packages** — Scan for Roblox and cloned packages not yet added. Avoids duplicates.
- **Detect / Refresh Usernames** — Re-run detection for every configured package and save results to **account_username** when found.

Submenu actions (after **Current Packages** at the top):

```
1. Add Package
2. Remove Package
3. Auto Detect Packages
4. Detect / Refresh Usernames
0. Back
```

Beta testers agree the tool may use **root only to read** small config/pref/JSON files under each configured Roblox package’s app-data path. It does **not** read cookies, sessions, or `.ROBLOSECURITY`, and it does **not** modify app data.

**Current Packages** lists each enabled package as `package — username` (or **Unknown**). If none are enabled, it shows **No Packages Configured.**

### 2. Roblox Launch Link

**Roblox Launch Link is optional.** Leave it blank and DENG will launch Roblox normally (app mode).

- **Set Roblox Launch Link** — Enter a public Roblox game URL, private server URL, or deeplink. Leave blank to skip.
- **Clear Roblox Launch Link** — Remove the saved link and revert to app mode.
- **Show Current Roblox Launch Link** — Display the masked current link.

If no launch link is set, Start launches Roblox using the app launcher normally — same as choosing "App Only, No Link."

### 3. Webhook

Configure Discord status updates. Snapshot and interval are inside this submenu — not separate top-level items.

Current webhook state (URL masked, interval, mode, snapshot) is shown at the top.

- **Webhook URL** — Set or update the Discord webhook URL. The full URL is never shown in logs or menus.
- **Webhook Interval** — Set how often DENG sends status updates (minimum 30 seconds).
- **Webhook Mode** — Off / Status Monitor / Alert Only / Status + Alerts.
- **Snapshot** — Enable phone screenshots attached to webhook messages. Requires Webhook URL to be set first.
- **Test Webhook** — Send a test message to verify the webhook works.

### 4. YesCaptcha

Configure the YesCaptcha API key for CAPTCHA solving.

- **Set YesCaptcha API Key** — Enter your API key (masked, never printed in full).
- **Clear YesCaptcha API Key** — Remove the saved key.
- **Check Balance / Points** — Show current account balance if API key is set.

The API key is masked (first four characters only) in all menus and logs. Missing key does not block Start.

## License Key

The license key is **not** configured from the Setup / Config menu. When you run **`deng-rejoin`** (main menu), DENG verifies your license with the public server (`https://rejoin.deng.my.id`) **before** the menu appears. The same check runs again when you use **Start** from the menu or `deng-rejoin-start`.

- `DENG_DEV=1` skips the license check (development mode).
- If a valid key is stored, you see **License OK** (or plain **OK: License Verified** with `--no-color`).
- If the key is missing or invalid, you stay on the license prompt until you fix it or exit.
- If the key is bound to another device, you see **Reset HWID** guidance for Discord.

In Discord, open **DENG Tool: Rejoin Key Panel** in your server: **Generate Key**, **Redeem Key**, **Reset HWID**, and **Key Stats**. By default you get **one key** per Discord account; it binds to **one device** until you **Reset HWID**. **Key Stats** and **Download Keys** are private; **Used** means an active device binding, **Unused** means none. With export storage enabled (**LICENSE_KEY_EXPORT_SECRET** + migration `002`), those views show the **full key**; older keys can use **Recover Full Key** or redeem the same key again once.

If the tool reports the key is already bound to another device, use **Reset HWID** in the panel (respect cooldowns), then try again on the new device.

Set `DENG_BRANDING_LOGO_URL` for a thumbnail, or `LICENSE_API_PUBLIC_URL` so `…/assets/denghub_logo.png` is used.

## Roblox Package Setup

The package screen automatically scans Android for Roblox-related packages. It uses safe package-name hints such as `roblox`, `rblx`, `blox`, and `moons`, so cloned packages like `com.moons.*` can be detected even when the package name does not contain `roblox`.

If packages are found, DENG shows them in a numbered list and marks `com.roblox.client` as recommended. Choose one or more packages to use them, including cloned packages.

Each selected package can have a Roblox username/account name such as `deng1629`, `AltAccount1`, or `MyCloud1`. DENG uses this only to make the Start table easy to read. DENG may use a safe Android app label or allowlisted display-name preference key when available, but it never reads Roblox credentials, cookies, tokens, or private session files. If it cannot safely detect a name, run **Package → Detect / Refresh Usernames** or leave it unset (shown as **Unknown** in the Start table).

If your clone uses another prefix, use **Auto Detect Packages** and add a safe fragment from the package name. For example, add `moons` for `com.moons.myroblox`.

You can also choose **Enter Manually**. Manual package names are validated and must look like a normal Android package, for example:

```text
com.roblox.client
```

If no package is detected, install Roblox, add a clone detection hint, reopen DENG, and rescan.

## Discord Webhook

Discord webhook updates are optional. DENG masks the webhook URL in status/config/logs and only sends safe device/rejoin information. It never sends Roblox cookies, tokens, passwords, or credentials.

Snapshots are optional and may show private screen information. Enable snapshots only on your own device/cloud phone.

Snapshot and Webhook Interval are configured inside the **Webhook** submenu, not as top-level items.

## Window Layout

Window layout is automatic during Start when more than one package is selected. DENG calculates a layout from the package count and display size/DPI. When App Cloner preference XML is accessible, DENG backs it up and updates only known window position keys. If root/file access is unavailable, DENG warns and continues normal launch.

When only one package is selected, window layout is skipped — but the package still launches normally.

## Private Server URL

Choose **Set Roblox Launch Link** → **Private Server URL** and paste your Roblox URL. DENG masks private query values in logs and status, for example:

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
