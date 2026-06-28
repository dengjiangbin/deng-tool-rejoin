'use strict';

require('./src/wmicRuntimeGuard');
require('./src/loadSiteEnv').loadSiteEnv();

const { isStateSecretConfigured } = require('./src/crypto');
const { listenWithReclaim } = require('./src/reclaimPort');
const { sendHealthz } = require('./src/healthz');
const { wrapHttpHandler } = require('./src/requestAccessLog');

if (!isStateSecretConfigured()) {
  console.error(
    '[deng-portal-license] FATAL: TOOL_SITE_STATE_SECRET is missing or shorter than 32 characters.',
  );
  process.exit(1);
}

const app = require('./src/portalApp');

const HOST = process.env.PORTAL_HOST || process.env.TOOL_SITE_HOST || '127.0.0.1';
const PORT = parseInt(process.env.PORTAL_PORT || '8790', 10);

const server = require('http').createServer(wrapHttpHandler('deng-portal-license', (req, res) => {
  const pathOnly = String(req.url || '').split('?')[0];
  if (req.method === 'GET' && (pathOnly === '/healthz' || pathOnly === '/health')) {
    if (pathOnly === '/healthz') {
      return sendHealthz(res, 'deng-portal-license', PORT);
    }
    res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    res.end(JSON.stringify({
      status: 'ok',
      service: 'deng-portal-license',
      port: PORT,
      timestamp: new Date().toISOString(),
    }));
    return;
  }
  app(req, res);
}, () => ({ lane: 'portal' })));

if (typeof server.setMaxListeners === 'function') server.setMaxListeners(0);
server.keepAliveTimeout = parseInt(process.env.PORTAL_KEEPALIVE_MS || '5000', 10);
server.headersTimeout = parseInt(process.env.PORTAL_HEADERS_TIMEOUT_MS || '10000', 10);
server.maxRequestsPerSocket = 0;

listenWithReclaim(server, PORT, HOST, '[deng-portal-license]', {
  pm2AppName: 'deng-portal-license',
  reclaimAfterMs: parseInt(process.env.PORTAL_RECLAIM_AFTER_MS || '9000', 10),
  retryDelayMs: parseInt(process.env.PORTAL_LISTEN_RETRY_DELAY_MS || '400', 10),
  maxMs: parseInt(process.env.PORTAL_LISTEN_RETRY_MAX_MS || '22000', 10),
});

let shuttingDown = false;
function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`[deng-portal-license] ${signal} received — closing`);
  try { server.close(); } catch (_) { /* ignore */ }
  try { if (typeof server.closeAllConnections === 'function') server.closeAllConnections(); } catch (_) { /* ignore */ }
  setTimeout(() => process.exit(0), 150).unref();
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
