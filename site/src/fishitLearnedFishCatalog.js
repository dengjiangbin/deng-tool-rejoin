'use strict';
/**
 * Persistent learned fish catalog (BLOCKER10M).
 * itemId -> fish name from catch-delta and other high-confidence sources.
 * Separate from image asset list and from manual/seed confirmed catalog.
 */

const path = require('path');
const fs = require('fs');

const STORE_PATH = process.env.FISHIT_LEARNED_FISH_CATALOG_PATH
  || path.join(__dirname, '..', 'data', 'fishit_learned_fish_catalog.json');

const HIGH_CONFIDENCE_SOURCES = new Set([
  'catch_delta_high_confidence',
  'manual_confirmed',
  'seed_confirmed',
]);

let _store = null;

function _defaultStore() {
  return { updatedAt: null, byItemId: {} };
}

function _load() {
  if (_store) return _store;
  try {
    if (fs.existsSync(STORE_PATH)) {
      const raw = JSON.parse(fs.readFileSync(STORE_PATH, 'utf8'));
      _store = {
        updatedAt: raw.updatedAt || null,
        byItemId: (raw.byItemId && typeof raw.byItemId === 'object') ? raw.byItemId : {},
      };
      return _store;
    }
  } catch (_) { /* fall through */ }
  _store = _defaultStore();
  return _store;
}

function _persist() {
  const dir = path.dirname(STORE_PATH);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(STORE_PATH, JSON.stringify(_store, null, 2), 'utf8');
}

function sanitiseItemId(raw) {
  const id = String(raw || '').trim();
  return /^\d+$/.test(id) ? id : null;
}

function isHighConfidence(entry) {
  if (!entry) return false;
  if (entry.confidence === 1 || entry.confidence === 1.0) return true;
  return HIGH_CONFIDENCE_SOURCES.has(entry.source);
}

function publicEligible(entry) {
  return !!(entry
    && entry.category === 'fish'
    && isHighConfidence(entry)
    && entry.name
    && !/^Item #\d+$/i.test(entry.name));
}

/**
 * Ingest one learned mapping. Never overwrites a different confirmed name.
 */
function ingestEntry(raw, mainCatalogLookup) {
  const itemId = sanitiseItemId(raw && raw.itemId);
  if (!itemId) return { updated: false, reason: 'invalid_id' };

  const name = typeof raw.name === 'string' ? raw.name.trim().slice(0, 100) : '';
  if (!name) return { updated: false, reason: 'empty_name' };

  const category = typeof raw.category === 'string' ? raw.category.trim().toLowerCase() : 'fish';
  const source = typeof raw.source === 'string' ? raw.source.slice(0, 80) : 'unknown';
  const confidence = Number.isFinite(Number(raw.confidence)) ? Number(raw.confidence) : null;
  const proof = (raw.proof && typeof raw.proof === 'object') ? raw.proof : null;
  const now = new Date().toISOString();

  _load();
  const existing = _store.byItemId[itemId];

  if (mainCatalogLookup) {
    const main = mainCatalogLookup(itemId);
    if (main && main.name && main.name !== name) {
      const mainHigh = main.source === 'seed_confirmed' || main.source === 'manual_confirmed'
        || main.confidence === 'confirmed';
      if (mainHigh) return { updated: false, reason: 'main_catalog_differs', itemId, existingName: main.name };
      if (main.category && main.category !== 'fish' && category === 'fish') {
        return { updated: false, reason: 'main_non_fish_protected', itemId, existingName: main.name };
      }
    }
  }

  if (existing && existing.name && existing.name !== name) {
    const exHigh = isHighConfidence(existing);
    const inHigh = isHighConfidence({ source, confidence });
    if (exHigh && !inHigh) return { updated: false, reason: 'existing_higher_confidence', itemId };
    if (exHigh && inHigh) return { updated: false, reason: 'name_conflict', itemId };
  }

  const entry = {
    itemId,
    name,
    category: category === 'fish' ? 'fish' : category,
    source,
    confidence: confidence != null ? confidence : (isHighConfidence({ source }) ? 1 : 0.3),
    proof,
    updatedAt: now,
    publicEligible: false,
  };
  entry.publicEligible = publicEligible(entry);

  const wasNew = !existing;
  _store.byItemId[itemId] = entry;
  _store.updatedAt = now;
  if (process.env.NODE_ENV !== 'test') _persist();

  return {
    updated: true,
    reason: wasNew ? 'inserted' : 'updated',
    itemId,
    entry,
    publicEligible: entry.publicEligible,
  };
}

function ingestBatch(entries, mainCatalogLookup) {
  if (!Array.isArray(entries) || entries.length === 0) return [];
  const results = [];
  for (const raw of entries.slice(0, 30)) {
    results.push(ingestEntry(raw, mainCatalogLookup));
  }
  return results;
}

function lookupById(itemId) {
  _load();
  const id = sanitiseItemId(itemId);
  if (!id) return null;
  const e = _store.byItemId[id];
  if (!e) return null;
  return { ...e, publicEligible: publicEligible(e) };
}

function getAllMappings() {
  _load();
  return Object.values(_store.byItemId).map((e) => ({
    ...e,
    publicEligible: publicEligible(e),
  }));
}

function getHighConfidenceFishIds() {
  return getAllMappings()
    .filter((e) => e.publicEligible)
    .map((e) => e.itemId);
}

function _reset() {
  _store = _defaultStore();
  try {
    if (fs.existsSync(STORE_PATH)) fs.unlinkSync(STORE_PATH);
  } catch (_) { /* ignore */ }
}

module.exports = {
  STORE_PATH,
  HIGH_CONFIDENCE_SOURCES,
  ingestEntry,
  ingestBatch,
  lookupById,
  getAllMappings,
  getHighConfidenceFishIds,
  isHighConfidence,
  publicEligible,
  _reset,
};
