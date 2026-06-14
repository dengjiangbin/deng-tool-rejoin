'use strict';

const { createUserRateLimit, resolveClientIp } = require('./rateLimitUtils');

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

/** @deprecated Hard 429 upload limiter — replaced by trackerUploadCoalesceMiddleware. */
const uploadLimiter = (req, res, next) => next();

module.exports = {
  trackerUploadKey,
  uploadLimiter,
};
