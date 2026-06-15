'use strict';

/**
 * Per-account current-state session shards — overwrite only, no append history.
 * Avoids full-file JSON.stringify of all accounts on every heartbeat/upload.
 */

const path = require('path');
const fs = require('fs');
const { getLagMs } = require('./trackerEventLoopMonitor');

const RETRYABLE_FS_CODES = new Set(['EBUSY', 'EPERM', 'EACCES', 'ENOENT']);
const ACCOUNT_FLUSH_DEBOUNCE_MS = Number(process.env.FISHIT_ACCOUNT_FLUSH_MS || 300);
const MAX_ACCOUNT_BYTES = Number(process.env.FISHIT_MAX_ACCOUNT_BYTES || 512000);
const MAX_FILE_BYTES_ALERT = Number(process.env.FISHIT_LIVE_JSON_MAX_BYTES || 25_000_000);
// Must comfortably exceed the live concurrent player count. When the cap was
// 200 and >200 accounts were active, trimAccountIndex + reloadChangedAccounts
// constantly evicted/re-added accounts, so get-backpack flapped 404 and the
// frontend intermittently showed nothing. Keep this well above peak players.
const MAX_ACCOUNTS = Number(process.env.FISHIT_MAX_PERSISTED_SESSIONS || 2000);

function shardedRoot() {
  return process.env.FISHIT_LIVE_SESSIONS_DIR
    || path.join(__dirname, '..', 'data', 'fishit_live_sessions');
}

function indexPath() {
  return path.join(shardedRoot(), 'index.json');
}

function accountsDir() {
  return path.join(shardedRoot(), 'accounts');
}

function accountFilePath(key) {
  const safe = String(key || '').toLowerCase().replace(/[^a-z0-9_-]/g, '_').slice(0, 80);
  return path.join(accountsDir(), `${safe || 'unknown'}.json`);
}

function legacyMonolithPath() {
  return process.env.FISHIT_LIVE_SESSIONS_PATH
    || path.join(__dirname, '..', 'data', 'fishit_live_sessions.json');
}

function useShardedStorage() {
  if (process.env.FISHIT_SESSION_SHARDED === '0') return false;
  if (process.env.FISHIT_LIVE_SESSIONS_PATH) return false;
  return true;
}

let _index = null;
let _accountCache = new Map();
let _dirtyAccounts = new Set();
let _accountFlushTimers = new Map();
let _indexDirty = false;
let _flushInFlight = false;
let _pendingFlush = false;
let _lastIndexMtimeMs = 0;
let _flushCount = 0;
let _flushFailCount = 0;
let _lastFlushMs = 0;
let _lastAccountFlushMs = 0;

function defaultIndex() {
  return { updatedAt: null, uidAliases: {}, accounts: {} };
}

async function renameAsyncWithRetry(tmp, target, maxAttempts = 4) {
  let lastErr;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      await fs.promises.rename(tmp, target);
      return;
    } catch (err) {
      lastErr = err;
      if (!RETRYABLE_FS_CODES.has(err.code) || attempt >= maxAttempts - 1) throw err;
      await new Promise((resolve) => setTimeout(resolve, 20 + attempt * 30));
    }
  }
  throw lastErr;
}

function readIndexFromDisk() {
  if (!fs.existsSync(indexPath())) return defaultIndex();
  const raw = JSON.parse(fs.readFileSync(indexPath(), 'utf8'));
  return {
    updatedAt: raw.updatedAt || null,
    uidAliases: raw.uidAliases && typeof raw.uidAliases === 'object' ? raw.uidAliases : {},
    accounts: raw.accounts && typeof raw.accounts === 'object' ? raw.accounts : {},
  };
}

function ensureIndexLoaded() {
  if (_index) return _index;
  _index = readIndexFromDisk();
  try {
    if (fs.existsSync(indexPath())) {
      _lastIndexMtimeMs = fs.statSync(indexPath()).mtimeMs;
    }
  } catch (_) { /* ignore */ }
  return _index;
}

