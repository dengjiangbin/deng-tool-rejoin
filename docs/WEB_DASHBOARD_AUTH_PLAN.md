# Web Dashboard Auth Plan

## Overview

The web dashboard provides a browser UI for license management.  Authentication uses **Discord OAuth2** — users log in with Discord, and their Discord user ID is matched against the `license_users` table.

This is a **planned feature**.  The Supabase schema (`001_license_system.sql`) already includes the required tables.

---

## Auth Flow

```
Browser → /login → Discord OAuth2 → callback → verify Discord ID
       → create/update web_accounts + discord_identities
       → issue session_token → store hash in web_sessions
       → redirect to /dashboard
```

1. User visits the dashboard and clicks "Login with Discord"
2. Browser redirects to Discord OAuth2 authorize URL with `identify` scope
3. Discord redirects back to `/auth/callback?code=...`
4. Backend exchanges `code` for an access token (server-side only)
5. Backend calls Discord API `/users/@me` to get `id`, `username`, `avatar`
6. Backend looks up `license_users` by `discord_user_id`
7. If not found → 403 (only licensed Discord users can log in)
8. If found → upsert `web_accounts` and `discord_identities`
9. Generate a cryptographically random 32-byte session token
10. Store SHA-256 hash in `web_sessions` with `expires_at = now + 7 days`
11. Set `session_token` as an HTTP-only Secure SameSite=Strict cookie
12. Redirect to `/dashboard`

---

## Session Validation (per request)

1. Read `session_token` from cookie
2. Hash it with SHA-256
3. Look up `web_sessions` where `session_token_hash = hash AND expires_at > now`
4. If not found → redirect to /login
5. Load associated `web_accounts` record
6. Attach `web_account` to request context

---

## Supabase Tables Used

| Table | Purpose |
|---|---|
| `license_users` | Gate: only licensed users can log in |
| `web_accounts` | Web-dashboard user accounts |
| `web_sessions` | Short-lived session tokens (hash only) |
| `discord_identities` | Discord OAuth identity linking |

---

## Security Notes

- **Never store Discord access tokens in plaintext**; only store SHA-256 hash in `access_token_hash` if needed for API calls
- Session tokens must be cryptographically random (32+ bytes from `secrets.token_bytes`)
- Always check `expires_at` server-side; do not trust client-supplied expiry
- Use HTTP-only, Secure, SameSite=Strict for session cookies
- Rotate session on privilege change (e.g. is_owner granted)
- Rate-limit `/auth/callback` and `/login` endpoints
- Discord access tokens must never be logged or included in error responses
- PKCE flow is recommended over implicit grant

---

## Environment Variables Required

```
DISCORD_CLIENT_ID=your-oauth-app-client-id
DISCORD_CLIENT_SECRET=your-oauth-app-client-secret
DISCORD_REDIRECT_URI=https://your-dashboard.example.com/auth/callback
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SESSION_SECRET=32-random-bytes-hex-or-base64
```

---

## Status

Not yet implemented.  This document describes the intended architecture.  Implementation starts after the Discord license panel (Phase 2) is complete.
