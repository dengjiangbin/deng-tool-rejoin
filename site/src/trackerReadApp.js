'use strict';

/**
 * Read API (Phase 7) — deng-tracker-read, port 8793.
 *
 * Serves precomputed per-user get-backpack snapshots from fishitPrecomputeStore
 * with NO recompute and NO image resolution. This is intentionally a tiny,
 * dependency-light process (it does NOT require the heavy fishitTrackerRoutes
 * module) so reads stay fast and the process memory stays small.
 *
 * Routes:
 *   GET /health
 *   GET /api/tracker/read-health            (+ /api/fishit-tracker/read-health)
 *   GET /api/tracker/get-backpack/:username (+ /api/fishit-tracker/...)
 *   GET /api/tracker/latest/:username        (+ alias)
 *   GET /api/tracker/snapshot/:username      (+ alias)
 *
 * Fallback: during parallel migration, a miss (no precomputed snapshot yet, or a
 * full/debug request we do not cache) is proxied to the legacy read path on the
 * website (8791) with X-DENG-Read-Fallback: 1 so nothing is lost. The fallback
 * is explicit in the headers and is NOT a hidden permanent slow path.
 */

const express = require('express');
const http = require('http');
const fs = require('fs');

const precomputeStore = require('./fishitPrecomputeStore');
const {
  deriveAccountPresenceStatus,
  ACCOUNT_ONLINE_THRESHOLD_MS,
  syncAgeSecondsFromTimestamp,
  parseTimestampMs,
} = require('./trackerAccountPresence');

// Fields deriveAccountPresenceStatus + the age contract read. We extract ONLY
// these (small) from the multi-hundred-KB precomputed body once at ingest time
// so per-request presence is computed from RAM without re-parsing the blob.
const PRESENCE_INPUT_FIELDS = [
  'isOnline', 'trackerBuild', 'lastUploadTrackerBuild',
  'lastAccountSeenAt', 'lastValidStatusAt', 'lastSuccessfulUploadAt',
  'lastSuccessfulHeartbeatAt', 'lastHeartbeatAt', 'lastUploadReceivedAt',
  'lastUploadAcceptedAt', 'lastSeenAt', 'lastSnapshotUploadAt', 'lastInventoryAt',
  'lastStatsUploadAt', 'lastOfflineAt', 'lastFailureReason', 'lastUploadRejectReason',
  'rejectReason', 'lastUploadStatusCodeReturned', 'lastUploadHttpStatus',
];

function extractPresenceInput(body) {
  const out = {};
  if (!body || typeof body !== 'object') return out;
  for (const f of PRESENCE_INPUT_FIELDS) {
    if (body[f] !== undefined) out[f] = body[f];
  }
  return out;
}

function bodyHasRenderableData(body) {
  if (!body || typeof body !== 'object') return false;
  if (body.playerStats && typeof body.playerStats === 'object') return true;
  if (Array.isArray(body.fishItems) && body.fishItems.length) return true;
  if (Array.isArray(body.stoneItems) && body.stoneItems.length) return true;
  if (Array.isArray(body.totemItems) && body.totemItems.length) return true;
  if (body.topCards && typeof body.topCards === 'object') return true;
  if (body.counts && typeof body.counts === 'object') return true;
  return false;
}

const FALLBACK_HOST = process.env.TRACKER_READ_FALLBACK_HOST || process.env.TOOL_SITE_HOST || '127.0.0.1';
const FALLBACK_PORT = parseInt(process.env.TRACKER_READ_FALLBACK_PORT || process.env.TOOL_SITE_PORT || '8791', 10);
const FALLBACK_ENABLED = process.env.TRACKER_READ_FALLBACK !== '0';
// Keep the fallback fast-fail: a miss must not drag the read lane's tail
// latency. In steady state every real (uploaded) user is precomputed, so
// fallback only fires for never-seen/orphan usernames where the legacy path
// has no meaningful data anyway.
const FALLBACK_TIMEOUT_MS = parseInt(process.env.TRACKER_READ_FALLBACK_TIMEOUT_MS || '2500', 10);
const METRICS_PATH = process.env.TRACKER_WORKER_METRICS_PATH
  || require('path').join(__dirname, '..', 'data', 'tracker_worker_metrics.json');
const PORT = parseInt(process.env.TRACKER_READ_PORT || '8793', 10);

