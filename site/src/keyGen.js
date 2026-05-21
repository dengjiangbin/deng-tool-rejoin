'use strict';
const crypto = require('crypto');

/**
 * Generate a DENG-XXXX-XXXX-XXXX-XXXX format key.
 * Each group is 4 uppercase hex characters (2 random bytes).
 *
 * @returns {{ raw: string, id: string, prefix: string, suffix: string }}
 *   raw    – the plaintext key (shown once to user, never stored)
 *   id     – SHA-256 hex digest (stored in license_keys.id)
 *   prefix – first two groups (e.g. "DENG-1A2B-3C4D")
 *   suffix – last two groups  (e.g. "5E6F-7A8B")
 */
function generateDengKey() {
  const groups = [];
  for (let i = 0; i < 4; i++) {
    groups.push(crypto.randomBytes(2).toString('hex').toUpperCase());
  }
  const raw    = `DENG-${groups.join('-')}`;
  const id     = crypto.createHash('sha256').update(raw).digest('hex');
  const prefix = `DENG-${groups[0]}-${groups[1]}`;
  const suffix = `${groups[2]}-${groups[3]}`;
  return { raw, id, prefix, suffix };
}

module.exports = { generateDengKey };
