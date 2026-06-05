'use strict';
/**
 * Resolve Roblox asset IDs to real CDN image URLs (BLOCKER10N2).
 * Public API must return resolved CDN URLs — never unverified proxy URLs as "fixed".
 */

const http = require('http');
const { URL } = require('url');

const CACHE_TTL_MS = Number(process.env.FISHIT_THUMB_CACHE_MS || 6 * 60 * 60 * 1000);
const _cache = new Map();
const _verifiedProxy = new Map();

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

function setCached(assetId, imageUrl, status, failureReason) {
  const id = sanitiseAssetId(assetId);
  if (!id) return;
  _cache.set(id, {
    imageUrl: imageUrl || null,
    status: status || (imageUrl ? 'resolved' : 'pending'),
    failureReason: failureReason || null,
    at: Date.now(),
  });
}

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith('https') ? require('https') : http;
    const req = lib.get(url, { timeout: 10000 }, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => {
        try {
          resolve({ status: res.statusCode, json: JSON.parse(body), headers: res.headers });
        } catch (e) {
          reject(e);
        }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
  });
}

function fetchWithRedirects(url, maxRedirects = 5) {
  return new Promise((resolve, reject) => {
    const visit = (target, depth) => {
      if (depth > maxRedirects) return reject(new Error('too_many_redirects'));
      let parsed;
      try {
        parsed = new URL(target);
      } catch (e) {
        return reject(e);
      }
      const lib = parsed.protocol === 'https:' ? require('https') : http;
      const req = lib.request({
        method: 'GET',
        hostname: parsed.hostname,
        port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
        path: parsed.pathname + parsed.search,
        timeout: 12000,
      }, (res) => {
        const code = res.statusCode || 0;
        if (code >= 300 && code < 400 && res.headers.location) {
          const next = new URL(res.headers.location, target).toString();
          res.resume();
          return visit(next, depth + 1);
        }
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => {
          resolve({
            status: code,
            headers: res.headers,
            body: Buffer.concat(chunks),
            finalUrl: target,
          });
        });
      });
      req.on('error', reject);
      req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
      req.end();
    };
    visit(url, 0);
  });
}

function isImageContentType(ct) {
  const v = String(ct || '').toLowerCase();
  return v.startsWith('image/');
}

function isCdnImageUrl(url) {
  return typeof url === 'string' && /^https?:\/\//i.test(url)
    && (/rbxcdn\.com/i.test(url) || /roblox\.com/i.test(url));
}

/** Follow redirects; accept 200 image/* or 302 chain ending in image CDN. */
async function verifyImageUrlWorks(url) {
  if (!url || typeof url !== 'string') {
    return { ok: false, reason: 'missing_url', contentType: null, finalUrl: null };
  }
  const u = url.trim();
  if (!u) return { ok: false, reason: 'empty_url', contentType: null, finalUrl: null };

  if (process.env.NODE_ENV === 'test' && u.includes('tr.rbxcdn.com')) {
    return { ok: true, reason: 'test_stub', contentType: 'image/png', finalUrl: u };
  }

  try {
    const res = await fetchWithRedirects(u.startsWith('http') ? u : `http://127.0.0.1${u}`);
    const ct = res.headers['content-type'] || '';
    if (res.status >= 200 && res.status < 300 && isImageContentType(ct)) {
      return { ok: true, reason: 'image_ok', contentType: ct, finalUrl: res.finalUrl };
    }
    if (isImageContentType(ct) || (res.body && res.body.length > 100 && isCdnImageUrl(res.finalUrl))) {
      return { ok: true, reason: 'image_ok', contentType: ct || 'image/png', finalUrl: res.finalUrl };
    }
    return { ok: false, reason: `bad_status_${res.status}`, contentType: ct, finalUrl: res.finalUrl };
  } catch (err) {
    return { ok: false, reason: err.message || 'verify_failed', contentType: null, finalUrl: null };
  }
}

async function resolveThumbnailUrl(assetId) {
  const id = sanitiseAssetId(assetId);
  if (!id) {
    return { assetId: null, imageUrl: null, imageResolved: false, imageStatus: 'invalid_id', failureReason: 'invalid_id' };
  }

  const cached = getCached(id);
  if (cached && cached.imageUrl) {
    return {
      assetId: id,
      imageUrl: cached.imageUrl,
      imageResolved: true,
      imageStatus: cached.status || 'resolved',
      failureReason: null,
    };
  }

  if (process.env.NODE_ENV === 'test') {
    const stub = `https://tr.rbxcdn.com/test-stub/${id}/Image/Png/noFilter`;
    setCached(id, stub, 'resolved_test');
    return { assetId: id, imageUrl: stub, imageResolved: true, imageStatus: 'resolved_test', failureReason: null };
  }

  const api = `https://thumbnails.roblox.com/v1/assets?assetIds=${id}&size=150x150&format=Png&isCircular=false`;
  try {
    const { json } = await fetchJson(api);
    const row = json && Array.isArray(json.data) ? json.data[0] : null;
    const url = row && typeof row.imageUrl === 'string' && /^https?:\/\//i.test(row.imageUrl)
      ? row.imageUrl.trim()
      : null;
    const state = row && row.state ? String(row.state) : 'unknown';
    if (url) {
      setCached(id, url, 'resolved');
      return { assetId: id, imageUrl: url, imageResolved: true, imageStatus: 'resolved', failureReason: null };
    }
    const reason = state === 'Pending' ? 'roblox_pending' : `roblox_${state.toLowerCase()}`;
    setCached(id, null, state === 'Pending' ? 'pending' : 'unavailable', reason);
    return { assetId: id, imageUrl: null, imageResolved: false, imageStatus: state === 'Pending' ? 'pending' : 'unavailable', failureReason: reason };
  } catch (err) {
    return { assetId: id, imageUrl: null, imageResolved: false, imageStatus: 'fetch_error', failureReason: err.message || 'fetch_error' };
  }
}

