'use strict';
/**
 * Resolve Roblox asset IDs to real CDN image URLs (BLOCKER10N).
 * Never expose thumbnails.roblox.com JSON to the frontend <img src>.
 */

const https = require('https');

const CACHE_TTL_MS = Number(process.env.FISHIT_THUMB_CACHE_MS || 6 * 60 * 60 * 1000);
const _cache = new Map();

function sanitiseAssetId(raw) {
  const id = String(raw || '').trim();
  return /^\d{10,22}$/.test(id) ? id : null;
}

function proxyImageUrl(assetId) {
  const id = sanitiseAssetId(assetId);
  return id ? `/api/fishit-tracker/image/${id}` : null;
}

function getCached(assetId) {
  const id = sanitiseAssetId(assetId);
  if (!id) return null;
  const row = _cache.get(id);
  if (!row) return null;
  if (Date.now() - row.at > CACHE_TTL_MS) {
    _cache.delete(id);
    return null;
  }
  return row;
}

function setCached(assetId, imageUrl, status) {
  const id = sanitiseAssetId(assetId);
  if (!id) return;
  _cache.set(id, {
    imageUrl: imageUrl || null,
    status: status || (imageUrl ? 'resolved' : 'pending'),
    at: Date.now(),
  });
}

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { timeout: 8000 }, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => {
        try {
          resolve(JSON.parse(body));
        } catch (e) {
          reject(e);
        }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
  });
}

async function resolveThumbnailUrl(assetId) {
  const id = sanitiseAssetId(assetId);
  if (!id) return { imageUrl: null, imageResolved: false, imageStatus: 'invalid_id' };

  const cached = getCached(id);
  if (cached && cached.imageUrl) {
    return { imageUrl: cached.imageUrl, imageResolved: true, imageStatus: cached.status || 'resolved' };
  }

  if (process.env.NODE_ENV === 'test') {
    const stub = `https://tr.rbxcdn.com/test-stub/${id}/Image/Png/noFilter`;
    setCached(id, stub, 'resolved_test');
    return { imageUrl: stub, imageResolved: true, imageStatus: 'resolved_test' };
  }

  const api = `https://thumbnails.roblox.com/v1/assets?assetIds=${id}&size=150x150&format=Png&isCircular=false`;
  try {
    const json = await fetchJson(api);
    const row = json && Array.isArray(json.data) ? json.data[0] : null;
    const url = row && typeof row.imageUrl === 'string' && /^https?:\/\//i.test(row.imageUrl)
      ? row.imageUrl.trim()
      : null;
    const state = row && row.state ? String(row.state) : 'unknown';
    if (url) {
      setCached(id, url, 'resolved');
      return { imageUrl: url, imageResolved: true, imageStatus: 'resolved' };
    }
    setCached(id, null, state === 'Pending' ? 'pending' : 'unavailable');
    return { imageUrl: null, imageResolved: false, imageStatus: state === 'Pending' ? 'pending' : 'unavailable' };
  } catch (err) {
    return { imageUrl: null, imageResolved: false, imageStatus: 'fetch_error' };
  }
}

/** Public API fields: proxy URL always usable; CDN URL when cached/resolved. */
function attachImageFields(item) {
  if (!item || typeof item !== 'object') return item;
  const assetId = sanitiseAssetId(item.imageAssetId);
  if (!assetId) {
    const hasUrl = !!(item.imageUrl && /^https?:\/\//i.test(item.imageUrl)
      && !/create\.roblox\.com\/store\/asset\//i.test(item.imageUrl));
    return {
      ...item,
      imageUrlPresent: hasUrl,
      imageResolved: hasUrl,
      imageStatus: hasUrl ? 'direct_url' : 'missing',
    };
  }

  const cached = getCached(assetId);
  const proxy = proxyImageUrl(assetId);
  const cdnUrl = cached && cached.imageUrl ? cached.imageUrl : null;
  return {
    ...item,
    imageAssetId: assetId,
    imageUrl: cdnUrl || proxy,
    imageUrlPresent: true,
    imageResolved: !!cdnUrl,
    imageStatus: cdnUrl ? 'resolved' : 'proxy',
    imageSource: item.imageSource || 'fish_image_asset_catalog',
  };
}

function attachImageFieldsToItems(items) {
  if (!Array.isArray(items)) return [];
  return items.map(attachImageFields);
}

async function warmCacheForAssetIds(assetIds) {
  const ids = [...new Set((assetIds || []).map(sanitiseAssetId).filter(Boolean))].slice(0, 30);
  const results = [];
  for (const id of ids) {
    results.push({ assetId: id, ...(await resolveThumbnailUrl(id)) });
  }
  return results;
}

function _resetCache() {
  _cache.clear();
}

module.exports = {
  sanitiseAssetId,
  proxyImageUrl,
  getCached,
  resolveThumbnailUrl,
  attachImageFields,
  attachImageFieldsToItems,
  warmCacheForAssetIds,
  _resetCache,
};
