'use strict';
/**
 * Tracker upload pipeline — inventory snapshots fast-path with coalesced deferred enrichment.
 *
 * inventory_snapshot: handler runs immediately (no global FIFO slot wait).
 * Heavy catalog/catch/enrichment work is coalesced per sessionKey — latest snapshot wins.
 */

const { getLagMs } = require('./trackerEventLoopMonitor');
const {
  recordCoalescedUpload,
  recordDroppedOldQueuedWork,
} = require('./trackerRouteMetrics');

const ENRICHMENT_MAX = Number(process.env.TRACKER_ENRICHMENT_MAX_CONCURRENT || 4);
const QUEUE_MAX = Number(process.env.TRACKER_QUEUE_MAX || 1000);
const LAG_WARN_MS = Number(process.env.TRACKER_EVENT_LOOP_LAG_WARN_MS || 250);
// Shed deep enrichment only when event-loop lag clears this bound. The threshold must
// stay ABOVE the steady-state lag floor produced by periodic shard flush / cache
// refresh (~800ms with ~600 live accounts); setting it below that floor would make
// shouldShedWork() permanently true and starve all deferred enrichment. The three
// fast-path lanes (status/leaderstats/inventory raw persist) always bypass this gate.
const LAG_SHED_MS = Number(process.env.TRACKER_EVENT_LOOP_LAG_SHED_MS || 1000);
// Hard pause: above this event-loop lag, start ZERO new enrichment jobs. The
// ingest thread then drains inbound uploads and answers the Cloudflare tunnel
// in time, which is what prevents edge 530 (origin unreachable) / 502 (bad
// gateway). In-flight jobs finish; new jobs wait until lag recovers. Display
// data (leaderstats/fish/stones/items) is already persisted synchronously on
// the fast path BEFORE enrichment is scheduled, so pausing supplemental
// enrichment never blanks an account — it only delays global catalog/catch
// learning, which self-heals on the next upload once lag clears.
// effectiveEnrichmentMax floored at 1 (the previous behaviour) meant a single
// CPU-bound enrichment job ran back-to-back forever and the loop never
// recovered — a stable bad equilibrium at ~5.8s lag. Allowing 0 breaks it.
const LAG_PAUSE_MS = Number(process.env.TRACKER_EVENT_LOOP_LAG_PAUSE_MS || 2500);
const HEAP_SHED_RATIO = Number(process.env.TRACKER_HEAP_SHED_RATIO || 0.92);

/** @type {Map<string, { fn: Function, enqueuedAt: number }>} */
const deferredPendingByKey = new Map();
const deferredInFlight = new Set();
/** @type {string[]} */
const deferredWaitQueue = [];
const deferredWaitSet = new Set();
let deferredActive = 0;
let deferredSuperseded = 0;
let deferredCompleted = 0;
let droppedJobs = 0;
let squashedJobs = 0;
let shedEvents = 0;
const BACKLOG_WARN_THROTTLE_MS = Number(process.env.TRACKER_BACKLOG_WARN_THROTTLE_MS || 5000);
let _lastBacklogWarnAt = 0;

function isStatusOnlyUpload(req) {
  const body = req && req.body;
  return body && body.type === 'tracker_status';
}

function isLeaderstatsOnlyUpload(req) {
  const body = req && req.body;
  if (!body || typeof body !== 'object') return false;
  if (body.leaderstatsOnlyUpload === true) return true;
  if (body.uploadPath === 'playerdata_leaderstats_only') return true;
  return false;
}

function isFastLaneUpload(req) {
  return isStatusOnlyUpload(req) || isLeaderstatsOnlyUpload(req);
}

function sessionKeyFromRequest(req) {
  const username = req?.body?.username;
  if (!username) return null;
  return String(username).trim().toLowerCase() || null;
}