function migrateLegacyMonolithIfNeeded(sanitiseSessionFn) {
  const legacy = legacyMonolithPath();
  if (!fs.existsSync(legacy)) return { migrated: 0 };
  if (fs.existsSync(indexPath())) return { migrated: 0 };
  const raw = JSON.parse(fs.readFileSync(legacy, 'utf8'));
  const sessions = raw.sessions && typeof raw.sessions === 'object' ? raw.sessions : {};
  fs.mkdirSync(accountsDir(), { recursive: true });
  _index = {
    updatedAt: raw.updatedAt || new Date().toISOString(),
    uidAliases: raw.uidAliases && typeof raw.uidAliases === 'object' ? raw.uidAliases : {},
    accounts: {},
  };
  let migrated = 0;
  for (const [key, data] of Object.entries(sessions)) {
    if (key.startsWith('uid:')) continue;
    const row = sanitiseSessionFn(key, data);
    if (!row) continue;
    row.restoredFromDisk = true;
    _accountCache.set(key, row);
    const payload = JSON.stringify(row);
    fs.writeFileSync(accountFilePath(key), payload, 'utf8');
    _index.accounts[key] = {
      updatedAt: row.updatedAt || row.lastSeenAt || _index.updatedAt,
      bytes: Buffer.byteLength(payload, 'utf8'),
    };
    migrated += 1;
  }
  const indexPayload = JSON.stringify(_index);
  fs.writeFileSync(indexPath(), indexPayload, 'utf8');
  const backup = `${legacy}.pre-shard-${Date.now()}.bak`;
  try { fs.renameSync(legacy, backup); } catch (_) { /* keep legacy if rename fails */ }
  _indexDirty = false;
  _dirtyAccounts.clear();
  return { migrated, backup };
}

function applyUidAliases(liveTrackDB) {
  if (!liveTrackDB || !_index) return;
  _index.uidAliases = {};
  for (const [k, v] of Object.entries(liveTrackDB)) {
    if (k.startsWith('uid:') && typeof v === 'string') _index.uidAliases[k] = v;
  }
}

async function flushAccountToDisk(key, row) {
  const started = Date.now();
  const dir = accountsDir();
  await fs.promises.mkdir(dir, { recursive: true });
  const payload = JSON.stringify(row);
  const bytes = Buffer.byteLength(payload, 'utf8');
  if (bytes > MAX_ACCOUNT_BYTES) {
    console.warn('[fishit] account session exceeds max bytes key=%s bytes=%d max=%d', key, bytes, MAX_ACCOUNT_BYTES);
  }
  const target = accountFilePath(key);
  const tmp = `${target}.tmp`;
  await fs.promises.writeFile(tmp, payload, 'utf8');
  await renameAsyncWithRetry(tmp, target);
  ensureIndexLoaded();
  _index.accounts[key] = {
    updatedAt: row.updatedAt || row.lastSeenAt || new Date().toISOString(),
    bytes,
  };
  _index.updatedAt = new Date().toISOString();
  _indexDirty = true;
  _lastAccountFlushMs = Date.now() - started;
  return { bytes, durationMs: _lastAccountFlushMs };
}

async function flushIndexToDisk() {
  if (!_indexDirty || !_index) return { flushed: false };
  const started = Date.now();
  const dir = shardedRoot();
  await fs.promises.mkdir(dir, { recursive: true });
  const tmp = `${indexPath()}.tmp`;
  await fs.promises.writeFile(tmp, JSON.stringify(_index), 'utf8');
  await renameAsyncWithRetry(tmp, indexPath());
  _indexDirty = false;
  try {
    _lastIndexMtimeMs = (await fs.promises.stat(indexPath())).mtimeMs;
  } catch (_) {
    _lastIndexMtimeMs = Date.now();
  }
  return { flushed: true, durationMs: Date.now() - started };
}

function scheduleAccountFlush(key) {
  if (!key) return;
  _dirtyAccounts.add(key);
  if (_accountFlushTimers.has(key)) return;
  const timer = setTimeout(() => {
    _accountFlushTimers.delete(key);
    flushDirtyAccountsAsync().catch(() => {});
  }, ACCOUNT_FLUSH_DEBOUNCE_MS);
  if (typeof timer.unref === 'function') timer.unref();
  _accountFlushTimers.set(key, timer);
}

