'use strict';

require('./src/wmicRuntimeGuard');

// Load env in priority order (dotenv never overrides already-set vars):
//   1. process.env (always wins – PM2 / system env)
//   2. site/.env   (portal-specific overrides)
//   3. ../.env     (project root – shared Discord/Supabase credentials)
//   4. ../env      (same root, alternate filename some setups use)
const path   = require('path');
const dotenv = require('dotenv');
const rootEnv = path.join(__dirname, '..', '.env');
const siteEnv = path.join(__dirname, '.env');
// Load env in priority order (dotenv never overrides already-set vars by default):
//   1. process.env (PM2 / system env)
//   2. site/.env
//   3. ../.env
dotenv.config({ path: siteEnv });
dotenv.config({ path: rootEnv });
dotenv.config({ path: path.join(__dirname, '..', 'env') });
// In production, real portal credentials from .env must win over stale PM2/test shell vars
// (e.g. SUPABASE_URL=https://placeholder.supabase.co left from prior test runs).
if (process.env.NODE_ENV === 'production') {
  dotenv.config({ path: rootEnv, override: true });
  dotenv.config({ path: siteEnv, override: true });
}

const app = require('./src/app');
const { isStateSecretConfigured } = require('./src/crypto');
const { createTrackerUploadProxy, shouldProxyTrackerUpload } = require('./src/trackerUploadProxy');
const {
  startStabilitySnapshotLoop,
  getCachedStabilityJson,
} = require('./src/stabilitySnapshot');

startStabilitySnapshotLoop();

function stabilityAllowed(req) {
  const token = process.env.STABILITY_STATUS_TOKEN || '';
  if (!token) return true;
  const provided = String(req.headers['x-stability-token'] || '');
  const q = String(req.url || '').split('?')[1] || '';
  const params = new URLSearchParams(q);
  return provided === token || params.get('token') === token;
}

if (!isStateSecretConfigured()) {
  console.error(
    '[deng-tool-site] FATAL: TOOL_SITE_STATE_SECRET is missing or shorter than 32 characters. '
    + 'License key provider redirects cannot be signed until this is set in .env',
  );
  process.exit(1);
}

const HOST = process.env.TOOL_SITE_HOST || '127.0.0.1';
const PORT = parseInt(process.env.TOOL_SITE_PORT || '8791', 10);

function healthPayload() {
  return JSON.stringify({
    status: 'ok',
    service: 'deng-tool-site',
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
  if (process.env.TRACKER_UPLOAD_PROXY !== '0' && shouldProxyTrackerUpload(req)) {
    return createTrackerUploadProxy()(req, res);
  }
  app(req, res);
});

server.listen(PORT, HOST, () => {
  console.log(`[deng-tool-site] Listening on http://${HOST}:${PORT}`);
});

server.on('error', (err) => {
  console.error('[deng-tool-site] Listen error:', err);
  if (err && err.code === 'EADDRINUSE') {
    console.error(`[deng-tool-site] Port ${PORT} already in use — PM2 should retry after kill_timeout`);
  }
  process.exit(1);
});

// Graceful shutdown — allow in-flight tracker uploads to finish before PM2 force-kills.
function shutdown(signal) {
  console.log(`[deng-tool-site] ${signal} received – flushing live sessions then shutting down`);
  const fishitTrackerRoutes = require('./src/fishitTrackerRoutes');
  Promise.resolve(fishitTrackerRoutes.flushAllLiveSessionsToDisk())
    .then((flushResult) => {
      console.log('[deng-tool-site] shutdown flush saved=%s mode=%s',
        flushResult?.saved ?? 0,
        flushResult?.metrics?.mode || '?');
    })
    .catch((err) => {
      console.warn('[deng-tool-site] shutdown flush error:', err?.message || err);
    })
    .finally(() => {
      server.close(() => {
        console.log('[deng-tool-site] HTTP server closed');
        process.exit(0);
      });
      setTimeout(() => process.exit(1), 15_000);
    });
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT',  () => shutdown('SIGINT'));
process.on('uncaughtException', (err) => {
  console.error('[deng-tool-site] Uncaught exception:', err);
  if (isRecoverableFsError(err)) {
    console.warn('[deng-tool-site] Recoverable filesystem error — continuing');
    return;
  }
  process.exit(1);
});
process.on('unhandledRejection', (reason) => {
  console.error('[deng-tool-site] Unhandled rejection:', reason);
  if (isRecoverableFsError(reason)) {
    console.warn('[deng-tool-site] Recoverable filesystem rejection — continuing');
    return;
  }
  process.exit(1);
});