// ---------------------------------------------------------------------------
// In-memory snapshot cache.
//
// node:sqlite DatabaseSync is SYNCHRONOUS: hitting SQLite on every read blocks
// the event loop and contends with the worker's WAL writes (busy-wait up to
// busy_timeout), which under concurrency produced 503s and multi-second p99s.
// A read API must serve from RAM. We warm-load every precomputed snapshot once
// at startup, then incrementally sync only CHANGED rows from SQLite on a short
// interval. Per-request reads then touch zero SQLite — pure Map lookups.
// ---------------------------------------------------------------------------
const CACHE_REFRESH_MS = parseInt(process.env.TRACKER_READ_CACHE_REFRESH_MS || '1000', 10);

const cacheByKey = new Map();   // session_key -> { json, lastPrecomputedAt, precomputedHash }
const uidToKey = new Map();     // numeric user_id -> session_key
let cacheMaxPrecomputedAt = ''; // high-water mark for incremental sync
let cacheWarmedAt = 0;
let cacheLastRefreshAt = 0;
let cacheLastRefreshCount = 0;
let cacheLastRefreshJsonPulls = 0;
let cacheRefreshTimer = null;

function ingestRow(row) {
  if (!row || !row.session_key) return;
  const key = String(row.session_key);
  // Parse the blob ONCE per content change to extract the small presence/age
  // inputs. Presence is then derived FRESH on every request (serve time), so a
  // body that stops being rebuilt (idle account, churn-skip) can NEVER keep a
  // stale baked accountPresenceLive=true alive. This is the authoritative source.
  let presenceInput = {};
  let hasRenderableData = false;
  let snapshotSource = 'precomputed';
  try {
    const parsed = JSON.parse(row.latest_precomputed_json);
    presenceInput = extractPresenceInput(parsed);
    hasRenderableData = bodyHasRenderableData(parsed);
    if (parsed && parsed.snapshotSource) snapshotSource = String(parsed.snapshotSource);
  } catch (_) { /* keep defaults; serve will treat as no_data */ }
  cacheByKey.set(key, {
    json: row.latest_precomputed_json,
    lastPrecomputedAt: row.last_precomputed_at || '',
    precomputedHash: row.precomputed_hash || '',
    presenceInput,
    hasRenderableData,
    snapshotSource,
  });
  if (row.user_id != null && row.user_id !== '') {
    uidToKey.set(String(row.user_id), key);
  }
  const at = row.last_precomputed_at || '';
  if (at > cacheMaxPrecomputedAt) cacheMaxPrecomputedAt = at;
}

// Authoritative presence + age contract, computed FRESH per request from the
// stable real timestamps (never from precompute/cache freshness or read time).
function buildPresenceContract(hit, nowMs) {
  const input = (hit && hit.presenceInput) || {};
  const presence = deriveAccountPresenceStatus(input, ACCOUNT_ONLINE_THRESHOLD_MS, nowMs);
  const hasRenderableData = !!(hit && hit.hasRenderableData);
  const lastRealStatusAt = presence.lastAccountSeenAt || null;
  const lastRealInventoryAt = input.lastInventoryAt || input.lastSnapshotUploadAt || null;
  const lastRealLeaderstatsAt = input.lastStatsUploadAt || null;
  const lastRealUploadAt = input.lastSuccessfulUploadAt || input.lastSnapshotUploadAt || lastRealStatusAt || null;
  const isOnline = presence.accountPresenceLive === true;
  let presenceState;
  if (isOnline) presenceState = 'online';
  else if (!hasRenderableData && (presence.accountPresenceReason === 'no_session' || !lastRealStatusAt)) presenceState = 'no_data';
  else presenceState = 'offline';
  return {
    presenceState,
    isOnline,
    accountPresenceLive: isOnline,
    accountPresenceStatus: presence.accountPresenceStatus,
    accountPresenceReason: presence.accountPresenceReason,
    statusAgeSeconds: presence.heartbeatAgeSeconds != null ? presence.heartbeatAgeSeconds : null,
    inventoryAgeSeconds: syncAgeSecondsFromTimestamp(lastRealInventoryAt, nowMs),
    leaderstatsAgeSeconds: syncAgeSecondsFromTimestamp(lastRealLeaderstatsAt, nowMs),
    lastRealStatusAt,
    lastRealUploadAt,
    lastRealInventoryAt,
    lastRealLeaderstatsAt,
    snapshotSource: (hit && hit.snapshotSource) || 'precomputed',
    isFallback: false,
    hasRenderableData,
  };
}

