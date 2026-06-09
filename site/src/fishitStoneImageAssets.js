'use strict';

const fs = require('fs');
const path = require('path');

const STONE_MANUAL_ASSET_SOURCE = 'stone_manual_asset';
const ADMIN_UPLOADED_STONE_SOURCE = 'admin_uploaded_stone_asset';
const CACHE_DIR = path.join(__dirname, '..', 'data', 'stone_image_cache');
const CATALOG_PATH = path.join(__dirname, '..', 'data', 'fishit_stone_image_assets.json');

const ENCHANT_STONES = {
  10: { name: 'Normal Enchant Stone', stoneType: 'Normal' },
  246: { name: 'Double Enchant Stone', stoneType: 'Double' },
  558: { name: 'Evolved Enchant Stone', stoneType: 'Evolved' },
  873: { name: 'Eggy Enchant Stone', stoneType: 'Eggy' },
  929: { name: 'Runic Enchant Stone', stoneType: 'Runic' },
};

let _catalog = null;

function loadCatalog() {
  if (_catalog) return _catalog;
  try {
    const raw = JSON.parse(fs.readFileSync(CATALOG_PATH, 'utf8'));
    _catalog = {
      version: raw.version || 1,
      updatedAt: raw.updatedAt || null,
      stones: raw.stones && typeof raw.stones === 'object' ? raw.stones : {},
    };
  } catch {
    _catalog = { version: 1, updatedAt: null, stones: {} };
  }
  return _catalog;
}

function getCacheDir() {
  return CACHE_DIR;
}

function localStoneUrl(baseUrl, filename) {
  const base = String(baseUrl || '').replace(/\/$/, '');
  return `${base}/api/fishit-tracker/assets/stones/${filename}`;
}

function lookupStoneAsset(itemId, stoneType) {
  const catalog = loadCatalog();
  const idKey = itemId != null ? String(itemId).trim() : '';
  if (idKey && catalog.stones[idKey]) return catalog.stones[idKey];
  const type = stoneType ? String(stoneType).trim() : '';
  if (type) {
    for (const entry of Object.values(catalog.stones)) {
      if (entry && entry.stoneType === type) return entry;
    }
  }
  const meta = ENCHANT_STONES[idKey];
  if (meta && catalog.stones[idKey]) return catalog.stones[idKey];
  return null;
}

function stoneAssetFileExists(filename) {
  if (!filename) return false;
  const file = path.basename(String(filename));
  return fs.existsSync(path.join(CACHE_DIR, file));
}

function attachStoneImagesToItems(items, baseUrl) {
  if (!Array.isArray(items)) return [];
  return items.map((item) => {
    const asset = lookupStoneAsset(item.itemId, item.stoneType);
    if (!asset || !asset.filename || !stoneAssetFileExists(asset.filename)) {
      return { ...item };
    }
    const imageSource = asset.imageSource || STONE_MANUAL_ASSET_SOURCE;
    return {
      ...item,
      name: asset.name || item.name,
      displayName: asset.name || item.displayName || item.name,
      imageUrl: localStoneUrl(baseUrl, asset.filename),
      imageUrlPresent: true,
      imageSource,
      dataSource: item.dataSource || item.source || 'playerdata_gameitemdb',
      source: item.source || 'playerdata_gameitemdb',
      category: 'stone',
    };
  });
}

function buildStoneAssetProof(stoneItems = []) {
  const catalog = loadCatalog();
  const rows = stoneItems.map((s) => {
    const asset = lookupStoneAsset(s.itemId, s.stoneType);
    return {
      itemId: s.itemId || null,
      stoneType: s.stoneType || null,
      name: s.name || null,
      imageSource: s.imageSource || null,
      imageUrlPresent: Boolean(s.imageUrlPresent || s.imageUrl),
      manualAsset: asset ? {
        filename: asset.filename,
        catalogSource: asset.imageSource || STONE_MANUAL_ASSET_SOURCE,
        fileExists: stoneAssetFileExists(asset.filename),
      } : null,
    };
  });
  return {
    catalogPath: CATALOG_PATH,
    cacheDir: CACHE_DIR,
    catalogCount: Object.keys(catalog.stones || {}).length,
    manualAssetCount: rows.filter((r) => r.imageSource === STONE_MANUAL_ASSET_SOURCE && r.imageUrlPresent).length,
    rows,
  };
}

module.exports = {
  STONE_MANUAL_ASSET_SOURCE,
  ADMIN_UPLOADED_STONE_SOURCE,
  ENCHANT_STONES,
  getCacheDir,
  loadCatalog,
  lookupStoneAsset,
  attachStoneImagesToItems,
  buildStoneAssetProof,
  localStoneUrl,
  stoneAssetFileExists,
};
