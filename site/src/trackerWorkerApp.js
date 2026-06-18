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
// Cadence for the lightweight presence sweep that keeps online/offline + age
// fresh independent of the heavy inventory rebuild. 2s gives sub-2s presence lag
// behind a real Roblox report while staying trivially cheap (a field copy +
// JSON.stringify per session, write only on change).
const PRESENCE_SWEEP_MS = parseInt(process.env.TRACKER_WORKER_PRESENCE_SWEEP_MS || '2000', 10);
const BASE_URL = process.env.TRACKER_PRECOMPUTE_BASE_URL
  || process.env.TOOL_SITE_PUBLIC_URL
  || 'https://aio.deng.my.id';
const METRICS_PATH = process.env.TRACKER_WORKER_METRICS_PATH
  || path.join(__dirname, '..', 'data', 'tracker_worker_metrics.json');

// ── Singleton guard (park-not-exit, liveness heartbeat) ───────────────────
// PM2 restarts / daemon churn can leave ORPHAN worker processes alive that PM2
// no longer tracks. Multiple workers all write the SAME precompute DB, and an
// orphan with a frozen in-memory liveTrackDB will clobber fresh presence/age
// with stale timestamps every idle-refresh tick — making online accounts look
// stale and freezing offline ages.
//
// The PREVIOUS design ("newest startMs wins; a superseded worker process.exit()s")
// fixed split-brain but INTRODUCED a 5-minute-class dead period: exiting feeds
// PM2 autorestart, the restarted worker gets a newer token, supersedes whoever
// is running, that one exits, PM2 restarts it newer… an infinite leapfrog. While
// it leapfrogs NO worker keeps presence_json fresh, so every actively-online
// account's age grows past the 195s hard-offline threshold and reads RED until a
// human manually breaks the loop. That is the reported "online username red for
// ~5 minutes while still playing".
//
// New design: a superseded worker PARKS (stays alive + idle, never writes the DB)
// instead of exiting. Because it never exits, PM2 never restarts it, so no new
// generation is ever spawned and the leapfrog can't start. Exactly ONE worker
// owns the lock and writes; the rest park. Ownership is decided by LIVENESS, not
// just recency: the owner heartbeats the lock every tick, and a parked/booting
// worker only yields to an owner whose pid is ALIVE and whose heartbeat is FRESH.
// A dead or stale owner (crash, kill, hung) is taken over within HB_STALE_MS, so
// there is always a live writer and never two.
const SINGLETON_LOCK_PATH = process.env.TRACKER_WORKER_LOCK_PATH
  || path.join(__dirname, '..', 'data', 'tracker_worker_singleton.json');
const MY_START_MS = Date.now();
const MY_PID = process.pid;
// A token unique to THIS process instance (survives across our own lock rewrites,
// changes across restarts). Used to tell "the lock is mine" from "someone else's".
const MY_TOKEN = `${MY_START_MS}.${MY_PID}`;
// How long an owner's heartbeat may be silent before a parked worker may take
// over. Must be comfortably > TICK_MS and the metrics flush, but well under the
// 195s hard-offline threshold so a real owner death is covered long before any
// account could false-red. 15s = 30 missed 500ms ticks.
// A parked peer waits this long with no heartbeat before declaring the owner dead
// and taking over. Must exceed the worst-case synchronous tick block (heavy
// precompute batch + history cleanup can briefly stall the event loop, which also
// stalls the heartbeat) so two live workers never ping-pong ownership, yet stay
// well under the 195s hard-offline threshold so a real owner death is recovered
// long before any account could false-red. 45s satisfies both.
const SINGLETON_HB_STALE_MS = parseInt(process.env.TRACKER_WORKER_HB_STALE_MS || '45000', 10);
// How often the active owner rewrites its heartbeat (throttle; << stale window).
const SINGLETON_HB_WRITE_MS = parseInt(process.env.TRACKER_WORKER_HB_WRITE_MS || '3000', 10);
let lastHbMs = 0;

function readLock() {
  try {
    if (!fs.existsSync(SINGLETON_LOCK_PATH)) return null;
    return JSON.parse(fs.readFileSync(SINGLETON_LOCK_PATH, 'utf8'));
  } catch (_) {
    return null;
  }
}

function pidAlive(pid) {
  const p = Number(pid) || 0;
  if (!p) return false;
  try {
    // signal 0 = existence/permission probe; throws ESRCH if the pid is gone.
    process.kill(p, 0);
    return true;
  } catch (e) {
    return e && e.code === 'EPERM'; // exists but not signalable → still alive
  }
}

