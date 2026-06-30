'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

describe('ingest capacity hardening (2026-06-28)', () => {
  test('ingest skips AIO cache rebuild — site lane owns aioDatasetCache', () => {
    const src = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'fishitTrackerRoutes.js'),
      'utf8',
    );
    assert.match(src, /TRACKER_INGEST_MODE === '1'\) return;/);
    assert.match(src, /ingestAccountDiskSyncMiddleware/);
  });

  test('cluster bootstrap module resolves worker count from env', () => {
    const prev = process.env.TRACKER_INGEST_WORKERS;
    process.env.TRACKER_INGEST_WORKERS = '4';
    delete require.cache[require.resolve('../src/trackerIngestCluster')];
    const mod = require('../src/trackerIngestCluster');
    assert.equal(mod.resolveWorkerCount(), 4);
    process.env.TRACKER_INGEST_WORKERS = '1';
    delete require.cache[require.resolve('../src/trackerIngestCluster')];
    const mod2 = require('../src/trackerIngestCluster');
    assert.equal(mod2.resolveWorkerCount(), 1);
    if (prev == null) delete process.env.TRACKER_INGEST_WORKERS;
    else process.env.TRACKER_INGEST_WORKERS = prev;
  });

  test('sharded store exposes single-account reload for cluster uploads', () => {
    const sharded = require('../src/fishitSessionStoreSharded');
    assert.equal(typeof sharded.reloadAccountShard, 'function');
    const store = require('../src/fishitSessionStore');
    assert.equal(typeof store.ensureAccountLoaded, 'function');
  });

  test('read warm-load uses batched SQL helper', () => {
    const precompute = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'fishitPrecomputeStore.js'),
      'utf8',
    );
    const readApp = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'trackerReadApp.js'),
      'utf8',
    );
    assert.match(precompute, /getWarmBatchRows/);
    assert.match(readApp, /getWarmBatchRows/);
    assert.match(readApp, /countLatestSnapshots/);
  });
});
