# Public User Guide

## Open The Menu

```sh
deng-rejoin
```

The pink DENG banner appears, then the local Termux menu.

## First run after install

1. Run **`deng-rejoin`** (see [PUBLIC_INSTALL.md](PUBLIC_INSTALL.md) if the command is missing).
2. **License** — When prompted, paste the key from the **DENG Tool: Rejoin Key Panel** in Discord. One key is usually bound to **one device** until an admin runs **Reset HWID**.
3. **First Time Setup Config** — Choose option `1` in the menu. Walk through:
   - **Package** — Pick from **auto-detected** installed clients/clones (table: #, Package, App Name, Launchable) or enter a package name **manually** if nothing is listed.
   - **Username / account name** (optional) — Only for the on-screen table; you can leave it unset and see **Unknown** (launch still works).
   - **Private server or game URL** (optional) — Paste a full `https://` private server link or use app-only mode by leaving the link blank.
   - **Discord webhook** (optional) and **snapshot** / interval if you enable webhook.
   - **Save** when offered.
4. **Start** — Choose option `3`. The **public** Start summary table has columns **#**, **Package**, **Username**, and **State** only (no Cache / Graphics / Status columns in normal output).

For a full beginner walkthrough with troubleshooting, use **[NEW_USER_TERMUX_GUIDE.md](NEW_USER_TERMUX_GUIDE.md)**.

## Root-aware package detection and supervisor

With root, DENG can list installed packages, check launcher activities, read safe `dumpsys` labels, and combine that with **hint fragments** (not a hard-coded package list) so official `com.roblox.client` and renamed clones can appear together in setup. If nothing matches, use manual package entry.

**Every Start** clears **only** safe cache-style paths under each selected package (`cache`, `code_cache`, `files/tmp`). It never runs `pm clear`, never wipes login/session storage, and failures there never block launch. **Cache** (cleanup) and **graphics** (low-quality settings when a safe JSON file is found) still run internally, but the **public Start table** only shows **Package / Username / State**. Use `--verbose` or `--debug` on Start (or set `log_level` to `DEBUG`) to print per-package cache/graphics/launch detail lines below the table—never a full private server URL.

**Low graphics** walks likely `files/` subtrees for known Roblox settings JSON names (for example `ClientAppSettings.json`); it skips secret-looking paths, backs up before write, and reports **Skipped** when nothing safe is found.

The **supervisor** (when Auto rejoin is enabled) monitors each selected package with configurable grace time, health interval, backoff, and hourly restart limits. It can **reopen** missing processes and attempt **reconnect** when a process is alive but no longer foreground for your client. Per-package **private server URLs** override the global URL; URLs are **masked** in normal output.

The live **Start** view is **one table** with public columns **#**, **Package**, **Username**, and **State** (for example Online, Launching, Failed).

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

- **Add Package** — Uses the **same full discovery table** as first-time setup (#, Package, App Name, Launchable), then multi-select or manual entry. DENG detects a safe display username when possible (app label, readable app data with **optional root read-only scan**). If nothing is found, the Start table shows **Unknown** (launch still works).
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

The package screen automatically scans Android for Roblox-related packages. It uses safe package-name hints such as `roblox`, `rblx`, `blox`, and optional **extra fragments** you or your admin add for clone naming (**example only:** some communities add `moons` when many clone ids share that substring — yours may differ).

If packages are found, DENG shows them in a numbered list and marks `com.roblox.client` as recommended when present. Choose one or more packages, or use **manual entry** for a clone’s exact package id.

Each selected package can have a Roblox username/account name such as `deng1629`, `AltAccount1`, or `MyCloud1`. DENG uses this only to make the Start table easy to read. DENG may use a safe Android app label or allowlisted display-name preference key when available, but it never reads Roblox credentials, cookies, tokens, or private session files. If it cannot safely detect a name, run **Package → Detect / Refresh Usernames** or leave it unset (shown as **Unknown** in the Start table).

If your clone does not appear, use **Auto Detect Packages** / **Add Package** and add a **safe fragment** from the package id in detection hints — **example:** for `com.vendor.myroblox` you might add `myroblox` (follow your host’s guidance).

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

Choose **Start**. If first-time setup is not complete, DENG will guide you into setup first. After setup, Start prints a short summary (packages selected, launch mode, webhook state), then launches each selected Roblox package using the best available Android launch command (`am`, `cmd`, or `monkey` as a fallback), and prints a **minimal** status table:

```
┌───┬───────────────────┬──────────┬────────────┐
│ # │ Package           │ Username │ State      │
├───┼───────────────────┼──────────┼────────────┤
│ 1 │ com.roblox.client │ Main     │ Online     │
└───┴───────────────────┴──────────┴────────────┘

Final:
1 package online.
```

On a real Android or cloud-phone Termux session, verify: package discovery lists expected clients/clones, Start clears safe cache and applies low graphics when possible, the table stays four columns without URLs, the supervisor reaches **Online**, and force-stopping a monitored package leads to reopen/reconnect without printing private URLs. **This Windows/desktop checkout is not a substitute for that device test** when you need physical verification.

**Username** shows the Roblox account name you configured. If no name is configured, it shows **Unknown**. An unknown username does not block launching.

**Android launch fallback**: DENG tries launch methods in order — `am start` (MAIN/LAUNCHER intent), then activity-component from `cmd package resolve-activity`, then `monkey` if available. `monkey` is optional and not required. If all Android launcher commands are unavailable, **State** shows **Failed** and **Final** includes a **failed** / **offline** tally with a short reason printed separately (not as extra table columns).

**Single package**: One selected package still launches. Only window layout/auto-resize is skipped when there is only one package.

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
