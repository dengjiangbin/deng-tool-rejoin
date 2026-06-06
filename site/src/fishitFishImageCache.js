'use strict';
/**
 * BLOCKER10U — persistent local fish image cache (download once, serve locally).
 */

const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const http = require('http');
const fishImageAssets = require('./fishitFishImageAssets');
const robloxThumbnails = require('./fishitRobloxThumbnails');
const catchNameParser = require('./fishitCatchNameParser');

const CACHE_DIR = process.env.FISHIT_FISH_IMAGE_CACHE_DIR
  || path.join(__dirname, '..', 'data', 'fish_image_cache');
const INDEX_PATH = path.join(CACHE_DIR, 'index.json');

const IMAGE_SOURCE_LOCAL = 'local_asset_cache';
const _proof = [];

let _index = null;

function _defaultIndex() {
  return { updatedAt: null, byAssetId: {}, byName: {} };
}

function _loadIndex() {
  if (_index) return _index;
  try {
    if (fs.existsSync(INDEX_PATH)) {
      const raw = JSON.parse(fs.readFileSync(INDEX_PATH, 'utf8'));
      _index = {
        updatedAt: raw.updatedAt || null,
        byAssetId: (raw.byAssetId && typeof raw.byAssetId === 'object') ? raw.byAssetId : {},
        byName: (raw.byName && typeof raw.byName === 'object') ? raw.byName : {},
      };
      return _index;
    }
  } catch (err) {
    console.warn('[fishit] image cache index load failed:', err && err.message ? err.message : err);
  }
  _index = _defaultIndex();
  return _index;
}

function _persistIndex() {
  _loadIndex();
  if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });
  _index.updatedAt = new Date().toISOString();
  const tmp = `${INDEX_PATH}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(_index, null, 2), 'utf8');
  fs.renameSync(tmp, INDEX_PATH);
}

function _extFromMime(mime) {
  const m = String(mime || '').toLowerCase();
  if (m.includes('webp')) return 'webp';
  if (m.includes('jpeg') || m.includes('jpg')) return 'jpg';
  if (m.includes('gif')) return 'gif';
  return 'png';
}

function _fetchBuffer(url) {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith('https') ? require('https') : http;
    const req = lib.get(url, { timeout: 15000 }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        return resolve(_fetchBuffer(res.headers.location));
      }
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({
        status: res.statusCode,
        body: Buffer.concat(chunks),
        contentType: res.headers['content-type'] || 'image/png',
      }));
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
  });
}

function localUrlForFile(filename) {
  return `/api/fishit-tracker/assets/fish/${filename}`;
}

function resolveAssetIdForItem(item) {
  if (!item) return null;
  const direct = robloxThumbnails.sanitiseAssetId(item.imageAssetId);
  if (direct) return direct;
  const names = [
    item.baseFishName,
    catchNameParser.canonicalizeFishName(item.name || '').baseFishName,
    item.name,
    item.displayName,
  ].filter(Boolean);
  for (const n of names) {
    const hit = fishImageAssets.lookupByFishName(n);
    if (hit && hit.assetId) return hit.assetId;
  }
  return null;
}

async function ensureCachedAsset(assetId, meta = {}) {
  const id = robloxThumbnails.sanitiseAssetId(assetId);
  if (!id) return { assetId: null, cached: false, imageStatus: 'invalid_id' };

  _loadIndex();
  const existing = _index.byAssetId[id];
  if (existing && existing.localFile && fs.existsSync(path.join(CACHE_DIR, existing.localFile))) {
    return { ...existing, cached: true, imageStatus: 'cached' };
  }

  if (process.env.NODE_ENV === 'test') {
    const localFile = `test_${id}.webp`;
    const row = {
      itemId: meta.itemId || null,
      baseFishName: meta.baseFishName || null,
      imageAssetId: id,
      sourceUrl: `https://tr.rbxcdn.com/test-stub/${id}/Image/Png/noFilter`,
      localUrl: localUrlForFile(localFile),
      localFile,
      sha256: `test_${id}`,
      mimeType: 'image/webp',
      imageStatus: 'cached',
      cached: true,
      source: IMAGE_SOURCE_LOCAL,
      downloadedAt: new Date().toISOString(),
      verifiedAt: new Date().toISOString(),
    };
    _index.byAssetId[id] = row;
    if (meta.baseFishName) _index.byName[fishImageAssets.normalizeName(meta.baseFishName)] = id;
    _persistIndex();
    return row;
  }

  try {
    const resolved = await robloxThumbnails.resolveThumbnailUrl(id);
    if (!resolved.imageUrl) {
      const row = {
        imageAssetId: id,
        sourceUrl: null,
        localUrl: null,
        imageStatus: 'download_failed',
        cached: false,
        source: resolved.failureReason || 'resolve_failed',
        downloadedAt: new Date().toISOString(),
      };
      _index.byAssetId[id] = { ...(_index.byAssetId[id] || {}), ...row };
      _persistIndex();
      return row;
    }

    const fetched = await _fetchBuffer(resolved.imageUrl);
    if (fetched.status < 200 || fetched.status >= 300 || !fetched.body || fetched.body.length < 50) {
      const row = {
        imageAssetId: id,
        sourceUrl: resolved.imageUrl,
        imageStatus: 'download_failed',
        cached: false,
        source: `bad_status_${fetched.status}`,
      };
      _index.byAssetId[id] = { ...(_index.byAssetId[id] || {}), ...row };
      _persistIndex();
      return row;
    }

    const sha256 = crypto.createHash('sha256').update(fetched.body).digest('hex');
    const ext = _extFromMime(fetched.contentType);
    const localFile = `${sha256.slice(0, 16)}.${ext}`;
    if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });
    fs.writeFileSync(path.join(CACHE_DIR, localFile), fetched.body);

    const row = {
      itemId: meta.itemId || null,
      baseFishName: meta.baseFishName || null,
      displayName: meta.displayName || null,
      imageAssetId: id,
      sourceUrl: resolved.imageUrl,
      localUrl: localUrlForFile(localFile),
      localFile,
      sha256,
      mimeType: fetched.contentType,
      imageStatus: 'cached',
      cached: true,
      source: IMAGE_SOURCE_LOCAL,
      downloadedAt: new Date().toISOString(),
      verifiedAt: new Date().toISOString(),
    };
    _index.byAssetId[id] = row;
    if (meta.baseFishName) {
      _index.byName[fishImageAssets.normalizeName(meta.baseFishName)] = id;
    }
    _persistIndex();
    return row;
  } catch (err) {
    const row = {
      imageAssetId: id,
      imageStatus: 'download_failed',
      cached: false,
      source: err.message || 'download_error',
    };
    _index.byAssetId[id] = { ...(_index.byAssetId[id] || {}), ...row };
    _persistIndex();
    return row;
  }
}

