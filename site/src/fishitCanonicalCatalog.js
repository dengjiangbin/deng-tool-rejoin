'use strict';
/**
 * BLOCKER10U2 — unified fish image/rarity catalog from every project source.
 */

const path = require('path');
const fs = require('fs');
const fishImageAssets = require('./fishitFishImageAssets');
const catchNameParser = require('./fishitCatchNameParser');
const catalogStore = require('./fishitCatalogStore');
const fishCatalog = require('./fishitFishCatalog');

const CANONICAL_PATH = process.env.FISHIT_CANONICAL_CATALOG_PATH
  || path.join(__dirname, '..', 'data', 'fishit_canonical_catalog.json');

const DATA_DIR = path.join(__dirname, '..', 'data');

const SOURCE_FILES = [
  { id: 'fishit_fish_image_assets', path: 'fishit_fish_image_assets.json', type: 'name_asset' },
  { id: 'fishit_image_assets', path: 'fishit_image_assets.json', type: 'name_url_map' },
  { id: 'fishit_fish_confirmed_catalog', path: 'fishit_fish_confirmed_catalog.json', type: 'item_list' },
  { id: 'fishit_catalog', path: 'fishit_catalog.json', type: 'catalog_store' },
  { id: 'fishit_learned_fish_catalog', path: 'fishit_learned_fish_catalog.json', type: 'learned' },
  { id: 'fishit_global_fish_item_catalog', path: 'fishit_global_fish_item_catalog.json', type: 'global' },
  { id: 'fish_image_cache_index', path: path.join('fish_image_cache', 'index.json'), type: 'image_cache' },
];

let _store = null;
let _audit = null;

function normName(raw) {
  return fishImageAssets.normalizeName(raw);
}

function normKey(raw) {
  return fishImageAssets.normalizeNamePunct(raw) || normName(raw);
}

function sanitiseAssetId(raw) {
  const id = String(raw || '').trim();
  return /^\d{10,22}$/.test(id) ? id : null;
}

function isHttpUrl(raw) {
  return typeof raw === 'string' && /^https?:\/\//i.test(raw.trim());
}

function emptyStore() {
  return {
    updatedAt: null,
    sourcesSearched: [],
    byItemId: {},
    byName: {},
    importRows: [],
  };
}

function _readJson(relPath) {
  const full = path.isAbsolute(relPath) ? relPath : path.join(DATA_DIR, relPath);
  try {
    if (!fs.existsSync(full)) return { path: full, data: null, exists: false };
    return { path: full, data: JSON.parse(fs.readFileSync(full, 'utf8')), exists: true };
  } catch (err) {
    return { path: full, data: null, exists: true, error: err.message };
  }
}

function _ensureEntry(map, itemId, baseFishName) {
  const id = itemId ? String(itemId).trim() : null;
  const base = baseFishName ? String(baseFishName).trim() : null;
  if (!id && !base) return null;
  const key = id || `name:${normKey(base)}`;
  if (!map[key]) {
    map[key] = {
      itemId: id || null,
      baseFishName: base || null,
      displayName: base || null,
      rarity: null,
      raritySource: null,
      rarityConfidence: null,
      imageAssetId: null,
      imageUrl: null,
      imageSource: null,
      sourceUrl: null,
      sources: [],
      triedAliases: [],
    };
  }
  if (id && !map[key].itemId) map[key].itemId = id;
  if (base && !map[key].baseFishName) map[key].baseFishName = base;
  return map[key];
}

function _touchSource(entry, sourceId) {
  if (!entry.sources.includes(sourceId)) entry.sources.push(sourceId);
}

function _mergeField(entry, field, value, sourceId, confidence) {
  if (value == null || value === '') return;
  if (!entry[field]) {
    entry[field] = value;
    if (field.startsWith('rarity') && field === 'rarity') {
      entry.raritySource = sourceId;
      entry.rarityConfidence = confidence || 'pending';
    }
    if (field === 'imageAssetId' || field === 'imageUrl') {
      entry.imageSource = sourceId;
    }
    _touchSource(entry, sourceId);
  }
}

function _loadDbSources() {
  const out = { images: [], rarities: [], sources: [] };
  try {
    const fishitDb = require('./fishitDb');
    if (typeof fishitDb.exportImageCatalog === 'function') {
      const imgs = fishitDb.exportImageCatalog();
      out.images = imgs;
      out.sources.push({ id: 'fishit_db_image_index', count: imgs.length, type: 'db' });
    }
    if (typeof fishitDb.exportRarityHints === 'function') {
      const rar = fishitDb.exportRarityHints();
      out.rarities = rar;
      out.sources.push({ id: 'fishit_db_rarity_hints', count: rar.length, type: 'db' });
    }
  } catch (err) {
    out.sources.push({ id: 'fishit_db', error: err.message || 'load_failed' });
  }
  return out;
}

