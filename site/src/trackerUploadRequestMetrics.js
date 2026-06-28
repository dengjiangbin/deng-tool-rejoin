'use strict';

const { getLagMs } = require('./trackerEventLoopMonitor');
const trackerConcurrencyGate = require('./trackerConcurrencyGate');

const byRoute = Object.create(null);
const byPayloadType = Object.create(null);
let totalCount = 0;

// Hot-path log guard. The per-upload upload_metric line is a synchronous
// console.log to the WinSW stdout pipe (blocking on Windows under backpressure),
// so on every upload it adds event-loop lag and can starve 8792 /healthz. We
// keep the COUNTERS above always (they feed /api/internal/stability), but only
// emit the per-request line when it carries tracing value (5xx, rejected, or
// slow) or when the loop is not lagging. 5xx and slow requests are NEVER
// suppressed so future 502s stay traceable.
const UPLOAD_METRIC_LOG_LAG_MS = Number(process.env.TRACKER_VERBOSE_LOG_LAG_MS || 1000);
const UPLOAD_METRIC_SLOW_MS = Number(process.env.TRACKER_UPLOAD_METRIC_SLOW_MS || 750);

function shouldLogUploadMetric(statusCode, durationMs, accepted, lagMs) {
  if (statusCode >= 500) return true;            // always trace server errors
  if (!accepted) return true;                    // always trace rejected uploads
  if (durationMs >= UPLOAD_METRIC_SLOW_MS) return true; // always trace slow uploads
  return lagMs < UPLOAD_METRIC_LOG_LAG_MS;        // routine success: only when loop healthy
}

function bucket(map, key) {
  const k = key || 'unknown';
  if (!map[k]) {
    map[k] = { count: 0, ok2xx: 0, err4xx: 0, err5xx: 0, status502: 0, durationSumMs: 0, durationMaxMs: 0 };
  }
  return map[k];
}

function recordUploadRequest(fields) {
  const route = fields.route || 'unknown';
  const payloadType = fields.payloadType || 'unknown';
  const statusCode = Number(fields.statusCode) || 0;
  const durationMs = Number(fields.durationMs) || 0;
  const accepted = fields.accepted === true;

  totalCount += 1;
  for (const [map, key] of [[byRoute, route], [byPayloadType, payloadType]]) {
    const row = bucket(map, key);
    row.count += 1;
    row.durationSumMs += durationMs;
    row.durationMaxMs = Math.max(row.durationMaxMs, durationMs);
    if (statusCode >= 200 && statusCode < 300) row.ok2xx += 1;
    else if (statusCode >= 400 && statusCode < 500) row.err4xx += 1;
    else if (statusCode >= 500) row.err5xx += 1;
    if (statusCode === 502) row.status502 += 1;
  }

  const lagMs = getLagMs();
  if (!shouldLogUploadMetric(statusCode, durationMs, accepted, lagMs)) return;
  const queue = trackerConcurrencyGate.stats();
  console.log(
    '[fishit-tracker] upload_metric route=%s payloadType=%s usernameKey=%s contentLength=%s' +
    ' durationMs=%s statusCode=%s accepted=%s rejectReason=%s errorClass=%s queueDepth=%s eventLoopLagMs=%s',
    route,
    payloadType,
    fields.usernameKey || '?',
    fields.contentLength != null ? fields.contentLength : '?',
    Math.round(durationMs),
    statusCode,
    accepted ? 'true' : 'false',
    fields.rejectReason || '-',
    fields.errorClass || '-',
    queue.queued != null ? queue.queued : '?',
    Math.round(lagMs),
  );
}

function snapshotUploadMetrics() {
  function summarise(map) {
    const out = {};
    for (const [key, row] of Object.entries(map)) {
      out[key] = {
        ...row,
        durationAvgMs: row.count ? Math.round(row.durationSumMs / row.count) : 0,
      };
    }
    return out;
  }
  return {
    totalCount,
    byRoute: summarise(byRoute),
    byPayloadType: summarise(byPayloadType),
  };
}

function _resetForTests() {
  for (const k of Object.keys(byRoute)) delete byRoute[k];
  for (const k of Object.keys(byPayloadType)) delete byPayloadType[k];
  totalCount = 0;
}

module.exports = {
  recordUploadRequest,
  snapshotUploadMetrics,
  _resetForTests,
};
