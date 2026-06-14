'use strict';

const http = require('http');
const { isTrackerUploadPath } = require('./trackerUploadPaths');
const { recordWebProxyForward } = require('./trackerRouteMetrics');

function createTrackerUploadProxy(options = {}) {
  const host = options.host || process.env.TRACKER_INGEST_HOST || '127.0.0.1';
  const port = Number(options.port || process.env.TRACKER_INGEST_PORT || 8792);
  const timeoutMs = Number(options.timeoutMs || process.env.TRACKER_UPLOAD_PROXY_TIMEOUT_MS || 30000);

  return function proxyTrackerUpload(req, res) {
    const started = Date.now();
    recordWebProxyForward();
    const proxyReq = http.request({
      host,
      port,
      method: req.method,
      path: req.url,
      headers: {
        ...req.headers,
        connection: 'close',
        'x-deng-via-web-proxy': '1',
        'x-deng-tracker-route': 'web-proxy-fallback',
      },
      timeout: timeoutMs,
    }, (proxyRes) => {
      const headers = { ...proxyRes.headers, 'x-deng-tracker-route': 'web-proxy-fallback' };
      res.writeHead(proxyRes.statusCode || 502, headers);
      proxyRes.pipe(res);
      proxyRes.on('end', () => {
        const ms = Date.now() - started;
        if (ms > 500) {
          console.warn('[tracker-proxy] slow forward path=%s ms=%d', req.url, ms);
        }
      });
    });

    proxyReq.on('timeout', () => {
      proxyReq.destroy();
      if (!res.headersSent) {
        res.writeHead(504, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'tracker_ingest_timeout' }));
      }
    });

    proxyReq.on('error', (err) => {
      console.warn('[tracker-proxy] forward failed path=%s err=%s', req.url, err.message);
      if (!res.headersSent) {
        res.writeHead(503, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: 'tracker_ingest_unavailable' }));
      }
    });

    req.pipe(proxyReq);
  };
}

function shouldProxyTrackerUpload(req) {
  return isTrackerUploadPath(req.method, req.url);
}

module.exports = {
  createTrackerUploadProxy,
  shouldProxyTrackerUpload,
};
