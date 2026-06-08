'use strict';
/**
 * BLOCKER10U5 — base fish names that must never be mutation-stripped.
 * Giant Squid, manual verified catalog entries, and other authoritative names.
 */

const manualVerified = require('./fishitManualVerifiedCatalog');

const HARDCODED_PROTECTED = [
  'Giant Squid',
  'Radiant Catfish',
  'Zebra Snakehead',
];

let _set = null;

function _buildSet() {
  if (_set) return _set;
  const set = new Set(HARDCODED_PROTECTED.map((n) => n.toLowerCase()));
  for (const row of manualVerified.getAll()) {
    if (row.baseFishName) set.add(String(row.baseFishName).trim().toLowerCase());
    if (row.name) set.add(String(row.name).trim().toLowerCase());
  }
  _set = set;
  return _set;
}

function isProtectedBaseName(name) {
  const s = String(name || '').trim();
  if (!s) return false;
  return _buildSet().has(s.toLowerCase());
}

function normalizeProtected(name) {
  return String(name || '').trim();
}

function _reset() {
  _set = null;
}

module.exports = {
  HARDCODED_PROTECTED,
  isProtectedBaseName,
  normalizeProtected,
  _reset,
};
