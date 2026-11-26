# DENG Rejoin — fleet control

Web control panel for your rejoin phones, with a Termux agent on each cloud phone.
szSolve-style theme, pebletz-style concept. Domain: **rejoin.deng.my.id**.

## Pieces
- `server.js` + `db.js` + `auth.js` + `mailer.js` — Node/Express backend (SQLite).
- `public/` — the web app (login, dashboard, reset).
- `agent/` — the Termux phone agent, launcher (`start deng`) and installer.

## Deploy the backend to Discloud
1. Zip the backend (everything except `node_modules/` and `data/`) — or upload this folder.
2. In Discloud, upload the app. `discloud.config` is included (TYPE=site, MAIN=server.js, RAM=512).
3. Set environment variables in Discloud (App → Settings → Variables):
   - `JWT_SECRET` — a long random string (required; keeps logins valid across restarts).
   - `PUBLIC_URL` — `https://rejoin.deng.my.id` (used in password-reset links).
   - Email (optional, for password resets): `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM`.
     Without SMTP, reset links are written to the app logs instead of emailed.
4. Point `rejoin.deng.my.id` at the Discloud app (custom domain), or use the Discloud URL.

Discloud builds `better-sqlite3` natively; the DB file lives in `data/` inside the app.

## Accounts
- Register with username + email + password. Email is used only for password resets.
- Multi-user from day one; billing/plans are stubbed (`role`, `plan` columns) but no payment yet.
- Change password or email under **Account**. Changing the password signs out all phones
  (they re-link automatically on next poll).

## Link a phone
On each cloud phone, one-time setup in Termux:

    curl -fsSL https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install-deng.sh | bash

Then launch anytime with:

    start deng

Pick **Website Agent**, then either enter a 6-digit pairing code (from the site's *Pair phone* tab)
or your username + password. The login is saved — you won't re-enter it unless you change your password.

## What the site controls
- **Fleet** — live status per phone (clones running/dead), start all, rejoin all, per-phone auto-rejoin.
- **Scripts** — push / remove a Lua script to every clone's Autoexecute folder.
- **Delta Key** — push a key to every clone's Delta cache (separate per-clone, or one shared cache).
- **Rejoin Target** — private server link or Place+Job ID; used on every rejoin.

## API (brief)
Auth: `POST /api/register|login|forgot|reset`, `GET /api/me`, `POST /api/account/password|email`.
Pairing: `POST /api/pair/start` (web), `POST /api/device/pair|login` (agent).
Agent: `POST /api/device/heartbeat|ack` (device token).
Web: `GET /api/fleet|config`, `POST /api/command|scripts|target|settings|key`.
