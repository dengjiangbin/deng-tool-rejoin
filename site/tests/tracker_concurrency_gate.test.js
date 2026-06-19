'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const gate = require('../src/trackerConcurrencyGate');

describe('trackerConcurrencyGate', () => {
  beforeEach(() => {
    gate._resetForTests();
  });

  test('status-only uploads bypass pipeline gate', () => {
    const source = require('fs').readFileSync(
      require('path').join(__dirname, '..', 'src', 'trackerConcurrencyGate.js'),
      'utf8',
    );
    assert.match(source, /tracker_status/);
    assert.match(source, /isFastLaneUpload/);
    assert.match(source, /trackerDeferEnrichment/);
    assert.match(source, /TRACKER_UPLOAD_BUSY_LAG_MS/);
    assert.doesNotMatch(source, /acquireSlot/);
  });

  test('defers enrichment under event loop lag instead of 503', () => {
    const loopMonitor = require('../src/trackerEventLoopMonitor');
    loopMonitor._setLagForTests(9000);
    try {
      const handler = gate.wrapTrackerUpload('lag-test', (req, res) => {
        assert.equal(req.trackerDeferEnrichment, true);
        res.status(200).json({ ok: true });
      });
      const res = {
        statusCode: 200,
        body: null,
        status(code) { this.statusCode = code; return this; },
        json(payload) { this.body = payload; return this; },
      };
      handler({ body: { username: 'LagUser', type: 'inventory_snapshot' } }, res);
      assert.equal(res.statusCode, 200);
      assert.equal(res.body.ok, true);
    } finally {
      loopMonitor._resetForTests();
    }
  });

  test('stats exposes deferred queue metrics', () => {
    const stats = gate.stats();
    assert.equal(typeof stats.deferredPending, 'number');
    assert.equal(typeof stats.deferredActive, 'number');
    assert.ok(stats.max >= 1);
  });

  test('shed threshold stays above steady-state lag floor so enrichment is not permanently starved', () => {
    // Regression guard: lowering TRACKER_EVENT_LOOP_LAG_SHED_MS below the ~800ms
    // flush/cache lag floor seen with ~600 live accounts makes shouldShedWork()
    // permanently true and deadlocks the deferred enrichment queue. Keep a margin.
    const source = require('fs').readFileSync(
      require('path').join(__dirname, '..', 'src', 'trackerConcurrencyGate.js'),
      'utf8',
    );
    const m = source.match(/TRACKER_EVENT_LOOP_LAG_SHED_MS\s*\|\|\s*(\d+)/);
    assert.ok(m, 'shed threshold default must be defined');
    const shedDefault = Number(m[1]);
    assert.ok(shedDefault >= 1000, `shed default must clear the lag floor (got ${shedDefault})`);

    const loopMonitor = require('../src/trackerEventLoopMonitor');
    try {
      // Healthy loop (below warn tier): full concurrency, no shed.
      loopMonitor._setLagForTests(50);
      assert.equal(gate.shouldShedWork(), false, 'low lag must not shed');
      assert.equal(gate.stats().effectiveMax, gate.stats().max, 'full concurrency on healthy loop');
      // At the steady-state floor (~800ms, below shed threshold): must NOT shed, so
      // deferred enrichment keeps draining instead of deadlocking.
      loopMonitor._setLagForTests(shedDefault - 100);
      assert.equal(gate.shouldShedWork(), false, 'floor-level lag below threshold must not shed');
      // Above threshold: genuine spike, shed and reduce concurrency.
      loopMonitor._setLagForTests(shedDefault + 500);
      assert.equal(gate.shouldShedWork(), true, 'lag above threshold must shed');
      assert.ok(gate.stats().effectiveMax < gate.stats().max, 'concurrency drops on real spike');
    } finally {
      loopMonitor._resetForTests();
    }
  });

  test('fast lanes (status + leaderstats) never blocked by shedding under high lag', () => {
    const loopMonitor = require('../src/trackerEventLoopMonitor');
    loopMonitor._setLagForTests(5000);
    try {
      for (const body of [
        { username: 'ShedStatus', type: 'tracker_status' },
        { username: 'ShedLeader', uploadPath: 'playerdata_leaderstats_only', leaderstatsOnlyUpload: true },
      ]) {
        let handlerRan = false;
        const handler = gate.wrapTrackerUpload('fastlane-shed', (req, res) => {
          handlerRan = true;
          // Fast lanes must not be flagged for deferral — required lane data persists inline.
          assert.notEqual(req.trackerDeferEnrichment, true);
          res.status(200).json({ ok: true });
        });
        const res = {
          statusCode: 0,
          body: null,
          status(code) { this.statusCode = code; return this; },
          json(payload) { this.body = payload; return this; },
        };
        handler({ body }, res);
        assert.equal(handlerRan, true, `fast lane ${body.username} must run inline`);
        assert.equal(res.statusCode, 200);
      }
    } finally {
      loopMonitor._resetForTests();
    }
  });

  test('inventory handler is invoked immediately under load', async () => {
    const holdMs = 30;
    const express = require('express');
    const request = require('supertest');
    const app = express();
    app.use(express.json());
    let handled = 0;
    app.post('/api/fishit-tracker/update-backpack', gate.wrapTrackerUpload('test-immediate', (req, res) => {
      handled += 1;
      setTimeout(() => res.status(200).json({ ok: true, user: req.body.username }), holdMs);
    }));

    const payloads = Array.from({ length: gate.stats().max + 6 }, (_, i) => ({
      username: `ImmediateUser${i}`,
      type: 'inventory_snapshot',
    }));
    const started = Date.now();
    const results = await Promise.all(
      payloads.map((body) => request(app).post('/api/fishit-tracker/update-backpack').send(body)),
    );
    const elapsed = Date.now() - started;

    for (const res of results) {
      assert.equal(res.status, 200);
    }
    assert.equal(handled, payloads.length);
    assert.ok(elapsed < 5000, `immediate path should not queue minutes (took ${elapsed}ms)`);
  });
});