function rebuildFromAllSources({ persist = true } = {}) {
  const map = {};
  const sourcesSearched = [];
  let rowsScanned = 0;

  const upsert = (row, sourceId) => {
    const canon = catchNameParser.canonicalizeFishName(row.baseFishName || row.name || row.fishName || '');
    const base = canon.baseFishName || row.baseFishName || row.name || row.fishName;
    if (!base) return null;
    const itemId = row.itemId != null ? String(row.itemId).trim() : null;
    const entry = _ensureEntry(map, itemId && /^\d+$/.test(itemId) ? itemId : null, base);
    if (!entry) return null;
    entry.displayName = row.displayName || canon.displayName || base;
    entry.mutation = row.mutation || canon.mutation || entry.mutation || null;
    if (canon.baseFishName) entry.triedAliases = [...new Set([...(entry.triedAliases || []), base, normKey(base)])];
    _touchSource(entry, sourceId);
    rowsScanned += 1;

    const rarity = fishCatalog.normalizeRarity(row.rarity || row.tier || row.normalizedRarity);
    if (rarity) _mergeField(entry, 'rarity', rarity, sourceId, row.rarityConfidence || 'pending');

    const assetId = sanitiseAssetId(row.imageAssetId || row.assetId);
    if (assetId) _mergeField(entry, 'imageAssetId', assetId, sourceId);

    const url = isHttpUrl(row.imageUrl) ? row.imageUrl.trim() : null;
    if (url) {
      _mergeField(entry, 'imageUrl', url, sourceId);
      entry.sourceUrl = entry.sourceUrl || url;
    }
    return entry;
  };

  for (const def of SOURCE_FILES) {
    const { path: filePath, data, exists, error } = _readJson(def.path);
    const src = { id: def.id, path: filePath, exists, type: def.type, rows: 0, error: error || null };
    if (!exists || !data) {
      sourcesSearched.push(src);
      continue;
    }

    if (def.type === 'name_asset') {
      const list = Array.isArray(data.fish) ? data.fish : (Array.isArray(data.entries) ? data.entries : []);
      for (const row of list) {
        const e = upsert({ name: row.name, baseFishName: row.name, assetId: row.assetId }, def.id);
        if (e) src.rows += 1;
      }
    } else if (def.type === 'name_url_map') {
      const images = data.images && typeof data.images === 'object' ? data.images : {};
      for (const [name, row] of Object.entries(images)) {
        const e = upsert({
          name,
          baseFishName: row.canonical_name || name,
          imageUrl: row.imageUrl,
        }, def.id);
        if (e) src.rows += 1;
      }
    } else if (def.type === 'item_list') {
      const list = Array.isArray(data.fish) ? data.fish : (Array.isArray(data.entries) ? data.entries : []);
      for (const row of list) upsert(row, def.id) && (src.rows += 1);
    } else if (def.type === 'catalog_store') {
      for (const row of Object.values(data.entries || {})) {
        if (!row?.itemId || !catalogStore.isFishCategory(row.category || 'fish')) continue;
        upsert(row, def.id) && (src.rows += 1);
      }
    } else if (def.type === 'learned') {
      for (const row of Object.values(data.byItemId || {})) {
        if (!row?.publicEligible) continue;
        const proof = row.proof || {};
        upsert({
          itemId: row.itemId,
          name: row.baseFishName || row.name,
          baseFishName: row.baseFishName || row.name,
          displayName: row.displayName,
          rarity: proof.rarityCandidate,
        }, def.id) && (src.rows += 1);
      }
    } else if (def.type === 'global') {
      for (const row of Object.values(data.byItemId || {})) {
        upsert({
          itemId: row.itemId,
          fishName: row.baseFishName || row.fishName,
          baseFishName: row.baseFishName || row.fishName,
          displayName: row.displayName,
          mutation: row.mutation,
          rarity: row.rarity,
          imageAssetId: row.imageAssetId,
          imageUrl: row.imageUrl,
          rarityConfidence: row.rarityConfidence,
        }, def.id) && (src.rows += 1);
      }
    } else if (def.type === 'image_cache') {
      for (const row of Object.values(data.byAssetId || {})) {
        if (!row?.imageAssetId) continue;
        const e = upsert({
          itemId: row.itemId,
          baseFishName: row.baseFishName,
          imageAssetId: row.imageAssetId,
          imageUrl: row.localUrl || row.sourceUrl,
        }, def.id);
        if (e) {
          if (row.localUrl) e.imageSource = 'local_asset_cache';
          src.rows += 1;
        }
      }
      for (const [name, assetId] of Object.entries(data.byName || {})) {
        const e = upsert({ name, baseFishName: name, assetId }, def.id);
        if (e) src.rows += 1;
      }
    }
    sourcesSearched.push(src);
  }

  const db = _loadDbSources();
  sourcesSearched.push(...db.sources);
  for (const row of db.images) {
    const e = upsert({ name: row.name, baseFishName: row.name, imageUrl: row.imageUrl }, row.source || 'fishit_db');
    if (e) rowsScanned += 1;
  }
  for (const row of db.rarities) {
    const e = upsert({ name: row.name, baseFishName: row.name, rarity: row.rarity }, row.source || 'fishit_db');
    if (e) rowsScanned += 1;
  }

  const nameAssets = new Map();
  const nameRarities = new Map();
  for (const entry of Object.values(map)) {
    if (!entry.baseFishName) continue;
    const nk = normKey(entry.baseFishName);
    if (entry.imageAssetId || entry.imageUrl) {
      if (!nameAssets.has(nk)) nameAssets.set(nk, entry);
    }
    if (entry.rarity) {
      if (!nameRarities.has(nk)) nameRarities.set(nk, entry);
    }
  }
  for (const entry of Object.values(map)) {
    if (!entry.itemId) continue;
    const nk = normKey(entry.baseFishName);
    const img = nameAssets.get(nk);
    if (img && !entry.imageAssetId && !entry.imageUrl) {
      entry.imageAssetId = img.imageAssetId || entry.imageAssetId;
      entry.imageUrl = img.imageUrl || img.sourceUrl || entry.imageUrl;
      entry.imageSource = img.imageSource || entry.imageSource;
      entry.sourceUrl = img.sourceUrl || entry.sourceUrl;
      _touchSource(entry, 'name_asset_link');
    }
    const rar = nameRarities.get(nk);
    if (rar && !entry.rarity) {
      entry.rarity = rar.rarity;
      entry.raritySource = rar.raritySource;
      entry.rarityConfidence = rar.rarityConfidence;
      _touchSource(entry, 'name_rarity_link');
    }
  }

  const byItemId = {};
  const byName = {};
  for (const entry of Object.values(map)) {
    if (entry.itemId) byItemId[entry.itemId] = entry;
    const keys = new Set([
      normName(entry.baseFishName),
      normKey(entry.baseFishName),
      normName(entry.displayName),
    ].filter(Boolean));
    for (const k of keys) {
      if (!byName[k]) byName[k] = { ...entry };
    }
  }

  _store = {
    updatedAt: new Date().toISOString(),
    sourcesSearched,
    byItemId,
    byName,
    rowsScanned,
  };

  _audit = buildAudit(_store);
  if (persist && (process.env.NODE_ENV !== 'test' || process.env.FISHIT_CANONICAL_PERSIST === '1')) {
    _persist();
  }
  fishCatalog._reset();
  return _store;
}

