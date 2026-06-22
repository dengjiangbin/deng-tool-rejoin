'use strict';

const fs = require('fs');
const path = require('path');

const stoneDisplayMap = require('./fishitStoneDisplayMap');
const robloxThumbnails = require('./fishitRobloxThumbnails');
const manualInventoryImages = require('./fishitInventoryManualImages');
const trackerItemImageOverrides = require('./fishitTrackerItemImageOverrides');
const { trackerReadAssetUrl, trackerReadImageUrl } = require('./fishitTrackerReadUrls');

const STONE_MANUAL_ASSET_SOURCE = 'stone_manual_asset';
const STONE_GAMEITEMDB_PROXY_SOURCE = 'stone_gameitemdb_proxy';
const ADMIN_UPLOADED_STONE_SOURCE = 'admin_uploaded_stone_asset';
const CACHE_DIR = path.join(__dirname, '..', 'data', 'stone_image_cache');
const CATALOG_PATH = path.join(__dirname, '..', 'data', 'fishit_stone_image_assets.json');

const ENCHANT_STONES = stoneDisplayMap.ENCHANT_STONES;

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

function getStoneAssetFilePath(filename) {
  const file = path.basename(String(filename || ''));
  if (!file) return null;
  return path.join(CACHE_DIR, file);
}

function getStoneAssetVersion(filename) {
  const full = getStoneAssetFilePath(filename);
  if (!full || !fs.existsSync(full)) return '0';
  try {
    const stat = fs.statSync(full);
    return String(Math.floor(stat.mtimeMs));
  } catch {
    return '0';
  }
}

function getStoneAssetUrl(baseUrl, filename) {
  const file = path.basename(String(filename || ''));
  const version = getStoneAssetVersion(file);
  return trackerReadAssetUrl(baseUrl, 'stones', file, version);
}

function localStoneUrl(baseUrl, filename) {
  return getStoneAssetUrl(baseUrl, filename);
}

function resolveCatalogStoneEntry(itemId, stoneType) {
  const catalog = loadCatalog();
  const idKey = itemId != null ? String(itemId).trim() : '';
  if (idKey && catalog.stones[idKey]) return catalog.stones[idKey];
  const type = stoneType ? String(stoneType).trim() : '';
  if (type) {
    for (const entry of Object.values(catalog.stones)) {
      if (entry && entry.stoneType === type) return entry;
    }
  }
  return null;
}

function lookupStoneAsset(itemId, stoneType) {
  const canonical = stoneDisplayMap.resolvePublicStoneMeta({ itemId, stoneType });
  const catalogEntry = resolveCatalogStoneEntry(itemId, stoneType);
  if (catalogEntry && catalogEntry.filename) {
    const legacy = stoneDisplayMap.isLegacyStoneImageFilename(catalogEntry.filename);
    if (!legacy) return catalogEntry;
  }
  if (canonical) {
    return {
      itemId: canonical.itemId,
      stoneType: canonical.stoneType,
      name: canonical.displayName,
      filename: canonical.imageFilename,
      imageSource: STONE_MANUAL_ASSET_SOURCE,
    };
  }
  return catalogEntry;
}

function stoneAssetFileExists(filename) {
  if (!filename) return false;
  const file = path.basename(String(filename));
  return fs.existsSync(path.join(CACHE_DIR, file));
}

function parseGameItemIcon(raw) {
  if (raw == null || raw === '') return null;
  if (typeof raw === 'number') {
    if (raw <= 0) return null;
    return { icon: `rbxassetid://${raw}`, assetId: String(raw) };
  }
  const s = String(raw).trim();
  if (!s || s === '0' || s.toLowerCase() === 'rbxassetid://0') return null;
  const prefixed = s.match(/^rbxassetid:\/\/(\d+)$/i);
  if (prefixed) {
    if (prefixed[1] === '0') return null;
    return { icon: s, assetId: prefixed[1] };
  }
  if (/^\d+$/.test(s)) {
    if (s === '0') return null;
    return { icon: `rbxassetid://${s}`, assetId: s };
  }
  return null;
}

function resolveStoneGameIconProxy(item, meta, baseUrl) {
  const canonical = meta || stoneDisplayMap.resolvePublicStoneMeta(item);
  const parsed = parseGameItemIcon(item?.icon || item?.iconRaw);
  const assetId = parsed?.assetId || canonical?.gameIconAssetId || null;
  if (!assetId) return null;
  const imageUrl = trackerReadImageUrl(baseUrl, assetId)
    || robloxThumbnails.proxyImageUrl(assetId);
  if (!imageUrl) return null;
  return {
    imageUrl,
    imageUrlPresent: true,
    imageSource: STONE_GAMEITEMDB_PROXY_SOURCE,
    imageResolver: 'stone_gameitemdb_proxy',
    icon: parsed?.icon || `rbxassetid://${assetId}`,
    imageAssetId: assetId,
    iconAssetId: assetId,
  };
}

