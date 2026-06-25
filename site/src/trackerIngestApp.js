'use strict';

const express = require('express');
const fishitTrackerRoutes = require('./fishitTrackerRoutes');
const trackerConcurrencyGate = require('./trackerConcurrencyGate');
const { getMetrics: getEventLoopMetrics, getLagMs } = require('./trackerEventLoopMonitor');
const { resolveTrustProxySetting } = require('./rateLimitUtils');
const { recordIngestRequest, getTrackerRouteMetrics } = require('./trackerRouteMetrics');
const { snapshotUploadMetrics } = require('./trackerUploadRequestMetrics');
const { getSessionStoreFlushMetrics } = require('./fishitSessionStore');
const stabilityRoutes = require('./stabilityRoutes');

const UPLOAD_HANDLER_TIMEOUT_MS = Number(process.env.TRACKER_UPLOAD_HANDLER_TIMEOUT_MS || 0);

const app = express();
app.disable('x-powered-by');
app.set('trust proxy', resolveTrustProxySetting());

const PORT = parseInt(process.env.TRACKER_INGEST_PORT || '8792', 10);

app.use((req, res, next) => {
  res.set('X-DENG-Served-By', 'deng-tracker-ingest');
  res.set('X-DENG-Ingest-Route', String(PORT));
  const viaProxy = String(req.headers['x-deng-via-web-proxy'] || '') === '1';
  recordIngestRequest(viaProxy);
  if (viaProxy) res.set('X-DENG-Tracker-Route', 'web-proxy-fallback');
  else res.set('X-DENG-Tracker-Route', 'direct-ingest');
  // Stamp server-side accept time on every ingest response. Patch the response
  // writers so the header lands before the body is flushed (additive, no change
  // to the upload handler's own logic).
  const startedAt = Date.now();
  const acceptedAt = new Date(startedAt).toISOString();
  res.set('X-DENG-Server-Now', acceptedAt);
  res.set('X-DENG-Upload-Accepted-At', acceptedAt);
  const stamp = () => { if (!res.headersSent) res.set('X-DENG-Ingest-Time-Ms', String(Date.now() - startedAt)); };
  const origJson = res.json.bind(res);
  const origSend = res.send.bind(res);
  res.json = (body) => { stamp(); return origJson(body); };
  res.send = (body) => { stamp(); return origSend(body); };
  next();
});

app.get('/health', (_req, res) => {
  res.set('Cache-Control', 'no-store');
  res.json({
    status: 'ok',
    service: 'deng-tracker-ingest',
    port: PORT,
    timestamp: new Date().toISOString(),
  });
});

app.get('/metrics', (_req, res) => {
  res.set('Cache-Control', 'no-store');
  res.json({
    service: 'deng-tracker-ingest',
    port: PORT,
    timestamp: new Date().toISOString(),
    queue: trackerConcurrencyGate.stats(),
    eventLoop: getEventLoopMetrics(),
    memory: process.memoryUsage(),
    trackerRoute: getTrackerRouteMetrics(),
    uploads: snapshotUploadMetrics(),
    sessionStore: getSessionStoreFlushMetrics(),
  });
});

app.use('/', stabilityRoutes);

app.use((req, res, next) => {
  if (req.method !== 'POST') return next();
  if (!UPLOAD_HANDLER_TIMEOUT_MS || UPLOAD_HANDLER_TIMEOUT_MS <= 0) return next();
  const path = req.path || req.url || '';
  if (!path.includes('fishit-tracker') && !path.includes('tracker/update')) return next();
  const started = Date.now();
  const timer = setTimeout(() => {
    if (res.headersSent) return;
    console.warn('[deng-tracker-ingest] upload handler timeout path=%s ms=%d lagMs=%d',
      path, Date.now() - started, getLagMs());
    res.status(503).json({
      ok: false,
      error: 'tracker_upload_timeout',
      retryable: true,
      route: String(req.headers['x-deng-tracker-route'] || 'direct-ingest'),
    });
  }, UPLOAD_HANDLER_TIMEOUT_MS);
  if (typeof timer.unref === 'function') timer.unref();
  res.once('finish', () => clearTimeout(timer));
  res.once('close', () => clearTimeout(timer));
  next();
});

app.use('/', fishitTrackerRoutes.uploadRouter);

// eslint-disable-next-line no-unused-vars
app.use((err, req, res, _next) => {
  if (err && err.type === 'entity.too.large') {
    return res.status(413).json({
      ok: false,
      error: 'payload_too_large',
      limit: err.limit || (process.env.TRACKER_UPLOAD_BODY_LIMIT || '8mb'),
    });
  }
  console.error('[deng-tracker-ingest] error:', err);
  return res.status(err.status || 500).json({
    ok: false,
    error: 'tracker_ingest_error',
    message: err.message || 'Unexpected tracker ingest error.',
  });
});

module.exports = app;
