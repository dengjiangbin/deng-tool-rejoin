'use strict';
/**
 * Precomputed AIO dataset cache — stale-while-revalidate.
 *
 * bootstrap/manifest read metadata only (no heavy rebuild per request).
 * sync/full returns cached payload immediately; refresh runs in background.
 */

const crypto = require('crypto');

const STALE_MS = {
  profile: 60 * 60 * 1000,
  app: 60 * 1000,
  accounts: 10 * 1000,
  dashboard: 10 * 1000,
  tracker: 10 * 1000,
};

const cache = new Map();
const pendingBuilds = new Map();

function versionHash(value) {
  return crypto.createHash('sha256')
    .update(typeof value === 'string' ? value : JSON.stringify(value))
    .digest('hex')
    .slice(0, 16);
}

function jsonSize(value) {
  return Buffer.byteLength(JSON.stringify(value), 'utf8');
}

function cacheKey(userId, name) {
  return `${String(userId)}:${name}`;
}

function itemCount(name, data) {
  if (!data || typeof data !== 'object') return 0;
  if (name === 'tracker' && Array.isArray(data.accounts)) return data.accounts.length;
  if (name === 'accounts' && Array.isArray(data.accounts)) return data.accounts.length;
  if (name === 'dashboard' && Array.isArray(data.fishCards)) return data.fishCards.length;
  return 0;
}

function wrapData(name, data) {
  const now = Date.now();
  return {
    data,
    version: versionHash(data),
    size: jsonSize(data),
    lastUpdatedAt: new Date(now).toISOString(),
    builtAtMs: now,
    stale: false,
    itemCount: itemCount(name, data),
  };
}

function isFresh(entry, name) {
  if (!entry || entry.stale) return false;
  const ttl = STALE_MS[name] || 60 * 1000;
  return (Date.now() - entry.builtAtMs) < ttl;
}

function getEntry(userId, name) {
  return cache.get(cacheKey(userId, name)) || null;
}

function getMeta(userId, name) {
  const entry = getEntry(userId, name);
  if (!entry) {
    return {
      version: 'pending',
      size: 0,
      lastUpdatedAt: null,
      itemCount: 0,
      cached: false,
    };
  }
  return {
    version: entry.version,
    size: entry.size,
    lastUpdatedAt: entry.lastUpdatedAt,
    itemCount: entry.itemCount,
    cached: true,
    stale: !!entry.stale,
  };
}

function getBuilt(userId, name) {
  const entry = getEntry(userId, name);
  if (!entry) return null;
  return {
    data: entry.data,
    version: entry.version,
    size: entry.size,
    lastUpdatedAt: entry.lastUpdatedAt,
    itemCount: entry.itemCount,
    cached: true,
    stale: !!entry.stale,
  };
}

function setBuilt(userId, name, data) {
  const entry = wrapData(name, data);
  cache.set(cacheKey(userId, name), entry);
  return entry;
}

function markStale(userId, names) {
  const list = Array.isArray(names) ? names : [names];
  for (const name of list) {
    const entry = getEntry(userId, name);
    if (entry) entry.stale = true;
  }
}

function scheduleBuild(userId, name, buildFn, opts = {}) {
  const key = cacheKey(userId, name);
  if (pendingBuilds.has(key) && !opts.force) return;
  pendingBuilds.set(key, true);
  const run = () => {
    Promise.resolve()
      .then(() => buildFn())
      .then((data) => {
        if (data != null) setBuilt(userId, name, data);
      })
      .catch((err) => {
        console.warn('[aio-cache] background build failed', name, err && err.message ? err.message : err);
      })
      .finally(() => {
        pendingBuilds.delete(key);
      });
  };
  if (opts.immediate) run();
  else setImmediate(run);
}

/**
 * Return cached payload immediately. On miss, build once (caller should be sync/full only).
 * When stale, schedule background refresh without blocking.
 */
async function getOrBuild(userId, name, buildFn, opts = {}) {
  const entry = getEntry(userId, name);
  if (entry && !opts.forceBuild) {
    if (!isFresh(entry, name)) {
      scheduleBuild(userId, name, buildFn, opts);
    }
    return {
      data: entry.data,
      version: entry.version,
      size: entry.size,
      lastUpdatedAt: entry.lastUpdatedAt,
      itemCount: entry.itemCount,
    };
  }
  const data = await buildFn();
  const built = setBuilt(userId, name, data);
  return {
    data: built.data,
    version: built.version,
    size: built.size,
    lastUpdatedAt: built.lastUpdatedAt,
    itemCount: built.itemCount,
  };
}

function warmAll(userId, builders) {
  for (const [name, buildFn] of Object.entries(builders)) {
    if (!getEntry(userId, name) || !isFresh(getEntry(userId, name), name)) {
      scheduleBuild(userId, name, buildFn);
    }
  }
}

function invalidateTrackerForOwners(ownerIds) {
  const ids = Array.isArray(ownerIds) ? ownerIds : [ownerIds];
  for (const ownerId of ids) {
    if (!ownerId) continue;
    markStale(ownerId, ['tracker', 'dashboard']);
  }
}

/** After a live upload, refresh owner datasets immediately (fast build). */
function refreshOwnersAfterUpload(ownerIds, builders = {}) {
  const ids = Array.isArray(ownerIds) ? ownerIds : [ownerIds];
  for (const ownerId of ids) {
    if (!ownerId) continue;
    markStale(ownerId, ['tracker', 'dashboard']);
    for (const [name, buildFn] of Object.entries(builders)) {
      scheduleBuild(ownerId, name, buildFn, { force: true, immediate: true });
    }
  }
}

function _resetForTests() {
  cache.clear();
  pendingBuilds.clear();
}

module.exports = {
  STALE_MS,
  getMeta,
  getBuilt,
  getOrBuild,
  setBuilt,
  markStale,
  scheduleBuild,
  warmAll,
  invalidateTrackerForOwners,
  refreshOwnersAfterUpload,
  versionHash,
  jsonSize,
  _resetForTests,
};
