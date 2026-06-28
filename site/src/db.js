'use strict';
const { createClient } = require('@supabase/supabase-js');

const url = process.env.SUPABASE_URL;
const key = process.env.SUPABASE_SERVICE_ROLE_KEY;

if (!url || !key) {
  throw new Error('SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set');
}

// Global hard ceiling on EVERY Supabase request. This is the safety net that
// keeps the DB strong/stable: without it, a single slow/stuck query (e.g. during
// a Supabase incident) blocks for the full Postgres statement_timeout (~125s),
// holding a PostgREST connection the entire time. A burst of those leaks enough
// connections to exhaust the pool and take the whole site down — exactly the
// outage we just recovered from.
//
// This applies to ALL call sites automatically (license, monitor panel, inventory
// tracked accounts, and any future code), so no individual query can ever hang
// forever. It is intentionally set ABOVE the largest per-call timeout
// (licenseUserQueryTimeoutMs ~25s for user-facing key history/generation) so it
// only acts as a backstop and never prematurely kills a legitimately-slow
// user-initiated query. With a healthy DB every query is sub-second, so this
// never fires in normal operation.
const GLOBAL_FETCH_TIMEOUT_MS = (() => {
  const raw = Number(process.env.SUPABASE_GLOBAL_FETCH_TIMEOUT_MS || 30000);
  return Number.isFinite(raw) && raw >= 1000 ? raw : 30000;
})();

const baseFetch = (typeof globalThis !== 'undefined' && typeof globalThis.fetch === 'function')
  ? globalThis.fetch.bind(globalThis)
  : null;

function timeoutFetch(input, init = {}) {
  // No global fetch available (very old Node) → let supabase-js use its own.
  if (!baseFetch) return undefined;
  const controller = new AbortController();
  const callerSignal = init && init.signal;
  // Respect any per-call abort signal (e.g. withSupabaseTimeout) by forwarding it.
  if (callerSignal) {
    if (callerSignal.aborted) {
      controller.abort();
    } else {
      callerSignal.addEventListener('abort', () => controller.abort(), { once: true });
    }
  }
  const timer = setTimeout(() => controller.abort(), GLOBAL_FETCH_TIMEOUT_MS);
  if (timer && typeof timer.unref === 'function') timer.unref();
  return baseFetch(input, { ...init, signal: controller.signal })
    .finally(() => clearTimeout(timer));
}

const clientOptions = {
  auth: {
    autoRefreshToken: false,
    persistSession: false,
    detectSessionInUrl: false,
  },
};
// Only override fetch when we actually have a base fetch to wrap.
if (baseFetch) {
  clientOptions.global = { fetch: timeoutFetch };
}

const supabase = createClient(url, key, clientOptions);

module.exports = supabase;