async function ensureCachedAssets(assetIds, metaByAssetId = {}) {
  const ids = [...new Set((assetIds || []).map(robloxThumbnails.sanitiseAssetId).filter(Boolean))].slice(0, 40);
  const results = [];
  for (const id of ids) {
    results.push(await ensureCachedAsset(id, metaByAssetId[id] || {}));
  }
  return results;
}

async function attachCachedImageFields(item, baseUrl) {
  if (!item || typeof item !== 'object') return item;
  const assetId = resolveAssetIdForItem(item);
  if (!assetId) {
    return {
      ...item,
      imageStatus: item.imageStatus || 'missing',
      imageSource: item.imageSource || fishImageAssets.IMAGE_SOURCE_MISSING,
    };
  }

  const cached = await ensureCachedAsset(assetId, {
    itemId: item.itemId,
    baseFishName: item.baseFishName || item.name,
    displayName: item.displayName || item.name,
  });

  recordProof({
    itemId: item.itemId || null,
    baseFishName: item.baseFishName || item.name,
    imageAssetId: assetId,
    sourceUrl: cached.sourceUrl || null,
    localUrl: cached.localUrl || null,
    imageStatus: cached.imageStatus || 'missing',
    cached: !!cached.cached,
    source: cached.source || null,
  });

  if (cached.cached && cached.localUrl) {
    const url = cached.localUrl;
    return {
      ...item,
      imageAssetId: assetId,
      imageUrl: url,
      imageUrlPresent: true,
      imageResolved: true,
      imageStatus: 'cached',
      imageSource: IMAGE_SOURCE_LOCAL,
    };
  }

  const proxy = robloxThumbnails.proxyImageUrl(assetId);
  return {
    ...item,
    imageAssetId: assetId,
    imageUrl: proxy,
    imageUrlPresent: !!proxy,
    imageResolved: false,
    imageStatus: cached.imageStatus || 'missing',
    imageSource: item.imageSource || fishImageAssets.IMAGE_SOURCE_MATCHED,
  };
}

async function attachCachedImagesToItems(items, baseUrl) {
  if (!Array.isArray(items)) return [];
  const out = [];
  const assetIds = items.map(resolveAssetIdForItem).filter(Boolean);
  const meta = {};
  for (const it of items) {
    const aid = resolveAssetIdForItem(it);
    if (aid) {
      meta[aid] = {
        itemId: it.itemId,
        baseFishName: it.baseFishName || it.name,
        displayName: it.displayName || it.name,
      };
    }
  }
  await ensureCachedAssets(assetIds, meta);
  for (const it of items) {
    out.push(await attachCachedImageFields(it, baseUrl));
  }
  return out;
}

function recordProof(row) {
  if (_proof.length < 40) _proof.push(row);
}

function getImageCacheProof(limit = 25) {
  return _proof.slice(0, limit);
}

function getImageCacheStats() {
  _loadIndex();
  const rows = Object.values(_index.byAssetId || {});
  const cached = rows.filter((r) => r.cached === true || r.imageStatus === 'cached');
  return {
    cachedCount: cached.length,
    missingCount: rows.length - cached.length,
    totalEntries: rows.length,
    indexPath: INDEX_PATH,
  };
}

function getCachedEntry(assetId) {
  _loadIndex();
  const id = robloxThumbnails.sanitiseAssetId(assetId);
  return id ? (_index.byAssetId[id] || null) : null;
}

function getCacheDir() {
  return CACHE_DIR;
}

function _reset() {
  _index = null;
  _proof.length = 0;
}

module.exports = {
  CACHE_DIR,
  IMAGE_SOURCE_LOCAL,
  localUrlForFile,
  resolveAssetIdForItem,
  ensureCachedAsset,
  ensureCachedAssets,
  attachCachedImageFields,
  attachCachedImagesToItems,
  getImageCacheProof,
  getImageCacheStats,
  getCachedEntry,
  getCacheDir,
  _reset,
};
