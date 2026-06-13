'use strict';
/**
 * Tracker upload pipeline — inventory snapshots fast-path with coalesced deferred enrichment.
 *
 * inventory_snapshot: handler runs immediately (no global FIFO slot wait).
 * Heavy catalog/catch/enrichment work is coalesced per sessionKey — latest snapshot wins.
 */

const { getLagMs } = require('./trackerEventLoopMonitor');

const ENRICHMENT_MAX = Number(process.env.TRACKER_ENRICHMENT_MAX_CONCURRENT || 4);
const QUEUE_MAX = Number(process.env.TRACKER_QUEUE_MAX || 1000);
const LAG_WARN_MS = Number(process.env.TRACKER_EVENT_LOOP_LAG_WARN_MS || 250);
const LAG_SHED_MS = Number(process.env.TRACKER_EVENT_LOOP_LAG_SHED_MS || 1000);
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

function isStatusOnlyUpload(req) {
  const body = req && req.body;
  return body && body.type === 'tracker_status';
}

function sessionKeyFromRequest(req) {
  const username = req?.body?.username;
  if (!username) return null;
  return String(username).trim().toLowerCase() || null;
}

function effectiveEnrichmentMax() {
  const lag = getLagMs();
  const heapRatio = process.memoryUsage().heapUsed / Math.max(1, process.memoryUsage().heapTotal);
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

  const totalQueued = deferredPendingByKey.size + deferredWaitQueue.length + deferredInFlight.size;
  if (totalQueued >= QUEUE_MAX && !deferredPendingByKey.has(key) && !deferredInFlight.has(key)) {
    droppedJobs += 1;
    return;
  }

  const superseded = deferredPendingByKey.has(key) || deferredInFlight.has(key);
  if (superseded) {
    deferredSuperseded += 1;
    squashedJobs += 1;
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

/**
 * Inventory uploads run immediately. tracker_status bypasses all gating.
 */
function wrapTrackerUpload(label, handler) {
  return function trackerUploadEntry(req, res) {
    if (isStatusOnlyUpload(req)) {
      return handler(req, res);
    }
    const key = sessionKeyFromRequest(req);
    const pending = deferredPendingByKey.size + deferredWaitQueue.length;
    if (pending >= QUEUE_MAX) {
      return res.status(503).json({ ok: false, error: 'tracker_queue_full' });
    }
    if (shouldShedWork()) {
      shedEvents += 1;
      return res.status(503).json({ ok: false, error: 'tracker_backpressure' });
    }
    if (pending >= ENRICHMENT_MAX * 4) {
      console.warn(
        '[tracker-gate] deep enrichment backlog label=%s sessionKey=%s pending=%d active=%d',
        label,
        key || '?',
        pending,
        deferredActive,
      );
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
  _resetForTests,
};
