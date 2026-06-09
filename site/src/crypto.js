'use strict';
/**
 * Cryptographic utilities for challenge signing and hashing.
 * All HMAC operations are constant-time to prevent timing attacks.
 */
const crypto = require('crypto');

function challengeSecret() {
  return process.env.TOOL_SITE_STATE_SECRET || '';
}

function isStateSecretConfigured() {
  const secret = challengeSecret();
  return Boolean(secret && secret.length >= 32);
}

function assertStateSecretConfigured() {
  if (!isStateSecretConfigured()) {
    const err = new Error('TOOL_SITE_STATE_SECRET must be at least 32 characters');
    err.code = 'STATE_SECRET_NOT_CONFIGURED';
    throw err;
  }
}

/**
 * Sign a challenge token.
 * Format: base64url(JSON payload) + "." + hex(HMAC-SHA256)
 *
 * @param {string} challengeId  - UUID from license_ad_challenges
 * @param {string|null} provider - 'lootlabs' | 'linkvertise' | null
 * @param {number} expiresAt     - Unix timestamp ms
 * @returns {string} signed token
 */
function signChallenge(challengeId, provider, expiresAt) {
  assertStateSecretConfigured();
  const secret = challengeSecret();
  const payload = Buffer.from(
    JSON.stringify({ cid: challengeId, p: provider || '', exp: expiresAt }),
  ).toString('base64url');
  const sig = crypto
    .createHmac('sha256', secret)
    .update(payload)
    .digest('hex');
  return `${payload}.${sig}`;
}

/**
 * Verify and decode a signed challenge token.
 *
 * @param {string} token
 * @returns {{ cid: string, p: string, exp: number } | null}
 */
function verifyChallenge(token) {
  assertStateSecretConfigured();
  const secret = challengeSecret();
  if (!token || typeof token !== 'string') return null;
  const dot = token.indexOf('.');
  if (dot < 1 || dot === token.length - 1) return null;
  const payload = token.slice(0, dot);
  const sig     = token.slice(dot + 1);

  let expectedBuf;
  try {
    const expectedHex = crypto
      .createHmac('sha256', secret)
      .update(payload)
      .digest('hex');
    expectedBuf = Buffer.from(expectedHex, 'hex');
  } catch {
    return null;
  }

  let sigBuf;
  try {
    sigBuf = Buffer.from(sig, 'hex');
  } catch {
    return null;
  }

  if (sigBuf.length !== expectedBuf.length) return null;
  if (!crypto.timingSafeEqual(sigBuf, expectedBuf)) return null;

  try {
    const data = JSON.parse(Buffer.from(payload, 'base64url').toString());
    if (!data.cid || typeof data.exp !== 'number') return null;
    if (Date.now() > data.exp) return null; // expired
    return data;
  } catch {
    return null;
  }
}

/**
 * SHA-256 hex digest of any string.
 */
function sha256(str) {
  return crypto.createHash('sha256').update(str).digest('hex');
}

/**
 * Cryptographically random hex string of <bytes> length.
 */
function randomHex(bytes = 16) {
  return crypto.randomBytes(bytes).toString('hex');
}

module.exports = { signChallenge, verifyChallenge, sha256, randomHex, isStateSecretConfigured, assertStateSecretConfigured };
