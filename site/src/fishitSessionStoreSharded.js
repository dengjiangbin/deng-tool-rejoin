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

// Monotonic activity timestamps, freshest-wins, for the disk-reload freshness
// guard (never clobber a newer in-memory row with an older shard on reload).
const FRESHNESS_FIELDS = [
  'lastUploadReceivedAt', 'lastUploadAcceptedAt', 'lastRealRobloxStatusAt',
  'lastHeartbeatAt', 'lastSeenAt', 'lastAccountSeenAt', 'updatedAt',
  'lastInventoryAt', 'lastStatsUploadAt', 'lastSnapshotUploadAt',
];

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
// Per-shard mtime cache (filename -> mtimeMs) driving the incremental reload so
// the worker tracks every account's latest shard without re-reading 800 files.
let _shardMtimes = new Map();
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

// Rebuild the account index by scanning the shard files themselves, which are
// the real source of truth. Used to self-heal when index.json is missing or
// corrupt so a single bad index file can never permanently block every persist.
function rebuildIndexFromAccounts() {
  const rebuilt = { updatedAt: new Date().toISOString(), uidAliases: {}, accounts: {} };
  let dir;
  try {
    dir = accountsDir();
    if (!fs.existsSync(dir)) return rebuilt;
  } catch (_) {
    return rebuilt;
  }
  let files = [];
  try {
    files = fs.readdirSync(dir).filter((f) => f.endsWith('.json') && !f.endsWith('.tmp'));
  } catch (_) {
    return rebuilt;
  }
  for (const f of files) {
    const full = path.join(dir, f);
    try {
      const stat = fs.statSync(full);
      const txt = fs.readFileSync(full, 'utf8');
      const row = JSON.parse(txt);
      const key = (row && (row.usernameKey || row.username))
        ? String(row.usernameKey || row.username).toLowerCase()
        : f.slice(0, -5);
      rebuilt.accounts[key] = {
        updatedAt: (row && (row.updatedAt || row.lastSeenAt)) || stat.mtime.toISOString(),
        bytes: Buffer.byteLength(txt, 'utf8'),
      };
    } catch (_) {
      // Skip an individual corrupt/unreadable shard rather than aborting the rebuild.
    }
  }
  return rebuilt;
}

function readIndexFromDisk() {
  if (!fs.existsSync(indexPath())) return defaultIndex();
  let raw;
  try {
    raw = JSON.parse(fs.readFileSync(indexPath(), 'utf8'));
  } catch (err) {
    console.warn(
      '[fishit] sharded index unreadable (%s); quarantining and rebuilding from account shards',
      err && err.message ? err.message : err,
    );
    try {
      fs.renameSync(indexPath(), `${indexPath()}.corrupt-${Date.now()}`);
    } catch (_) { /* ignore quarantine failure; rebuild still proceeds */ }
    const rebuilt = rebuildIndexFromAccounts();
    // Force the healed index to be written back on the next flush cycle.
    _indexDirty = true;
    return rebuilt;
  }
  return {
    updatedAt: raw.updatedAt || null,
    uidAliases: raw.uidAliases && typeof raw.uidAliases === 'object' ? raw.uidAliases : {},
    accounts: raw.accounts && typeof raw.accounts === 'object' ? raw.accounts : {},
  };
}

