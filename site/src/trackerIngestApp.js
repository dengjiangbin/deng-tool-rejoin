'use strict';

const express = require('express');
const fishitTrackerRoutes = require('./fishitTrackerRoutes');
const trackerConcurrencyGate = require('./trackerConcurrencyGate');
const { getMetrics: getEventLoopMetrics } = require('./trackerEventLoopMonitor');
const { resolveTrustProxySetting } = require('./rateLimitUtils');

const app = express();
app.disable('x-powered-by');
app.set('trust proxy', resolveTrustProxySetting());

const PORT = parseInt(process.env.TRACKER_INGEST_PORT || '8792', 10);

app.get('/health', (_req, res) => {
  res.set('Cache-Control', 'no-store');
  res.json({
    status: 'ok',
    service: 'deng-tracker-ingest',
    port: PORT,
    timestamp: new Date().toISOString(),
    queue: trackerConcurrencyGate.stats(),
    eventLoop: getEventLoopMetrics(),
    memory: {
      heapUsed: process.memoryUsage().heapUsed,
      heapTotal: process.memoryUsage().heapTotal,
    },
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
  });
});

app.use('/', fishitTrackerRoutes.uploadRouter);

// eslint-disable-next-line no-unused-vars
app.use((err, req, res, _next) => {
  if (err && err.type === 'entity.too.large') {
    return res.status(413).json({
      ok: false,
      error: 'payload_too_large',
      limit: err.limit || '512kb',
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
