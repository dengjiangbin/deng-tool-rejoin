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

/**
 * Timeout wrapper for a Supabase PostgREST query builder that ACTUALLY ABORTS
 * the underlying request on timeout (via .abortSignal). Plain Promise.race only
 * stops US waiting — the HTTP request keeps running and holds a PostgREST/Postgres
 * connection until the server finishes, so a slow DB cascades into connection
 * exhaustion (every portal request leaks one in-flight query). Aborting on
 * timeout releases the connection immediately and lets the DB recover.
 *
 * Accepts a builder (thenable with .abortSignal) and chains the signal before it
 * is executed. Falls back to a plain race if the builder has no .abortSignal.
 */
function withSupabaseTimeout(builder, label, ms = portalUpstreamTimeoutMs()) {
  if (!builder || typeof builder.abortSignal !== 'function' || typeof AbortController === 'undefined') {
    return withUpstreamTimeout(Promise.resolve(builder), label, ms);
  }
  const controller = new AbortController();
  const signalled = builder.abortSignal(controller.signal);
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      try { controller.abort(); } catch (_) { /* ignore */ }
      reject(new Error(`${label} upstream request timeout`));
    }, ms);
  });
  return Promise.race([signalled, timeout]).finally(() => {
    if (timer) clearTimeout(timer);
  });
}

module.exports = {
  portalUpstreamTimeoutMs,
  withUpstreamTimeout,
  withSupabaseTimeout,
};
