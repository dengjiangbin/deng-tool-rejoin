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

const LISTEN_RETRY_MAX_MS = parseInt(process.env.TRACKER_READ_LISTEN_RETRY_MAX_MS || '8000', 10);
const LISTEN_RETRY_DELAY_MS = parseInt(process.env.TRACKER_READ_LISTEN_RETRY_DELAY_MS || '500', 10);
let listenRetryStartedAt = 0;

function startListening() {
  server.listen(PORT, HOST);
}

server.on('listening', () => {
  listenRetryStartedAt = 0;
  console.log(`[deng-tracker-read] Listening on http://${HOST}:${PORT}`);
});

server.on('error', (err) => {
  if (err && err.code === 'EADDRINUSE') {
    const nowMs = Date.now();
    if (!listenRetryStartedAt) listenRetryStartedAt = nowMs;
    const waitedMs = nowMs - listenRetryStartedAt;
    if (waitedMs <= LISTEN_RETRY_MAX_MS) {
      console.warn('[deng-tracker-read] %d busy, retrying bind in %dms (waited %dms)', PORT, LISTEN_RETRY_DELAY_MS, waitedMs);
      setTimeout(() => {
        try { server.close(); } catch (_) { /* not yet listening */ }
        startListening();
      }, LISTEN_RETRY_DELAY_MS);
      return;
    }
    console.error('[deng-tracker-read] %d still busy after %dms — exiting for clean PM2 restart', PORT, waitedMs);
  } else {
    console.error('[deng-tracker-read] Listen error:', err);
  }
  process.exit(1);
});

startListening();

function shutdown(signal) {
  console.log(`[deng-tracker-read] ${signal} received — closing`);
  try { server.close(); } catch (_) { /* ignore */ }
  setTimeout(() => process.exit(0), 300);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('uncaughtException', (err) => {
  console.error('[deng-tracker-read] uncaught exception:', err);
});
process.on('unhandledRejection', (reason) => {
  console.error('[deng-tracker-read] unhandled rejection:', reason);
});
