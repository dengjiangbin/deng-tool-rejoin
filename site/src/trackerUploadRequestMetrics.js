'use strict';

const { getLagMs } = require('./trackerEventLoopMonitor');
const trackerConcurrencyGate = require('./trackerConcurrencyGate');

const byRoute = Object.create(null);
const byPayloadType = Object.create(null);
let totalCount = 0;

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
    Math.round(getLagMs()),
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
