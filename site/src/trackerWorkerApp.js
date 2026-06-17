'use strict';

/**
 * Background precompute worker (Phase 6) — deng-tracker-worker.
 *
 * Responsibilities:
 *   - Keep an in-memory liveTrackDB fresh from the shared session shards
 *     (fishitTrackerRoutes auto-syncs from disk every 2s in web mode).
 *   - For each session, when its raw snapshot changed (new upload) OR the cached
 *     snapshot is stale, rebuild the full get-backpack body via the SHARED
 *     builder (buildBackpackBodyForKey) — this resolves + caches images and
 *     computes the authoritative Ruby Gemstone top card off the read path.
 *   - UPSERT the precomputed snapshot into fishitPrecomputeStore.
 *   - Coalesce per session: only the CURRENT (latest) liveTrackDB row is ever
 *     processed, so older pending uploads for the same user are never wastefully
 *     fully processed.
 *   - Record metrics: queue length, oldest job age, processed/min, failures,
 *     precompute p50/p95, last success time.
 *
 * The worker exposes NO public port. Metrics are written to a JSON file that the
 * read API (8793) surfaces via /api/tracker/read-health.
 */

const fs = require('fs');
const path = require('path');

const precomputeStore = require('./fishitPrecomputeStore');

const TICK_MS = parseInt(process.env.TRACKER_WORKER_TICK_MS || '500', 10);
// Idle backstop only. A full re-enrichment is expensive (CPU-bound ~60ms) so we
// only rebuild on real INVENTORY/leaderstats change (see sourceSig). Presence
// freshness is served by the separate lightweight /account-status poll and the
// client re-derives per-second durations from absolute timestamps in the body,
// so idle snapshots do NOT need frequent rebuilds.
const REFRESH_MS = parseInt(process.env.TRACKER_WORKER_REFRESH_MS || '120000', 10);
const MAX_PER_TICK = parseInt(process.env.TRACKER_WORKER_MAX_PER_TICK || '60', 10);
const CONCURRENCY = parseInt(process.env.TRACKER_WORKER_CONCURRENCY || '4', 10);
const HISTORY_ON_CHANGE = process.env.TRACKER_WORKER_HISTORY !== '0';
const CLEANUP_EVERY_MS = parseInt(process.env.TRACKER_WORKER_CLEANUP_MS || String(5 * 60 * 1000), 10);
const METRICS_FLUSH_MS = parseInt(process.env.TRACKER_WORKER_METRICS_MS || '3000', 10);
const BASE_URL = process.env.TRACKER_PRECOMPUTE_BASE_URL
  || process.env.TOOL_SITE_PUBLIC_URL
  || 'https://aio.deng.my.id';
const METRICS_PATH = process.env.TRACKER_WORKER_METRICS_PATH
  || path.join(__dirname, '..', 'data', 'tracker_worker_metrics.json');

// Per-key tracking for coalescing + staleness.
const lastSourceSig = new Map(); // key -> updatedAt string used at last precompute
const lastPrecomputedMs = new Map(); // key -> Date.now() of last precompute
const firstDirtySeenMs = new Map(); // key -> when it first became dirty (for oldest-age metric)

const buildMsSamples = []; // ring buffer
const BUILD_SAMPLE_MAX = 500;
const processedTimestamps = []; // ms timestamps of successful precomputes (for per-min)

const metrics = {
  service: 'deng-tracker-worker',
  startedAt: new Date().toISOString(),
  tickMs: TICK_MS,
  refreshMs: REFRESH_MS,
  maxPerTick: MAX_PER_TICK,
  concurrency: CONCURRENCY,
  baseUrl: BASE_URL,
  ticks: 0,
  totalProcessed: 0,
  totalFailed: 0,
  lastTickProcessed: 0,
  lastTickDirty: 0,
  queueLength: 0,
  oldestJobAgeMs: 0,
  processedPerMin: 0,
  precomputeP50Ms: 0,
  precomputeP95Ms: 0,
  lastSuccessAt: null,
  lastErrorAt: null,
  lastError: null,
  store: null,
};

let routes = null;
let running = false;
let stopped = false;

function pct(sortedArr, p) {
  if (!sortedArr.length) return 0;
  const idx = Math.min(sortedArr.length - 1, Math.max(0, Math.ceil((p / 100) * sortedArr.length) - 1));
  return Math.round(sortedArr[idx]);
}

function recordBuildMs(ms) {
  buildMsSamples.push(ms);
  if (buildMsSamples.length > BUILD_SAMPLE_MAX) buildMsSamples.shift();
}

