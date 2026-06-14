'use strict';

const path = require('path');
const dotenv = require('dotenv');

dotenv.config({ path: path.join(__dirname, '.env') });
dotenv.config({ path: path.join(__dirname, '..', '.env') });
dotenv.config({ path: path.join(__dirname, '..', 'env') });
if (process.env.NODE_ENV === 'production') {
  dotenv.config({ path: path.join(__dirname, '..', '.env'), override: true });
  dotenv.config({ path: path.join(__dirname, '.env'), override: true });
}

process.env.TRACKER_INGEST_MODE = '1';
process.env.SKIP_TRACKER_UPLOAD_ROUTES = '0';

const app = require('./src/trackerIngestApp');
const { isTrackerUploadPath } = require('./src/trackerUploadPaths');
const {
  startStabilitySnapshotLoop,
  getCachedStabilityJson,
} = require('./src/stabilitySnapshot');

const HOST = process.env.TRACKER_INGEST_HOST || '127.0.0.1';
const PORT = parseInt(process.env.TRACKER_INGEST_PORT || '8792', 10);

startStabilitySnapshotLoop();

function stabilityAllowed(req) {
  const token = process.env.STABILITY_STATUS_TOKEN || '';
  if (!token) return true;
  const provided = String(req.headers['x-stability-token'] || '');
  const q = String(req.url || '').split('?')[1] || '';
  const params = new URLSearchParams(q);
  return provided === token || params.get('token') === token;
}

function healthPayload() {
  return JSON.stringify({
    status: 'ok',
    service: 'deng-tracker-ingest',
    port: PORT,
    timestamp: new Date().toISOString(),
  });
}

function isRecoverableFsError(err) {
  const code = err && (err.code || err.errno);
  return code === 'EBUSY' || code === 'EPERM' || code === 'EACCES';
}

const server = require('http').createServer((req, res) => {
  const pathOnly = String(req.url || '').split('?')[0];
  if (req.method === 'GET' && pathOnly === '/health') {
    res.writeHead(200, {
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
    });
    res.end(healthPayload());
    return;
  }
  if (req.method === 'GET' && pathOnly === '/api/internal/stability') {
    if (!stabilityAllowed(req)) {
      res.writeHead(403, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      res.end(JSON.stringify({ ok: false, error: 'forbidden' }));
      return;
    }
    res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    res.end(getCachedStabilityJson());
    return;
  }
  if (req.method === 'POST' && !isTrackerUploadPath(req.method, pathOnly)) {
    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: false, error: 'not_found' }));
    return;
  }
  app(req, res);
});

server.listen(PORT, HOST, () => {
  console.log(`[deng-tracker-ingest] Listening on http://${HOST}:${PORT}`);
});

server.on('error', (err) => {
  console.error('[deng-tracker-ingest] Listen error:', err);
  process.exit(1);
});

function shutdown(signal) {
  console.log(`[deng-tracker-ingest] ${signal} received – shutting down`);
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 15_000);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('uncaughtException', (err) => {
  console.error('[deng-tracker-ingest] Uncaught exception:', err);
  if (isRecoverableFsError(err)) return;
  process.exit(1);
});
process.on('unhandledRejection', (reason) => {
  console.error('[deng-tracker-ingest] Unhandled rejection:', reason);
  if (isRecoverableFsError(reason)) return;
  process.exit(1);
});
