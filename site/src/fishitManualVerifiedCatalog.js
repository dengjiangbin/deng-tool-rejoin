'use strict';
/**
 * BLOCKER10U3-U4 — owner-verified itemId→fish mappings for unresolved numeric rows.
 */

const path = require('path');
const fs = require('fs');

const MANUAL_PATH = process.env.FISHIT_MANUAL_VERIFIED_CATALOG_PATH
  || path.join(__dirname, '..', 'data', 'fishit_manual_verified_catalog.json');

let _rows = null;

function _load() {
  if (_rows) return _rows;
  try {
    if (!fs.existsSync(MANUAL_PATH)) {
      _rows = [];
      return _rows;
    }
    const raw = JSON.parse(fs.readFileSync(MANUAL_PATH, 'utf8'));
    _rows = Array.isArray(raw) ? raw : (Array.isArray(raw.entries) ? raw.entries : []);
  } catch (err) {
    console.warn('[fishit] manual verified catalog load failed:', err && err.message ? err.message : err);
    _rows = [];
  }
  return _rows;
}

function getAll() {
  return _load().slice();
}

function lookupByItemId(itemId) {
  const id = String(itemId || '').trim();
  if (!id) return null;
  return _load().find((r) => String(r.itemId).trim() === id) || null;
}

function getCount() {
  return _load().length;
}

function _reset() {
  _rows = null;
}

module.exports = {
  MANUAL_PATH,
  getAll,
  lookupByItemId,
  getCount,
  _reset,
  _load,
};