function effectiveEnrichmentMax() {
  const lag = getLagMs();
  const heapRatio = process.memoryUsage().heapUsed / Math.max(1, process.memoryUsage().heapTotal);
  // Extreme lag: fully pause new enrichment so the loop can recover and keep
  // answering uploads (prevents Cloudflare origin-timeout 530/502).
  if (lag >= LAG_PAUSE_MS) {
    return 0;
  }
  if (lag >= LAG_SHED_MS || heapRatio >= HEAP_SHED_RATIO) {
    return Math.max(1, Math.floor(ENRICHMENT_MAX / 2));
  }
  if (lag >= LAG_WARN_MS) {
    return Math.max(1, ENRICHMENT_MAX - 1);
  }
  return ENRICHMENT_MAX;
}

function shouldShedWork() {
  const lag = getLagMs();
  const heapRatio = process.memoryUsage().heapUsed / Math.max(1, process.memoryUsage().heapTotal);
  return lag >= LAG_SHED_MS || heapRatio >= HEAP_SHED_RATIO;
}

function enqueueDeferredKey(key) {
  if (deferredWaitSet.has(key)) return;
  deferredWaitSet.add(key);
  deferredWaitQueue.push(key);
}

function tryStartDeferredWork() {
  const max = effectiveEnrichmentMax();
  while (deferredActive < max && deferredWaitQueue.length > 0) {
    if (shouldShedWork()) {
      shedEvents += 1;
      break;
    }
    const key = deferredWaitQueue.shift();
    deferredWaitSet.delete(key);
    if (deferredInFlight.has(key) || !deferredPendingByKey.has(key)) continue;
    startDeferredJob(key);
  }
}

function startDeferredJob(key) {
  const job = deferredPendingByKey.get(key);
  if (!job || deferredInFlight.has(key)) return;
  deferredPendingByKey.delete(key);
  deferredInFlight.add(key);
  deferredActive += 1;

  const queuedMs = Date.now() - job.enqueuedAt;
  const supersededOnEnqueue = job.supersededPending === true;
  setImmediate(() => {
    const enrichStart = Date.now();
    let enrichmentMs = 0;
    try {
      const result = job.fn({ enrichmentQueueMs: queuedMs });
      if (result && typeof result.then === 'function') {
        result
          .then(() => {
            enrichmentMs = Date.now() - enrichStart;
            finishDeferred(key, enrichmentMs, queuedMs, supersededOnEnqueue);
          })
          .catch((err) => {
            enrichmentMs = Date.now() - enrichStart;
            console.warn(
              '[tracker-gate] deferred enrichment failed key=%s err=%s enrichMs=%d queueMs=%d',
              key,
              err?.message || err,
              enrichmentMs,
              queuedMs,
            );
            finishDeferred(key, enrichmentMs, queuedMs, supersededOnEnqueue);
          });
        return;
      }
      enrichmentMs = Date.now() - enrichStart;
    } catch (err) {
      enrichmentMs = Date.now() - enrichStart;
      console.warn(
        '[tracker-gate] deferred enrichment failed key=%s err=%s enrichMs=%d queueMs=%d',
        key,
        err?.message || err,
        enrichmentMs,
        queuedMs,
      );
    }
    finishDeferred(key, enrichmentMs, queuedMs, supersededOnEnqueue);
  });
}

function finishDeferred(key, enrichmentMs, queuedMs, supersededOnEnqueue = false) {
  deferredInFlight.delete(key);
  deferredActive = Math.max(0, deferredActive - 1);
  deferredCompleted += 1;
  if (enrichmentMs >= 500) {
    console.warn(
      '[tracker-gate] slow enrichment key=%s enrichMs=%d queueMs=%d pending=%d active=%d',
      key,
      Math.round(enrichmentMs),
      Math.round(queuedMs),
      deferredPendingByKey.size,
      deferredActive,
    );
  }
  if (deferredPendingByKey.has(key)) {
    enqueueDeferredKey(key);
  }
  tryStartDeferredWork();
}

/**
 * Coalesce deferred enrichment per account. Replaces any pending or in-flight snapshot for the same key.
 */
