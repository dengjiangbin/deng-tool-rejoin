# Client Protection Plan — DENG Tool: Rejoin

## Principle: Minimum Exposure

The Android/Termux client receives **only what it needs to operate** — no server secrets,
no admin credentials, no Supabase keys, no GitHub tokens.

---

## What Lives Where

### Server-Side Only (never sent to client)

| Secret / Credential | Location | Notes |
|---|---|---|
| `SUPABASE_SERVICE_ROLE_KEY` | `.env` on server | Used only in `bot/license_api.py` server-side |
| `DISCORD_TOKEN` | `.env` on server | Used only by Discord bot |
| `GITHUB_TOKEN` | `.env` on server | Used for GitHub API (if any) |
| Supabase database connection | Server only | Client never connects to Supabase directly |
| Raw `install_id` | Client only | Client hashes it before any network call |

### Client-Side (in config.json)

| Value | Notes |
|---|---|
| `license.key` | The user's personal DENG- license key |
| `license.install_id` | Random UUID, never sent raw (SHA-256 hash is sent) |
| `license.server_url` | URL of the bot's license API (not Supabase URL) |
| `license.channel` | `stable`, `beta`, or `dev` |

---

## install_id Protection

The `install_id` is used as a hardware fingerprint to prevent key sharing.
It is stored in `config.json` locally, but **never transmitted raw**.

Before any API call, the client computes:
```python
install_id_hash = hashlib.sha256(install_id.encode()).hexdigest()
```

The hash is what gets sent. The server stores only the hash in Supabase.
This means even if the API response is intercepted, the attacker cannot
use a captured `install_id_hash` without knowing the original `install_id`.

---

## License Key Protection

The license key (`DENG-XXXX-XXXX-XXXX-XXXX`) is:

- Stored in `config.json` (client device only).
- Sent to the license API over HTTPS for verification.
- **Never logged in full** — masked version (`DENG-XXXX...XXXX`) is used in all log output.
- Never embedded in the package ZIP.
- Never sent to Supabase directly from the client.

---

## Package ZIP Contents — Security Gate

The `scripts/package_release.py` builder:

1. Collects only permitted files (see [LICENSED_DOWNLOAD_UPDATE_PLAN.md](LICENSED_DOWNLOAD_UPDATE_PLAN.md)).
2. After building the ZIP, **re-scans it** to verify no forbidden paths snuck in.
3. If any forbidden path is found, the ZIP is **deleted** and the build fails.

Forbidden paths detected by the security gate:
- `.env` (at any depth)
- `bot/` directory (Discord bot server code)
- `tests/` directory
- `supabase/` directory
- `__pycache__/` bytecode
- `keydb.json`, `license_store.json`
- `ecosystem.bot.json` (PM2 server config)

---

## Download Token Security

Download tokens are:

| Property | Value |
|---|---|
| Generated with | `secrets.token_urlsafe(32)` — cryptographically secure |
| Stored on server as | `sha256(raw_token)` — never the raw token |
| Lifetime | 300 seconds (configurable) |
| Use policy | Single-use: invalidated immediately after first download |
| Character validation | `[A-Za-z0-9_-]{1,100}` — rejects path traversal chars |
| Path validation | Resolved path must be within `LICENSE_DOWNLOAD_ROOT` |

---

## HTTPS Requirement

The license API (`bot/license_api.py`) should be served behind HTTPS in production:

```
Android client → HTTPS → nginx reverse proxy → localhost:8787
```

Recommended nginx config snippet:

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8787;
    proxy_set_header X-Real-IP $remote_addr;
}
```

With `LICENSE_API_SHARED_SECRET` set, every client request must include:

```
Authorization: Bearer <shared-secret>
```

---

## What the Client Package Does NOT Contain

Verified by `_verify_no_secrets()` in `scripts/package_release.py`:

- No `.env` files at any path depth
- No `bot/` server code (would expose server architecture)
- No `tests/` (not needed at runtime, reduces attack surface)
- No `supabase/` migrations
- No `keydb.json` or `license_store.json`
- No `ecosystem.bot.json` (PM2 config with server paths)
- No `requirements-bot.txt`

---

## Threat Model Summary

| Threat | Mitigation |
|---|---|
| Attacker reads client `.env` | Client `.env` contains no server secrets |
| Attacker intercepts API traffic | HTTPS + token expiry (5 min) + single-use |
| Attacker replays download token | Single-use: server marks token as used on first serve |
| Attacker crafts token download URL | Token validated against SHA-256 hash stored server-side |
| Path traversal via token metadata | `Path.resolve().relative_to(download_root)` checked |
| Attacker extracts `.env` from ZIP | `_verify_no_secrets()` post-build gate + extractor skips .env |
| Key sharing (multiple devices) | `install_id_hash` device binding in Supabase |
| Key sharing HWID bypass | HWID reset rate-limited (max resets per 24h, set by operator) |
| Brute-force authorize endpoint | Rate limit: max 10 requests per 60s per IP |
