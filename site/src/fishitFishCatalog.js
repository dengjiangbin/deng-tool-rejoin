'use strict';
/**
 * Confirmed fish catalog by itemId (BLOCKER10N).
 * Merges seed mappings, persisted catalog, image assets, and learned fish.
 * Does NOT invent itemId mappings from image list index or name-only tables.
 */

const path = require('path');
const fs = require('fs');
const catalogStore = require('./fishitCatalogStore');
const learnedFishCatalog = require('./fishitLearnedFishCatalog');
const fishImageAssets = require('./fishitFishImageAssets');
const canonicalCatalog = require('./fishitCanonicalCatalog');

const CONFIRMED_PATH = process.env.FISHIT_FISH_CONFIRMED_CATALOG_PATH
  || path.join(__dirname, '..', 'data', 'fishit_fish_confirmed_catalog.json');

const RARITY_ALIASES = {
  common: 'Common',
  uncommon: 'Uncommon',
  rare: 'Rare',
  epic: 'Epic',
  legend: 'Legendary',
  legendary: 'Legendary',
  mythic: 'Mythic',
  secret: 'Secret',
  forgotten: 'Forgotten',
  limited: 'Limited',
  event: 'Event',
  grade: null,
  rank: null,
  quality: null,
};

let _byItemId = null;
let _sources = [];

function normalizeRarity(raw) {
  if (!raw) return null;
  const t = String(raw).trim().toLowerCase();
  if (!t || t === 'unknown' || t === '-') return null;
  if (Object.prototype.hasOwnProperty.call(RARITY_ALIASES, t)) {
    return RARITY_ALIASES[t];
  }
  const norm = catalogStore.normalizeTier(t);
  if (RARITY_ALIASES[norm] === null) return null;
  if (RARITY_ALIASES[norm]) return RARITY_ALIASES[norm];
  return norm ? norm.charAt(0).toUpperCase() + norm.slice(1) : null;
}

function normalizeName(raw) {
  return String(raw || '').trim();
}

function registerEntry(map, sources, raw, sourceTag) {
  if (!raw || raw.itemId == null) return;
  const itemId = String(raw.itemId).trim();
  if (!/^\d+$/.test(itemId)) return;
  const name = normalizeName(raw.name);
  if (!name) return;
  const category = String(raw.category || 'fish').toLowerCase();
  if (category !== 'fish') return;

  const img = fishImageAssets.lookupByFishName(name);
  const rarity = normalizeRarity(raw.rarity || raw.tier);
  const existing = map.get(itemId);
  const entry = {
    itemId,
    name,
    category: 'fish',
    rarity,
    tier: rarity,
    imageAssetId: sanitiseAssetId(raw.imageAssetId) || (img && img.assetId) || null,
    imageUrl: raw.imageUrl || (img && img.imageUrl) || null,
    source: raw.source || sourceTag || 'confirmed_catalog',
    confidence: raw.confidence || null,
    updatedAt: raw.updatedAt || null,
  };

  if (!existing) {
    map.set(itemId, entry);
    if (!sources.includes(sourceTag)) sources.push(sourceTag);
    return;
  }
  if (existing.source === 'seed_confirmed' && sourceTag !== 'seed_confirmed') return;
  if (existing.name !== name && (existing.source === 'seed_confirmed' || existing.source === 'manual_confirmed')) return;
  map.set(itemId, { ...existing, ...entry, name: existing.name === name ? name : existing.name });
}

function sanitiseAssetId(raw) {
  const id = String(raw || '').trim();
  return /^\d{10,22}$/.test(id) ? id : null;
}

function loadConfirmedFile(map, sources) {
  try {
    if (!fs.existsSync(CONFIRMED_PATH)) return;
    const parsed = JSON.parse(fs.readFileSync(CONFIRMED_PATH, 'utf8'));
    const list = Array.isArray(parsed.fish) ? parsed.fish : (Array.isArray(parsed.entries) ? parsed.entries : []);
    for (const row of list) registerEntry(map, sources, row, 'confirmed_file');
  } catch (err) {
    console.warn('[fishit] fish confirmed catalog load failed:', err && err.message ? err.message : err);
  }
}

function loadCatalogStore(map, sources) {
  const cat = catalogStore.getCatalog();
  for (const e of Object.values(cat.entries || {})) {
    if (!e || !e.itemId || !catalogStore.isFishCategory(e.category)) continue;
    if (catalogStore.isPlaceholderItemName(e.name, e.itemId)) continue;
    registerEntry(map, sources, {
      itemId: e.itemId,
      name: e.name,
      category: 'fish',
      tier: e.tier,
      imageUrl: e.imageUrl,
      source: e.source,
      confidence: e.confidence,
      updatedAt: e.updatedAt,
    }, e.source || 'catalog_store');
  }
}