async function flushDirtyAccountsAsync(options = {}) {
  const priority = options.priority === true;
  if (_dirtyAccounts.size === 0 && !_indexDirty) return { flushed: false };
  if (_flushInFlight) {
    _pendingFlush = true;
    return { flushed: false, inFlight: true };
  }
  const lagMs = getLagMs();
  if (!priority && lagMs > 400) {
    scheduleAccountFlush([..._dirtyAccounts][0]);
    return { flushed: false, deferred: true, lagMs };
  }
  _flushInFlight = true;
  const started = Date.now();
  try {
    const keys = [..._dirtyAccounts];
    _dirtyAccounts.clear();
    for (const key of keys) {
      const row = _accountCache.get(key);
      if (!row) continue;
      await flushAccountToDisk(key, row);
    }
    await flushIndexToDisk();
    _flushCount += 1;
    _lastFlushMs = Date.now() - started;
    return { flushed: true, accounts: keys.length, durationMs: _lastFlushMs };
  } catch (err) {
    _flushFailCount += 1;
    console.warn('[fishit] sharded session flush failed:', err && err.message ? err.message : err);
    return { flushed: false, error: err.message };
  } finally {
    _flushInFlight = false;
    if (_pendingFlush || _dirtyAccounts.size > 0) {
      _pendingFlush = false;
      setImmediate(() => { flushDirtyAccountsAsync().catch(() => {}); });
    }
  }
}

function trimAccountIndex() {
  if (!_index) return;
  const keys = Object.keys(_index.accounts || {});
  if (keys.length <= MAX_ACCOUNTS) return;
  const sorted = keys.sort((a, b) => {
    const ta = Date.parse(_index.accounts[a]?.updatedAt || 0);
    const tb = Date.parse(_index.accounts[b]?.updatedAt || 0);
    return tb - ta;
  });
  for (const drop of sorted.slice(MAX_ACCOUNTS)) {
    delete _index.accounts[drop];
    _accountCache.delete(drop);
    try {
      if (fs.existsSync(accountFilePath(drop))) fs.unlinkSync(accountFilePath(drop));
    } catch (_) { /* ignore */ }
  }
}

function saveAccount(key, row, liveTrackDB) {
  if (!key || !row) return false;
  ensureIndexLoaded();
  _accountCache.set(key, row);
  applyUidAliases(liveTrackDB);
  trimAccountIndex();
  _dirtyAccounts.add(key);
  scheduleAccountFlush(key);
  return true;
}

function loadAllIntoLiveTrackDB(liveTrackDB, sanitiseSessionFn) {
  if (!liveTrackDB || typeof liveTrackDB !== 'object') return { loaded: 0 };
  migrateLegacyMonolithIfNeeded(sanitiseSessionFn);
  ensureIndexLoaded();
  let loaded = 0;
  for (const key of Object.keys(_index.accounts || {})) {
    try {
      const file = accountFilePath(key);
      if (!fs.existsSync(file)) continue;
      const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
      const row = sanitiseSessionFn(key, raw);
      if (!row) continue;
      row.restoredFromDisk = true;
      liveTrackDB[key] = row;
      _accountCache.set(key, row);
      loaded += 1;
    } catch (err) {
      console.warn('[fishit] sharded account load failed key=%s err=%s', key, err.message);
    }
  }
  for (const [alias, usernameKey] of Object.entries(_index.uidAliases || {})) {
    liveTrackDB[alias] = usernameKey;
  }
  return { loaded, path: shardedRoot(), updatedAt: _index.updatedAt || null, mode: 'sharded' };
}

