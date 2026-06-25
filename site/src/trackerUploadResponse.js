'use strict';

const trackerConcurrencyGate = require('./trackerConcurrencyGate');
const sessionStore = require('./fishitSessionStore');
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
  const responseStartedAt = Date.now();
  const acceptedAt = new Date(responseStartedAt).toISOString();
  recordResponseBeforeEnrichment();
  recordLatestPersistSuccess();
  res.set('X-DENG-Served-By', 'deng-tracker-ingest');
  res.set('X-DENG-Ingest-Route', '8792');
  res.set('X-DENG-Tracker-Route', route);
  res.set('X-DENG-Server-Now', acceptedAt);
  res.set('X-DENG-Upload-Accepted-At', acceptedAt);

  const logResponseSent = (statusCode) => {
    console.log(
      '[fishit-tracker] upload_response_sent route=%s sessionKey=%s status=%d responseMs=%d lagMs=%d beforeDiskFlush=true',
      route,
      sessionKey || '?',
      statusCode,
      Date.now() - responseStartedAt,
      require('./trackerEventLoopMonitor').getLagMs(),
    );
  };

  const schedulePostResponseFlush = () => {
    if (sessionKey && process.env.TRACKER_INGEST_MODE === '1') {
      res.once('finish', () => {
        sessionStore.schedulePriorityFlush();
      });
    }
  };

  if (shouldReturn202(req, sessionKey)) {
    recordAccepted202();
    recordDeferredEnrichment();
    schedulePostResponseFlush();
    logResponseSent(202);
    return res.status(202).json({
      ok: true,
      accepted: responsePayload.accepted !== false,
      deferred: true,
      route,
      minNextUploadSeconds: 60,
      status: responsePayload.status || 'success',
      acceptedCount: responsePayload.acceptedCount,
      snapshotComplete: responsePayload.snapshotComplete,
      lastSeenAt: responsePayload.lastSeenAt,
      serverTime: responsePayload.serverTime,
      serverNow: acceptedAt,
      uploadAcceptedAt: acceptedAt,
    });
  }

  schedulePostResponseFlush();
  logResponseSent(200);
  return res.status(200).json({
    ...responsePayload,
    serverNow: responsePayload.serverNow || acceptedAt,
    uploadAcceptedAt: responsePayload.uploadAcceptedAt || acceptedAt,
  });
}

module.exports = {
  finishTrackerUploadResponse,
  shouldReturn202,
  trackerRouteLabel,
};
