# DENG Tool Rejoin — Termux Install Tutorial

This guide walks a first-time Android user through installing and running the
DENG Tool Rejoin agent on a phone using Termux, from unboxing to daily use.

---

## A. What is Termux?

Termux is a free Android terminal emulator that runs a real Linux environment
on your phone—no root required. It lets you run Python, shell scripts, and
persistent background processes just like a Linux server.

**Download Termux** from **F-Droid** (not the Play Store version, which is
outdated):

```
https://f-droid.org/packages/com.termux/
```

> ⚠️  The Play Store version of Termux is abandoned. Always use the F-Droid
> build to get up-to-date packages.

---

## B. First-Time Termux Setup

Open Termux and run these commands to bring the package list up to date:

```bash
pkg update -y
pkg upgrade -y
```

Grant storage permission so the tool can write log files:

```bash
termux-setup-storage
```

Tap **Allow** when Android asks.

---

## C. Install Python and Git

```bash
pkg install python git -y
```

Verify the install:

```bash
python --version   # must be 3.10 or higher
git --version
```

---

## D. Clone and Install the Tool

```bash
cd ~
git clone https://github.com/YOUR_ORG/deng-tool-rejoin.git
cd deng-tool-rejoin
pip install -r requirements-bot.txt
```

> **Offline / private install**: if you received a ZIP instead of a git URL,
> copy it to your phone and extract it:
> ```bash
> pkg install unzip
> cp /sdcard/Download/deng-tool-rejoin.zip ~/
> unzip deng-tool-rejoin.zip
> cd deng-tool-rejoin
> ```

---

## E. Configure Environment Variables

The tool reads secrets from a `.env` file in the project root.
Create it with `nano` (included in Termux):

```bash
nano .env
```

Paste the following, filling in your own values:

```
# ── Required ──────────────────────────────────────────────────────────────────
DENG_LICENSE_STORE=supabase
SUPABASE_URL=https://xxxxxxxxxxxxxxxxxxx.supabase.co
# ⚠️  NEVER share the service role key below with anyone.
# It grants full admin access to your Supabase project.
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6...

# ── Optional remote license API ───────────────────────────────────────────────
# Only needed if you use the Discord panel HWID reset from a remote device.
# LICENSE_API_ENABLED=true
# LICENSE_API_HOST=0.0.0.0
# LICENSE_API_PORT=8787
# LICENSE_API_SHARED_SECRET=change_me_to_a_long_random_string
```

Save and exit: `Ctrl+O` → Enter → `Ctrl+X`.

> ⛔  **NEVER put this file online, in a screenshot, or in chat.**
> The `SUPABASE_SERVICE_ROLE_KEY` gives complete admin access.
> The `.gitignore` already excludes `.env` from git commits.

### Account usernames on the phone

DENG can fill in each Roblox package’s **account username** for the Start table and webhooks. It uses normal Android info first, then an optional **read-only root scan** of that package’s app data (small XML/JSON only — never cookies or `.ROBLOSECURITY`). If detection fails, use **Package → Set / Edit Username**. Beta terms include allowing this root use for display names only.

---

## F. Getting Your License Key

The license key is issued and managed through the Discord bot panel.

### Generate a new key

1. Open your Discord server and find the **#license-panel** channel.
2. Click **Generate Key**.
3. The bot sends you a private (ephemeral) message with your key, e.g.:
   `DENG-8F3A-B3C4-D5E6-44F0`
4. Copy the key exactly.

### Redeem an existing key

If someone gave you a key or you already have one:

