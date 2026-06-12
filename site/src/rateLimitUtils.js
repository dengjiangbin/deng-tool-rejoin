'use strict';

/**
 * Shared rate-limit helpers for the DENG Tool site.
 *
 * Keys prefer Discord user ID, then session ID, then client IP.
 * Normal read/page traffic should not use a site-wide limiter — apply
 * limiters only on sensitive write/auth routes.
 */

function cleanEnv(name, fallback = '') {
  return String(process.env[name] || fallback).trim();
}

function resolveTrustProxySetting() {
  const raw = cleanEnv('TOOL_SITE_TRUST_PROXY');
  if (!raw) {
    return process.env.NODE_ENV === 'production';
  }
  if (/^(1|true|yes|on)$/i.test(raw)) return true;
  if (/^(0|false|no|off)$/i.test(raw)) return false;
  const hops = Number(raw);
  return Number.isFinite(hops) && hops >= 0 ? hops : true;
}

function resolveClientIp(req) {
  const cf = req.headers['cf-connecting-ip'];
  if (typeof cf === 'string' && cf.trim()) return cf.trim();
  const xRealIp = req.headers['x-real-ip'];
  if (typeof xRealIp === 'string' && xRealIp.trim()) return xRealIp.trim();
  return req.ip || req.socket?.remoteAddress || 'unknown';
}

function resolveDiscordUserId(req) {
  const fromSession = req.session?.user?.discord_user_id;
  if (fromSession != null && String(fromSession).trim()) return String(fromSession).trim();
  const fromFish = req.fishOwner;
  if (fromFish != null && String(fromFish).trim()) return String(fromFish).trim();
  return null;
}

function hasSessionCookie(req) {
  const cookie = String(req.headers.cookie || '');
  return cookie.includes('deng_sid=');
}

function resolveRateLimitKey(req, prefix = '') {
  const discordUserId = resolveDiscordUserId(req);
  if (discordUserId) return `${prefix}discord:${discordUserId}`;
  if (req.sessionID && hasSessionCookie(req)) {
    return `${prefix}sid:${req.sessionID}`;
  }
  return `${prefix}ip:${resolveClientIp(req)}`;
}

function isRateLimitTestSkipped() {
  return process.env.NODE_ENV === 'test' && process.env.ENABLE_RATE_LIMIT_TEST !== '1';
}

function wantsJson(req) {
  const accept = String(req.headers.accept || '').toLowerCase();
  const path = String(req.path || req.originalUrl || '');
  if (path.startsWith('/api/')) return true;
  if (accept.includes('application/json')) return true;
  const contentType = String(req.headers['content-type'] || '').toLowerCase();
  return contentType.includes('application/json');
}

function logRateLimitHit(req, meta = {}) {
  const payload = {
    event: 'rate_limit_hit',
    route: req.originalUrl || req.path || '',
    method: req.method || '',
    key: meta.key || resolveRateLimitKey(req, meta.keyPrefix || ''),
    ip: resolveClientIp(req),
    discordUserId: resolveDiscordUserId(req),
    sessionId: req.sessionID || null,
    limit: meta.limit ?? null,
    windowMs: meta.windowMs ?? null,
    remaining: meta.remaining ?? null,
    userAgent: req.headers['user-agent'] || '',
  };
  console.warn(JSON.stringify(payload));
}

function createRateLimitHandler(handlerOptions = {}) {
  const {
    jsonError = 'too_many_requests',
    jsonMessage = 'Too many requests. Please wait a moment and try again.',
    htmlMessage = 'Too many requests. Please wait a moment and try again.',
    redirectTo = null,
    keyPrefix = '',
  } = handlerOptions;

  return function rateLimitHandler(req, res, _next, limiterOptions) {
    const windowMs = limiterOptions.windowMs || 60_000;
    const max = limiterOptions.max ?? limiterOptions.limit ?? null;
    const rl = req.rateLimit || {};
    const remaining = typeof rl.remaining === 'number' ? rl.remaining : 0;
    const resetMs = rl.resetTime instanceof Date
      ? rl.resetTime.getTime()
      : Number(rl.resetTime) || (Date.now() + windowMs);
    const retryAfterSeconds = Math.max(1, Math.ceil((resetMs - Date.now()) / 1000));

    logRateLimitHit(req, {
      keyPrefix,
      key: typeof limiterOptions.keyGenerator === 'function'
        ? limiterOptions.keyGenerator(req)
        : resolveRateLimitKey(req, keyPrefix),
      limit: max,
      windowMs,
      remaining,
    });

    res.set('Retry-After', String(retryAfterSeconds));
    res.set('Cache-Control', 'no-store');

    const htmlPreferred = !wantsJson(req)
      && ((req.headers.accept || '').includes('text/html')
        || !String(req.path || '').startsWith('/api/'));

    if (htmlPreferred && redirectTo && req.session) {
      req.session.flash = {
        ...(req.session.flash || {}),
        error: `${htmlMessage} Try again in about ${retryAfterSeconds} seconds.`,
      };
      return res.redirect(303, redirectTo);
    }

    if (htmlPreferred) {
      return res.status(429).render('error', {
        code: 429,
        message: `${htmlMessage} Try again in about ${retryAfterSeconds} seconds.`,
      });
    }

    return res.status(429).json({
      error: jsonError,
      message: `${jsonMessage} Try again in ${retryAfterSeconds} seconds.`,
      retry_after_seconds: retryAfterSeconds,
    });
  };
}

function createUserRateLimit(options = {}) {
  const rateLimit = require('express-rate-limit');
  const keyPrefix = options.keyPrefix || '';
  const skip = options.skip;
  const handlerOptions = options.handlerOptions || {};

  return rateLimit({
    windowMs: options.windowMs || 60_000,
    max: options.max || 60,
    standardHeaders: true,
    legacyHeaders: false,
    keyGenerator: options.keyGenerator || ((req) => resolveRateLimitKey(req, keyPrefix)),
    skip: (req) => isRateLimitTestSkipped() || (typeof skip === 'function' && skip(req)),
    handler: options.handler || createRateLimitHandler({ ...handlerOptions, keyPrefix }),
    ...(options.extra || {}),
  });
}

module.exports = {
  resolveTrustProxySetting,
  resolveClientIp,
  resolveDiscordUserId,
  resolveRateLimitKey,
  isRateLimitTestSkipped,
  wantsJson,
  logRateLimitHit,
  createRateLimitHandler,
  createUserRateLimit,
};
