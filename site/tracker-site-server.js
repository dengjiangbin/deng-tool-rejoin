'use strict';

require('./src/wmicRuntimeGuard');
require('./src/loadSiteEnv').loadSiteEnv();

const { createTrackerUploadProxy, shouldProxyTrackerUpload } = require('./src/trackerUploadProxy');
const {
  createTrackerReadProxy,
  shouldProxyTrackerRead,
  isTrackerReadHealthPath,
  handleTrackerReadHealth,
} = require('./src/trackerReadProxy');
const {
  startStabilitySnapshotLoop,
  getCachedStabilityJson,
} = require('./src/stabilitySnapshot');
const { listenWithReclaim } = require('./src/reclaimPort');
const { sendHealthz } = require('./src/healthz');
const { wrapHttpHandler } = require('./src/requestAccessLog');

startStabilitySnapshotLoop();

function stabilityAllowed(req) {
  const token = process.env.STABILITY_STATUS_TOKEN || '';
  if (!token) return true;
  const provided = String(req.headers['x-stability-token'] || '');
  const q = String(req.url || '').split('?')[1] || '';
  const params = new URLSearchParams(q);
  return provided === token || params.get('token') === token;
}

const app = require('./src/trackerSiteApp');

const HOST = process.env.TOOL_SITE_HOST || '127.0.0.1';
const PORT = parseInt(process.env.TOOL_SITE_PORT || '8791', 10);

function isRecoverableFsError(err) {
  const code = err && (err.code || err.errno);
  return code === 'EBUSY' || code === 'EPERM' || code === 'EACCES';
}

const server = require('http').createServer(wrapHttpHandler('deng-tracker-site', (req, res) => {
  const pathOnly = String(req.url || '').split('?')[0];
  if (req.method === 'GET' && pathOnly === '/healthz') {
    return sendHealthz(res, 'deng-tracker-site', PORT);
  }
  if (req.method === 'GET' && pathOnly === '/health') {
    res.writeHead(200, {
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
    });
    res.end(JSON.stringify({
      status: 'ok',
      service: 'deng-tracker-site',
      port: PORT,
      timestamp: new Date().toISOString(),
    }));
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
  if (process.env.TRACKER_UPLOAD_PROXY !== '0' && shouldProxyTrackerUpload(req)) {
    return createTrackerUploadProxy()(req, res);
  }
  if (process.env.TRACKER_READ_PROXY !== '0' && isTrackerReadHealthPath(pathOnly)) {
    return handleTrackerReadHealth(req, res);
  }
  if (process.env.TRACKER_READ_PROXY !== '0' && shouldProxyTrackerRead(req)) {
    return createTrackerReadProxy()(req, res);
  }
  app(req, res);
}, (req) => {
  const pathOnly = String(req.url || '').split('?')[0];
  let lane = 'tracker-site';
  if (shouldProxyTrackerUpload(req)) lane = 'upload-proxy';
  else if (shouldProxyTrackerRead(req)) lane = 'read-proxy';
  return { lane, path: pathOnly };
}));

if (typeof server.setMaxListeners === 'function') server.setMaxListeners(0);
server.keepAliveTimeout = parseInt(process.env.TOOL_SITE_KEEPALIVE_MS || '5000', 10);
server.headersTimeout = parseInt(process.env.TOOL_SITE_HEADERS_TIMEOUT_MS || '10000', 10);
server.maxRequestsPerSocket = 0;

listenWithReclaim(server, PORT, HOST, '[deng-tracker-site]', {
  pm2AppName: 'deng-tracker-site',
  reclaimAfterMs: parseInt(process.env.TOOL_SITE_RECLAIM_AFTER_MS || '9000', 10),
  retryDelayMs: parseInt(process.env.TOOL_SITE_LISTEN_RETRY_DELAY_MS || '400', 10),
  maxMs: parseInt(process.env.TOOL_SITE_LISTEN_RETRY_MAX_MS || '22000', 10),
});

let siteShuttingDown = false;
function shutdown(signal) {
  if (siteShuttingDown) return;
  siteShuttingDown = true;
  console.log(`[deng-tracker-site] ${signal} received – releasing port then flushing live sessions`);
  try { server.close(() => console.log('[deng-tracker-site] HTTP server closed')); } catch (_) { /* ignore */ }
  try { if (typeof server.closeAllConnections === 'function') server.closeAllConnections(); } catch (_) { /* ignore */ }
  const fishitTrackerRoutes = require('./src/fishitTrackerRoutes');
  Promise.resolve(fishitTrackerRoutes.flushAllLiveSessionsToDisk())
    .then((flushResult) => {
      console.log('[deng-tracker-site] shutdown flush saved=%s mode=%s',
        flushResult?.saved ?? 0,
        flushResult?.metrics?.mode || '?');
    })
    .catch((err) => {
      console.warn('[deng-tracker-site] shutdown flush error:', err?.message || err);
    })
    .finally(() => {
      process.exit(0);
    });
  setTimeout(() => process.exit(0), 8_000).unref();
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('uncaughtException', (err) => {
  console.error('[deng-tracker-site] Uncaught exception:', err);
  if (isRecoverableFsError(err)) return;
  process.exit(1);
});
process.on('unhandledRejection', (reason) => {
  console.error('[deng-tracker-site] Unhandled rejection:', reason);
  if (isRecoverableFsError(reason)) return;
  process.exit(1);
});