1. Click **Redeem Key** in the panel.
2. Paste the key into the modal.
3. The bot confirms: *"Key Already Attached"* (if it's yours) or *"Key redeemed."*

> Keys are stored as hashed values — the bot never stores your raw key.
> Keep your key private; it binds to one device at a time.

---

## G. Start the Tool

### Manual start

```bash
cd ~/deng-tool-rejoin
source .env 2>/dev/null || set -a && . ./.env && set +a
python -m agent
```

Or use the bundled start script:

```bash
bash scripts/start-agent.sh
```

On first run, the key you entered is verified against Supabase and your device
is **bound** automatically. You will see a confirmation in the terminal.

To check status:

```bash
bash scripts/status-agent.sh
```

To stop:

```bash
bash scripts/stop-agent.sh
```

---

## H. 40 / 60 Window Layout (Split-Screen)

For comfortable monitoring on a phone, use Android Split-Screen:

1. Open **Termux** and start the tool.
2. Swipe up and hold to open the recent-apps drawer.
3. Tap the **Termux** app icon at the top → **Split Screen**.
4. In the bottom half, open **Discord**.

Resize the divider to roughly **40 % Termux / 60 % Discord**.

This lets you watch the tool logs in the top window while operating the bot
panel in the bottom window — no need to switch between apps.

> **Tip**: Termux supports multiple sessions. Swipe right from the left edge to
> open the session drawer and create a second Termux session for a second tool
> instance or for running commands while the agent is running.

---

## I. Resetting Your HWID from Discord

If you switch phones or reinstall the app, your device hash changes and the
tool will report `wrong_device`. You must reset your HWID first.

### Steps

1. **Stop the tool** on your current device first:
   ```bash
   bash scripts/stop-agent.sh
   ```
2. **Wait 5 minutes.** The bot requires the key to have been
   *inactive* for at least 5 minutes before allowing a reset.
3. In Discord, click **Reset HWID** in the license panel.
4. The bot replies:
   - ✅ *"HWID Reset — Your device binding has been cleared."*  → go to step 5.
   - ⚠️  *"Key Recently Active"* → wait the remaining minutes and retry.
   - ⚠️  *"No Device Bound"* → your key has never been started; nothing to reset.
     This does **not** count against your 5 daily HWID resets.
   - ⛔ *"Reset Limit Reached"* → 5/5 resets used in 24 hours; wait and retry.
5. Start the tool on the new device. It will bind automatically.

> You have **5 HWID resets per 24 hours** per key.

---

## J. Auto-Start with Termux:Boot

Termux:Boot lets the agent restart automatically when your phone reboots.

### Install Termux:Boot

Download from **F-Droid**:
```
https://f-droid.org/packages/com.termux.boot/
```

Open the app once after install to enable the boot trigger.

### Create the boot script

```bash
mkdir -p ~/.termux/boot
nano ~/.termux/boot/start-deng.sh
```

Paste:

```bash
#!/data/data/com.termux/files/usr/bin/bash
# Wait for network to come up
sleep 10

cd ~/deng-tool-rejoin
source .env 2>/dev/null || set -a && . ./.env && set +a
nohup python -m agent >> ~/deng-rejoin.log 2>&1 &
```

Save and make executable:

```bash
chmod +x ~/.termux/boot/start-deng.sh
```

The agent will now start within ~10 seconds of every reboot.

---

## K. Security Checklist

Before sharing screenshots or asking for help, **check these items**:

| Item | Safe? |
|------|-------|
| `.env` file in screenshot | ❌ Never |
| `SUPABASE_SERVICE_ROLE_KEY` in chat | ❌ Never — full DB admin access |
| Discord bot token | ❌ Never — bot takeover risk |
| Your license key (raw) | ❌ Never publicly — can be stolen |
| Masked key (e.g. `DENG-8F3A...44F0`) | ✅ Safe to share for support |
| Tool log output (no secrets) | ✅ Usually safe |

> The service role key grants complete read/write access to your entire
> Supabase database. Treat it like a password to your main server.
> If it is ever leaked, regenerate it immediately in the Supabase Dashboard
> under **Settings → API → Service Role Key → Regenerate**.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `wrong_device` on first start | No reset needed — device binds on first use automatically |
| `wrong_device` after reinstall | Reset HWID in Discord (section I) |
| `Key not found` | Check for typos; key format is `DENG-XXXX-XXXX-XXXX-XXXX` |
| `Reset Limit Reached` | Wait 24 hours; each key gets 5 resets per day |
| `No Device Bound` when resetting | Start the tool once first so a binding is created |
| Agent crashes immediately | Check `.env` values; run `python -m agent.doctor` for diagnostics |
| Termux:Boot not starting | Open the Termux:Boot app once and verify `~/.termux/boot/` script is executable |

For more details see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

*Tutorial version: see [../VERSION](../VERSION)*
