# DENG Tool: Rejoin — New User Termux Setup Guide

This guide is for people who **already installed the Termux app** but are not sure what to type next. You can **copy and paste** the commands from each step into Termux (long-press the terminal, then tap **Paste**).

---

## What You Need Before Starting

- An **Android phone**, tablet, or **cloud phone** where you are allowed to install apps and (for best results) enable **root** if your host offers it.
- **Termux** installed from [F-Droid](https://f-droid.org/) or another trusted source (recommended over outdated store builds).
- **Roblox** installed *or* a **Roblox-compatible clone APK** installed — the tool only works if a real Roblox client is on the device.
- A **DENG Tool: Rejoin license key** from the **Key Panel** in Discord (see your server’s instructions).
- If you want the tool to open a **specific private server**, have the **full private server link** from Roblox ready (you can paste it during setup). You never need to paste your Roblox password into Termux.
- A **stable internet** connection for install, license check, and updates.

Root (superuser) is **optional**: without it, DENG can still try to open Roblox; with root, cache cleanup and stronger restart behavior work better on many devices.

---

## Step 1 — Open Termux

1. Open the **Termux** app like any other app.
2. Wait until you see a **black (or dark) screen with a command prompt** — usually a `$` at the end of a line.
3. That line is where you type or **paste** commands. After pasting, press **Enter** on the keyboard to run the command.

---

## Step 2 — Update Termux Packages

Termux works best when its own packages are up to date. Run:

```sh
pkg update -y && pkg upgrade -y
```

- If Termux asks questions, you can usually accept the defaults (yes).
- This may take a few minutes on a slow connection.

---

## Step 3 — Install Required Tools

Installing `curl` and `git` first lets you download the installer and lets the installer clone the project if needed. The official **install script** (next step) also runs `pkg update` / `pkg upgrade` and installs **python**, **sqlite**, **curl**, **git**, tries **android-tools**, and optionally **tsu**.  

If you prefer to install something yourself **before** running the installer, you can run:

```sh
pkg install -y curl git python sqlite
```

- **python** — required to run DENG.
- **sqlite** — used for local data (the installer installs this too).
- **curl** — used to download the install script.
- **git** — used if the installer needs to clone the repository.

Optional extras the installer may add: **android-tools** (when available in your Termux mirror), **tsu** (optional helper for some root setups).

---

## Step 4 — Install DENG Tool: Rejoin

Paste **one** command:

```sh
curl -fsSL https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh -o install.sh && bash install.sh
```

**What this does (accurately):**

- Downloads the `install.sh` script from GitHub into your current folder.
- Runs that script, which:
  - Updates Termux packages (`pkg update` / `pkg upgrade`) and installs **python**, **sqlite**, **curl**, **git**, and tries **android-tools** (with a fallback if that package is missing).
  - Optionally installs **tsu** if the package exists on your device.
  - May ask for **storage access** (`termux-setup-storage`) so launchers can be created on your Download folder when possible.
  - Copies (or clones) the tool into **`~/.deng-tool/rejoin`**.
  - Creates **short commands** in Termux’s bin folder, including **`deng-rejoin`**, **`deng-rejoin-start`**, **`deng-rejoin-update`**, and others.
  - Runs a quick **doctor** check at the end (safe if something is missing).

It does **not** install Roblox for you and does **not** know your license key until you enter it when you start the tool.

**Wget alternative** (if you do not have `curl`):

```sh
wget -O install.sh https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh && bash install.sh
```

---

## Step 5 — Start The Tool

After a successful install, start the **main menu** with:

```sh
deng-rejoin
```

**What you should see:** a pink **DENG** banner, a short **Setup Status** summary (license, config, packages, optional private URL, next step), then a numbered menu:

1. Enter / Update License Key  
2. First Time Setup Config  
3. Setup / Edit Config  
4. Start  
5. New User Help  
0. Exit  

If you are lost, choose **5 — New User Help** for the built-in tutorial (same content ideas as this doc, shorter).

**If `deng-rejoin` says “command not found”:** close Termux completely, open it again, and try again. If it still fails, run:

```sh
source ~/.bashrc
```

or open a **new session**, then try `deng-rejoin` again.

**Advanced (same as menu):** from the install folder:

```sh
cd "$HOME/.deng-tool/rejoin" && python agent/deng_tool_rejoin.py menu
```

---

## Step 6 — Enter License Key

The **main menu always opens** so you can read status and choose what to do next.

- Get your key from the **DENG Tool: Rejoin Key Panel** in Discord (**Generate Key** / **Redeem Key** as your community explains).
- In the menu, choose **1 — Enter / Update License Key**, paste the key when asked, and wait for verification.
- **Start** (option **4**) still runs a license check before it launches — a missing or invalid key shows a clear message with next steps instead of failing silently.
- **One key is usually tied to one device** until an admin uses **Reset HWID** for you.
- If you see a **wrong device** message after moving to a new phone, contact the key owner or admin and use **Reset HWID** in the panel (respect cooldown rules), then try again on the new device.

Developer note: `DENG_DEV=1` skips license checks — **only for developers**, not normal users.

---

## Step 7 — First Time Setup Config

In the menu, choose **2 — First Time Setup Config**. The wizard asks simple questions — no JSON editing.

Typical topics (wording may vary slightly by version):

1. **Roblox packages** — The tool **scans the device** for installed packages that match **safe name hints** (fragments like `roblox`, `rblx`, `blox`, not a fixed list of vendors). You pick one or more rows from a small table (**#**, **Package**, **App Name**, **Launchable**). If nothing is found, you can **type a package name manually** (for example the official client is often `com.roblox.client`, but *your* clone will have its own name — use what Android shows or what the app developer told you).  
2. **Username / account name** (optional) — A label **only for your Start table**, e.g. `Main`, `Alt`, or a Roblox username. If you skip it, the table shows **Unknown** — **launch still works**.  
3. **Launch link** (optional) — Public game URL, **private server** `https://` link, or a `roblox://` deeplink. Leave blank to open the Roblox app only.  
4. **Discord webhook** (optional) — Status messages to Discord; leave off if you do not use it.  
5. **Snapshot / webhook interval** — Only if you enabled webhook; snapshots can show what is on screen — use only on **your** device.  
6. **Save** — Saves config and can offer to start the tool.

You can change most options later via **2. Setup / Edit Config**.

---

## Step 8 — Start Rejoin

From the main menu, choose **4 — Start** (or use **`deng-rejoin-start`** from the command line — same flow).

After Start runs, the **public** summary table has **four** columns:

| # | Package | Username | State |

- **Username** may be **Unknown** — that is normal and does **not** block launching.
- **State** is a short status, for example:
  - **Preparing** / **Optimizing** — getting ready (you may see these in some builds).
  - **Launching** — Roblox is being opened.
  - **Online** — process looks healthy.
  - **Background** — app may be in the background while the supervisor decides what to do.
  - **Reconnecting** — tool is trying to bring Roblox back or reconnect.
  - **Warning** — environment or limits (read any short message above the table if shown).
  - **Offline** / **Failed** — something went wrong; see troubleshooting below.

**Cache**, **graphics**, and long **reason** strings are **not** normal columns — they appear only with **verbosity / debug** when you or support need details.

---

## Step 9 — How To Update

Preferred:

```sh
deng-rejoin-update
```

This runs the built-in update command (Git pull when the app was installed from Git).

**Fallback** (re-run the public installer — safe upgrade path):

```sh
curl -fsSL https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh -o install.sh && bash install.sh
```

---

## Step 10 — Common Problems

### “command not found” (deng-rejoin)

- Fully **close** Termux (swipe away from recents) and open again.
- Run: `source ~/.bashrc` or `source ~/.profile`
- Confirm install finished without errors; re-run the **install** command from Step 4.

### No license / cannot pass license screen

- Paste a valid key from the **Key Panel**.
- If the device changed, ask for **Reset HWID** and try again.
- Never share your full key in **public** Discord channels.

### No Roblox packages detected

- Install **Roblox** (or your clone) from APK / store and **open it once** manually so Android registers the package.
- Run **First Time Setup** or **Setup → Package → Auto Detect** again.
- Use **manual package entry** if you know the exact package id (Android settings or the clone vendor).

### Start shows **Failed**

- Run **`deng-rejoin-status`** and **`deng-rejoin doctor`**.
- Confirm the **package name** in config matches an **installed** app.
- Try opening **Roblox manually** once, then **Start** again.

### Root not found / root unavailable

- On a cloud phone, root may be off by policy — contact the **provider**.
- If you expect root, in Termux try: `su -c id` (device-dependent).

### Private server does not open

- Paste the **full** private server link from Roblox (**https://** …).
- Open the same link **once in Chrome** on the phone to confirm it works.
- Ensure **Launch mode** / link type in setup matches a **web** URL or **deeplink** as instructed in [PUBLIC_USER_GUIDE.md](PUBLIC_USER_GUIDE.md).

### Tool stops reconnecting

- Keep **Termux open**; disable **battery optimization** / “background restriction” for Termux where possible.
- Do not force-stop Termux while you expect monitoring to run.
- Use **`--verbose` / `--debug`** only when **support** asks — it prints extra lines.

### Reset local config (careful)

```sh
deng-rejoin-reset
```

This can wipe config / data — read the prompts. For **full removal** of wrappers + optional data, see [PUBLIC_INSTALL.md](PUBLIC_INSTALL.md) (uninstall script).

---

## What To Send Support

When asking for help, send **screenshots or text** of:

- The **Start** table (**Package | Username | State**).
- Output of: `deng-rejoin` → if you can’t open menu, run  
  `cd "$HOME/.deng-tool/rejoin" && python agent/deng_tool_rejoin.py version --no-color`  
  and  
  `cd "$HOME/.deng-tool/rejoin" && python agent/deng_tool_rejoin.py status --no-color`
- The **package name** you selected (e.g. `com.roblox.client` or your clone’s id).
- Whether **Roblox opens manually** when you tap the app icon.

**Do not** paste your **full private server URL** or **full license key** in **public** chat. If support needs a link, send it in a **private** channel.

---

## More Reading

- [Public install (formal)](PUBLIC_INSTALL.md) — prerequisites, update, reset, uninstall.
- [Public user guide](PUBLIC_USER_GUIDE.md) — menus, webhook, license overview.
- [Troubleshooting](TROUBLESHOOTING.md)
- [Architecture overview](ARCHITECTURE.md) (technical)

Repository: [https://github.com/dengjiangbin/deng-tool-rejoin](https://github.com/dengjiangbin/deng-tool-rejoin)
