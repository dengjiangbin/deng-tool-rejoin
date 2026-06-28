'use strict';

// Regression: homepage public-stats must not saturate the shared Supabase
// connection pool during a slow/bloated license_keys incident. The heavy
// full-table scan must (a) be single-flighted, (b) trip a per-source circuit
// breaker on timeout so it is not relaunched on every request, and (c) still
// serve a payload from the remaining (cheap) sources + last-known rows. This is
// what unblocks user-facing key generation + history when the DB is under load.
//
// We inject a fake Supabase client via require.cache so NO real network/DB is
// touched, and we control which table "hangs" vs resolves.

const { describe, test, before, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');

process.env.NODE_ENV = 'test';
// Tight timeouts so the simulated hang trips quickly inside the test.
process.env.PORTAL_UPSTREAM_TIMEOUT_MS = '150';
process.env.PUBLIC_STATS_CACHE_MS = '50';
process.env.PUBLIC_STATS_SOURCE_COOLDOWN_MS = '10000';

// ── Controllable fake Supabase ────────────────────────────────────────────────
const tableCallCounts = {};
let hangTables = new Set();

function makeThenable(table) {
  const exec = () => new Promise((resolve, reject) => {
    tableCallCounts[table] = (tableCallCounts[table] || 0) + 1;
    if (hangTables.has(table)) {
      // Never resolve on its own — withSupabaseTimeout must abort/timeout it.
      return; // dangling promise; the abort signal path rejects via race timer
    }
    resolve({ data: [], error: null });
  });
  const builder = {
    _exec: exec,
    abortSignal() { return builder; },
    select() { return builder; },
    eq() { return builder; },
    order() { return builder; },
    limit() { return builder; },
    maybeSingle() { return builder; },
    then(onF, onR) { return exec().then(onF, onR); },
    catch(onR) { return exec().catch(onR); },
    finally(fn) { return exec().finally(fn); },
  };
  return builder;
}

const fakeSupabase = {
  from(table) { return makeThenable(table); },
};

let licenseService;

before(() => {
  const dbPath = require.resolve('../src/db');
  require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true, exports: fakeSupabase };
  licenseService = require('../src/licenseService');
});

beforeEach(() => {
  for (const k of Object.keys(tableCallCounts)) delete tableCallCounts[k];
  hangTables = new Set();
  licenseService._clearPublicStatsCache();
});

describe('public-stats pool protection (single-flight + circuit breaker)', () => {
  test('concurrent cold requests collapse onto ONE scan per source (single-flight)', async () => {
    const [a, b, c] = await Promise.all([
      licenseService.getPublicStats({ forceRefresh: false }),
      licenseService.getPublicStats({ forceRefresh: false }),
      licenseService.getPublicStats({ forceRefresh: false }),
    ]);
    assert.ok(a && b && c, 'all callers receive a payload');
    // Only one in-flight refresh => each table scanned exactly once despite 3 callers.
    assert.equal(tableCallCounts.license_keys, 1, 'license_keys scanned once for 3 concurrent callers');
    assert.equal(tableCallCounts.site_users, 1, 'site_users scanned once for 3 concurrent callers');
  });

  test('a hanging license_keys scan trips the circuit breaker and is NOT relaunched while in cooldown', async () => {
    hangTables = new Set(['license_keys']);

    // First refresh: license_keys hangs -> times out -> breaker trips; other
    // sources still succeed so we still get a payload (partial success).
    const first = await licenseService.getPublicStats({ forceRefresh: true });
    assert.ok(first, 'partial payload returned despite license_keys timeout');
    assert.equal(tableCallCounts.license_keys, 1, 'license_keys attempted once');

    // Force more refreshes within the cooldown window. license_keys must NOT be
    // hit again (breaker open) — this is what stops pool exhaustion.
    await licenseService.getPublicStats({ forceRefresh: true });
    await licenseService.getPublicStats({ forceRefresh: true });
    assert.equal(
      tableCallCounts.license_keys,
      1,
      'license_keys not relaunched while circuit breaker is in cooldown',
    );
    // Cheap sources keep refreshing normally.
    assert.ok(tableCallCounts.site_users >= 2, 'cheap sources still refresh');
  });

  test('stale-while-revalidate serves cached payload without blocking on a slow source', async () => {
    // Warm a good cache first.
    const warm = await licenseService.getPublicStats({ forceRefresh: true });
    assert.ok(warm);
    const callsAfterWarm = tableCallCounts.license_keys;

    // Now make license_keys hang and let the cache go stale.
    hangTables = new Set(['license_keys']);
    await new Promise((r) => setTimeout(r, 60)); // exceed PUBLIC_STATS_CACHE_MS=50

    const t0 = Date.now();
    const stale = await licenseService.getPublicStats({ forceRefresh: false });
    const elapsed = Date.now() - t0;

    assert.ok(stale, 'served a payload');
    // Must return fast (stale cache), not wait on the 150ms hang/timeout.
    assert.ok(elapsed < 120, `served stale quickly (elapsed=${elapsed}ms)`);
    assert.ok(
      tableCallCounts.license_keys >= callsAfterWarm,
      'background refresh may be triggered but caller is not blocked',
    );
  });
});
