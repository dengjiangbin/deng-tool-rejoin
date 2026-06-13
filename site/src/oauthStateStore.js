'use strict';
/**
 * Short-lived Discord OAuth state store (in-memory).
 * Avoids blocking file session writes before redirect to Discord and survives
 * cross-subdomain login (aio start → tool callback) without session cookie split.
 */

const crypto = require('crypto');

const TTL_MS = Number(process.env.OAUTH_STATE_TTL_MS || 15 * 60 * 1000);
const states = new Map();

function pruneExpired() {
  const now = Date.now();
  for (const [key, row] of states) {
    if (!row || row.expiresAtMs <= now) states.delete(key);
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
  pruneExpired();
  const state = crypto.randomBytes(24).toString('hex');
  states.set(state, {
    redirectUri: String(payload.redirectUri || ''),
    returnPublicUrl: String(payload.returnPublicUrl || '').replace(/\/+$/, ''),
    oauthApkReturn: payload.oauthApkReturn === true,
    authReturnTo: payload.authReturnTo || '/dashboard',
    expiresAtMs: Date.now() + TTL_MS,
  });
  return state;
}

/**
 * @param {string} state
 * @returns {object|null}
 */
function consumeOAuthState(state) {
  if (!state) return null;
  pruneExpired();
  const key = String(state);
  const row = states.get(key);
  states.delete(key);
  if (!row || row.expiresAtMs <= Date.now()) return null;
  return row;
}

module.exports = {
  createOAuthState,
  consumeOAuthState,
  pruneExpired,
};
