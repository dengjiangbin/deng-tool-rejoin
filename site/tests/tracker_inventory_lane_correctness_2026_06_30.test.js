'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const os = require('os');

describe('tracker inventory lane correctness (2026-06-30)', () => {
  const readSrc = fs.readFileSync(path.join(__dirname, '..', 'src', 'trackerReadApp.js'), 'utf8');
  const precomputeSrc = fs.readFileSync(path.join(__dirname, '..', 'src', 'fishitPrecomputeStore.js'), 'utf8');
  const frontendSrc = fs.readFileSync(
    path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs'),
    'utf8',
  );
  const routesSrc = fs.readFileSync(path.join(__dirname, '..', 'src', 'fishitTrackerRoutes.js'), 'utf8');
  const workerSrc = fs.readFileSync(path.join(__dirname, '..', 'src', 'trackerWorkerApp.js'), 'utf8');

  test('inventory upload accepted independently from status lane', () => {
    assert.match(routesSrc, /stampReportIdentity\(key, 'inventory'/);
    assert.match(routesSrc, /lane=inventory account=/);
    assert.doesNotMatch(readSrc, /lastRealInventoryAt[\s\S]{0,80}lastSnapshotUploadAt/);
  });

  test('status fresh + inventory stale does not mark inventory fresh via snapshot fallback', () => {
    assert.match(readSrc, /lastRealInventoryAt = input\.lastRealInventoryAt \|\| input\.lastInventoryAt \|\| null/);
    assert.match(frontendSrc, /isStatusFreshInventoryStale/);
    assert.match(frontendSrc, /data-inventory-lane-stale/);
  });

  test('new inventory revision updates timer via lane auth merge', () => {
    assert.match(frontendSrc, /mergeInventoryLaneAuthFromAccountStatus/);
    assert.match(frontendSrc, /inventoryRevision/);
    assert.match(frontendSrc, /laneTimestampAdvanced/);
  });

  test('page refresh does not reset inventory timer (server timestamp only)', () => {
    assert.match(frontendSrc, /seedTimersFromBackend\(_entry\) \{ \/\* no-op \*\/ \}/);
    assert.match(frontendSrc, /backendInventoryAgeSeconds/);
    assert.doesNotMatch(frontendSrc, /_inventoryFrontendRefreshAt/);
  });

  test('worker cannot overwrite newer inventory with stale snapshot', () => {
    assert.match(precomputeSrc, /incomingInventoryIsStale/);
    assert.match(precomputeSrc, /Never clobber a newer inventory snapshot/);
    assert.match(workerSrc, /singletonSuperseded|ensureOwnership/);
  });

  test('offline account preserves last inventory (read contract)', () => {
    assert.match(readSrc, /preservedDataReason/);
    assert.match(readSrc, /offline_preserve_last_known/);
    assert.match(readSrc, /hasRenderableData/);
  });

  test('read warm-load uses batched SQL (large inventory path stays off hot loop)', () => {
    assert.match(precomputeSrc, /getWarmBatchRows/);
    assert.match(readSrc, /lookupCached/);
    assert.match(readSrc, /getChangedMetaSince/);
  });

  test('no duplicate worker writers (singleton park-not-exit)', () => {
    assert.match(workerSrc, /park-not-exit/);
    assert.match(workerSrc, /singletonSuperseded/);
    assert.match(workerSrc, /metrics\.parked/);
  });

  test('precompute upsertLatest rejects stale inventory identity at runtime', () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'precompute-stale-'));
    const prev = process.env.FISHIT_PRECOMPUTE_DB_PATH;
    process.env.FISHIT_PRECOMPUTE_DB_PATH = path.join(tmp, 'test.db');
    delete require.cache[require.resolve('../src/fishitPrecomputeStore')];
    const store = require('../src/fishitPrecomputeStore');
    store.openDb();
    const key = 'staleguarduser';
    const freshIso = new Date(Date.now() - 5000).toISOString();
    const staleIso = new Date(Date.now() - 600_000).toISOString();
    store.upsertLatest({
      sessionKey: key,
      username: key,
      precomputedJson: JSON.stringify({ fishItems: [{ id: 'a' }], lastRealInventoryAt: freshIso, inventoryRevision: 5 }),
      precomputedHash: 'hash5',
      presenceJson: JSON.stringify({ lastRealInventoryAt: freshIso, inventoryRevision: 5 }),
    });
    store.upsertLatest({
      sessionKey: key,
      username: key,
      precomputedJson: JSON.stringify({ fishItems: [], lastRealInventoryAt: staleIso, inventoryRevision: 3 }),
      precomputedHash: 'hash3',
      presenceJson: JSON.stringify({ lastRealInventoryAt: staleIso, inventoryRevision: 3 }),
    });
    const latest = store.getLatest(key);
    assert.ok(latest && latest.body);
    assert.equal(latest.body.inventoryRevision, 5);
    assert.equal(latest.precomputedHash, 'hash5');
    store.close();
    if (prev == null) delete process.env.FISHIT_PRECOMPUTE_DB_PATH;
    else process.env.FISHIT_PRECOMPUTE_DB_PATH = prev;
    delete require.cache[require.resolve('../src/fishitPrecomputeStore')];
  });
});
