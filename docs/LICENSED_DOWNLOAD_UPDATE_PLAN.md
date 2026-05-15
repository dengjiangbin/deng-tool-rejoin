# Licensed Download & Update Architecture

## Overview

DENG Tool: Rejoin uses a **license-gated package delivery** system to:

1. Keep source code private (GitHub repo can be set to private).
2. Ensure only licensed users receive updates.
3. Verify package integrity via SHA-256 checksums.
4. Prevent credential leakage to Android/Termux clients.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Server (your VPS / Discord bot machine)                │
│                                                         │
│  ┌───────────────┐   ┌──────────────────────────────┐   │
│  │  Discord bot  │   │  License API (port 8787)     │   │
│  │  (bot/main.py)│   │  (bot/license_api.py)        │   │
│  └───────────────┘   │                              │   │
│         │            │  POST /api/license/check     │   │
│         │            │  POST /api/license/heartbeat │   │
│         ▼            │  POST /api/download/authorize│   │
│  ┌───────────────┐   │  GET  /api/download/package/ │   │
│  │  Supabase DB  │   │         <token>              │   │
│  │  (licenses,   │◄──┤                              │   │
│  │   bindings)   │   │  dist/releases/              │   │
│  └───────────────┘   │    stable/1.0.0/             │   │
│                      │      *.zip                   │   │
│                      │      manifest.json           │   │
│                      │      SHA256SUMS.txt          │   │
│                      └──────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
                             ▲ HTTPS
                             │
┌─────────────────────────────────────────────────────────┐
│  Android / Termux Client                                │
│                                                         │
│  1. bootstrap_install.sh (first install)                │
│     OR agent/updater.py   (cmd_update)                  │
│                                                         │
│  2. POST /api/download/authorize                        │
│     {key, install_id_hash, device_model, app_version,  │
│      channel}                                           │
│             → {download_token, sha256, version,...}     │
│                                                         │
│  3. GET /api/download/package/<download_token>          │
│             → ZIP bytes                                 │
│                                                         │
│  4. Verify SHA-256                                      │
│  5. Backup current install                              │
│  6. Extract ZIP (skip .env, path traversal)             │
│  7. Restore config + permissions                        │
└─────────────────────────────────────────────────────────┘
```

---

## Download Flow Details

### Step 1: Authorize

**Client → Server** `POST /api/download/authorize`:

```json
{
  "key": "DENG-XXXX-XXXX-XXXX-XXXX",
  "install_id_hash": "<sha256 of install_id>",
  "device_model": "Pixel 7",
  "app_version": "1.0.0",
  "channel": "stable"
}
```

**Server validates:**
- Key exists in Supabase
- `install_id_hash` matches the bound device (or binds if first time)
- License is `active` (not revoked, not expired)

**Server responds** (on success):

```json
{
  "result": "active",
  "download_token": "<44-char URL-safe token>",
  "expires_at": "2025-06-01T12:05:00+00:00",
  "version": "1.0.0",
  "channel": "stable",
  "filename": "deng-tool-rejoin-1.0.0-stable.zip",
  "sha256": "<64-hex>",
  "size_bytes": 152438,
  "download_url": "http://host:8787/api/download/package/<token>",
  "notes": "..."
}
```

### Step 2: Download

**Client → Server** `GET /api/download/package/<token>`:

- Token is **single-use** and **short-lived** (default: 300 seconds).
- Server validates token hash (SHA-256 of `raw_token`).
- Server validates file path is within `LICENSE_DOWNLOAD_ROOT` (no traversal).
- Server streams the ZIP bytes.

### Step 3: Verify + Install

Client-side:
1. Compute SHA-256 of downloaded ZIP.
2. Compare with `sha256` from authorize response.
3. On mismatch: abort, delete ZIP.
4. Backup existing install (skip `data/`, `logs/`, `.env`).
5. Extract ZIP into install dir (skip `.env`, skip `..` traversal).
6. `chmod 700/600` on all files (Unix).
7. On extraction failure: rollback from backup.

---

## Package Contents

The release ZIP (built by `scripts/package_release.py`) contains:

```
agent/               ← All client Python modules
examples/            ← config.example.json
scripts/             ← Client scripts (start, stop, update, etc.)
VERSION
README.md
INSTALL_TERMUX.md
SECURITY.md
install.sh
```

**Never included:**
- `bot/` — Discord bot server code
- `tests/` — Test suite
- `supabase/` — DB migrations
- `.env` / `*.env` — Any environment files
- `keydb.json` / `license_store.json` — User data files
- `__pycache__/` — Python bytecode

---

## Channel System

| Channel | Directory | Audience |
|---|---|---|
| `stable` | `dist/releases/stable/` | All licensed users |
| `beta` | `dist/releases/beta/` | Beta testers |
| `dev` | `dist/releases/dev/` | Internal only |

The server picks the **newest version** within the requested channel by sorting version directory names semantically.

---

## Token Security

| Property | Value |
|---|---|
| Token generation | `secrets.token_urlsafe(32)` (40+ chars) |
| Storage on server | SHA-256 hash of raw token only |
| TTL | `LICENSE_DOWNLOAD_TOKEN_TTL_SECONDS` (default: 300s) |
| Use policy | Single-use (invalidated on first successful download) |
| Validation | `re.match(r'^[A-Za-z0-9_\-]{1,100}$', token)` |

---

## Building a Release

```bash
# Stable release (version from VERSION file)
python scripts/package_release.py --channel stable

# Beta release with custom version and notes
python scripts/package_release.py --channel beta --version 1.1.0-beta1 --notes "New feature X"

# Overwrite existing
python scripts/package_release.py --channel stable --force
```

Output goes to `dist/releases/<channel>/<version>/`.

---

## Environment Variables (Server)

| Variable | Default | Description |
|---|---|---|
| `LICENSE_API_ENABLED` | `false` | Must be `true` to start API |
| `LICENSE_API_HOST` | `127.0.0.1` | Bind address |
| `LICENSE_API_PORT` | `8787` | Port |
| `LICENSE_API_SHARED_SECRET` | _(none)_ | Bearer token for client→server auth |
| `LICENSE_DOWNLOAD_ROOT` | _(none)_ | Path to `dist/` directory |
| `LICENSE_DOWNLOAD_TOKEN_TTL_SECONDS` | `300` | Token lifetime |

---

## Client Config (config.json)

```json
{
  "license": {
    "enabled": true,
    "mode": "remote",
    "key": "DENG-XXXX-XXXX-XXXX-XXXX",
    "server_url": "https://your-domain.example.com",
    "install_id": "<uuid-hex>",
    "channel": "stable",
    "device_label": "my-phone",
    "last_status": "active",
    "last_check_at": "2025-06-01T12:00:00+00:00"
  }
}
```