function scheduleDeferredUploadWork(sessionKey, workFn) {
  const key = String(sessionKey || '').trim().toLowerCase();
  if (!key || typeof workFn !== 'function') return;

  // Fast path already persisted — under load skip net-new deferred enrichment to avoid backlog growth.
  if (shouldShedWork() && !deferredPendingByKey.has(key) && !deferredInFlight.has(key)) {
    shedEvents += 1;
    return;
  }

  const totalQueued = deferredPendingByKey.size + deferredWaitQueue.length + deferredInFlight.size;
  if (totalQueued >= QUEUE_MAX && !deferredPendingByKey.has(key) && !deferredInFlight.has(key)) {
    droppedJobs += 1;
    recordDroppedOldQueuedWork();
    return;
  }

  const superseded = deferredPendingByKey.has(key) || deferredInFlight.has(key);
  if (superseded) {
    deferredSuperseded += 1;
    squashedJobs += 1;
    recordCoalescedUpload();
    console.log(
      '[tracker-gate] heavy_job username=%s waitMs=0 runMs=0 superseded=true',
      key,
    );
  }
  deferredPendingByKey.set(key, { fn: workFn, enqueuedAt: Date.now(), supersededPending: superseded });

  if (deferredInFlight.has(key)) {
    return;
  }
  if (deferredActive < effectiveEnrichmentMax() && !shouldShedWork()) {
    startDeferredJob(key);
    return;
  }
  enqueueDeferredKey(key);
  tryStartDeferredWork();
}

// Self-healing drain: while enrichment is paused/shed under lag, inbound uploads
// normally re-trigger tryStartDeferredWork, but if arrivals stall this timer
// guarantees the backlog resumes the moment event-loop lag recovers. Unref'd so
// it never keeps the process alive on shutdown.
const DRAIN_TICK_MS = Number(process.env.TRACKER_GATE_DRAIN_TICK_MS || 250);
const _drainTimer = setInterval(() => {
  if (deferredWaitQueue.length === 0 && deferredPendingByKey.size === 0) return;
  if (effectiveEnrichmentMax() <= 0) return;
  tryStartDeferredWork();
}, DRAIN_TICK_MS);
if (typeof _drainTimer.unref === 'function') _drainTimer.unref();

function getOldestQueueAgeMs() {
  const now = Date.now();
  let oldest = 0;
  for (const job of deferredPendingByKey.values()) {
    oldest = Math.max(oldest, now - job.enqueuedAt);
  }
  return oldest;
}

function shouldDeferEnrichmentResponse() {
  if (shouldShedWork()) return true;
  const pending = deferredPendingByKey.size + deferredWaitQueue.length;
  return pending >= ENRICHMENT_MAX;
}

/**
 * Inventory uploads run immediately. tracker_status bypasses shed gating.
 * Under load, defer enrichment — handler returns 202 after persist, not 503.
 */
function wrapTrackerUpload(label, handler) {
  return function trackerUploadEntry(req, res) {
    const lag = getLagMs();
    const busyLagMs = Number(process.env.TRACKER_UPLOAD_BUSY_LAG_MS || 8000);
    if (isFastLaneUpload(req)) {
      return handler(req, res);
    }
    if (lag >= busyLagMs) {
      req.trackerDeferEnrichment = true;
      req.trackerBusyDefer = true;
    }
    const key = sessionKeyFromRequest(req);
    const pending = deferredPendingByKey.size + deferredWaitQueue.length;
    if (pending >= QUEUE_MAX && key && !deferredPendingByKey.has(key) && !deferredInFlight.has(key)) {
      req.trackerDeferEnrichment = true;
      req.trackerQueueDefer = true;
    }
    if (shouldShedWork() || pending >= ENRICHMENT_MAX * 2) {
      shedEvents += 1;
      req.trackerDeferEnrichment = true;
    }
    // A deep backlog is EXPECTED while enrichment is intentionally paused/shed
    // under lag — logging it per-upload floods stderr (synchronous writes) and
    // worsens the very saturation we are recovering from. Throttle to at most
    // one line per BACKLOG_WARN_THROTTLE_MS.
    if (pending >= ENRICHMENT_MAX * 4) {
      const nowTs = Date.now();
      if (nowTs - _lastBacklogWarnAt >= BACKLOG_WARN_THROTTLE_MS) {
        _lastBacklogWarnAt = nowTs;
        console.warn(
          '[tracker-gate] deep enrichment backlog label=%s sessionKey=%s pending=%d active=%d effectiveMax=%d (throttled 1/%dms)',
          label,
          key || '?',
          pending,
          deferredActive,
          effectiveEnrichmentMax(),
          BACKLOG_WARN_THROTTLE_MS,
        );
      }
    }
    return handler(req, res);
  };
}