function applyPresenceHeaders(res, c) {
  res.set('X-DENG-Presence-State', c.presenceState);
  res.set('X-DENG-Is-Online', c.isOnline ? '1' : '0');
  res.set('X-DENG-Presence-Reason', c.accountPresenceReason || '');
  if (c.statusAgeSeconds != null) res.set('X-DENG-Status-Age', String(c.statusAgeSeconds));
  if (c.inventoryAgeSeconds != null) res.set('X-DENG-Inventory-Age', String(c.inventoryAgeSeconds));
  if (c.leaderstatsAgeSeconds != null) res.set('X-DENG-Leaderstats-Age', String(c.leaderstatsAgeSeconds));
  if (c.lastRealStatusAt) res.set('X-DENG-Last-Real-Status-At', c.lastRealStatusAt);
  if (c.lastRealInventoryAt) res.set('X-DENG-Last-Real-Inventory-At', c.lastRealInventoryAt);
  if (c.lastRealLeaderstatsAt) res.set('X-DENG-Last-Real-Leaderstats-At', c.lastRealLeaderstatsAt);
  res.set('X-DENG-Snapshot-Source', c.snapshotSource);
  res.set('X-DENG-Has-Renderable', c.hasRenderableData ? '1' : '0');
}

function warmLoadCache() {
  const started = Date.now();
  let rows = [];
  try {
    rows = precomputeStore.getAllRowsForCache();
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error('[read] warm-load failed:', err.message);
    return;
  }
  for (const row of rows) ingestRow(row);
  cacheWarmedAt = Date.now();
  cacheLastRefreshAt = cacheWarmedAt;
  // eslint-disable-next-line no-console
  console.log(`[read] warm-loaded ${cacheByKey.size} snapshots in ${Date.now() - started}ms`);
}

function refreshCache() {
  const started = Date.now();
  // STEP 1 — cheap metadata-only probe (no JSON blob). This is the only query
  // that runs against SQLite every tick in steady state, and it never moves a
  // snapshot's multi-hundred-KB JSON unless that snapshot's content hash
  // actually changed. Keeps the synchronous node:sqlite read off the hot
  // serving event loop.
  let metaRows = [];
  try {
    metaRows = precomputeStore.getChangedMetaSince(cacheMaxPrecomputedAt);
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error('[read] cache refresh failed:', err.message);
    return;
  }
  let changed = 0;
  let jsonPulls = 0;
  for (const meta of metaRows) {
    const key = String(meta.session_key);
    const newHash = meta.precomputed_hash || '';
    const newAt = meta.last_precomputed_at || '';
    const existing = cacheByKey.get(key);
    const contentChanged = !existing || existing.precomputedHash !== newHash;
    if (contentChanged) {
      // STEP 2 — only now pull the heavy JSON, and only for this one row.
      let full = null;
      try {
        full = precomputeStore.getJsonByKey(key);
      } catch (_) { full = null; }
      if (full) {
        jsonPulls += 1;
        changed += 1;
        ingestRow({
          session_key: key,
          user_id: meta.user_id,
          latest_precomputed_json: full.json,
          precomputed_hash: newHash,
          last_precomputed_at: newAt || full.lastPrecomputedAt,
        });
        continue;
      }
    }
    // Unchanged content (or JSON vanished): just advance the watermark/alias
    // bookkeeping without re-reading or re-storing the blob.
    if (existing) {
      if (newAt && newAt !== existing.lastPrecomputedAt) existing.lastPrecomputedAt = newAt;
    }
    if (meta.user_id != null && meta.user_id !== '') uidToKey.set(String(meta.user_id), key);
    if (newAt > cacheMaxPrecomputedAt) cacheMaxPrecomputedAt = newAt;
  }
  cacheLastRefreshAt = Date.now();
  cacheLastRefreshCount = changed;
  cacheLastRefreshJsonPulls = jsonPulls;
  if (changed > 0 && process.env.TRACKER_READ_CACHE_LOG === '1') {
    // eslint-disable-next-line no-console
    console.log(`[read] refreshed ${changed} snapshots (json pulls=${jsonPulls}, probed=${metaRows.length}) in ${Date.now() - started}ms`);
  }
}

function startCache() {
  if (cacheRefreshTimer) return;
  warmLoadCache();
  cacheRefreshTimer = setInterval(refreshCache, CACHE_REFRESH_MS);
  if (cacheRefreshTimer.unref) cacheRefreshTimer.unref();
}

