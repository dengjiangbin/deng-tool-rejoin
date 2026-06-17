'use strict';

// deng-tracker-worker entrypoint (PM2). No public port.
// Precomputes per-user get-backpack snapshots into the precompute SQLite store
// so the 8793 read API can serve them with no recompute / no image resolution.

const path = require('path');
const dotenv = require('dotenv');

dotenv.config({ path: path.join(__dirname, '.env') });
dotenv.config({ path: path.join(__dirname, '..', '.env') });
if (process.env.NODE_ENV === 'production') {
  dotenv.config({ path: path.join(__dirname, '..', '.env'), override: true });
  dotenv.config({ path: path.join(__dirname, '.env'), override: true });
}

// Web mode = read-only consumer of the shared session shards (no upload routes).
process.env.TRACKER_WEB_MODE = '1';
process.env.SKIP_TRACKER_UPLOAD_ROUTES = '1';

const worker = require('./src/trackerWorkerApp');

worker.start();

function shutdown(signal) {
  console.log(`[deng-tracker-worker] ${signal} received — flushing metrics and exiting`);
  try { worker.stop(); } catch (_) { /* ignore */ }
  setTimeout(() => process.exit(0), 300);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('uncaughtException', (err) => {
  console.error('[deng-tracker-worker] uncaught exception:', err);
});
process.on('unhandledRejection', (reason) => {
  console.error('[deng-tracker-worker] unhandled rejection:', reason);
});
