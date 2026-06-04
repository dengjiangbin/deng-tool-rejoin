'use strict';
/**
 * Fish It catalog store.
 *
 * The Lua tracker recursively scans ReplicatedStorage and POSTs a
 * `fish_catalog_snapshot` to /api/tracker/update-catalog. This module keeps
 * that catalog (normalized fish/rod/item metadata: real name, tier/rarity and
 * image URL) in memory AND persists it to site/data/fishit_catalog.json so the
 * website can render real images + tiers even across restarts and while a
 * player is offline.
 *
 * Nothing is invented: entries only ever come from the scanned game data
 * (or, as a fallback, the existing Fish It DB image resolver). Stat/UI labels
 * such as "Caught" or "Rarest Fish" are never stored as catalog entries.
 */

const path = require('path');
const fs = require('fs');

const STORE_PATH = process.env.FISHIT_CATALOG_PATH
  || path.join(__dirname, '..', 'data', 'fishit_catalog.json');

// Stat/UI labels that must never be treated as a real fish/item.
const STAT_LABEL_DENYLIST = new Set([
  'caught', 'rarest fish', 'total', 'total fish', 'fish', 'weight',
  'search', 'inventory', 'owned', 'best catch', 'rarity', 'tier',
  'amount', 'count', 'value', 'oldest', 'newest', 'all', 'sort',
  'filter', 'equip', 'equipped', 'use', 'sell', 'buy', 'lock', 'unlock',
  'backpack', 'collection', 'bag', 'myfish', 'myitems', 'none',
  'weight (kg)', 'max weight', 'total weight', 'close', 'back', 'next',
  'prev', 'previous', 'page', 'tab', 'menu', 'stats', 'info', 'profile',
  'shop', 'store', 'trade', 'donate', 'rank', 'level', 'exp', 'coins',
  'cash', 'gold', 'gems', 'ok', 'yes', 'no', 'cancel', 'confirm', 'submit',
  'reset', 'settings', 'options', 'help', 'credits', 'about', 'exit', 'quit',
  'leave', 'loading', 'please wait', 'equipped rod', 'current rod',
  'best', 'item', 'items',
]);

const TIER_NORMALIZE = {
  common: 'common', uncommon: 'uncommon', rare: 'rare', epic: 'epic',
  legendary: 'legend', legend: 'legend', mythic: 'epic', mythical: 'epic',
  secret: 'secret', forgotten: 'forgotten', special: 'rare', ultra: 'epic',
};