function shouldPreferGameDbStone(item) {
  const meta = stoneDisplayMap.resolvePublicStoneMeta(item);
  return meta?.preferGameDbIcon === true && Boolean(meta?.gameIconAssetId);
}

function lookupManualStoneOverride(item) {
  const override = manualInventoryImages.lookupManualOverride(item, 'stones');
  if (!override?.uploadedFile) return null;
  if (!manualInventoryImages.manualFileExists('stones', override.uploadedFile)) return null;
  return override;
}

function publicStoneDisplayName(item) {
  return stoneDisplayMap.publicStoneDisplayName(item);
}

function attachStoneImagesToItems(items, baseUrl) {
  if (!Array.isArray(items)) return [];
  return items.map((item) => {
    const displayName = publicStoneDisplayName(item);
    const meta = stoneDisplayMap.resolvePublicStoneMeta(item);
    const rowBase = {
      ...item,
      name: displayName,
      displayName,
      category: 'stone',
      dataSource: item.dataSource || item.source || 'playerdata_gameitemdb',
      source: item.source || 'playerdata_gameitemdb',
    };

    const trackerTopGridOverride = trackerItemImageOverrides.resolveTrackerItemImageOverride(rowBase);
    if (trackerTopGridOverride) {
      return {
        ...rowBase,
        ...trackerTopGridOverride,
        imageResolved: true,
      };
    }

    // Manual override wins over EVERY other source (incl. preferGameDbIcon) so
    // explicitly-uploaded art (e.g. Runic Stone) always beats broken catalog/
    // gameDB images.
    const manualOverride = lookupManualStoneOverride(item);
    if (manualOverride) {
      const imageUrl = manualInventoryImages.buildManualImageUrl(
        baseUrl,
        'stones',
        manualOverride.uploadedFile,
      );
      if (imageUrl) {
        manualInventoryImages.logImageOverrideMatch(
          manualOverride.originalName,
          manualOverride.normalizedName,
        );
        return {
          ...rowBase,
          name: manualOverride.originalName || displayName,
          displayName: manualOverride.originalName || displayName,
          imageUrl,
          imageUrlPresent: true,
          imageSource: manualInventoryImages.MANUAL_OVERRIDE_SOURCE,
          imageResolver: 'stone_manual_override',
        };
      }
    }

    if (shouldPreferGameDbStone(item)) {
      const gameDb = resolveStoneGameIconProxy(item, meta, baseUrl);
      if (gameDb) {
        const resolvedName = meta?.displayName || displayName;
        return {
          ...rowBase,
          ...gameDb,
          name: resolvedName,
          displayName: resolvedName,
        };
      }
    }

    const asset = lookupStoneAsset(item.itemId, item.stoneType);
    const filename = asset?.filename || stoneDisplayMap.publicStoneImageFilename(item);
    if (
      filename
      && !stoneDisplayMap.isLegacyStoneImageFilename(filename)
      && stoneAssetFileExists(filename)
    ) {
      const imageSource = asset?.imageSource || STONE_MANUAL_ASSET_SOURCE;
      const resolvedName = asset?.name || displayName;
      return {
        ...rowBase,
        name: resolvedName,
        displayName: resolvedName,
        imageUrl: localStoneUrl(baseUrl, filename),
        imageUrlPresent: true,
        imageSource,
        imageResolver: 'stone_catalog',
      };
    }

    const gameDbFallback = resolveStoneGameIconProxy(item, meta, baseUrl);
    if (gameDbFallback) {
      return { ...rowBase, ...gameDbFallback };
    }

    return {
      ...rowBase,
      imageResolver: 'stone_missing',
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
  STONE_GAMEITEMDB_PROXY_SOURCE,
  ADMIN_UPLOADED_STONE_SOURCE,
  ENCHANT_STONES,
  publicStoneDisplayName,
  getCacheDir,
  getStoneAssetFilePath,
  getStoneAssetVersion,
  getStoneAssetUrl,
  loadCatalog,
  lookupStoneAsset,
  attachStoneImagesToItems,
  buildStoneAssetProof,
  localStoneUrl,
  stoneAssetFileExists,
  resolveStoneGameIconProxy,
  shouldPreferGameDbStone,
};
