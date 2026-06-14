'use strict';
/**
 * Lightweight request timing for AIO + tracker routes.
 * Logs duration only — never secrets, tokens, or OAuth codes.
 */

const SLOW_MS = 500;

function routeLabel(req) {
  const path = req.path || req.url || '';
  if (path.includes('/api/aio/auth/start')) return 'aio.auth.start';
  if (path.includes('/api/aio/auth/callback')) return 'aio.auth.callback';
  if (path.includes('/api/aio/auth/exchange')) return 'aio.auth.exchange';
  if (path.includes('/api/aio/app/latest')) return 'aio.app.latest';
  if (path.includes('/api/aio/bootstrap')) return 'aio.bootstrap';
  if (path.includes('/api/aio/sync/manifest')) return 'aio.sync.manifest';
  if (path.includes('/api/aio/sync/full')) return 'aio.sync.full';
  if (path.includes('/api/aio/sync/delta')) return 'aio.sync.delta';
  if (path.includes('update-backpack')) return 'tracker.upload';
  if (path.includes('/api/aio/')) return 'aio.other';
  return null;
}

function aioTimingMiddleware(req, res, next) {
  const label = routeLabel(req);
  if (!label) return next();
  const started = process.hrtime.bigint();
  res.on('finish', () => {
    const ms = Number(process.hrtime.bigint() - started) / 1e6;
    const level = ms >= SLOW_MS ? 'warn' : 'info';
    const payload = {
      route: label,
      ms: Math.round(ms * 100) / 100,
      status: res.statusCode,
      dataset: req.query && req.query.dataset ? String(req.query.dataset) : undefined,
      payloadBytes: label === 'tracker.upload'
        ? Number(req.headers['content-length'] || 0) || undefined
        : undefined,
    };
    const line = `[aio-timing] ${JSON.stringify(payload)}`;
    if (level === 'warn') console.warn(line);
    else if (process.env.AIO_TIMING_LOG === '1' || ms >= 100) console.log(line);
  });
  return next();
}

module.exports = { aioTimingMiddleware, routeLabel };
