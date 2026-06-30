'use strict';

// --- 8792 /healthz protection: non-blocking stdout/stderr ---------------------
// On Windows, process.stdout/stderr connected to the WinSW capture pipe default
// to SYNCHRONOUS writes. The ingest emits several diagnostic lines per upload, so
// whenever the pipe backs up (e.g. WinSW log roll) every console.log BLOCKS the
// event loop — which is exactly what starved /healthz and accept() (observed
// eventLoopLagMs > 5000 and connection-refused health probes). Making the handles
// non-blocking lets writes buffer in-process instead of stalling the loop. Logs
// are diagnostic only (counters + error/5xx tracing are preserved), so the worst
// case under extreme backpressure is dropped log lines, never blocked uploads.
for (const stream of [process.stdout, process.stderr]) {
  try {
    if (stream && stream._handle && typeof stream._handle.setBlocking === 'function') {
      stream._handle.setBlocking(false);
    }
  } catch (_) { /* best effort — keep default behaviour if unsupported */ }
}

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

const { runIngestCluster } = require('./src/trackerIngestCluster');

function startIngestServer() {
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
  if (req.method === 'GET' && (pathOnly === '/healthz' || pathOnly === '/health')) {
    if (pathOnly === '/healthz') {
      const { sendHealthz } = require('./src/healthz');
      return sendHealthz(res, 'deng-tracker-ingest', PORT);
    }
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
  if (
    req.method === 'GET'
    && (pathOnly === '/api/fishit-tracker/read-health' || pathOnly === '/api/tracker/read-health')
  ) {
    const readPort = parseInt(process.env.TRACKER_READ_PORT || '8793', 10);
    const readHost = process.env.TRACKER_READ_HOST || '127.0.0.1';
    const probeMs = Number(process.env.TRACKER_READ_HEALTH_PROBE_MS || 2500);
    const started = Date.now();
    const proxyReq = require('http').request({
      host: readHost,
      port: readPort,
      method: 'GET',
      path: '/health',
      headers: { connection: 'close', 'x-deng-via-ingest-fallback': '1' },
      timeout: probeMs,
    }, (proxyRes) => {
      let body = '';
      proxyRes.setEncoding('utf8');
      proxyRes.on('data', (chunk) => { body += chunk; });
      proxyRes.on('end', () => {
        let upstream = null;
        try { upstream = JSON.parse(body); } catch (_) { upstream = null; }
        const ok = proxyRes.statusCode === 200 && upstream && upstream.status === 'ok';
        res.writeHead(ok ? 200 : 503, {
          'Content-Type': 'application/json',
          'Cache-Control': 'no-store',
          'x-deng-tracker-read-route': 'ingest-light-probe',
        });
        res.end(JSON.stringify({
          status: ok ? 'ok' : 'degraded',
          service: 'deng-tracker-ingest',
          probe: 'tracker-read-health',
          probeMs: Date.now() - started,
          upstreamStatus: proxyRes.statusCode,
          upstream: upstream
            ? { status: upstream.status, service: upstream.service, port: upstream.port }
            : null,
          timestamp: new Date().toISOString(),
        }));
      });
    });
    proxyReq.on('timeout', () => {
      proxyReq.destroy();
      if (!res.headersSent) {
        res.writeHead(503, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
        res.end(JSON.stringify({
          status: 'degraded',
          service: 'deng-tracker-ingest',
          probe: 'tracker-read-health',
          error: 'tracker_read_health_probe_timeout',
          probeMs: Date.now() - started,
          timestamp: new Date().toISOString(),
        }));
      }
    });
    proxyReq.on('error', () => {
      if (!res.headersSent) {
        res.writeHead(503, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
        res.end(JSON.stringify({
          status: 'degraded',
          service: 'deng-tracker-ingest',
          probe: 'tracker-read-health',
          error: 'tracker_read_unavailable',
          probeMs: Date.now() - started,
          timestamp: new Date().toISOString(),
        }));
      }
    });
    proxyReq.end();
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
const { listenWithReclaim, preBindReclaimSingleOwner } = require('./src/reclaimPort');
const cluster = require('cluster');
const { resolveWorkerCount } = require('./src/trackerIngestCluster');
// 8792 is single-owner. If a stale node listener still holds the port at startup
// the new instance cannot accept uploads anyway, and any in-flight data on a
// stuck orphan is already lost — so deterministically reclaim before binding to
// stop the crash-loop instead of waiting out the health-gated path.
// In cluster mode the primary reclaims once before forking workers.
if (resolveWorkerCount() <= 1 || !cluster.isWorker) {
  try {
    const killed = preBindReclaimSingleOwner(PORT, '[deng-tracker-ingest]');
    if (killed > 0) {
      const waitUntil = Date.now() + 1200;
      while (Date.now() < waitUntil) { /* brief pre-listen spin, startup only */ }
    }
  } catch (_) { /* best effort */ }
}
listenWithReclaim(server, PORT, HOST, '[deng-tracker-ingest]', {
  pm2AppName: 'deng-tracker-ingest',
  // Ingest holds 800+ concurrent upload sockets; a deep accept backlog keeps new
  // /healthz and Cloudflare connections from being refused during loop-busy
  // spikes (kernel queues them until the next accept tick).
  backlog: parseInt(process.env.TRACKER_INGEST_LISTEN_BACKLOG || '1024', 10),
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
  // CRITICAL: force-destroy all keep-alive sockets. The ingest holds ~1000+
  // long-lived Cloudflare/Roblox keep-alive connections; server.close() alone
  // waits for every one of them to drain, so the dying process keeps port 8792
  // bound for seconds. The PM2-restarted instance then loses the bind race and
  // either retries forever or detaches as an orphan that permanently owns 8792
  // (the restart loop + 530/502 we observed). The read + site servers already
  // do this on shutdown; the ingest was missing it.
  try { if (typeof server.closeAllConnections === 'function') server.closeAllConnections(); } catch (_) { /* ignore */ }
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
}

runIngestCluster(startIngestServer);