function refreshLatencyMetrics() {
  const sorted = [...buildMsSamples].sort((a, b) => a - b);
  metrics.precomputeP50Ms = pct(sorted, 50);
  metrics.precomputeP95Ms = pct(sorted, 95);
  const cutoff = Date.now() - 60 * 1000;
  while (processedTimestamps.length && processedTimestamps[0] < cutoff) processedTimestamps.shift();
  metrics.processedPerMin = processedTimestamps.length;
}

function liveKeys() {
  const db = routes.liveTrackDB || {};
  const keys = [];
  for (const k of Object.keys(db)) {
    if (k.startsWith('uid:')) continue;
    const v = db[k];
    if (!v || typeof v !== 'object') continue; // skip alias strings
    keys.push(k);
  }
  return keys;
}

function sourceSig(data) {
  // INVENTORY-content change signal. We deliberately do NOT key on lastSeenAt /
  // updatedAt (those bump on every status heartbeat with no inventory change),
  // which would force wasteful full re-enrichment ~50k extra times. We rebuild
  // only when the displayed dataset can actually change: inventory snapshot,
  // leaderstats, online/offline transition, or raw row counts.
  const f = Array.isArray(data.playerDataFishItems) ? data.playerDataFishItems.length : 0;
  const s = Array.isArray(data.playerDataStoneItems) ? data.playerDataStoneItems.length : 0;
  const t = Array.isArray(data.playerDataTotemItems) ? data.playerDataTotemItems.length : 0;
  return [
    data.lastInventoryAt || '',
    data.lastStatsUploadAt || data.playerStatsUpdatedAt || '',
    data.lastStatsChangeAt || '',
    data.isOnline ? 1 : 0,
    f, s, t,
  ].join('|');
}

function computeDirty() {
  const now = Date.now();
  const dirty = [];
  for (const key of liveKeys()) {
    const data = routes.liveTrackDB[key];
    const sig = sourceSig(data);
    const prevSig = lastSourceSig.get(key);
    const lastMs = lastPrecomputedMs.get(key) || 0;
    const changed = sig !== prevSig;
    const stale = (now - lastMs) > REFRESH_MS;
    if (changed || stale) {
      if (!firstDirtySeenMs.has(key)) firstDirtySeenMs.set(key, now);
      dirty.push({ key, changed, sig, firstSeen: firstDirtySeenMs.get(key) });
    } else {
      firstDirtySeenMs.delete(key);
    }
  }
  // Fresh uploads (changed) first, then oldest-waiting stale refreshes.
  dirty.sort((a, b) => {
    if (a.changed !== b.changed) return a.changed ? -1 : 1;
    return a.firstSeen - b.firstSeen;
  });
  return dirty;
}

function stableProjection(body) {
  // Hash only content fields (not volatile time-since fields) so history rows
  // are written when the displayed dataset actually changes.
  return JSON.stringify({
    fishItems: body.fishItems || [],
    stoneItems: body.stoneItems || [],
    totemItems: body.totemItems || [],
    playerStats: body.playerStats || null,
    counts: body.counts || null,
    topCards: body.topCards || null,
    status: body.status || null,
    isOnline: body.isOnline === true,
  });
}

function hashString(str) {
  // Lightweight FNV-1a 32-bit hash; sufficient for change detection.
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i += 1) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16);
}

async function precomputeOne(item) {
  const { key, sig } = item;
  const startedAt = Date.now();
  try {
    const res = await routes.buildBackpackBodyForKey(key, {
      wantLite: true,
      baseUrl: BASE_URL,
      syncDisk: false,
    });
    if (res.status !== 200 || !res.body) {
      // Session vanished between scan and build — drop tracking, do not write.
      lastSourceSig.set(key, sig);
      lastPrecomputedMs.set(key, Date.now());
      firstDirtySeenMs.delete(key);
      return false;
    }
    const body = res.body;
    const buildMs = Date.now() - startedAt;
    const json = JSON.stringify(body);
    const precomputedHash = hashString(stableProjection(body));
    const rubyCount = body.topCards && body.topCards.rubyGemstone
      ? Number(body.topCards.rubyGemstone.count) || 0
      : 0;
    const prevMeta = precomputeStore.getMeta(key);
    precomputeStore.upsertLatest({
      sessionKey: key,
      username: body.username || key,
      userId: body.userId || null,
      precomputedJson: json,
      precomputedHash,
      rawHash: hashString(sig),
      rubyGemstoneCount: rubyCount,
      fishTypeCount: Array.isArray(body.fishItems) ? body.fishItems.length : 0,
      buildMs,
      lastUploadAt: body.lastSnapshotUploadAt || body.updatedAt || null,
      lastInventoryAt: body.lastInventoryAt || null,
    });
    if (HISTORY_ON_CHANGE && (!prevMeta || prevMeta.precomputed_hash !== precomputedHash)) {
      try { precomputeStore.recordHistory(key, precomputedHash, rubyCount); } catch (_) { /* non-fatal */ }
    }
    recordBuildMs(buildMs);
    processedTimestamps.push(Date.now());
    metrics.totalProcessed += 1;
    metrics.lastSuccessAt = new Date().toISOString();
    lastSourceSig.set(key, sig);
    lastPrecomputedMs.set(key, Date.now());
    firstDirtySeenMs.delete(key);
    return true;
  } catch (err) {
    metrics.totalFailed += 1;
    metrics.lastErrorAt = new Date().toISOString();
    metrics.lastError = `${key}: ${err && err.message ? err.message : err}`;
    // Back off this key's staleness clock so one bad payload does not hot-loop.
    lastPrecomputedMs.set(key, Date.now());
    return false;
  }
}

