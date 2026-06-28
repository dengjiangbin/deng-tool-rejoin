'use strict';

// deng-tracker-read entrypoint (PM2) — port 8793.
// Read-only API serving precomputed per-user tracker snapshots.

const path = require('path');
const dotenv = require('dotenv');

dotenv.config({ path: path.join(__dirname, '.env') });
dotenv.config({ path: path.join(__dirname, '..', '.env') });
if (process.env.NODE_ENV === 'production') {
  dotenv.config({ path: path.join(__dirname, '..', '.env'), override: true });
  dotenv.config({ path: path.join(__dirname, '.env'), override: true });
}

const app = require('./src/trackerReadApp');
const { sendHealthz } = require('./src/healthz');

const HOST = process.env.TRACKER_READ_HOST || '127.0.0.1';
const PORT = parseInt(process.env.TRACKER_READ_PORT || '8793', 10);

const server = require('http').createServer((req, res) => {
  const pathOnly = String(req.url || '').split('?')[0];
  if (req.method === 'GET' && pathOnly === '/healthz') {
    return sendHealthz(res, 'deng-tracker-read', PORT);
  }
  app(req, res);
});
server.keepAliveTimeout = parseInt(process.env.TRACKER_READ_KEEPALIVE_MS || '61000', 10);
server.headersTimeout = parseInt(process.env.TRACKER_READ_HEADERS_TIMEOUT_MS || '65000', 10);
server.maxRequestsPerSocket = 0;
if (typeof server.setMaxListeners === 'function') server.setMaxListeners(0);

const { listenWithReclaim, preBindReclaimSingleOwner } = require('./src/reclaimPort');
// 8793 has exactly one owner and no critical in-process flush (reads come from a
// RAM cache). Deterministically clear any stale node listener BEFORE binding so a
// half-alive orphan can never crash-loop this PM2 child (the 256-restart bug).
try {
  const killed = preBindReclaimSingleOwner(PORT, '[deng-tracker-read]');
  if (killed > 0) {
    // Give Windows a moment to release the socket after the orphan dies.
    const waitUntil = Date.now() + 1200;
    while (Date.now() < waitUntil) { /* brief spin; startup only, pre-listen */ }
  }
} catch (_) { /* best effort */ }
listenWithReclaim(server, PORT, HOST, '[deng-tracker-read]', {
  pm2AppName: 'deng-tracker-read',
  reclaimAfterMs: parseInt(process.env.TRACKER_READ_RECLAIM_AFTER_MS || '9000', 10),
  retryDelayMs: parseInt(process.env.TRACKER_READ_LISTEN_RETRY_DELAY_MS || '400', 10),
  maxMs: parseInt(process.env.TRACKER_READ_LISTEN_RETRY_MAX_MS || '22000', 10),
  onListening: () => {
    if (typeof app.startCache === 'function') app.startCache();
  },
});

let readShuttingDown = false;
function shutdown(signal) {
  if (readShuttingDown) return;
  readShuttingDown = true;
  console.log(`[deng-tracker-read] ${signal} received — closing`);
  try { server.close(); } catch (_) { /* ignore */ }
  // CRITICAL: forcibly destroy keep-alive sockets so the listening port is
  // released IMMEDIATELY. Without this, server.close() waits for persistent
  // poller connections to drain (keepAliveTimeout=61s), the old process keeps
  // 8793 bound, the PM2-restarted instance loses the bind race and exits, and
  // the slow-dying fork becomes an orphan PID holding the port -> restart loop.
  try { if (typeof server.closeAllConnections === 'function') server.closeAllConnections(); } catch (_) { /* ignore */ }
  setTimeout(() => process.exit(0), 150).unref();
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('uncaughtException', (err) => {
  console.error('[deng-tracker-read] uncaught exception:', err);
});
process.on('unhandledRejection', (reason) => {
  console.error('[deng-tracker-read] unhandled rejection:', reason);
});
