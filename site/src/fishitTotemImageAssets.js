'use strict';

const fs = require('fs');
const path = require('path');

const totemDisplayMap = require('./fishitTotemDisplayMap');
const robloxThumbnails = require('./fishitRobloxThumbnails');
const manualInventoryImages = require('./fishitInventoryManualImages');
const crypto = require('crypto');
const { trackerReadAssetUrl, trackerReadImageUrl } = require('./fishitTrackerReadUrls');

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

const TOTEM_MANUAL_ASSET_SOURCE = 'totem_manual_asset';
const TOTEM_GAMEITEMDB_PROXY_SOURCE = 'totem_gameitemdb_proxy';
const CACHE_DIR = path.join(__dirname, '..', 'data', 'totem_image_cache');
const CATALOG_PATH = path.join(__dirname, '..', 'data', 'fishit_totem_image_assets.json');

let _catalog = null;

function loadCatalog() {
  if (_catalog) return _catalog;
  try {
    const raw = JSON.parse(fs.readFileSync(CATALOG_PATH, 'utf8'));
    _catalog = {
      version: raw.version || 1,
      updatedAt: raw.updatedAt || null,
      totems: raw.totems && typeof raw.totems === 'object' ? raw.totems : {},
    };
  } catch {
    _catalog = { version: 1, updatedAt: null, totems: {} };
  }
  return _catalog;
}

function getCacheDir() {
  return CACHE_DIR;
}

function getTotemAssetFilePath(filename) {
  const file = path.basename(String(filename || ''));
  if (!file) return null;
  return path.join(CACHE_DIR, file);
}

function getTotemAssetVersion(filename) {
  const full = getTotemAssetFilePath(filename);
  if (!full || !fs.existsSync(full)) return '0';
  try {
    const stat = fs.statSync(full);
    return String(Math.floor(stat.mtimeMs));
  } catch {
    return '0';
  }
}

function getTotemAssetUrl(baseUrl, filename) {
  const file = path.basename(String(filename || ''));
  const version = getTotemAssetVersion(file);
  return trackerReadAssetUrl(baseUrl, 'totems', file, version);
}

function resolveCatalogTotemEntry(itemId, canonicalName) {
  const catalog = loadCatalog();
  const nameKey = totemDisplayMap.normalizeTotemName(canonicalName);
  if (nameKey && catalog.totems[nameKey]) return catalog.totems[nameKey];
  const idKey = itemId != null ? String(itemId).trim() : '';
  if (idKey && catalog.totems[idKey]) return catalog.totems[idKey];
  return null;
}

function lookupTotemAsset(item) {
  const canonical = totemDisplayMap.resolvePublicTotemMeta(item);
  const catalogEntry = resolveCatalogTotemEntry(item?.itemId, item?.name || item?.displayName);
  if (catalogEntry?.filename) return catalogEntry;
  if (canonical?.imageFilename) {
    return {
      itemId: canonical.itemId || item?.itemId || null,
      canonicalName: canonical.canonicalName,
      name: canonical.displayName,
      filename: canonical.imageFilename,
      imageSource: TOTEM_MANUAL_ASSET_SOURCE,
    };
  }
  return null;
}

function totemAssetFileExists(filename) {
  if (!filename) return false;
  const file = path.basename(String(filename));
  return fs.existsSync(path.join(CACHE_DIR, file));
}

function totemCatalogFileSha256(filename) {
  const full = getTotemAssetFilePath(filename);
  if (!full || !fs.existsSync(full)) return null;
  try {
    const buf = fs.readFileSync(full);
    return crypto.createHash('sha256').update(buf).digest('hex');
  } catch {
    return null;
  }
}

/** Known bad placeholder copied to multiple totem cache files (2026-06-14 regression). */
function isStaleTotemCatalogFile(filename) {
  const hash = totemCatalogFileSha256(filename);
  if (!hash) return true;
  return hash === 'd75451816539eb8a50806749f77afb2af8afe566ed236309197103b364b08ae6';
}

function lookupManualTotemOverride(item) {
  const override = manualInventoryImages.lookupManualOverride(item, 'totems');
  if (!override?.uploadedFile) return null;
  if (!manualInventoryImages.manualFileExists('totems', override.uploadedFile)) return null;
  return override;
}

