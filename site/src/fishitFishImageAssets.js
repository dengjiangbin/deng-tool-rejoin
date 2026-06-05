'use strict';
/**
 * Fish image asset catalog (BLOCKER10L).
 *
 * Maps confirmed fish display names -> Roblox catalog asset IDs for thumbnails.
 * This is NOT an inventory itemId catalog: never map Item #N by array index.
 */

const path = require('path');
const fs = require('fs');

const ASSET_PATH = process.env.FISHIT_FISH_IMAGE_ASSETS_PATH
  || path.join(__dirname, '..', 'data', 'fishit_fish_image_assets.json');

const IMAGE_SOURCE_MATCHED = 'fish_image_asset_catalog';
const IMAGE_SOURCE_MISSING = 'missing_image_asset';

let _maps = null;
let _entryCount = 0;

function normalizeName(raw) {
  return String(raw || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

/** Fallback match: strip simple punctuation, collapse spaces. */
function normalizeNamePunct(raw) {
  return normalizeName(raw)
    .replace(/[''`]/g, '')
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function buildStoreUrl(assetId) {
  const id = String(assetId || '').trim();
  if (!/^\d{10,22}$/.test(id)) return null;
  return `https://create.roblox.com/store/asset/${id}/rbxassetid-Finder`;
}

function sanitiseAssetId(raw) {
  const id = String(raw || '').trim();
  return /^\d{10,22}$/.test(id) ? id : null;
}

function registerEntry(maps, name, assetId) {
  const id = sanitiseAssetId(assetId);
  if (!id) return;
  const display = String(name || '').trim();
  if (!display) return;
  const lower = normalizeName(display);
  const punct = normalizeNamePunct(display);
  const row = { name: display, assetId: id, imageUrl: null, imageSource: IMAGE_SOURCE_MATCHED };
  maps.byLower.set(lower, row);
  if (punct && punct !== lower) maps.byNormalized.set(punct, row);
}

function loadMaps() {
  if (_maps) return _maps;
  const byLower = new Map();
  const byNormalized = new Map();
  let rawList = [];
  try {
    if (fs.existsSync(ASSET_PATH)) {
      const parsed = JSON.parse(fs.readFileSync(ASSET_PATH, 'utf8'));
      if (Array.isArray(parsed)) rawList = parsed;
      else if (Array.isArray(parsed.fish)) rawList = parsed.fish;
      else if (Array.isArray(parsed.entries)) rawList = parsed.entries;
    }
  } catch (err) {
    console.warn('[fishit] fish image assets load failed:', err && err.message ? err.message : err);
  }
  for (const row of rawList) {
    if (!row || typeof row !== 'object') continue;
    registerEntry({ byLower, byNormalized }, row.name, row.assetId);
  }
  _entryCount = byLower.size;
  _maps = { byLower, byNormalized };
  return _maps;
}

/** Forbidden: asset list must never be keyed by inventory itemId. */
function lookupByItemId() {
  console.warn('[fishit] asset_catalog_index_mapping_forbidden');
  return null;
}

/** Forbidden: asset list must never be keyed by array index. */
function lookupByIndex() {
  console.warn('[fishit] asset_catalog_index_mapping_forbidden');
  return null;
}

/**
 * Resolve image metadata by confirmed fish display name only.
 * @returns {{ assetId: string, imageUrl: string, imageSource: string } | null}
 */
function lookupByFishName(name) {
  const maps = loadMaps();
  const lower = normalizeName(name);
  if (!lower) return null;
  let hit = maps.byLower.get(lower);
  if (!hit) {
    const punct = normalizeNamePunct(name);
    if (punct) hit = maps.byNormalized.get(punct);
  }
  return hit || null;
}

function attachFishImageFields(item) {
  if (!item || typeof item !== 'object') return item;
  const cat = String(item.category || '').toLowerCase();
  if (cat !== 'fish') return item;
  const finalName = item.name;
  if (!finalName || /^Item #\d+$/i.test(String(finalName).trim())) return item;

  const img = lookupByFishName(finalName);
  if (img) {
    return {
      ...item,
      imageAssetId: img.assetId,
      imageUrl: img.imageUrl,
      imageSource: img.imageSource,
    };
  }
  return {
    ...item,
    imageAssetId: null,
    imageUrl: null,
    imageSource: IMAGE_SOURCE_MISSING,
  };
}

function attachFishImagesToItems(items) {
  if (!Array.isArray(items)) return [];
  return items.map(attachFishImageFields);
}

function buildImageResolutionProof(fishItems) {
  if (!Array.isArray(fishItems)) return [];
  return fishItems.map((it) => ({
    itemId: it.itemId || null,
    finalName: it.name || null,
    category: it.category || null,
    imageAssetMatched: !!it.imageAssetId,
    imageAssetId: it.imageAssetId || null,
    imageUrl: it.imageUrl || null,
    imageUrlPresent: it.imageUrlPresent === true || !!it.imageUrl,
    imageResolved: it.imageResolved === true,
    imageStatus: it.imageStatus || null,
    imageSource: it.imageSource || IMAGE_SOURCE_MISSING,
  }));
}

function getCatalogEntryCount() {
  loadMaps();
  return _entryCount;
}

function _resetCache() {
  _maps = null;
  _entryCount = 0;
}

module.exports = {
  ASSET_PATH,
  IMAGE_SOURCE_MATCHED,
  IMAGE_SOURCE_MISSING,
  normalizeName,
  normalizeNamePunct,
  buildStoreUrl,
  lookupByFishName,
  lookupByItemId,
  lookupByIndex,
  attachFishImageFields,
  attachFishImagesToItems,
  buildImageResolutionProof,
  getCatalogEntryCount,
  _resetCache,
};