function reloadChangedAccounts(liveTrackDB, sanitiseSessionFn) {
  if (!liveTrackDB || typeof liveTrackDB !== 'object') return { reloaded: false };
  try {
    if (!fs.existsSync(indexPath())) return { reloaded: false, path: shardedRoot() };
    const st = fs.statSync(indexPath());
    if (st.mtimeMs <= _lastIndexMtimeMs) return { reloaded: false };
    const prevKeys = new Set(Object.keys(_index?.accounts || {}));
    _index = readIndexFromDisk();
    _lastIndexMtimeMs = st.mtimeMs;
    let merged = 0;
    for (const key of Object.keys(_index.accounts || {})) {
      const file = accountFilePath(key);
      if (!fs.existsSync(file)) continue;
      const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
      const row = sanitiseSessionFn(key, raw);
      if (!row) continue;
      row.restoredFromDisk = true;
      liveTrackDB[key] = { ...(liveTrackDB[key] || {}), ...row };
      _accountCache.set(key, row);
      merged += 1;
    }
    // Only evict from the in-memory DB when the shard file is genuinely gone.
    // A cross-process index that momentarily omits an account (e.g. another
    // writer mid-flush, or trim churn) must NOT erase a still-persisted account
    // and make the frontend show empty.
    for (const key of prevKeys) {
      if (_index.accounts[key]) continue;
      let fileStillExists = false;
      try { fileStillExists = fs.existsSync(accountFilePath(key)); } catch (_) { fileStillExists = false; }
      if (!fileStillExists) delete liveTrackDB[key];
    }
    for (const [alias, usernameKey] of Object.entries(_index.uidAliases || {})) {
      liveTrackDB[alias] = usernameKey;
    }
    return { reloaded: merged > 0, merged, path: shardedRoot(), updatedAt: _index.updatedAt || null, mode: 'sharded' };
  } catch (err) {
    return { reloaded: false, error: err.message };
  }
}

function getShardedMetrics() {
  ensureIndexLoaded();
  const accountKeys = Object.keys(_index?.accounts || {});
  let totalBytes = 0;
  let maxBytes = 0;
  let maxKey = null;
  for (const [key, meta] of Object.entries(_index.accounts || {})) {
    const b = Number(meta?.bytes) || 0;
    totalBytes += b;
    if (b > maxBytes) {
      maxBytes = b;
      maxKey = key;
    }
  }
  let indexBytes = 0;
  try {
    if (fs.existsSync(indexPath())) indexBytes = fs.statSync(indexPath()).size;
  } catch (_) { /* ignore */ }
  return {
    mode: 'sharded',
    path: shardedRoot(),
    accountCount: accountKeys.length,
    totalAccountBytes: totalBytes,
    indexBytes,
    totalBytes: totalBytes + indexBytes,
    avgAccountBytes: accountKeys.length ? Math.round(totalBytes / accountKeys.length) : 0,
    maxAccountBytes: maxBytes,
    maxAccountKey: maxKey,
    fileSizeAlert: totalBytes + indexBytes > MAX_FILE_BYTES_ALERT,
    pendingDirtyAccounts: _dirtyAccounts.size,
    flushCount: _flushCount,
    flushFailCount: _flushFailCount,
    lastFlushMs: _lastFlushMs,
    lastAccountFlushMs: _lastAccountFlushMs,
  };
}

function resetShardedForTests() {
  for (const timer of _accountFlushTimers.values()) clearTimeout(timer);
  _accountFlushTimers.clear();
  _accountCache = new Map();
  _dirtyAccounts.clear();
  _index = null;
  _indexDirty = false;
  _flushInFlight = false;
  _pendingFlush = false;
  _flushCount = 0;
  _flushFailCount = 0;
  _lastFlushMs = 0;
  _lastIndexMtimeMs = 0;
  try {
    const root = shardedRoot();
    if (fs.existsSync(root)) {
      fs.rmSync(root, { recursive: true, force: true });
    }
  } catch (_) { /* test seam */ }
}

function invalidateReloadCursorForTests() {
  _lastIndexMtimeMs = 0;
}

module.exports = {
  useShardedStorage,
  shardedRoot,
  indexPath,
  legacyMonolithPath,
  migrateLegacyMonolithIfNeeded,
  saveAccount,
  loadAllIntoLiveTrackDB,
  reloadChangedAccounts,
  flushDirtyAccountsAsync,
  scheduleAccountFlush,
  getShardedMetrics,
  resetShardedForTests,
  invalidateReloadCursorForTests,
  ensureIndexLoaded,
};
