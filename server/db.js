'use strict';
const Database = require('better-sqlite3');
const path = require('path');
const fs = require('fs');

// Discloud persists the app directory; keep the DB file alongside the app.
const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, 'data');
fs.mkdirSync(DATA_DIR, { recursive: true });
const db = new Database(path.join(DATA_DIR, 'rejoin.db'));
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');

db.exec(`
CREATE TABLE IF NOT EXISTS users (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  username   TEXT UNIQUE NOT NULL COLLATE NOCASE,
  email      TEXT UNIQUE COLLATE NOCASE,
  pass_hash  TEXT NOT NULL,
  role       TEXT NOT NULL DEFAULT 'user',      -- user | admin  (billing/plan slots in later)
  plan       TEXT NOT NULL DEFAULT 'free',
  sess_ver   INTEGER NOT NULL DEFAULT 0,         -- bumped on password change; invalidates saved device logins
  created_at INTEGER NOT NULL
);

-- password reset tokens (hashed); one-shot, short-lived
CREATE TABLE IF NOT EXISTS password_resets (
  token_hash TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  used       INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS devices (
  id          TEXT PRIMARY KEY,                  -- dev_xxxx
  user_id     INTEGER NOT NULL,
  name        TEXT NOT NULL,
  token_hash  TEXT NOT NULL,                     -- sha256 of the device token
  sess_ver    INTEGER NOT NULL DEFAULT 0,        -- must match users.sess_ver or the token is rejected
  created_at  INTEGER NOT NULL,
  last_seen   INTEGER,
  status_json TEXT,                              -- last heartbeat snapshot
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- short-lived codes a logged-in user generates; the agent redeems one to pair hands-free
CREATE TABLE IF NOT EXISTS pair_codes (
  code       TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  used       INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- queued commands per device; agent pulls pending, runs, acks
CREATE TABLE IF NOT EXISTS commands (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id  TEXT NOT NULL,
  action     TEXT NOT NULL,                      -- start_all | stop_all | rejoin_all | push_script | remove_script | set_target | set_settings
  payload    TEXT,                               -- JSON
  status     TEXT NOT NULL DEFAULT 'pending',    -- pending | done | failed
  result     TEXT,
  created_at INTEGER NOT NULL,
  FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);

-- per-user fleet config (rejoin target, scripts, settings) pushed to every device
CREATE TABLE IF NOT EXISTS settings (
  user_id    INTEGER PRIMARY KEY,
  target_json TEXT,
  scripts_json TEXT NOT NULL DEFAULT '[]',
  config_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
`);

// --- migrations for DBs created before a column existed ---
const ucols = db.prepare("PRAGMA table_info(users)").all().map(c => c.name);
if (!ucols.includes('email')) {
  db.exec("ALTER TABLE users ADD COLUMN email TEXT");
  try { db.exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email IS NOT NULL"); } catch {}
}

module.exports = db;
