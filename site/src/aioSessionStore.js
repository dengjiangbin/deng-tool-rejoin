'use strict';
/**
 * DENG AIO session + one-time login-code store.
 *
 * This backs the APK ("DENG AIO") authentication path that is intentionally
 * SEPARATE from the website cookie session:
 *
 *   1. Browser completes Discord OAuth on the backend.
 *   2. Backend mints a short-lived, single-use login CODE bound to the
 *      resolved Discord user and hands it to the APK via a deep link.
 *   3. APK exchanges that code over HTTPS for a long-lived APK SESSION TOKEN.
 *
 * Only hashes of codes/tokens are persisted; the plaintext values are returned
 * to the caller exactly once.
 *
 * Storage is file-backed (so it survives restarts like the live tracker store)
 * with a pure in-memory mode under tests. It deliberately does NOT depend on
 * Supabase so the APK auth path keeps working even when the portal DB is
 * unavailable, and so it requires no new SQL migration to deploy.
 */

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const STORE_PATH = process.env.DENG_AIO_SESSIONS_PATH
  || path.join(__dirname, '..', 'data', 'aio_sessions.json');

const MEMORY_ONLY = process.env.NODE_ENV === 'test' || process.env.DENG_AIO_MEMORY === '1';

// Short-lived one-time login code handed to the APK via deep link.
const LOGIN_CODE_TTL_SEC = Number(process.env.DENG_AIO_LOGIN_CODE_TTL_SEC || 120);
// Long-lived APK session token.
const SESSION_TTL_SEC = Number(process.env.DENG_AIO_SESSION_TTL_SEC || 30 * 24 * 60 * 60);
const MAX_SESSIONS = Number(process.env.DENG_AIO_MAX_SESSIONS || 5000);

function _defaultFile() {
  return { updatedAt: null, codes: {}, sessions: {}, acks: {} };
}

let state = _defaultFile();
let loaded = false;

function sha256(value) {
  return crypto.createHash('sha256').update(String(value)).digest('hex');
}

function randomToken(bytes = 32) {
  return crypto.randomBytes(bytes).toString('base64url');
}

function randomLoginCode() {
  // URL/deep-link safe, high entropy, not user-typed so length favours safety.
  return crypto.randomBytes(24).toString('base64url');
}

function nowMs() {
  return Date.now();
}

function _load() {
  if (loaded) return;
  loaded = true;
  if (MEMORY_ONLY) {
    state = _defaultFile();
    return;
  }
  try {
    const raw = fs.readFileSync(STORE_PATH, 'utf8');
    const parsed = JSON.parse(raw);
    state = {
      updatedAt: parsed.updatedAt || null,
      codes: parsed.codes && typeof parsed.codes === 'object' ? parsed.codes : {},
      sessions: parsed.sessions && typeof parsed.sessions === 'object' ? parsed.sessions : {},
      acks: parsed.acks && typeof parsed.acks === 'object' ? parsed.acks : {},
    };
  } catch {
    state = _defaultFile();
  }
  _pruneExpired();
}

