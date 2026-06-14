'use strict';

const { createUserRateLimit, resolveClientIp } = require('./rateLimitUtils');
const { recordRateLimit429 } = require('./trackerRouteMetrics');

function trackerUploadKey(req) {
  const body = req.body || {};
  const userId = body.userId != null ? String(body.userId).trim() : '';
  const username = body.username != null ? String(body.username).trim().toLowerCase() : '';
  const runId = body.runId != null ? String(body.runId).trim() : '';
  if (userId) return `uid:${userId}`;
  if (username) return `user:${username}`;
  if (runId) return `run:${runId}`;
  return `ip:${resolveClientIp(req)}`;
}

const uploadLimiter = createUserRateLimit({
  keyPrefix: 'tracker-upload:',
  windowMs: 60 * 1000,
  max: Number(process.env.TRACKER_UPLOAD_RATE_MAX_PER_MIN || 10),
  keyGenerator: trackerUploadKey,
  handler: (req, res, _next, options) => {
    recordRateLimit429();
    res.set('Retry-After', '30');
    return res.status(429).json({
      ok: false,
      error: 'rate_limited',
      message: 'Tracker upload rate exceeded. Expected cadence is about one upload per lane every 60 seconds.',
      windowMs: options.windowMs,
      max: options.max,
    });
  },
});

module.exports = {
  trackerUploadKey,
  uploadLimiter,
};