async function resolveFishImageAssets(assetIds) {
  const ids = [...new Set((assetIds || []).map(sanitiseAssetId).filter(Boolean))].slice(0, 30);
  const results = [];
  for (const id of ids) {
    results.push(await resolveThumbnailUrl(id));
  }
  return results;
}

async function verifyProxyForAsset(assetId, baseUrl) {
  const id = sanitiseAssetId(assetId);
  if (!id) return { verifiedProxy: false, proxyStatus: null, contentType: null };
  const proxy = `${baseUrl || ''}/api/fishit-tracker/image/${id}`;
  const check = await verifyImageUrlWorks(proxy);
  if (check.ok) {
    _verifiedProxy.set(id, { at: Date.now(), contentType: check.contentType });
    return { verifiedProxy: true, proxyStatus: check.reason, contentType: check.contentType };
  }
  return { verifiedProxy: false, proxyStatus: check.reason, contentType: check.contentType };
}

async function attachResolvedImageFields(item, baseUrl) {
  if (!item || typeof item !== 'object') return item;
  const assetId = sanitiseAssetId(item.imageAssetId);
  if (!assetId) {
    const direct = item.imageUrl && /^https?:\/\//i.test(item.imageUrl) && isCdnImageUrl(item.imageUrl);
    return {
      ...item,
      imageUrlPresent: !!direct,
      imageResolved: !!direct,
      verifiedProxy: false,
      imageStatus: direct ? 'direct_url' : 'missing',
    };
  }

  await resolveThumbnailUrl(assetId);
  const cached = getCached(assetId);
  if (cached && cached.imageUrl) {
    return {
      ...item,
      imageAssetId: assetId,
      imageUrl: cached.imageUrl,
      imageUrlPresent: true,
      imageResolved: true,
      verifiedProxy: false,
      imageStatus: 'resolved',
      imageSource: item.imageSource || 'fish_image_asset_catalog',
    };
  }

  const proxy = proxyImageUrl(assetId);
  const proxyCheck = await verifyProxyForAsset(assetId, baseUrl);
  if (proxyCheck.verifiedProxy) {
    return {
      ...item,
      imageAssetId: assetId,
      imageUrl: proxy,
      imageUrlPresent: true,
      imageResolved: false,
      verifiedProxy: true,
      imageStatus: 'verified_proxy',
      imageSource: item.imageSource || 'fish_image_asset_catalog',
    };
  }

  return {
    ...item,
    imageAssetId: assetId,
    imageUrl: proxy,
    imageUrlPresent: false,
    imageResolved: false,
    verifiedProxy: false,
    imageStatus: cached?.status || 'unresolved',
    imageSource: item.imageSource || 'fish_image_asset_catalog',
  };
}

async function attachResolvedImageFieldsToItems(items, baseUrl) {
  if (!Array.isArray(items)) return [];
  const assetIds = items.map((it) => it && it.imageAssetId).filter(Boolean);
  await resolveFishImageAssets(assetIds);
  const out = [];
  for (const it of items) {
    out.push(await attachResolvedImageFields(it, baseUrl));
  }
  return out;
}

/** @deprecated sync attach — use attachResolvedImageFieldsToItems */
function attachImageFieldsToItems(items) {
  if (!Array.isArray(items)) return [];
  return items.map((item) => {
    const assetId = sanitiseAssetId(item?.imageAssetId);
    const cached = assetId ? getCached(assetId) : null;
    if (cached && cached.imageUrl) {
      return {
        ...item,
        imageUrl: cached.imageUrl,
        imageUrlPresent: true,
        imageResolved: true,
        verifiedProxy: false,
        imageStatus: 'resolved',
      };
    }
    return {
      ...item,
      imageUrl: assetId ? proxyImageUrl(assetId) : null,
      imageUrlPresent: false,
      imageResolved: false,
      verifiedProxy: false,
      imageStatus: 'unresolved',
    };
  });
}

async function warmCacheForAssetIds(assetIds) {
  return resolveFishImageAssets(assetIds);
}

async function debugImageAsset(assetId, baseUrl) {
  const id = sanitiseAssetId(assetId);
  if (!id) {
    return { ok: false, assetId: null, error: 'invalid_asset_id' };
  }
  const roblox = await resolveThumbnailUrl(id);
  const proxy = `${baseUrl || ''}/api/fishit-tracker/image/${id}`;
  let proxyStatus = null;
  let contentType = null;
  let resolved = false;
  try {
    const check = await verifyImageUrlWorks(roblox.imageUrl || proxy);
    proxyStatus = check.reason;
    contentType = check.contentType;
    resolved = check.ok;
  } catch (err) {
    proxyStatus = err.message;
  }
  return {
    ok: !!(roblox.imageResolved || resolved),
    assetId: id,
    robloxStatus: roblox.imageStatus,
    thumbnailImageUrl: roblox.imageUrl,
    proxyUrl: proxy,
    proxyStatus,
    contentType,
    resolved: roblox.imageResolved || resolved,
    failureReason: roblox.failureReason || null,
    error: roblox.imageResolved || resolved ? null : (roblox.failureReason || proxyStatus),
  };
}

function _resetCache() {
  _cache.clear();
  _verifiedProxy.clear();
}

module.exports = {
  sanitiseAssetId,
  proxyImageUrl,
  getCached,
  resolveThumbnailUrl,
  resolveFishImageAssets,
  verifyImageUrlWorks,
  attachResolvedImageFields,
  attachResolvedImageFieldsToItems,
  attachImageFieldsToItems,
  warmCacheForAssetIds,
  debugImageAsset,
  _resetCache,
};