function ensureIndexLoaded() {
  if (_index) return _index;
  try {
    _index = readIndexFromDisk();
  } catch (err) {
    // Last-resort guard: never leave _index null, or every saveAccount would
    // re-read and re-throw forever, silently dropping all uploads.
    console.warn('[fishit] sharded index load failed hard (%s); using empty index', err && err.message ? err.message : err);
    _index = defaultIndex();
  }
  if (!_index || typeof _index !== 'object') _index = defaultIndex();
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

// Highest monotonic activity timestamp on a session row. Used as the freshness
// guard so a disk reload NEVER clobbers a newer in-memory row with older data.
function rowFreshnessMs(row) {
  if (!row || typeof row !== 'object') return 0;
  let best = 0;
  for (const f of FRESHNESS_FIELDS) {
    const v = row[f];
    if (v == null || v === '') continue;
    const ms = typeof v === 'number' ? v : Date.parse(v);
    if (Number.isFinite(ms) && ms > best) best = ms;
  }
  return best;
}

// Per-lane monotonic identity bundles. A lane's sequence number only ever moves
// forward on a genuine new report, so a disk row carrying an OLDER seq must never
// be allowed to overwrite a higher in-memory seq during a cross-process reload —
// otherwise an actively-uploading account's statusSeq/statusReportId visibly
// regresses (and its client_explicit identity reverts) when a slightly-stale
// shard write lands with a fresh timestamp. We keep whichever side has the higher
// seq, carrying that side's full identity bundle so the lane never goes backwards.
const MONOTONIC_LANES = [
  {
    seq: 'statusSeq',
    revision: 'statusRevision',
    fields: [
      'statusSeq', 'statusReportId', 'statusSessionId', 'statusCapturedAt',
      'statusSentAt', 'statusRevision', 'lastRealRobloxStatusAt',
      'serverReceivedStatusAt', 'statusIdentityReason', 'reportIdentitySource',
    ],
  },
  {
    seq: 'leaderstatsSeq',
    revision: 'leaderstatsRevision',
    fields: [
      'leaderstatsSeq', 'leaderstatsReportId', 'leaderstatsSessionId',
      'leaderstatsCapturedAt', 'leaderstatsRevision', 'lastRealLeaderstatsAt',
      'serverReceivedLeaderstatsAt', 'leaderstatsIdentitySource',
    ],
  },
  {
    seq: 'inventorySeq',
    revision: 'inventoryRevision',
    fields: [
      'inventorySeq', 'inventoryReportId', 'inventorySessionId',
      'inventoryCapturedAt', 'inventoryHash', 'inventoryRevision',
      'lastRealInventoryAt', 'serverReceivedInventoryAt', 'inventoryIdentitySource',
    ],
  },
];

function numOr(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function tsMs(value) {
  if (value == null || value === '') return null;
  const ms = typeof value === 'number' ? value : Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
}

function laneSessionId(lane, row) {
  if (!row || typeof row !== 'object') return null;
  if (lane.seq === 'statusSeq') return row.statusSessionId || null;
  if (lane.seq === 'leaderstatsSeq') return row.leaderstatsSessionId || null;
  if (lane.seq === 'inventorySeq') return row.inventorySessionId || null;
  return null;
}

function laneLastRealMs(lane, row) {
  if (!row || typeof row !== 'object') return null;
  if (lane.seq === 'statusSeq') return tsMs(row.lastRealRobloxStatusAt);
  if (lane.seq === 'leaderstatsSeq') return tsMs(row.lastRealLeaderstatsAt);
  if (lane.seq === 'inventorySeq') return tsMs(row.lastRealInventoryAt);
  return null;
}

function laneRealTimestampsAdvanced(existing, row) {
  if (!existing || !row) return false;
  for (const lane of MONOTONIC_LANES) {
    const existingReal = laneLastRealMs(lane, existing);
    const rowReal = laneLastRealMs(lane, row);
    if (rowReal != null && (existingReal == null || rowReal > existingReal + 500)) return true;
  }
  return false;
}

// Mutates `merged` so each lane keeps the higher-seq identity bundle between the
// prior in-memory row (`existing`) and the freshly-merged disk row.
function preserveMonotonicLanes(existing, merged) {
  if (!existing || typeof existing !== 'object' || !merged || typeof merged !== 'object') return merged;
  for (const lane of MONOTONIC_LANES) {
    const existingSid = laneSessionId(lane, existing);
    const mergedSid = laneSessionId(lane, merged);
    const existingReal = laneLastRealMs(lane, existing);
    const mergedReal = laneLastRealMs(lane, merged);
    // A new Roblox join resets seq to a small number. Never keep a stale in-memory
    // high-seq bundle from the prior session when disk carries a newer real lane ts.
    if (existingSid && mergedSid && existingSid !== mergedSid) {
      if (mergedReal != null && (existingReal == null || mergedReal >= existingReal - 1000)) {
        continue;
      }
      for (const f of lane.fields) {
        if (existing[f] != null) merged[f] = existing[f];
      }
      continue;
    }
    const existingSeq = numOr(existing[lane.seq], -Infinity);
    const mergedSeq = numOr(merged[lane.seq], -Infinity);
    if (existingSeq > mergedSeq) {
      // In-memory lane is ahead of disk — restore its whole identity bundle.
      for (const f of lane.fields) {
        if (existing[f] != null) merged[f] = existing[f];
      }
    }
    // Revision is independently monotonic (reinforcement can bump it without seq).
    const existingRev = numOr(existing[lane.revision], -Infinity);
    const mergedRev = numOr(merged[lane.revision], -Infinity);
    if (existingRev > mergedRev) merged[lane.revision] = existing[lane.revision];
  }
  return merged;
}

function reloadChangedAccounts(liveTrackDB, sanitiseSessionFn) {
  if (!liveTrackDB || typeof liveTrackDB !== 'object') return { reloaded: false };
  try {
    const dir = accountsDir();
    if (!fs.existsSync(dir)) return { reloaded: false, path: shardedRoot() };

    // Refresh the index + uid aliases only when index.json actually changed
    // (cheap gate). The index is used for aliases and for conservative eviction
    // of accounts that were genuinely removed — it is NOT the freshness signal.
    let indexChanged = false;
    let prevIndexKeys = null;
    try {
      if (fs.existsSync(indexPath())) {
        const ist = fs.statSync(indexPath());
        if (ist.mtimeMs > _lastIndexMtimeMs) {
          indexChanged = true;
          prevIndexKeys = new Set(Object.keys(_index?.accounts || {}));
          _index = readIndexFromDisk();
          _lastIndexMtimeMs = ist.mtimeMs;
          for (const [alias, usernameKey] of Object.entries(_index.uidAliases || {})) {
            liveTrackDB[alias] = usernameKey;
          }
        }
      }
    } catch (_) { /* index errors are non-fatal; per-shard scan below is source of truth */ }

    // PER-SHARD incremental reload — the authoritative freshness signal. Each
    // account shard is overwritten on EVERY upload WITHOUT touching index.json,
    // so gating reloads on index mtime alone left the read-only worker's
    // liveTrackDB (hence the read API's online/offline + age) stale for minutes
    // between account-set changes — the root cause of an actively-online account
    // intermittently reading RED. We stat every shard each sync and reload only
    // those whose mtime advanced. The freshness guard guarantees we never clobber
    // a newer in-memory row with older disk data, so the ingest (the writer, whose
    // memory is always >= disk) is effectively a no-op while the worker always
    // converges to the latest shard within one sync interval.
    let files;
    try {
      files = fs.readdirSync(dir).filter((f) => f.endsWith('.json') && !f.endsWith('.tmp'));
    } catch (_) {
      return { reloaded: false, path: shardedRoot() };
    }
    let merged = 0;
    const seenFiles = new Set();
    for (const f of files) {
      seenFiles.add(f);
      const full = path.join(dir, f);
      let stat;
      try { stat = fs.statSync(full); } catch (_) { continue; }
      const prevMtime = _shardMtimes.get(f);
      if (prevMtime != null && stat.mtimeMs <= prevMtime) continue; // unchanged since last sync
      let raw;
      try { raw = JSON.parse(fs.readFileSync(full, 'utf8')); } catch (_) { continue; }
      _shardMtimes.set(f, stat.mtimeMs);
      const key = (raw && (raw.usernameKey || raw.username))
        ? String(raw.usernameKey || raw.username).toLowerCase()
        : f.slice(0, -5);
      const row = sanitiseSessionFn(key, raw);
      if (!row) continue;
      const existing = liveTrackDB[key];
      // Never overwrite a strictly-newer in-memory row (protects the ingest's
      // just-received upload from being clobbered by the slightly-older shard).
      // Still reload when any real lane timestamp advanced on disk even if a
      // non-authoritative auxiliary field (lastUploadReceivedAt) is older.
      if (existing && typeof existing === 'object'
        && rowFreshnessMs(existing) > rowFreshnessMs(row)
        && !laneRealTimestampsAdvanced(existing, row)) continue;
      row.restoredFromDisk = true;
      const mergedRow = { ...(existing && typeof existing === 'object' ? existing : {}), ...row };
      preserveMonotonicLanes(existing, mergedRow);
      liveTrackDB[key] = mergedRow;
      _accountCache.set(key, row);
      merged += 1;
    }
    // Forget mtime bookkeeping for shards that vanished.
    if (_shardMtimes.size > seenFiles.size) {
      for (const f of Array.from(_shardMtimes.keys())) {
        if (!seenFiles.has(f)) _shardMtimes.delete(f);
      }
    }

    // Conservative eviction: only when the index explicitly dropped an account
    // AND its shard file is genuinely gone. A cross-process index that momentarily
    // omits an account (another writer mid-flush, trim churn) must NEVER erase a
    // still-persisted account and make the frontend show empty.
    if (indexChanged && prevIndexKeys) {
      for (const key of prevIndexKeys) {
        if (_index.accounts[key]) continue;
        let fileStillExists = false;
        try { fileStillExists = fs.existsSync(accountFilePath(key)); } catch (_) { fileStillExists = false; }
        if (!fileStillExists) delete liveTrackDB[key];
      }
    }

    return { reloaded: merged > 0, merged, path: shardedRoot(), updatedAt: (_index && _index.updatedAt) || null, mode: 'sharded' };
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
  _shardMtimes.clear();
  try {
    const root = shardedRoot();
    if (fs.existsSync(root)) {
      fs.rmSync(root, { recursive: true, force: true });
    }
  } catch (_) { /* test seam */ }
}

function invalidateReloadCursorForTests() {
  _lastIndexMtimeMs = 0;
  _shardMtimes.clear();
}

// Drop only the in-memory index/cache (keep on-disk shards) to simulate a fresh
// process start that must re-read index.json from disk.
function dropInMemoryIndexForTests() {
  _index = null;
  _accountCache = new Map();
  _dirtyAccounts.clear();
  _indexDirty = false;
  _lastIndexMtimeMs = 0;
  _shardMtimes.clear();
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
  preserveMonotonicLanes,
  flushDirtyAccountsAsync,
  scheduleAccountFlush,
  getShardedMetrics,
  resetShardedForTests,
  invalidateReloadCursorForTests,
  dropInMemoryIndexForTests,
  rebuildIndexFromAccounts,
  ensureIndexLoaded,
};
