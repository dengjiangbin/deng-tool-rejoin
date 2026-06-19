'use strict';

/**
 * Regression (2026-06-18) — identity source persistence + monotonic status lane.
 *
 * Root cause of denghub2/dengjiangbin reading `backend_derived` while online and
 * intermittently flipping red:
 *
 *   1. PERSIST DROP — `sanitiseSession` (the on-disk whitelist) omitted
 *      reportIdentitySource / leaderstatsIdentitySource / inventoryIdentitySource,
 *      so every shard write stripped the identity classification. The read API +
 *      worker (which only ever see the on-disk row) could therefore never report
 *      client_explicit even though the ingest classified it correctly in memory.
 *
 *   2. NON-MONOTONIC RELOAD — the cross-process reload merge `{...existing,...row}`
 *      let a slightly-stale disk row (older statusSeq, older lastRealRobloxStatusAt)
 *      overwrite a fresher in-memory row, visibly regressing statusSeq AND aging the
 *      status lane backwards (the false-red while still in game).
 *
 * These tests lock both fixes.
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const store = require('../src/fishitSessionStore');
const sharded = require('../src/fishitSessionStoreSharded');

// ── 1. Persist whitelist round-trip ───────────────────────────────────────────
test('sanitiseSession persists reportIdentitySource (client_explicit survives disk)', () => {
  const row = store.sanitiseSession('denghub2', {
    username: 'denghub2',
    statusSeq: 159,
    statusReportId: 'sess:159',
    reportIdentitySource: 'client_explicit',
    leaderstatsIdentitySource: 'client_explicit',
    inventoryIdentitySource: 'client_explicit',
  });
  assert.equal(row.reportIdentitySource, 'client_explicit');
  assert.equal(row.leaderstatsIdentitySource, 'client_explicit');
  assert.equal(row.inventoryIdentitySource, 'client_explicit');
  // Survives an actual JSON disk round-trip (what flushAccountToDisk writes).
  const roundTripped = JSON.parse(JSON.stringify(row));
  assert.equal(roundTripped.reportIdentitySource, 'client_explicit');
});

test('sanitiseSession keeps backend_derived as-is (no field invented)', () => {
  const row = store.sanitiseSession('x', {
    username: 'x', statusSeq: 1, reportIdentitySource: 'backend_derived',
  });
  assert.equal(row.reportIdentitySource, 'backend_derived');
  // Lane sources default to null when never reported (not fabricated as explicit).
  assert.equal(row.leaderstatsIdentitySource, null);
  assert.equal(row.inventoryIdentitySource, null);
});

// ── 2. Monotonic lane guard ───────────────────────────────────────────────────
test('preserveMonotonicLanes: stale disk row never lowers in-memory statusSeq', () => {
  const existing = {
    statusSeq: 37,
    statusReportId: 'sess:37',
    statusRevision: 20,
    lastRealRobloxStatusAt: '2026-06-18T18:58:12.264Z',
    reportIdentitySource: 'client_explicit',
  };
  // Disk row is older on the status lane (seq 27) but arrived with a fresher
  // timestamp — the exact regression that flipped statusSeq 37 -> 27.
  const merged = {
    statusSeq: 27,
    statusReportId: 'sess:27',
    statusRevision: 18,
    lastRealRobloxStatusAt: '2026-06-18T18:55:00.000Z',
    reportIdentitySource: undefined,
  };
  sharded.preserveMonotonicLanes(existing, merged);
  assert.equal(merged.statusSeq, 37, 'statusSeq must not regress');
  assert.equal(merged.statusReportId, 'sess:37');
  assert.equal(merged.statusRevision, 20, 'statusRevision must not regress');
  assert.equal(merged.lastRealRobloxStatusAt, '2026-06-18T18:58:12.264Z',
    'status freshness must not age backwards (false-red guard)');
  assert.equal(merged.reportIdentitySource, 'client_explicit',
    'identity must follow the higher-seq side');
});

test('preserveMonotonicLanes: a genuinely-newer disk row IS allowed forward', () => {
  const existing = { statusSeq: 37, statusReportId: 'sess:37', statusRevision: 20 };
  const merged = { statusSeq: 41, statusReportId: 'sess:41', statusRevision: 22 };
  sharded.preserveMonotonicLanes(existing, merged);
  assert.equal(merged.statusSeq, 41, 'forward progress preserved');
  assert.equal(merged.statusReportId, 'sess:41');
  assert.equal(merged.statusRevision, 22);
});

test('preserveMonotonicLanes: leaderstats + inventory lanes are independently monotonic', () => {
  const existing = {
    leaderstatsSeq: 250, leaderstatsRevision: 30, leaderstatsIdentitySource: 'client_explicit',
    inventorySeq: 107, inventoryRevision: 12, inventoryIdentitySource: 'client_explicit',
  };
  const merged = {
    leaderstatsSeq: 100, leaderstatsRevision: 5, leaderstatsIdentitySource: undefined,
    inventorySeq: 200, inventoryRevision: 40, inventoryIdentitySource: 'client_explicit',
  };
  sharded.preserveMonotonicLanes(existing, merged);
  // Leaderstats was ahead in memory -> keep it.
  assert.equal(merged.leaderstatsSeq, 250);
  assert.equal(merged.leaderstatsIdentitySource, 'client_explicit');
  // Inventory was ahead on disk -> let it advance.
  assert.equal(merged.inventorySeq, 200);
  assert.equal(merged.inventoryRevision, 40);
});

test('preserveMonotonicLanes: revision advances even when seq is unchanged (reinforcement)', () => {
  // A non-status lane can reinforce status truth and bump statusRevision without
  // a new statusSeq — that revision must still be monotonic.
  const existing = { statusSeq: 61, statusRevision: 40 };
  const merged = { statusSeq: 61, statusRevision: 36 };
  sharded.preserveMonotonicLanes(existing, merged);
  assert.equal(merged.statusSeq, 61);
  assert.equal(merged.statusRevision, 40, 'higher revision retained');
});