function _persist() {
  state.updatedAt = new Date().toISOString();
  if (MEMORY_ONLY) return;
  try {
    fs.mkdirSync(path.dirname(STORE_PATH), { recursive: true });
    const tmp = `${STORE_PATH}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(state), 'utf8');
    fs.renameSync(tmp, STORE_PATH);
  } catch (err) {
    console.warn('[aio] session store persist failed:', err && err.message ? err.message : err);
  }
}

function _pruneExpired() {
  const t = nowMs();
  let changed = false;
  for (const [k, v] of Object.entries(state.codes)) {
    if (!v || v.usedAt || (v.expiresAtMs && v.expiresAtMs < t)) {
      delete state.codes[k];
      changed = true;
    }
  }
  for (const [k, v] of Object.entries(state.sessions)) {
    if (!v || v.revokedAt || (v.expiresAtMs && v.expiresAtMs < t)) {
      delete state.sessions[k];
      changed = true;
    }
  }
  // Bound session table size (drop oldest by lastUsed/created).
  const sessionKeys = Object.keys(state.sessions);
  if (sessionKeys.length > MAX_SESSIONS) {
    sessionKeys
      .map((k) => ({ k, ts: state.sessions[k].lastUsedAtMs || state.sessions[k].createdAtMs || 0 }))
      .sort((a, b) => a.ts - b.ts)
      .slice(0, sessionKeys.length - MAX_SESSIONS)
      .forEach(({ k }) => { delete state.sessions[k]; changed = true; });
  }
  return changed;
}

function _normalizeUser(user) {
  return {
    discordUserId: user && user.discordUserId != null ? String(user.discordUserId) : null,
    siteUserId: user && user.siteUserId != null ? String(user.siteUserId) : null,
    username: user && user.username ? String(user.username) : null,
    avatar: user && user.avatar ? String(user.avatar) : null,
  };
}

/**
 * Mint a one-time login code bound to a resolved Discord user.
 * Returns the plaintext code (handed to the APK via deep link).
 */
function createLoginCode(user) {
  _load();
  const norm = _normalizeUser(user);
  if (!norm.discordUserId) throw new Error('createLoginCode requires discordUserId');
  const code = randomLoginCode();
  state.codes[sha256(code)] = {
    ...norm,
    createdAtMs: nowMs(),
    expiresAtMs: nowMs() + LOGIN_CODE_TTL_SEC * 1000,
    usedAt: null,
  };
  _persist();
  return { code, expiresInSeconds: LOGIN_CODE_TTL_SEC };
}

/**
 * Consume a one-time login code. Returns the bound user once, then the code
 * is invalidated. Returns null if missing/expired/already used.
 */
function consumeLoginCode(code) {
  _load();
  if (!code) return null;
  const key = sha256(code);
  const row = state.codes[key];
  if (!row) return null;
  if (row.usedAt || (row.expiresAtMs && row.expiresAtMs < nowMs())) {
    delete state.codes[key];
    _persist();
    return null;
  }
  delete state.codes[key];
  _persist();
  return {
    discordUserId: row.discordUserId,
    siteUserId: row.siteUserId,
    username: row.username,
    avatar: row.avatar,
  };
}

/**
 * Create a long-lived APK session token bound to the Discord user.
 * Returns the plaintext token once.
 */
function createSession(user, deviceName) {
  _load();
  const norm = _normalizeUser(user);
  if (!norm.discordUserId) throw new Error('createSession requires discordUserId');
  const token = randomToken(32);
  state.sessions[sha256(token)] = {
    ...norm,
    deviceName: deviceName ? String(deviceName).slice(0, 64) : null,
    createdAtMs: nowMs(),
    expiresAtMs: nowMs() + SESSION_TTL_SEC * 1000,
    lastUsedAtMs: nowMs(),
    revokedAt: null,
  };
  _pruneExpired();
  _persist();
  return {
    token,
    expiresAt: new Date(nowMs() + SESSION_TTL_SEC * 1000).toISOString(),
    expiresInSeconds: SESSION_TTL_SEC,
  };
}

/**
 * Resolve an APK session by its bearer token. Touches lastUsed on success.
 * Returns the bound user (never another user's data) or null.
 */
function resolveSession(token) {
  _load();
  if (!token) return null;
  const key = sha256(token);
  const row = state.sessions[key];
  if (!row) return null;
  if (row.revokedAt || (row.expiresAtMs && row.expiresAtMs < nowMs())) {
    delete state.sessions[key];
    _persist();
    return null;
  }
  row.lastUsedAtMs = nowMs();
  // lastUsed touch is best-effort; persist lazily to avoid write amplification.
  return {
    discordUserId: row.discordUserId,
    siteUserId: row.siteUserId,
    username: row.username,
    avatar: row.avatar,
    deviceName: row.deviceName,
    expiresAt: new Date(row.expiresAtMs).toISOString(),
  };
}

function revokeSession(token) {
  _load();
  if (!token) return false;
  const key = sha256(token);
  if (!state.sessions[key]) return false;
  delete state.sessions[key];
  _persist();
  return true;
}

/** Record the last applied sync cursor for a (user, dataset). */
function setAck(discordUserId, dataset, cursor) {
  _load();
  const uid = String(discordUserId);
  if (!state.acks[uid]) state.acks[uid] = {};
  state.acks[uid][String(dataset)] = { cursor: cursor != null ? String(cursor) : null, at: new Date().toISOString() };
  _persist();
  return true;
}

function getAck(discordUserId, dataset) {
  _load();
  const uid = String(discordUserId);
  return (state.acks[uid] && state.acks[uid][String(dataset)]) || null;
}

function _reset() {
  state = _defaultFile();
  loaded = true;
}

module.exports = {
  STORE_PATH,
  LOGIN_CODE_TTL_SEC,
  SESSION_TTL_SEC,
  createLoginCode,
  consumeLoginCode,
  createSession,
  resolveSession,
  revokeSession,
  setAck,
  getAck,
  sha256,
  _reset,
};
