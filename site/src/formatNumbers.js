'use strict';
/**
 * Exact integer formatting for Fish It stats and download counts.
 * No K/M/B compact notation — always full numbers with thousands separators.
 */

/** @param {number|string|null|undefined} n */
function formatExact(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '0';
  return Math.round(v).toLocaleString('en-US');
}

module.exports = { formatExact };
