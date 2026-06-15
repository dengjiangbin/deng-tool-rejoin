'use strict';
/**
 * Short-lived Discord OAuth state store (file-backed in production).
 * Avoids blocking file session writes before redirect to Discord and survives
 * PM2 restarts / cross-subdomain login without session cookie split.
 */

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const os = require('os');

const TTL_MS = Number(process.env.OAUTH_STATE_TTL_MS || 15 * 60 * 1000);
const MEMORY_ONLY = process.env.NODE_ENV === 'test' || process.env.OAUTH_STATE_MEMORY === '1';

const STORE_DIR = process.env.OAUTH_STATE_DIR
  || path.join(process.env.TOOL_SITE_SESSION_DIR || path.join(os.tmpdir(), 'deng-tool-site-sessions'), '..', 'oauth-states');

const states = new Map();

function stateFileKey(state) {
  return crypto.createHash('sha256').update(String(state)).digest('hex');
}

function stateFilePath(state) {
  return path.join(STORE_DIR, `${stateFileKey(state)}.json`);
}

function ensureStoreDir() {
  fs.mkdirSync(STORE_DIR, { recursive: true });
}

function writeStateFile(state, row) {
  ensureStoreDir();
  const file = stateFilePath(state);
  const tmp = `${file}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(row), { encoding: 'utf8', mode: 0o600 });
  try {
    fs.renameSync(tmp, file);
  } catch (err) {
    try {
      fs.copyFileSync(tmp, file);
    } finally {
      fs.unlinkSync(tmp);
    }
    if (err && err.code !== 'EBUSY' && err.code !== 'EPERM') throw err;
  }
}

function readStateFile(state) {
  try {
    const text = fs.readFileSync(stateFilePath(state), 'utf8');
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function deleteStateFile(state) {
  try {
    fs.unlinkSync(stateFilePath(state));
  } catch (err) {
    if (err && err.code !== 'ENOENT') {
      console.warn('[oauth-state] delete failed:', err.message);
    }
  }
}

function pruneExpiredMemory() {
  const now = Date.now();
  for (const [key, row] of states) {
    if (!row || row.expiresAtMs <= now) states.delete(key);
  }
}

function pruneExpiredFiles() {
  if (MEMORY_ONLY) return;
  try {
    ensureStoreDir();
    const now = Date.now();
    for (const name of fs.readdirSync(STORE_DIR)) {
      if (!name.endsWith('.json')) continue;
      const full = path.join(STORE_DIR, name);
      try {
        const row = JSON.parse(fs.readFileSync(full, 'utf8'));
        if (!row || !row.expiresAtMs || row.expiresAtMs <= now) fs.unlinkSync(full);
      } catch {
        fs.unlinkSync(full);
      }
    }
  } catch (err) {
    console.warn('[oauth-state] prune failed:', err.message);
  }
}

/**
 * @param {object} payload
 * @param {string} payload.redirectUri
 * @param {string} [payload.returnPublicUrl]
 * @param {boolean} [payload.oauthApkReturn]
 * @param {string} [payload.authReturnTo]
 * @returns {string} state nonce
 */
function createOAuthState(payload) {
  pruneExpiredMemory();
  pruneExpiredFiles();
  const state = crypto.randomBytes(24).toString('hex');
  const row = {
    redirectUri: String(payload.redirectUri || ''),
    returnPublicUrl: String(payload.returnPublicUrl || '').replace(/\/+$/, ''),
    oauthApkReturn: payload.oauthApkReturn === true,
    authReturnTo: payload.authReturnTo || '/dashboard',
    mobileTransactionId: payload.mobileTransactionId ? String(payload.mobileTransactionId) : null,
    expiresAtMs: Date.now() + TTL_MS,
  };
  if (MEMORY_ONLY) {
    states.set(state, row);
  } else {
    writeStateFile(state, row);
  }
  return state;
}

/**
 * @param {string} state
 * @returns {object|null}
 */
function consumeOAuthState(state) {
  if (!state) return null;
  const key = String(state);
  if (MEMORY_ONLY) {
    pruneExpiredMemory();
    const row = states.get(key);
    states.delete(key);
    if (!row || row.expiresAtMs <= Date.now()) return null;
    return row;
  }
  const row = readStateFile(key);
  deleteStateFile(key);
  if (!row || row.expiresAtMs <= Date.now()) return null;
  return row;
}

function pruneExpired() {
  pruneExpiredMemory();
  pruneExpiredFiles();
}

module.exports = {
  createOAuthState,
  consumeOAuthState,
  pruneExpired,
  STORE_DIR,
};