function lookupCached(key) {
  let hit = cacheByKey.get(key);
  if (!hit && /^\d+$/.test(key)) {
    const aliasKey = uidToKey.get(key);
    if (aliasKey) hit = cacheByKey.get(aliasKey);
  }
  return hit || null;
}

const app = express();
app.disable('x-powered-by');

function sanitiseUsername(raw) {
  const s = String(raw == null ? '' : raw).trim();
  if (!s || s.length > 64) return '';
  // Roblox usernames: letters, digits, underscore. Allow numeric userId too.
  if (!/^[A-Za-z0-9_]+$/.test(s)) return '';
  return s;
}

function wantsFallbackOnly(req) {
  // We only cache the lite body. full/debug requests must use the legacy path.
  const q = req.query || {};
  if (q.full === '1' || q.full === 'true') return true;
  if (q.debug !== undefined && q.debug !== '0' && q.debug !== '') return true;
  return false;
}

function setBaseHeaders(res) {
  res.set('X-DENG-Served-By', 'deng-tracker-read');
  res.set('X-DENG-Tracker-Read-Route', '8793');
  res.set('Cache-Control', 'no-store');
}

// A clean "no data" miss. NEVER 502/503/530 — the read lane must not surface
// gateway errors even when the legacy 8791 path is overloaded or down.
function respondMiss(res, reason, mode) {
  if (res.headersSent) return;
  setBaseHeaders(res);
  res.set('X-DENG-Read-Mode', mode || 'miss');
  res.set('X-DENG-Read-Fallback', '0');
  res.set('X-DENG-Precomputed', '0');
  res.set('X-DENG-Fallback-Reason', reason || 'miss');
  res.status(404).json({ error: 'No precomputed snapshot for this user.', reason });
}

function proxyToFallback(req, res, reason) {
  if (!FALLBACK_ENABLED) {
    return respondMiss(res, reason, 'miss');
  }
  const options = {
    host: FALLBACK_HOST,
    port: FALLBACK_PORT,
    method: 'GET',
    path: req.originalUrl,
    headers: {
      ...req.headers,
      host: `${FALLBACK_HOST}:${FALLBACK_PORT}`,
      'x-deng-read-fallback': '1',
    },
  };
  const upstream = http.request(options, (up) => {
    const status = up.statusCode || 0;
    // Do NOT propagate legacy gateway/5xx errors — convert to an honest miss so
    // the read lane can never emit 502/503/530.
    if (status >= 500 || status === 0) {
      up.resume(); // drain
      return respondMiss(res, `fallback_status_${status}`, 'fallback-miss');
    }
    setBaseHeaders(res);
    res.set('X-DENG-Read-Mode', 'fallback');
    res.set('X-DENG-Read-Fallback', '1');
    res.set('X-DENG-Precomputed', '0');
    res.set('X-DENG-Fallback-Reason', reason || 'miss');
    res.status(status);
    if (up.headers['content-type']) res.set('Content-Type', up.headers['content-type']);
    return up.pipe(res);
  });
  upstream.on('error', (err) => respondMiss(res, `fallback_error:${err.message}`, 'fallback-miss'));
  upstream.setTimeout(FALLBACK_TIMEOUT_MS, () => {
    upstream.destroy(new Error('fallback_timeout'));
  });
  upstream.end();
}

function servePrecomputed(req, res) {
  const clean = sanitiseUsername(req.params.username);
  if (!clean) {
    setBaseHeaders(res);
    return res.status(400).json({ error: 'Invalid username.' });
  }
  if (wantsFallbackOnly(req)) {
    return proxyToFallback(req, res, 'full_or_debug_not_cached');
  }
  const key = clean.toLowerCase();
  // Pure in-memory lookup — no SQLite on the hot path.
  const hit = lookupCached(key);
  if (!hit) {
    return proxyToFallback(req, res, 'not_precomputed_yet');
  }
  const now = Date.now();
  const ageMs = hit.lastPrecomputedAt ? (now - Date.parse(hit.lastPrecomputedAt)) : null;
  const contract = buildPresenceContract(hit, now);
  setBaseHeaders(res);
  res.set('X-DENG-Precomputed', '1');
  res.set('X-DENG-Read-Mode', 'precomputed');
  res.set('X-DENG-Read-Fallback', '0');
  if (ageMs != null) res.set('X-DENG-Precomputed-Age-Ms', String(ageMs));
  res.set('X-DENG-Precomputed-At', hit.lastPrecomputedAt || '');
  res.set('X-DENG-Snapshot-Hash', hit.precomputedHash || '');
  applyPresenceHeaders(res, contract);
  res.type('application/json');
  // Content-hash conditional fetch: when the caller already holds this exact
  // snapshot (?h=<hash>), do NOT re-ship the (up to multi-MB, no-cap) body.
  // Return a tiny "unchanged" envelope carrying the fresh authoritative
  // presence/age contract. This kills the repeated large JSON.parse stalls
  // behind the slow updates + 10-minute frontend degradation, while preserving
  // complete data whenever the snapshot actually changes.
  const knownHash = req.query && (req.query.h || req.query.hash);
  if (knownHash && hit.precomputedHash && String(knownHash) === hit.precomputedHash) {
    res.set('X-DENG-Unchanged', '1');
    return res.status(200).send(JSON.stringify({
      unchanged: true,
      snapshotHash: hit.precomputedHash,
      presence: contract,
    }));
  }
  res.set('X-DENG-Unchanged', '0');
  return res.status(200).send(hit.json);
}

