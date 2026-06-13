'use strict';
/**
 * Tracker upload pipeline — inventory snapshots fast-path with coalesced deferred enrichment.
 *
 * inventory_snapshot: handler runs immediately (no global FIFO slot wait).
 * Heavy catalog/catch/enrichment work is coalesced per sessionKey — latest snapshot wins.
 */

const ENRICHMENT_MAX = Number(process.env.TRACKER_ENRICHMENT_MAX_CONCURRENT || 8);

/** @type {Map<string, { fn: Function, enqueuedAt: number }>} */
const deferredPendingByKey = new Map();
const deferredInFlight = new Set();
/** @type {string[]} */
const deferredWaitQueue = [];
const deferredWaitSet = new Set();
let deferredActive = 0;
let deferredSuperseded = 0;
let deferredCompleted = 0;

function isStatusOnlyUpload(req) {
  const body = req && req.body;
  return body && body.type === 'tracker_status';
}

function sessionKeyFromRequest(req) {
  const username = req?.body?.username;
  if (!username) return null;
  return String(username).trim().toLowerCase() || null;
}

function enqueueDeferredKey(key) {
  if (deferredWaitSet.has(key)) return;
  deferredWaitSet.add(key);
  deferredWaitQueue.push(key);
}

function tryStartDeferredWork() {
  while (deferredActive < ENRICHMENT_MAX && deferredWaitQueue.length > 0) {
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
  setImmediate(() => {
    const enrichStart = Date.now();
    let enrichmentMs = 0;
    try {
      const result = job.fn({ enrichmentQueueMs: queuedMs });
      if (result && typeof result.then === 'function') {
        result
          .then(() => {
            enrichmentMs = Date.now() - enrichStart;
            finishDeferred(key, enrichmentMs, queuedMs);
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
            finishDeferred(key, enrichmentMs, queuedMs);
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
    finishDeferred(key, enrichmentMs, queuedMs);
  });
}

function finishDeferred(key, enrichmentMs, queuedMs) {
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

  if (deferredPendingByKey.has(key) || deferredInFlight.has(key)) {
    deferredSuperseded += 1;
  }
  deferredPendingByKey.set(key, { fn: workFn, enqueuedAt: Date.now() });

  if (deferredInFlight.has(key)) {
    return;
  }
  if (deferredActive < ENRICHMENT_MAX) {
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
    deferredPending: deferredPendingByKey.size,
    deferredQueued: deferredWaitQueue.length,
    deferredActive,
    deferredSuperseded,
    deferredCompleted,
    perAccountPending: deferredPendingByKey.size,
  };
}

function perAccountPendingCount(sessionKey) {
  const key = String(sessionKey || '').trim().toLowerCase();
  if (!key) return 0;
  return (deferredPendingByKey.has(key) ? 1 : 0) + (deferredInFlight.has(key) ? 1 : 0);
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
}

module.exports = {
  wrapTrackerUpload,
  scheduleDeferredUploadWork,
  stats,
  perAccountPendingCount,
  logUploadTiming,
  _resetForTests,
};
