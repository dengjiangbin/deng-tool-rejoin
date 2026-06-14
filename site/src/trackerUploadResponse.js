'use strict';

const trackerConcurrencyGate = require('./trackerConcurrencyGate');
const {
  recordAccepted202,
  recordDeferredEnrichment,
  recordLatestPersistSuccess,
  recordResponseBeforeEnrichment,
} = require('./trackerRouteMetrics');

function trackerRouteLabel(req) {
  const viaProxy = String(req.headers['x-deng-via-web-proxy'] || '') === '1';
  return viaProxy ? 'web-proxy-fallback' : 'direct-ingest';
}

function shouldReturn202(req, sessionKey) {
  if (req.trackerDeferEnrichment === true) return true;
  if (trackerConcurrencyGate.shouldDeferEnrichmentResponse()) return true;
  if (sessionKey && trackerConcurrencyGate.perAccountPendingCount(sessionKey) > 0) return true;
  return false;
}

function finishTrackerUploadResponse(req, res, responsePayload, sessionKey) {
  const route = trackerRouteLabel(req);
  recordResponseBeforeEnrichment();
  recordLatestPersistSuccess();

  if (shouldReturn202(req, sessionKey)) {
    recordAccepted202();
    recordDeferredEnrichment();
    return res.status(202).json({
      ok: true,
      accepted: responsePayload.accepted !== false,
      deferred: true,
      route,
      status: responsePayload.status || 'success',
      acceptedCount: responsePayload.acceptedCount,
      snapshotComplete: responsePayload.snapshotComplete,
      lastSeenAt: responsePayload.lastSeenAt,
      serverTime: responsePayload.serverTime,
    });
  }

  return res.status(200).json(responsePayload);
}

module.exports = {
  finishTrackerUploadResponse,
  shouldReturn202,
  trackerRouteLabel,
};
