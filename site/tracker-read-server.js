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

const HOST = process.env.TRACKER_READ_HOST || '127.0.0.1';
const PORT = parseInt(process.env.TRACKER_READ_PORT || '8793', 10);

const server = require('http').createServer(app);
server.keepAliveTimeout = parseInt(process.env.TRACKER_READ_KEEPALIVE_MS || '61000', 10);
server.headersTimeout = parseInt(process.env.TRACKER_READ_HEADERS_TIMEOUT_MS || '65000', 10);
server.maxRequestsPerSocket = 0;
if (typeof server.setMaxListeners === 'function') server.setMaxListeners(0);

const { listenWithReclaim } = require('./src/reclaimPort');
listenWithReclaim(server, PORT, HOST, '[deng-tracker-read]', {
  // reclaimAfterMs > PM2 kill_timeout (8000ms): never reclaim a sibling that is
  // still gracefully shutting down on restart (avoids the mutual-kill loop).
  reclaimAfterMs: parseInt(process.env.TRACKER_READ_RECLAIM_AFTER_MS || '9000', 10),
  retryDelayMs: parseInt(process.env.TRACKER_READ_LISTEN_RETRY_DELAY_MS || '400', 10),
  maxMs: parseInt(process.env.TRACKER_READ_LISTEN_RETRY_MAX_MS || '22000', 10),
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