function loadGlobalConfirmed(map, sources) {
  const globalFishCatalog = require('./fishitGlobalFishItemCatalog');
  for (const e of globalFishCatalog.getConfirmedMappings()) {
    if (!e.publicEligible || !e.fishName) continue;
    registerEntry(map, sources, {
      itemId: e.itemId,
      name: e.fishName,
      category: 'fish',
      rarity: e.rarity,
      tier: e.rarity,
      imageAssetId: e.imageAssetId,
      imageUrl: e.imageUrl,
      source: 'global_catalog_confirmed',
      confidence: e.confidence,
      updatedAt: e.lastConfirmedAt || e.lastSeenAt,
    }, 'global_catalog_confirmed');
  }
}

function loadLearned(map, sources) {
  for (const e of learnedFishCatalog.getAllMappings()) {
    if (!e.publicEligible || e.category !== 'fish') continue;
    registerEntry(map, sources, {
      itemId: e.itemId,
      name: e.name,
      category: 'fish',
      source: e.source,
      confidence: e.confidence,
    }, 'learned_catch_delta');
  }
}

function loadSeeds(map, sources) {
  for (const raw of catalogStore.KNOWN_ID_SEEDS) {
    if (!catalogStore.isFishCategory(raw.category)) continue;
    registerEntry(map, sources, raw, 'seed_confirmed');
  }
}

function loadCanonical(map, sources) {
  const store = canonicalCatalog._load();
  for (const row of Object.values(store.byItemId || {})) {
    if (!row?.itemId || !row.baseFishName) continue;
    registerEntry(map, sources, {
      itemId: row.itemId,
      name: row.baseFishName,
      category: 'fish',
      rarity: row.rarity,
      tier: row.rarity,
      imageAssetId: row.imageAssetId,
      imageUrl: row.imageUrl || row.sourceUrl,
      source: row.raritySource || row.imageSource || 'canonical_catalog',
      confidence: row.rarityConfidence || 'confirmed',
    }, 'canonical_catalog');
  }
}

function _load() {
  if (_byItemId) return _byItemId;
  const map = new Map();
  const sources = [];
  loadSeeds(map, sources);
  loadConfirmedFile(map, sources);
  loadCanonical(map, sources);
  loadGlobalConfirmed(map, sources);
  loadCatalogStore(map, sources);
  loadLearned(map, sources);
  _byItemId = map;
  _sources = sources;
  return _byItemId;
}

function lookupByItemId(itemId) {
  const map = _load();
  const id = String(itemId || '').trim();
  return map.get(id) || null;
}

function lookupByName(name) {
  const map = _load();
  const lower = normalizeName(name).toLowerCase();
  for (const e of map.values()) {
    if (e.name.toLowerCase() === lower) return e;
  }
  return null;
}

function getAllFish() {
  return [..._load().values()];
}

function getStats() {
  const all = getAllFish();
  const withImages = all.filter((e) => !!e.imageAssetId);
  const withRarity = all.filter((e) => !!e.rarity);
  const missingImageFishIds = all.filter((e) => !e.imageAssetId).map((e) => e.itemId);
  const missingRarityFishIds = all.filter((e) => !e.rarity).map((e) => e.itemId);
  return {
    fishCatalogTotal: all.length,
    fishCatalogWithImages: withImages.length,
    fishCatalogWithRarity: withRarity.length,
    fishCatalogSources: [..._sources],
    missingImageFishIds: missingImageFishIds.slice(0, 50),
    missingRarityFishIds: missingRarityFishIds.slice(0, 50),
  };
}

function catalogMapForItemIds(itemIds) {
  const out = {};
  for (const rawId of itemIds || []) {
    const meta = lookupByItemId(rawId);
    if (!meta) continue;
    out[String(rawId)] = {
      itemId: meta.itemId,
      name: meta.name,
      category: meta.category,
      rarity: meta.rarity || null,
      tier: meta.rarity || null,
      imageAssetId: meta.imageAssetId || null,
      source: meta.source || null,
    };
  }
  return out;
}

function _reset() {
  _byItemId = null;
  _sources = [];
}

module.exports = {
  CONFIRMED_PATH,
  normalizeRarity,
  lookupByItemId,
  lookupByName,
  getAllFish,
  getStats,
  catalogMapForItemIds,
  _reset,
};
