'use strict';

const http = require('http');

/**
 * Portal fallback proxy (runs on the 8791 catch-all).
 *
 * Cloudflare is supposed to route portal paths (/license, /dashboard, /stats …)
 * straight to 8790. But the production tunnel is token-managed, so its path rules
 * are edited by hand in the dashboard and can drift — a missing rule sends a
 * portal path to the 8791 catch-all, which 404s (e.g. /stats). To make the site
 * robust regardless of dashboard drift, 8791 forwards any portal-owned path to
 * 8790 instead of 404ing. Tracker/home/static and tracker read/upload APIs are
 * NOT in this list, so the hot tracker paths never touch this proxy.
 */
const PORTAL_PREFIXES = [
  '/license',
  '/unlock',
  '/key',
  '/dashboard',
  '/download',   // also covers /downloads/* and /download
  '/stats',
  '/app',
  '/fishit',
  '/api/license',
  '/api/key',
  '/api/unlock',
  '/api/public-stats',   // homepage Platform Stats (owner: portal/8790)
  '/api/stats/public',   // alias for the same handler
];

// Exact paths that must stay on 8791 even though they share a prefix above
// (none today, but keep the hook explicit for future-proofing).
const PORTAL_EXCLUDE_EXACT = new Set();

let portalAgent = null;
function getPortalAgent() {
  if (!portalAgent) {
    portalAgent = new http.Agent({
      keepAlive: true,
      keepAliveMsecs: 5000,
      maxSockets: Number(process.env.PORTAL_PROXY_MAX_SOCKETS || 32),
      maxFreeSockets: Number(process.env.PORTAL_PROXY_MAX_FREE_SOCKETS || 8),
    });
  }
  return portalAgent;
}

function shouldProxyToPortal(req) {
  if (!req) return false;
  const pathOnly = String(req.url || '').split('?')[0];
  if (PORTAL_EXCLUDE_EXACT.has(pathOnly)) return false;
  // Never hijack tracker read/upload APIs.
  if (pathOnly.startsWith('/api/tracker/') || pathOnly.startsWith('/api/fishit-tracker/')) return false;
  for (const prefix of PORTAL_PREFIXES) {
    if (pathOnly === prefix || pathOnly.startsWith(`${prefix}/`) || pathOnly.startsWith(prefix)) {
      // Guard: '/app' prefix must not match '/apple' etc.; require exact or
      // followed by '/'. Most prefixes are distinct enough, but be precise.
      if (pathOnly === prefix || pathOnly.startsWith(`${prefix}/`)) return true;
      // '/download' should also catch '/downloads' (plural) — handle explicitly.
      if (prefix === '/download' && pathOnly.startsWith('/downloads')) return true;
    }
  }
  return false;
}

let cachedProxy = null;
function createPortalFallbackProxy(options = {}) {
  if (cachedProxy && !options.force) return cachedProxy;
  const host = options.host || process.env.PORTAL_HOST || '127.0.0.1';
  const port = Number(options.port || process.env.PORTAL_PORT || 8790);
  const timeoutMs = Number(options.timeoutMs || process.env.PORTAL_PROXY_TIMEOUT_MS || 15000);

  cachedProxy = function proxyToPortal(req, res) {
    const proxyReq = http.request({
      host,
      port,
      method: req.method,
      path: req.url,
      agent: getPortalAgent(),
      headers: {
        ...req.headers,
        host: req.headers.host || `${host}:${port}`,
        'x-deng-via-portal-proxy': '1',
      },
      timeout: timeoutMs,
    }, (proxyRes) => {
      res.writeHead(proxyRes.statusCode || 502, proxyRes.headers);
      proxyRes.pipe(res);
    });
    proxyReq.on('timeout', () => {
      proxyReq.destroy();
      if (!res.headersSent) {
        res.writeHead(504, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'portal_timeout' }));
      }
    });
    proxyReq.on('error', (err) => {
      if (!res.headersSent) {
        res.writeHead(502, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'portal_unavailable', message: err.message }));
      }
    });
    if (req.method === 'GET' || req.method === 'HEAD') {
      proxyReq.end();
    } else {
      req.pipe(proxyReq);
    }
  };
  return cachedProxy;
}

module.exports = {
  createPortalFallbackProxy,
  shouldProxyToPortal,
  PORTAL_PREFIXES,
};
