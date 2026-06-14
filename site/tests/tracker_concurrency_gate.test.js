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
    assert.match(source, /isStatusOnlyUpload/);
    assert.match(source, /server_busy/);
    assert.match(source, /TRACKER_UPLOAD_BUSY_LAG_MS/);
    assert.doesNotMatch(source, /acquireSlot/);
  });

  test('returns JSON 503 server_busy when event loop lag exceeds threshold', () => {
    const loopMonitor = require('../src/trackerEventLoopMonitor');
    loopMonitor._setLagForTests(3000);
    try {
      const handler = gate.wrapTrackerUpload('lag-test', (_req, res) => {
        res.status(200).json({ ok: true });
      });
      const res = {
        statusCode: 200,
        body: null,
        status(code) { this.statusCode = code; return this; },
        json(payload) { this.body = payload; return this; },
      };
      handler({ body: { username: 'LagUser', type: 'inventory_snapshot' } }, res);
      assert.equal(res.statusCode, 503);
      assert.equal(res.body.error, 'server_busy');
      assert.equal(res.body.retryable, true);
      assert.ok(res.body.lagMs >= 2500);
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