function _persist() {
  if (!_store) return;
  const dir = path.dirname(CANONICAL_PATH);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  const tmp = `${CANONICAL_PATH}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(_store, null, 2), 'utf8');
  fs.renameSync(tmp, CANONICAL_PATH);
}

function _load() {
  if (_store) return _store;
  try {
    if (fs.existsSync(CANONICAL_PATH)) {
      const raw = JSON.parse(fs.readFileSync(CANONICAL_PATH, 'utf8'));
      if (raw && raw.byItemId) {
        _store = raw;
        _audit = buildAudit(_store);
        return _store;
      }
    }
  } catch (err) {
    console.warn('[fishit] canonical catalog load failed:', err && err.message ? err.message : err);
  }
  return rebuildFromAllSources({ persist: true });
}

function lookupByItemId(itemId) {
  const store = _load();
  const id = String(itemId || '').trim();
  return store.byItemId[id] || null;
}

function lookupByName(name) {
  const store = _load();
  const canon = catchNameParser.canonicalizeFishName(name || '');
  const base = canon.baseFishName || name;
  const tries = [normName(base), normKey(base), normName(name), normKey(name)].filter(Boolean);
  for (const k of tries) {
    if (store.byName[k]) return store.byName[k];
  }
  return null;
}

function resolveForItem(item) {
  if (!item) return null;
  const itemId = item.itemId ? String(item.itemId).trim() : null;
  const canon = catchNameParser.canonicalizeFishName(item.baseFishName || item.name || '');
  const base = canon.baseFishName || item.baseFishName || item.name;
  const triedAliases = [base, item.displayName, item.name, canon.displayName].filter(Boolean);
  const searchedSources = [];

  let hit = itemId ? lookupByItemId(itemId) : null;
  if (hit) searchedSources.push('canonical_by_item_id');
  if (!hit && base) {
    hit = lookupByName(base);
    if (hit) searchedSources.push('canonical_by_name');
  }
  if (!hit) {
    const img = fishImageAssets.lookupByFishName(base || item.name);
    if (img) {
      hit = {
        baseFishName: base,
        imageAssetId: img.assetId,
        imageSource: img.imageSource,
        sources: ['fish_image_asset_catalog'],
      };
      searchedSources.push('fish_image_asset_catalog');
    }
  }

  return hit ? { ...hit, triedAliases, searchedSources } : { triedAliases, searchedSources };
}

function importRows(rows, { sourceTag = 'import_script', persist = true } = {}) {
  _load();
  const results = { accepted: 0, rejected: [], updated: [] };
  for (const raw of rows || []) {
    const itemId = raw.itemId != null ? String(raw.itemId).trim() : null;
    const baseFishName = raw.baseFishName || raw.name;
    if (!itemId || !/^\d+$/.test(itemId)) {
      results.rejected.push({ row: raw, reason: 'invalid_item_id' });
      continue;
    }
    if (!baseFishName) {
      results.rejected.push({ row: raw, reason: 'missing_baseFishName' });
      continue;
    }
    const canon = catchNameParser.canonicalizeFishName(baseFishName);
    const entry = {
      itemId,
      baseFishName: canon.baseFishName || baseFishName,
      displayName: raw.displayName || canon.displayName || baseFishName,
      mutation: raw.mutation || canon.mutation || null,
      rarity: fishCatalog.normalizeRarity(raw.rarity || raw.tier),
      raritySource: sourceTag,
      rarityConfidence: raw.rarityConfidence || 'confirmed',
      imageAssetId: sanitiseAssetId(raw.imageAssetId || raw.assetId),
      imageUrl: isHttpUrl(raw.imageUrl || raw.sourceUrl) ? String(raw.imageUrl || raw.sourceUrl).trim() : null,
      sourceUrl: isHttpUrl(raw.sourceUrl) ? raw.sourceUrl.trim() : null,
      imageSource: sourceTag,
      sources: [sourceTag],
      importedAt: new Date().toISOString(),
    };
    _store.byItemId[itemId] = { ...(_store.byItemId[itemId] || {}), ...entry };
    const keys = [normName(entry.baseFishName), normKey(entry.baseFishName)];
    for (const k of keys) _store.byName[k] = { ...entry };
    results.accepted += 1;
    results.updated.push({ itemId, baseFishName: entry.baseFishName });
  }
  _store.updatedAt = new Date().toISOString();
  _audit = buildAudit(_store);
  if (persist) _persist();
  fishCatalog._reset();
  return results;
}

function buildAudit(store) {
  const byItemId = Object.values(store?.byItemId || {});
  const withImage = byItemId.filter((e) => e.imageAssetId || e.imageUrl);
  const withRarity = byItemId.filter((e) => e.rarity);
  const missingImage = byItemId.filter((e) => !e.imageAssetId && !e.imageUrl).map((e) => ({
    itemId: e.itemId,
    baseFishName: e.baseFishName,
    triedAliases: e.triedAliases || [e.baseFishName],
    searchedSources: e.sources || [],
  }));
  const missingRarity = byItemId.filter((e) => !e.rarity).map((e) => ({
    itemId: e.itemId,
    baseFishName: e.baseFishName,
    triedAliases: e.triedAliases || [e.baseFishName],
    searchedSources: e.sources || [],
  }));
  return {
    sourcesSearched: store?.sourcesSearched || [],
    totalEntries: byItemId.length,
    imageKnownCount: withImage.length,
    imageMissingCount: missingImage.length,
    rarityKnownCount: withRarity.length,
    rarityMissingCount: missingRarity.length,
    missingImageRows: missingImage.slice(0, 100),
    missingRarityRows: missingRarity.slice(0, 100),
    rowsScanned: store?.rowsScanned || 0,
  };
}

function getAudit() {
  if (!_audit) _load();
  return _audit || buildAudit(_store || emptyStore());
}

function getSourcesSearched() {
  return getAudit().sourcesSearched;
}

function _reset() {
  _store = null;
  _audit = null;
}

module.exports = {
  CANONICAL_PATH,
  rebuildFromAllSources,
  importRows,
  lookupByItemId,
  lookupByName,
  resolveForItem,
  getAudit,
  getSourcesSearched,
  _load,
  _reset,
};
