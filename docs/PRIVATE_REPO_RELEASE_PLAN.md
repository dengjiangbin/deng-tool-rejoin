# Private Repo Release Plan — DENG Tool: Rejoin

## Current State (Before This Change)

| Component | Method | Works when repo is private? |
|---|---|---|
| Public install | `git clone https://github.com/…` | ❌ No |
| Update (`cmd_update`) | `git pull` | ❌ No |
| License check | HTTP API (`/api/license/check`) | ✅ Yes |
| License panel (Discord bot) | Supabase service role key | ✅ Yes (server-side) |

## After This Change

| Component | Method | Works when repo is private? |
|---|---|---|
| Public install | `bootstrap_install.sh` → license API → download ZIP | ✅ Yes |
| Update (`cmd_update`) | License API → download ZIP | ✅ Yes |
| License check | HTTP API (unchanged) | ✅ Yes |
| License panel | Unchanged | ✅ Yes |

## Making the Repo Private — Checklist

### 1. Build and publish a release

```bash
# On the server/bot machine:
python scripts/package_release.py --channel stable --version 1.0.0 --notes "First public release"
```

This creates:

```
dist/releases/stable/1.0.0/
    deng-tool-rejoin-1.0.0-stable.zip
    manifest.json
    SHA256SUMS.txt
```

### 2. Set environment variable on the bot server

```bash
export LICENSE_DOWNLOAD_ROOT=/path/to/deng-tool-rejoin/dist
export LICENSE_DOWNLOAD_TOKEN_TTL_SECONDS=300
```

Add to `.env` (not committed to git):

```
LICENSE_DOWNLOAD_ROOT=/home/user/deng-tool-rejoin/dist
LICENSE_API_HOST=0.0.0.0   # or 127.0.0.1 if behind nginx
```

### 3. Host `bootstrap_install.sh` at a stable public URL

```bash
# Example: host the installer at your domain
cp scripts/bootstrap_install.sh /var/www/html/install.sh
```

Users now install with:

```bash
curl -sfL https://your-domain.example.com/install.sh | bash
```

### 4. Make the GitHub repo private

```
GitHub → Settings → Danger Zone → Make this repository private
```

### 5. Verify end-to-end

```bash
# 1. New install (simulate as a user)
DENG_LICENSE_SERVER=https://your-domain.example.com bash scripts/bootstrap_install.sh

# 2. Update (from within an existing install)
python -m agent.commands update
```

---

## What the Client NEVER Receives

| Secret | Where it lives | Sent to client? |
|---|---|---|
| `SUPABASE_SERVICE_ROLE_KEY` | Server `.env` | ❌ NEVER |
| `DISCORD_TOKEN` | Server `.env` | ❌ NEVER |
| `GITHUB_TOKEN` | Server `.env` | ❌ NEVER |
| Raw `install_id` | Client only | SHA-256 hash only |

---

## Rollback Plan

If the private repo migration causes issues:

1. Temporarily make the repo public again.
2. Clients on `mode: local` (old installs) continue working.
3. Fix the issue, rebuild the release package, re-privatize.

---

## Channel Strategy

| Channel | Audience | Stability |
|---|---|---|
| `stable` | All licensed users | Production-ready |
| `beta` | Beta testers only | Feature preview |
| `dev` | Internal dev | May be broken |

Default channel: **stable**. Users can switch in `config.json → license.channel`.
