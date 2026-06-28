'use strict';

const http = require('http');

const READ_HEALTH_PATHS = new Set([
  '/api/tracker/read-health',
  '/api/fishit-tracker/read-health',
]);

/** GET routes served by deng-tracker-read (8793) — proxied from web (8791) so
 *  license/auth pages never share the event loop with tracker polls. */
const READ_PROXY_PREFIXES = [
  '/api/tracker/get-backpack/',
  '/api/fishit-tracker/get-backpack/',
  '/api/tracker/latest/',
  '/api/fishit-tracker/latest/',
  '/api/tracker/snapshot/',
  '/api/fishit-tracker/snapshot/',
  '/api/tracker/account-status',
  '/api/fishit-tracker/account-status',
];

let readAgent = null;

function getReadAgent() {
  if (!readAgent) {
    readAgent = new http.Agent({
      keepAlive: true,
      keepAliveMsecs: 5000,
      maxSockets: Number(process.env.TRACKER_READ_PROXY_MAX_SOCKETS || 64),
      maxFreeSockets: Number(process.env.TRACKER_READ_PROXY_MAX_FREE_SOCKETS || 16),
    });
  }
  return readAgent;
}

let cachedReadProxy = null;

function createTrackerReadProxy(options = {}) {
  if (cachedReadProxy && !options.force) return cachedReadProxy;

  const host = options.host || process.env.TRACKER_READ_HOST || '127.0.0.1';
  const port = Number(options.port || process.env.TRACKER_READ_PORT || 8793);
  const timeoutMs = Number(options.timeoutMs || process.env.TRACKER_READ_PROXY_TIMEOUT_MS || 12000);

  cachedReadProxy = function proxyTrackerRead(req, res) {
    const started = Date.now();
    const proxyReq = http.request({
      host,
      port,
      method: req.method,
      path: req.url,
      agent: getReadAgent(),
      headers: {
        ...req.headers,
        host: `${host}:${port}`,
        connection: 'keep-alive',
        'x-deng-via-web-proxy': '1',
        'x-deng-tracker-read-route': 'web-proxy-8793',
      },
      timeout: timeoutMs,
    }, (proxyRes) => {
      const headers = {
        ...proxyRes.headers,
        'x-deng-tracker-read-route': 'web-proxy-8793',
      };
      res.writeHead(proxyRes.statusCode || 502, headers);
      proxyRes.pipe(res);
      proxyRes.on('end', () => {
        const ms = Date.now() - started;
        if (ms > 500) {
          console.warn('[tracker-read-proxy] slow forward path=%s ms=%d', req.url, ms);
        }
      });
    });

    proxyReq.on('timeout', () => {
      proxyReq.destroy();
      if (!res.headersSent) {
        res.writeHead(504, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'tracker_read_timeout' }));
      }
    });

    proxyReq.on('error', (err) => {
      console.warn('[tracker-read-proxy] forward failed path=%s err=%s', req.url, err.message);
      if (!res.headersSent) {
        res.writeHead(503, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'tracker_read_unavailable' }));
      }
    });

    if (req.method === 'GET' || req.method === 'HEAD') {
      proxyReq.end();
    } else {
      req.pipe(proxyReq);
    }
  };

  return cachedReadProxy;
}

function isTrackerReadHealthPath(pathOnly) {
  return READ_HEALTH_PATHS.has(String(pathOnly || '').split('?')[0]);
}

/** Lightweight read-health on 8791: probe 8793 /health only (never the heavy readHealth JSON). */
function handleTrackerReadHealth(_req, res, options = {}) {
  const host = options.host || process.env.TRACKER_READ_HOST || '127.0.0.1';
  const port = Number(options.port || process.env.TRACKER_READ_PORT || 8793);
  const probeMs = Number(options.probeMs || process.env.TRACKER_READ_HEALTH_PROBE_MS || 2500);
  const started = Date.now();

  const proxyReq = http.request({
    host,
    port,
    method: 'GET',
    path: '/health',
    agent: getReadAgent(),
    headers: {
      accept: 'application/json',
      connection: 'keep-alive',
      'x-deng-tracker-read-health': 'web-light-probe',
    },
    timeout: probeMs,
  }, (proxyRes) => {
    let body = '';
    proxyRes.setEncoding('utf8');
    proxyRes.on('data', (chunk) => { body += chunk; });
    proxyRes.on('end', () => {
      const ms = Date.now() - started;
      let upstream = null;
      try { upstream = JSON.parse(body); } catch (_) { upstream = null; }
      const ok = proxyRes.statusCode === 200 && upstream && upstream.status === 'ok';
      res.writeHead(ok ? 200 : 503, {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-store',
        'X-DENG-Tracker-Read-Health': 'web-light-probe',
        'X-DENG-Tracker-Read-Probe-Ms': String(ms),
      });
      res.end(JSON.stringify({
        status: ok ? 'ok' : 'degraded',
        service: 'deng-tool-site',
        probe: 'tracker-read-health',
        readHost: host,
        readPort: port,
        probeMs: ms,
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
      res.writeHead(503, {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-store',
        'X-DENG-Tracker-Read-Health': 'web-light-probe-timeout',
      });
      res.end(JSON.stringify({
        status: 'degraded',
        service: 'deng-tool-site',
        probe: 'tracker-read-health',
        error: 'tracker_read_health_probe_timeout',
        probeMs: Date.now() - started,
        timestamp: new Date().toISOString(),
      }));
    }
  });

  proxyReq.on('error', (err) => {
    console.warn('[tracker-read-proxy] read-health probe failed err=%s', err.message);
    if (!res.headersSent) {
      res.writeHead(503, {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-store',
        'X-DENG-Tracker-Read-Health': 'web-light-probe-error',
      });
      res.end(JSON.stringify({
        status: 'degraded',
        service: 'deng-tool-site',
        probe: 'tracker-read-health',
        error: 'tracker_read_unavailable',
        message: err.message,
        probeMs: Date.now() - started,
        timestamp: new Date().toISOString(),
      }));
    }
  });

  proxyReq.end();
}

function shouldProxyTrackerRead(req) {
  if (!req || (req.method !== 'GET' && req.method !== 'HEAD')) return false;
  const pathOnly = String(req.url || '').split('?')[0];
  if (isTrackerReadHealthPath(pathOnly)) return false;
  for (const prefix of READ_PROXY_PREFIXES) {
    if (pathOnly === prefix || pathOnly.startsWith(prefix)) return true;
  }
  return false;
}

module.exports = {
  createTrackerReadProxy,
  handleTrackerReadHealth,
  isTrackerReadHealthPath,
  shouldProxyTrackerRead,
  READ_HEALTH_PATHS,
  READ_PROXY_PREFIXES,
};