async function processBatch(batch) {
  let i = 0;
  async function worker() {
    while (i < batch.length) {
      const item = batch[i];
      i += 1;
      // eslint-disable-next-line no-await-in-loop
      await precomputeOne(item);
    }
  }
  const runners = [];
  for (let c = 0; c < Math.max(1, CONCURRENCY); c += 1) runners.push(worker());
  await Promise.all(runners);
}

let lastCleanupMs = 0;
let lastMetricsFlushMs = 0;

function flushMetrics(force) {
  const now = Date.now();
  if (!force && now - lastMetricsFlushMs < METRICS_FLUSH_MS) return;
  lastMetricsFlushMs = now;
  refreshLatencyMetrics();
  try {
    metrics.store = precomputeStore.getStoreStats();
  } catch (_) { /* ignore */ }
  metrics.updatedAt = new Date().toISOString();
  try {
    fs.mkdirSync(path.dirname(METRICS_PATH), { recursive: true });
    fs.writeFileSync(METRICS_PATH, JSON.stringify(metrics, null, 2));
  } catch (_) { /* ignore */ }
}

async function tick() {
  if (running || stopped) return;
  running = true;
  try {
    metrics.ticks += 1;
    const dirty = computeDirty();
    metrics.queueLength = dirty.length;
    metrics.oldestJobAgeMs = dirty.length ? (Date.now() - dirty[dirty.length - 1].firstSeen) : 0;
    const batch = dirty.slice(0, MAX_PER_TICK);
    metrics.lastTickDirty = dirty.length;
    metrics.lastTickProcessed = batch.length;
    if (batch.length) await processBatch(batch);

    const now = Date.now();
    if (now - lastCleanupMs > CLEANUP_EVERY_MS) {
      lastCleanupMs = now;
      try {
        const removed = precomputeStore.cleanupHistory();
        if (removed) console.log('[deng-tracker-worker] history cleanup removed=%d rows', removed);
      } catch (_) { /* ignore */ }
    }
    flushMetrics(false);
  } catch (err) {
    metrics.lastErrorAt = new Date().toISOString();
    metrics.lastError = `tick: ${err && err.message ? err.message : err}`;
    console.error('[deng-tracker-worker] tick error:', err);
  } finally {
    running = false;
  }
}

function start() {
  // Ensure web-mode disk sync runs in this process so liveTrackDB stays fresh.
  process.env.TRACKER_WEB_MODE = process.env.TRACKER_WEB_MODE || '1';
  process.env.SKIP_TRACKER_UPLOAD_ROUTES = process.env.SKIP_TRACKER_UPLOAD_ROUTES || '1';
  routes = require('./fishitTrackerRoutes');
  precomputeStore.openDb();
  console.log('[deng-tracker-worker] starting tick=%dms refresh=%dms maxPerTick=%d concurrency=%d base=%s',
    TICK_MS, REFRESH_MS, MAX_PER_TICK, CONCURRENCY, BASE_URL);
  // Force an initial disk load before the first tick.
  try { routes.syncLiveTrackFromDisk(); } catch (_) { /* ignore */ }
  const timer = setInterval(tick, TICK_MS);
  if (typeof timer.unref === 'function') { /* keep process alive: do NOT unref */ }
  // First tick shortly after boot.
  setTimeout(tick, 1500);
  return { tick, metrics };
}

function stop() {
  stopped = true;
  flushMetrics(true);
}

module.exports = { start, stop, tick, metrics, _internals: { computeDirty, precomputeOne } };
