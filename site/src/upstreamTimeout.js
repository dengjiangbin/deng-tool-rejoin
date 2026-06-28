'use strict';

/**
 * Fail fast on slow Supabase/HTTP upstream calls so portal routes never block
 * long enough to trigger Cloudflare 524 (~100s origin timeout).
 */
function portalUpstreamTimeoutMs() {
  const raw = Number(process.env.PORTAL_UPSTREAM_TIMEOUT_MS || 8000);
  return Number.isFinite(raw) && raw >= 1000 ? raw : 8000;
}

function withUpstreamTimeout(promise, label, ms = portalUpstreamTimeoutMs()) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      reject(new Error(`${label} upstream request timeout`));
    }, ms);
  });
  return Promise.race([promise, timeout]).finally(() => {
    if (timer) clearTimeout(timer);
  });
}

module.exports = {
  portalUpstreamTimeoutMs,
  withUpstreamTimeout,
};