function stats() {
  return {
    active: deferredActive,
    queued: deferredPendingByKey.size + deferredWaitQueue.length,
    max: ENRICHMENT_MAX,
    queueMax: QUEUE_MAX,
    deferredPending: deferredPendingByKey.size,
    deferredQueued: deferredWaitQueue.length,
    deferredActive,
    deferredSuperseded,
    deferredCompleted,
    perAccountPending: deferredPendingByKey.size,
    droppedJobs,
    squashedJobs,
    shedEvents,
    effectiveMax: effectiveEnrichmentMax(),
    eventLoopLagMs: getLagMs(),
  };
}

function perAccountPendingCount(sessionKey) {
  const key = String(sessionKey || '').trim().toLowerCase();
  if (!key) return 0;
  return (deferredPendingByKey.has(key) ? 1 : 0) + (deferredInFlight.has(key) ? 1 : 0);
}

function logHeavyJob(username, waitMs, runMs, superseded = false) {
  console.log(
    '[tracker-gate] heavy_job username=%s waitMs=%s runMs=%s superseded=%s',
    username || '?',
    Math.round(Number(waitMs) || 0),
    Math.round(Number(runMs) || 0),
    superseded ? 'true' : 'false',
  );
}

function logQueueStatus() {
  const s = stats();
  console.log(
    '[tracker-gate] upload_queue_status fastQueued=0 heavyQueued=%s perAccountPending=%s heavyActive=%s shed=%s dropped=%s',
    s.deferredQueued,
    s.perAccountPending,
    s.deferredActive,
    s.shedEvents,
    s.droppedJobs,
  );
}

function logUploadTiming(payload) {
  console.log(
    '[fishit-tracker] upload_timing user=%s sessionKey=%s userId=%s' +
    ' gate_wait_ms=%s raw_persist_ms=%s cache_refresh_ms=%s enrichment_queue_ms=%s' +
    ' enrichment_ms=%s total_response_ms=%s pending_queue=%s per_account_pending=%s',
    payload.username || '?',
    payload.sessionKey || '?',
    payload.userId != null ? payload.userId : '?',
    payload.gate_wait_ms != null ? payload.gate_wait_ms : 0,
    payload.raw_persist_ms != null ? payload.raw_persist_ms : '?',
    payload.cache_refresh_ms != null ? payload.cache_refresh_ms : '?',
    payload.enrichment_queue_ms != null ? payload.enrichment_queue_ms : 0,
    payload.enrichment_ms != null ? payload.enrichment_ms : 0,
    payload.total_response_ms != null ? payload.total_response_ms : '?',
    payload.pending_queue != null ? payload.pending_queue : stats().queued,
    payload.per_account_pending != null ? payload.per_account_pending : 0,
  );
}

function _resetForTests() {
  deferredPendingByKey.clear();
  deferredWaitQueue.length = 0;
  deferredWaitSet.clear();
  deferredInFlight.clear();
  deferredActive = 0;
  deferredSuperseded = 0;
  deferredCompleted = 0;
  droppedJobs = 0;
  squashedJobs = 0;
  shedEvents = 0;
}

module.exports = {
  wrapTrackerUpload,
  scheduleDeferredUploadWork,
  stats,
  perAccountPendingCount,
  logUploadTiming,
  logHeavyJob,
  logQueueStatus,
  shouldDeferEnrichmentResponse,
  getOldestQueueAgeMs,
  shouldShedWork,
  _resetForTests,
};