function resolveTotemGameIconProxy(item, baseUrl) {
  const parsed = parseGameItemIcon(item?.icon || item?.iconRaw);
  if (!parsed?.assetId) return null;
  if (totemDisplayMap.isRejectedTotemGameIcon(item, parsed.assetId)) return null;
  const imageUrl = trackerReadImageUrl(baseUrl, parsed.assetId)
    || robloxThumbnails.proxyImageUrl(parsed.assetId);
  if (!imageUrl) return null;
  return {
    imageUrl,
    imageSource: TOTEM_GAMEITEMDB_PROXY_SOURCE,
    icon: parsed.icon,
    imageAssetId: parsed.assetId,
  };
}

function attachTotemImagesToItems(items, baseUrl) {
  if (!Array.isArray(items)) return [];
  return items.map((item) => {
    const displayName = totemDisplayMap.publicTotemDisplayName(item);
    const rowBase = {
      ...item,
      name: displayName,
      displayName,
      category: 'totem',
      dataSource: item.dataSource || item.source || 'playerdata_gameitemdb',
      source: item.source || 'playerdata_gameitemdb',
    };

    const manualOverride = lookupManualTotemOverride(item);
    if (manualOverride) {
      const imageUrl = manualInventoryImages.buildManualImageUrl(
        baseUrl,
        'totems',
        manualOverride.uploadedFile,
      );
      if (imageUrl) {
        return {
          ...rowBase,
          name: manualOverride.originalName || displayName,
          displayName: manualOverride.originalName || displayName,
          imageUrl,
          imageUrlPresent: true,
          imageSource: manualInventoryImages.MANUAL_OVERRIDE_SOURCE,
          imageResolver: 'totem_manual_override',
        };
      }
    }

    const asset = lookupTotemAsset(item);
    const filename = asset?.filename || totemDisplayMap.publicTotemImageFilename(item);
    if (
      filename
      && totemAssetFileExists(filename)
      && !isStaleTotemCatalogFile(filename)
    ) {
      const imageSource = asset?.imageSource || TOTEM_MANUAL_ASSET_SOURCE;
      const resolvedName = asset?.name || displayName;
      return {
        ...rowBase,
        name: resolvedName,
        displayName: resolvedName,
        imageUrl: getTotemAssetUrl(baseUrl, filename),
        imageUrlPresent: true,
        imageSource,
        imageResolver: 'totem_catalog',
      };
    }

    const proxy = resolveTotemGameIconProxy(item, baseUrl);
    if (proxy) {
      return {
        ...rowBase,
        imageUrl: proxy.imageUrl,
        imageUrlPresent: true,
        imageSource: proxy.imageSource,
        imageResolver: 'totem_gameitemdb_proxy',
        icon: proxy.icon,
        imageAssetId: proxy.imageAssetId,
      };
    }

    return {
      ...rowBase,
      imageResolver: 'totem_missing',
    };
  });
}

function buildTotemAssetProof(totemItems = []) {
  const catalog = loadCatalog();
  const rows = totemItems.map((t) => {
    const asset = lookupTotemAsset(t);
    return {
      itemId: t.itemId || null,
      name: t.name || null,
      imageSource: t.imageSource || null,
      imageResolver: t.imageResolver || null,
      imageUrlPresent: Boolean(t.imageUrlPresent || t.imageUrl),
      imageUrl: t.imageUrl || null,
      usesFishAssetPath: Boolean(t.imageUrl && String(t.imageUrl).includes('/assets/fish/')),
      manualAsset: asset ? {
        filename: asset.filename,
        catalogSource: asset.imageSource || TOTEM_MANUAL_ASSET_SOURCE,
        fileExists: totemAssetFileExists(asset.filename),
      } : null,
    };
  });
  return {
    catalogPath: CATALOG_PATH,
    cacheDir: CACHE_DIR,
    catalogCount: Object.keys(catalog.totems || {}).length,
    manualAssetCount: rows.filter((r) => r.imageSource === TOTEM_MANUAL_ASSET_SOURCE && r.imageUrlPresent).length,
    rows,
  };
}

module.exports = {
  TOTEM_MANUAL_ASSET_SOURCE,
  TOTEM_GAMEITEMDB_PROXY_SOURCE,
  getCacheDir,
  getTotemAssetFilePath,
  getTotemAssetVersion,
  getTotemAssetUrl,
  loadCatalog,
  lookupTotemAsset,
  attachTotemImagesToItems,
  buildTotemAssetProof,
  totemAssetFileExists,
  isStaleTotemCatalogFile,
};
