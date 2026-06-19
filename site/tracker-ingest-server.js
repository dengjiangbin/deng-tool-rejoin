'use strict';

require('./src/wmicRuntimeGuard');

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
  getCachedStabilityJson,
} = require('./src/stabilitySnapshot');

const HOST = process.env.TRACKER_INGEST_HOST || '127.0.0.1';
const PORT = parseInt(process.env.TRACKER_INGEST_PORT || '8792', 10);

// Ingest never runs the stability snapshot loop — periodic disk/JSON rebuilds block the upload event loop.

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

// Keep upstream sockets healthy under burst — Cloudflare/origin reuse connections.
server.keepAliveTimeout = parseInt(process.env.TRACKER_INGEST_KEEPALIVE_MS || '61000', 10);
server.headersTimeout = parseInt(process.env.TRACKER_INGEST_HEADERS_TIMEOUT_MS || '65000', 10);
server.maxRequestsPerSocket = 0;
if (typeof server.setMaxListeners === 'function') server.setMaxListeners(0);

// EADDRINUSE retry — on Windows a restarted process can race the previous
// instance that has not yet released 8792. Retry the bind with backoff
// instead of exiting; exiting here is what produced the 500+ PM2 restart loop
// and an orphan PID permanently holding the port.
// Keep the retry window SHORTER than PM2's listen_timeout (10s). If we retry
// longer than PM2 is willing to wait, PM2 spawns a second child while the first
// is still retrying — overlapping children, one of which binds and detaches
// from PM2's tracked process (the orphan-on-restart symptom). Bounded < 10s,
// a child either binds during a normal restart race or exits for one clean respawn.
const { listenWithReclaim } = require('./src/reclaimPort');
listenWithReclaim(server, PORT, HOST, '[deng-tracker-ingest]', {
  // reclaimAfterMs > PM2 kill_timeout (8000ms): never reclaim a sibling that is
  // still gracefully flushing on restart — that mutual kill was the 8792 loop.
  reclaimAfterMs: parseInt(process.env.TRACKER_INGEST_RECLAIM_AFTER_MS || '9000', 10),
  retryDelayMs: parseInt(process.env.TRACKER_INGEST_LISTEN_RETRY_DELAY_MS || '400', 10),
  maxMs: parseInt(process.env.TRACKER_INGEST_LISTEN_RETRY_MAX_MS || '22000', 10),
});

let shuttingDown = false;
function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`[deng-tracker-ingest] ${signal} received – releasing port then flushing live sessions`);
  // Close the listening socket FIRST so a restarted PM2 instance can bind 8792
  // immediately instead of racing this process (the orphan-PID/EADDRINUSE cause).
  try { server.close(); } catch (_) { /* ignore */ }
  const fishitTrackerRoutes = require('./src/fishitTrackerRoutes');
  Promise.resolve(fishitTrackerRoutes.flushAllLiveSessionsToDisk())
    .then((flushResult) => {
      console.log('[deng-tracker-ingest] shutdown flush saved=%s mode=%s',
        flushResult?.saved ?? 0,
        flushResult?.metrics?.mode || '?');
    })
    .catch((err) => {
      console.warn('[deng-tracker-ingest] shutdown flush error:', err?.message || err);
    })
    .finally(() => {
      process.exit(0);
    });
  setTimeout(() => process.exit(0), 8_000).unref();
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