function writeLock(extra) {
  try {
    fs.mkdirSync(path.dirname(SINGLETON_LOCK_PATH), { recursive: true });
    const tmp = `${SINGLETON_LOCK_PATH}.${MY_PID}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(Object.assign({
      startMs: MY_START_MS,
      pid: MY_PID,
      token: MY_TOKEN,
      claimedAt: new Date(MY_START_MS).toISOString(),
      hbAt: Date.now(),
    }, extra || {})));
    fs.renameSync(tmp, SINGLETON_LOCK_PATH);
    return true;
  } catch (_) {
    return false;
  }
}

function claimSingleton() {
  return writeLock();
}

// Refresh OUR heartbeat (only meaningful when we own the lock). Cheap; called
// every tick by the active owner so parked/booting peers see we're alive.
function heartbeatSingleton() {
  return writeLock();
}

// Is the lock holder strictly NEWER than us (later startMs, or same ms + higher
// pid as a deterministic tiebreaker)? Newest-wins makes a freshly-restarted PM2
// worker supersede any lingering older orphan.
function lockIsNewer(lock) {
  const otherStart = Number(lock.startMs) || 0;
  const otherPid = Number(lock.pid) || 0;
  if (otherStart > MY_START_MS) return true;
  if (otherStart === MY_START_MS && otherPid > MY_PID) return true;
  return false;
}

// Is the lock holder a verifiably-live owner (pid alive + heartbeat fresh)?
// Legacy locks (no hbAt) are treated as live so the pure recency rule applies.
function lockOwnerLive(lock) {
  if (!pidAlive(lock.pid)) return false;
  const hbAt = Number(lock.hbAt) || 0;
  if (hbAt <= 0) return true;
  return (Date.now() - hbAt) <= SINGLETON_HB_STALE_MS;
}

// True when a DIFFERENT, strictly-NEWER, verifiably-LIVE worker owns the lock —
// i.e. WE must yield (park). Newest-wins + liveness: we never yield to an older
// worker (we'd take over) nor to a dead/stale newer worker (it crashed). This is
// the exact condition that makes the older peer park forever while the newer
// owner runs — so there is no flapping and no leapfrog. Fail-open on
// missing/corrupt lock so the sole worker is never blocked.
function singletonSuperseded() {
  const lock = readLock();
  if (!lock) return false;
  if (lock.token && lock.token === MY_TOKEN) return false; // it's ours
  if (!lockIsNewer(lock)) return false; // we're newer/equal → we win, not superseded
  const hbAt = Number(lock.hbAt) || 0;
  if (hbAt > 0) return lockOwnerLive(lock); // modern: yield only to a live newer owner
  return true; // legacy lock (no heartbeat): pure recency rule (back-compat)
}

// Decide whether THIS worker should be the active writer. Returns true if we own
// (or just claimed/took over) the lock; false if we must PARK because a newer,
// live owner holds it. Park-not-exit: a parked worker stays alive (so PM2 never
// restarts it → no leapfrog) and re-checks every tick, taking over instantly if
// the newer owner dies or goes stale.
function ensureOwnership() {
  const lock = readLock();
  if (!lock) { claimSingleton(); lastHbMs = Date.now(); return true; }
  if (lock.token === MY_TOKEN) {
    // We own it: refresh heartbeat (throttled — every 500ms tick would be wasteful;
    // SINGLETON_HB_WRITE_MS keeps hbAt comfortably fresh vs the stale window even
    // across heavy ticks).
    const now = Date.now();
    if (now - lastHbMs >= SINGLETON_HB_WRITE_MS) { heartbeatSingleton(); lastHbMs = now; }
    return true;
  }
  // A different worker holds it. Park only if it is a newer, live owner.
  if (lockIsNewer(lock) && lockOwnerLive(lock)) return false;
  // Otherwise WE win: either we're newer, or the holder is dead/stale. Take over.
  claimSingleton();
  lastHbMs = Date.now();
  return true;
}

// Per-key tracking for coalescing + staleness.
const lastSourceSig = new Map(); // key -> updatedAt string used at last precompute
const lastPrecomputedMs = new Map(); // key -> Date.now() of last precompute
const firstDirtySeenMs = new Map(); // key -> when it first became dirty (for oldest-age metric)
const lastPresenceSig = new Map(); // key -> last presence_json string written (heartbeat decoupling)

// The small presence/age fields the read API (8793) derives authoritative
// red/green + ages from. These are written to the lightweight presence record
// on EVERY real heartbeat — even when the inventory body is byte-stable — so an
// actively-uploading account never reads stale "offline" with a frozen age.
const WORKER_PRESENCE_FIELDS = [
  'isOnline', 'trackerBuild', 'lastUploadTrackerBuild',
  'lastAccountSeenAt', 'lastValidStatusAt', 'lastSuccessfulUploadAt',
  'lastSuccessfulHeartbeatAt', 'lastHeartbeatAt', 'lastUploadReceivedAt',
  'lastUploadAcceptedAt', 'lastSeenAt', 'lastSnapshotUploadAt', 'lastInventoryAt',
  'lastStatsUploadAt', 'lastOfflineAt', 'lastFailureReason', 'lastUploadRejectReason',
  'rejectReason', 'lastUploadStatusCodeReturned', 'lastUploadHttpStatus',
  // Source-of-truth report identity — the read API derives online/offline + ages
  // ONLY from these (never from precompute time). The worker copies them through
  // unchanged; it never invents freshness.
  'lastRealRobloxStatusAt', 'statusRevision', 'statusReportId', 'statusSeq',
  'statusSessionId', 'statusCapturedAt', 'statusSentAt', 'serverReceivedStatusAt',
  'statusIdentityReason',
  'lastRealLeaderstatsAt', 'leaderstatsRevision', 'leaderstatsReportId', 'leaderstatsSeq',
  'lastRealInventoryAt', 'inventoryRevision', 'inventoryReportId', 'inventorySeq', 'inventoryHash',
  'reportIdentitySource', 'leaderstatsIdentitySource', 'inventoryIdentitySource',
];

function buildPresenceJson(body) {
  if (!body || typeof body !== 'object') return null;
  const out = {};
  for (const f of WORKER_PRESENCE_FIELDS) {
    if (body[f] !== undefined) out[f] = body[f];
  }
  return JSON.stringify(out);
}

// LIGHTWEIGHT presence sweep. The heavy dirty/rebuild path (sourceSig) keys ONLY
// on inventory/leaderstats content, so a STATUS-ONLY heartbeat (which advances
// lastRealRobloxStatusAt + statusRevision but leaves the inventory body
// byte-stable) would NOT mark the session dirty and would only refresh presence
// on the slow REFRESH_MS (120s) staleness backstop — making an actively-online
// account read RED for up to ~2 minutes. This sweep fixes that the cheap way:
// every tick it rebuilds the tiny presence_json DIRECTLY from the live session
// (every WORKER_PRESENCE_FIELD is a raw ingest-written field, no enrichment
// needed) and writes it ONLY when it actually changed. No heavy JSON blob is
// rewritten, so the read lane is never forced to re-pull a snapshot. This keeps
// online/offline + age fresh within one tick of every real Roblox report while
// never inventing freshness (a duplicate/stale report leaves identity unchanged,
// so presence_json is byte-identical and nothing is written).
function sweepPresence() {
  if (!routes || !routes.liveTrackDB) return 0;
  let updated = 0;
  for (const key of liveKeys()) {
    const data = routes.liveTrackDB[key];
    if (!data || typeof data !== 'object') continue;
    const presenceJson = buildPresenceJson(data);
    if (!presenceJson) continue;
    if (lastPresenceSig.get(key) === presenceJson) continue;
    try {
      precomputeStore.updatePresence(key, presenceJson);
      lastPresenceSig.set(key, presenceJson);
      updated += 1;
    } catch (_) { /* non-fatal: next tick retries */ }
  }
  return updated;
}

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
let parked = false;

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
    const presenceJson = buildPresenceJson(body);
    const prevMeta = precomputeStore.getMeta(key);
    const contentUnchanged = prevMeta && prevMeta.precomputed_hash === precomputedHash;
    // PERF: when the rebuilt body is byte-stable (only the idle staleness
    // backstop fired, no real inventory/leaderstats/status change), do NOT
    // re-UPSERT. Re-writing would bump last_precomputed_at and force the read
    // lane (8793) to re-pull this snapshot's multi-hundred-KB JSON every cache
    // tick — the dominant source of read-lane event-loop stalls. We still mark
    // the staleness clock satisfied in-memory so the backstop does not hot-loop.
    if (contentUnchanged) {
      // Inventory body is byte-stable, so we do NOT rewrite the heavy JSON (that
      // would force the read lane to re-pull this snapshot's blob every tick).
      // BUT presence/heartbeat timestamps may have advanced — refresh the tiny
      // presence record so the read API serves FRESH red/green + age even when
      // the inventory content has not changed. Only write when it actually moved.
      if (presenceJson && lastPresenceSig.get(key) !== presenceJson) {
        try {
          precomputeStore.updatePresence(key, presenceJson);
          lastPresenceSig.set(key, presenceJson);
        } catch (_) { /* non-fatal: next tick retries */ }
      }
      recordBuildMs(buildMs);
      metrics.lastSuccessAt = new Date().toISOString();
      lastSourceSig.set(key, sig);
      lastPrecomputedMs.set(key, Date.now());
      firstDirtySeenMs.delete(key);
      return true;
    }
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
      presenceJson,
    });
    if (presenceJson) lastPresenceSig.set(key, presenceJson);
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
let lastPresenceSweepMs = 0;

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
  // Singleton enforcement (park-not-exit): if another LIVE worker owns the lock
  // we PARK — stay alive but write nothing to the shared precompute DB, so we can
  // never clobber the owner's fresh presence/age AND we never feed PM2 autorestart
  // (which is what created the leapfrog 5-minute dead period). We keep ticking so
  // that the instant the owner dies/goes stale we take ownership and resume,
  // guaranteeing there is always exactly one live writer.
  if (!ensureOwnership()) {
    // PURE PARK: stay alive, write nothing. We never self-exit — a non-owner exit
    // would feed PM2 autorestart and recreate the leapfrog cascade. The current
    // OWNER reaps us (we're an older duplicate) within a few seconds instead.
    if (!parked) {
      parked = true;
      console.log('[deng-tracker-worker] parked — a newer live worker owns the lock (pid=%d startMs=%d); awaiting reap', MY_PID, MY_START_MS);
    }
    metrics.parked = true;
    return;
  }
  if (parked) {
    parked = false;
    console.log('[deng-tracker-worker] resumed ownership — previous owner died/stale (pid=%d startMs=%d)', MY_PID, MY_START_MS);
  }
  metrics.parked = false;
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

    // Decoupled status/presence freshness: keep every account's tiny presence
    // record current within PRESENCE_SWEEP_MS of its last real Roblox report,
    // independent of whether its (heavy) inventory body changed. This is what
    // prevents an actively-online account from reading RED while its heartbeat
    // advances but inventory stays byte-stable.
    const sweepNow = Date.now();
    if (sweepNow - lastPresenceSweepMs >= PRESENCE_SWEEP_MS) {
      lastPresenceSweepMs = sweepNow;
      try {
        const n = sweepPresence();
        if (n) metrics.lastPresenceSweepUpdated = n;
      } catch (_) { /* non-fatal: next tick retries */ }
    }

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
  // Ownership-aware boot: take the lock only if no LIVE owner holds it; otherwise
  // park (the tick loop keeps re-checking and takes over the instant the owner
  // dies/goes stale). This is what stops the PM2 restart leapfrog at the source —
  // a freshly-restarted worker never blindly steals from a live peer.
  if (ensureOwnership()) {
    console.log('[deng-tracker-worker] starting (OWNER) tick=%dms refresh=%dms maxPerTick=%d concurrency=%d base=%s pid=%d startMs=%d',
      TICK_MS, REFRESH_MS, MAX_PER_TICK, CONCURRENCY, BASE_URL, MY_PID, MY_START_MS);
  } else {
    parked = true;
    console.log('[deng-tracker-worker] starting (PARKED — live owner present) pid=%d startMs=%d', MY_PID, MY_START_MS);
  }
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

module.exports = {
  start,
  stop,
  tick,
  metrics,
  _internals: {
    computeDirty,
    precomputeOne,
    claimSingleton,
    singletonSuperseded,
    heartbeatSingleton,
    ensureOwnership,
    readLock,
    pidAlive,
    SINGLETON_LOCK_PATH,
    SINGLETON_HB_STALE_MS,
    MY_START_MS,
    MY_PID,
    MY_TOKEN,
    buildPresenceJson,
    sourceSig,
    sweepPresence,
    WORKER_PRESENCE_FIELDS,
    PRESENCE_SWEEP_MS,
  },
};