app.get('/health', (_req, res) => {
  res.set('Cache-Control', 'no-store');
  res.json({ status: 'ok', service: 'deng-tracker-read', port: PORT, timestamp: new Date().toISOString() });
});

function readHealth(_req, res) {
  res.set('Cache-Control', 'no-store');
  let store = null;
  let worker = null;
  try { store = precomputeStore.getStoreStats(); } catch (err) { store = { error: err.message }; }
  try {
    worker = JSON.parse(fs.readFileSync(METRICS_PATH, 'utf8'));
  } catch (_) { worker = null; }
  const workerStaleMs = worker && worker.updatedAt ? Date.now() - Date.parse(worker.updatedAt) : null;
  res.json({
    status: 'ok',
    service: 'deng-tracker-read',
    port: PORT,
    timestamp: new Date().toISOString(),
    fallback: { enabled: FALLBACK_ENABLED, host: FALLBACK_HOST, port: FALLBACK_PORT },
    cache: {
      size: cacheByKey.size,
      uidAliases: uidToKey.size,
      warmedAt: cacheWarmedAt ? new Date(cacheWarmedAt).toISOString() : null,
      lastRefreshAt: cacheLastRefreshAt ? new Date(cacheLastRefreshAt).toISOString() : null,
      lastRefreshChanged: cacheLastRefreshCount,
      lastRefreshJsonPulls: cacheLastRefreshJsonPulls,
      maxPrecomputedAt: cacheMaxPrecomputedAt || null,
      refreshMs: CACHE_REFRESH_MS,
    },
    store,
    worker,
    workerStaleMs,
  });
}

app.get('/api/tracker/read-health', readHealth);
app.get('/api/fishit-tracker/read-health', readHealth);

app.get('/api/tracker/get-backpack/:username', servePrecomputed);
app.get('/api/fishit-tracker/get-backpack/:username', servePrecomputed);
app.get('/api/tracker/latest/:username', servePrecomputed);
app.get('/api/fishit-tracker/latest/:username', servePrecomputed);
app.get('/api/tracker/snapshot/:username', servePrecomputed);
app.get('/api/fishit-tracker/snapshot/:username', servePrecomputed);

// Any other tracker read that we do not serve precomputed is proxied to 8791 so
// the read lane never silently drops a route during migration.
app.get(['/api/tracker/*', '/api/fishit-tracker/*'], (req, res) => proxyToFallback(req, res, 'unhandled_read_route'));

// Warm the in-memory cache and begin incremental sync as soon as the app is
// loaded (whether started by the server entry point or required in a test).
startCache();

module.exports = app;
module.exports.PORT = PORT;
module.exports.startCache = startCache;
module.exports.refreshCache = refreshCache;
module.exports._cacheStats = () => ({ size: cacheByKey.size, maxPrecomputedAt: cacheMaxPrecomputedAt });
// Exported for unit tests of the authoritative presence/age contract.
module.exports._buildPresenceContract = buildPresenceContract;
module.exports._extractPresenceInput = extractPresenceInput;
module.exports._bodyHasRenderableData = bodyHasRenderableData;
module.exports._ingestForTest = (key, body) => ingestRow({
  session_key: key,
  latest_precomputed_json: typeof body === 'string' ? body : JSON.stringify(body),
  precomputed_hash: 'testhash',
  last_precomputed_at: new Date().toISOString(),
});
module.exports._cacheEntryForTest = (key) => cacheByKey.get(key) || null;