function normalizeName(raw) {
  return String(raw || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

function normalizeTier(raw) {
  if (!raw) return null;
  const t = String(raw).trim().toLowerCase();
  return TIER_NORMALIZE[t] || t || null;
}

function isStatLabel(name) {
  const n = normalizeName(name);
  if (n.length <= 2) return true;
  return STAT_LABEL_DENYLIST.has(n);
}

function isPlaceholderItemName(name, itemId) {
  if (!name) return true;
  const s = String(name).trim();
  if (/^Item #\d+$/.test(s)) return true;
  if (itemId != null && s === `Item #${itemId}`) return true;
  return false;
}

function isFishCategory(category) {
  return String(category || '').toLowerCase() === 'fish';
}

/** Confirmed itemId mappings from prior successful tracker/site output (BLOCKER10H). */
const KNOWN_ID_SEEDS = [
  { itemId: '117', name: 'Bandit Angelfish', category: 'fish', source: 'seed_confirmed', confidence: 'confirmed' },
  { itemId: '119', name: 'Ballina Angelfish', category: 'fish', source: 'seed_confirmed', confidence: 'confirmed' },
  { itemId: '68', name: 'Flame Angelfish', category: 'fish', source: 'seed_confirmed', confidence: 'confirmed' },
  { itemId: '71', name: 'Darwin Clownfish', category: 'fish', source: 'seed_confirmed', confidence: 'confirmed' },
  { itemId: '70', name: 'Yello Damselfish', category: 'fish', source: 'seed_confirmed', confidence: 'confirmed' },
  { itemId: '10', name: 'Topwater Bait', category: 'bait', source: 'seed_confirmed', confidence: 'confirmed' },
  { itemId: '990', name: 'Common Crate', category: 'items', source: 'seed_confirmed', confidence: 'confirmed' },
  { itemId: '388', name: 'Carbon Rod', category: 'rod', source: 'seed_confirmed', confidence: 'confirmed' },
];

function isHttpUrl(u) {
  return typeof u === 'string' && /^https?:\/\//i.test(u.trim());
}

// ── In-memory catalog: normalizedKey -> { name, key, tier, imageUrl, source }
// idIndex: numeric/string item id -> normalizedKey (BLOCKER9)
let _catalog = null;
let _idIndex = null;

function _emptyCatalog() {
  return { entries: {}, updatedAt: null, counts: { fish: 0, rods: 0, items: 0 }, seeded: false };
}

function _rebuildIdIndex() {
  _idIndex = {};
  if (!_catalog || !_catalog.entries) return;
  for (const [key, e] of Object.entries(_catalog.entries)) {
    if (e && e.itemId && String(e.itemId).match(/^\d+$/)) {
      _idIndex[String(e.itemId)] = key;
    }
  }
}

function _load() {
  if (_catalog) return _catalog;
  _catalog = _emptyCatalog();
  _idIndex = {};
  try {
    if (fs.existsSync(STORE_PATH)) {
      const parsed = JSON.parse(fs.readFileSync(STORE_PATH, 'utf8'));
      if (parsed && parsed.entries && typeof parsed.entries === 'object') {
        _catalog = {
          entries: parsed.entries,
          updatedAt: parsed.updatedAt || null,
          counts: parsed.counts || _emptyCatalog().counts,
          seeded: parsed.seeded === true,
        };
      }
    }
  } catch (err) {
    console.warn('[fishit-catalog] load failed:', err && err.message ? err.message : err);
    _catalog = _emptyCatalog();
  }
  _rebuildIdIndex();
  if (!_catalog.seeded) {
    let seeded = 0;
    for (const raw of KNOWN_ID_SEEDS) {
      const r = upsertByItemIdCore(raw);
      if (r.updated) seeded += 1;
    }
    _catalog.seeded = true;
    if (seeded > 0) _persist();
  }
  return _catalog;
}

/** Seed confirmed itemId→name mappings once (test seam). */
function seedKnownMappings() {
  _load();
  return { seeded: _catalog.seeded };
}

function _persist() {
  try {
    const dir = path.dirname(STORE_PATH);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    const tmp = `${STORE_PATH}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(_catalog), 'utf8');
    fs.renameSync(tmp, STORE_PATH); // atomic replace
  } catch (err) {
    console.warn('[fishit-catalog] persist failed:', err && err.message ? err.message : err);
  }
}

/**
 * Merge a scanned catalog snapshot into the persistent store.
 * @param {object} snapshot { catalog: { fish:[], rods:[], items:[] } }
 * @returns {object} summary counts
 */
function ingestSnapshot(snapshot) {
  _load();
  const cat = (snapshot && snapshot.catalog) || {};
  const groups = [
    ['fish', cat.fish],
    ['rods', cat.rods],
    ['items', cat.items],
  ];

  let added = 0;
  let enriched = 0;

  for (const [category, list] of groups) {
    if (!Array.isArray(list)) continue;
    for (const raw of list.slice(0, 5000)) {
      const name = typeof raw.name === 'string' ? raw.name.trim() : '';
      if (!name || isStatLabel(name)) continue;
      const itemId = (typeof raw.itemId === 'string' || typeof raw.itemId === 'number')
        ? String(raw.itemId).trim().slice(0, 40) : null;
      if (isPlaceholderItemName(name, itemId)) continue;
      const key = (typeof raw.key === 'string' && raw.key) ? normalizeName(raw.key) : normalizeName(name);
      if (!key || isStatLabel(key)) continue;

      const tier = normalizeTier(raw.tier);
      const imageUrl = isHttpUrl(raw.imageUrl) ? raw.imageUrl.trim().slice(0, 300) : null;

      const existing = _catalog.entries[key];
      if (!existing) {
        _catalog.entries[key] = {
          name: name.slice(0, 100),
          key,
          tier: tier && tier !== 'unknown' ? tier : null,
          imageUrl,
          category,
          itemId: itemId && itemId.match(/^\d+$/) ? itemId : null,
          source: typeof raw.source === 'string' ? raw.source.slice(0, 120) : null,
          confidence: typeof raw.confidence === 'string' ? raw.confidence.slice(0, 40) : null,
          updatedAt: new Date().toISOString(),
        };
        added += 1;
      } else {
        // Enrich: never overwrite good data with empty data.
        let changed = false;
        if ((!existing.tier || existing.tier === 'unknown') && tier && tier !== 'unknown') {
          existing.tier = tier; changed = true;
        }
        if (!existing.imageUrl && imageUrl) { existing.imageUrl = imageUrl; changed = true; }
        if (!existing.itemId && itemId && itemId.match(/^\d+$/)) { existing.itemId = itemId; changed = true; }
        if (changed) enriched += 1;
      }
      if (itemId && itemId.match(/^\d+$/)) _idIndex[itemId] = key;
    }
  }

  // Recount by category.
  const counts = { fish: 0, rods: 0, items: 0 };
  for (const e of Object.values(_catalog.entries)) {
    if (e.category === 'rods' || e.category === 'rod') counts.rods += 1;
    else if (e.category === 'items') counts.items += 1;
    else counts.fish += 1;
  }
  _catalog.counts = counts;
  _catalog.updatedAt = new Date().toISOString();
  _persist();

  return { added, enriched, total: Object.keys(_catalog.entries).length, counts };
}

/** Look up catalog metadata for a name, or null. */
function lookup(name) {
  _load();
  const key = normalizeName(name);
  return _catalog.entries[key] || null;
}

/** Look up catalog metadata by numeric item id (BLOCKER9). */
function lookupById(itemId) {
  _load();
  if (itemId == null) return null;
  const id = String(itemId).trim();
  if (!id.match(/^\d+$/)) return null;
  const key = _idIndex[id];
  return key ? (_catalog.entries[key] || null) : null;
}

/**
 * Upsert catalog metadata by item id (BLOCKER10G/10H).
 * Real name beats placeholder; fish category is never overwritten by item category.
 */
function upsertByItemIdCore(raw) {
  if (!raw || raw.itemId == null) return { updated: false, reason: 'missing_id' };
  const itemId = String(raw.itemId).trim();
  if (!itemId.match(/^\d+$/)) return { updated: false, reason: 'invalid_id' };
  const name = typeof raw.name === 'string' ? raw.name.trim() : '';
  if (!name || isStatLabel(name) || isPlaceholderItemName(name, itemId)) {
    return { updated: false, reason: 'placeholder_or_empty' };
  }
  const category = typeof raw.category === 'string' ? raw.category.trim().slice(0, 40) : 'items';
  const tier = normalizeTier(raw.tier);
  const source = typeof raw.source === 'string' ? raw.source.slice(0, 120) : 'catalog_cache';
  const confidence = typeof raw.confidence === 'string' ? raw.confidence.slice(0, 40) : 'tracker';
  const now = new Date().toISOString();
  const key = normalizeName(name);
  if (!key || isStatLabel(key)) return { updated: false, reason: 'invalid_key' };

  const existingKey = _idIndex[itemId];
  const existing = existingKey ? _catalog.entries[existingKey] : null;
  if (existing) {
    if (isPlaceholderItemName(existing.name, itemId)) {
      existing.name = name.slice(0, 100);
      existing.key = key;
      existing.category = category;
      if (tier && tier !== 'unknown') existing.tier = tier;
      existing.source = source;
      existing.confidence = confidence;
      existing.itemId = itemId;
      existing.updatedAt = now;
      _catalog.updatedAt = now;
      _persist();
      return { updated: true, reason: 'replaced_placeholder' };
    }
    if (isFishCategory(existing.category) && !isFishCategory(category)) {
      return { updated: false, reason: 'fish_category_protected' };
    }
    if (existing.name === name) return { updated: false, reason: 'unchanged' };
    return { updated: false, reason: 'real_name_exists' };
  }

  _catalog.entries[key] = {
    name: name.slice(0, 100),
    key,
    tier: tier && tier !== 'unknown' ? tier : null,
    imageUrl: null,
    category,
    itemId,
    source,
    confidence,
    updatedAt: now,
  };
  _idIndex[itemId] = key;
  const counts = { fish: 0, rods: 0, items: 0 };
  for (const e of Object.values(_catalog.entries)) {
    if (e.category === 'rods' || e.category === 'rod') counts.rods += 1;
    else if (e.category === 'items') counts.items += 1;
    else counts.fish += 1;
  }
  _catalog.counts = counts;
  _catalog.updatedAt = now;
  _persist();
  return { updated: true, reason: 'inserted' };
}

function upsertByItemId(raw) {
  _load();
  return upsertByItemIdCore(raw);
}

/** Learn real names from tracker uploads; placeholders never enter catalog. */
function learnFromTrackerItems(items) {
  if (!Array.isArray(items) || items.length === 0) return { learned: 0 };
  let learned = 0;
  for (const it of items.slice(0, 300)) {
    if (!it || it.itemId == null) continue;
    const name = typeof it.name === 'string' ? it.name.trim() : '';
    if (!name || isPlaceholderItemName(name, it.itemId) || isStatLabel(name)) continue;
    const r = upsertByItemId({
      itemId: it.itemId,
      name,
      category: it.category || 'items',
      tier: it.rarity || it.tier,
      source: 'tracker_upload',
      confidence: 'tracker',
    });
    if (r.updated) learned += 1;
  }
  return { learned };
}

/** Catalog metadata for an itemId (debug/display). */
function catalogMetaForItemId(itemId) {
  const meta = lookupById(itemId);
  if (!meta) return null;
  return {
    itemId: String(itemId),
    name: meta.name,
    category: meta.category || null,
    source: meta.source || null,
    confidence: meta.confidence || null,
    updatedAt: meta.updatedAt || null,
  };
}

/** Return the full catalog (for /api/fishit-tracker/catalog and tests). */
function getCatalog() {
  _load();
  return {
    updatedAt: _catalog.updatedAt,
    counts: _catalog.counts,
    entries: _catalog.entries,
  };
}

/** Test seam. */
function _reset() { _catalog = null; _idIndex = null; }

module.exports = {
  STORE_PATH,
  STAT_LABEL_DENYLIST,
  KNOWN_ID_SEEDS,
  ingestSnapshot,
  lookup,
  lookupById,
  upsertByItemId,
  seedKnownMappings,
  learnFromTrackerItems,
  catalogMetaForItemId,
  getCatalog,
  normalizeName,
  normalizeTier,
  isStatLabel,
  isPlaceholderItemName,
  isFishCategory,
  _reset,
};
