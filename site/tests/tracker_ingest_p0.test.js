'use strict';

const { describe, test, beforeEach, mock } = require('node:test');
const assert = require('node:assert/strict');

process.env.NODE_ENV = 'test';

const trackerRouteMetrics = require('../src/trackerRouteMetrics');
const trackerConcurrencyGate = require('../src/trackerConcurrencyGate');
const {
  finishTrackerUploadResponse,
  shouldReturn202,
} = require('../src/trackerUploadResponse');
const stabilitySnapshot = require('../src/stabilitySnapshot');

function mockRes() {
  const res = {
    statusCode: 200,
    body: null,
    status(code) {
      this.statusCode = code;
      return this;
    },
    json(payload) {
      this.body = payload;
      return this;
    },
  };
  return res;
}

describe('tracker ingest P0 latency hardening', () => {
  beforeEach(() => {
    trackerRouteMetrics._resetForTests();
    trackerConcurrencyGate._resetForTests();
    stabilitySnapshot._resetForTests();
  });

  test('A: finishTrackerUploadResponse returns 200 when load is normal', () => {
    const req = { headers: {} };
    const res = mockRes();
    const payload = { ok: true, status: 'success', acceptedCount: 1, serverTime: 't' };
    finishTrackerUploadResponse(req, res, payload, 'user1');
    assert.equal(res.statusCode, 200);
    assert.equal(res.body.ok, true);
    assert.equal(trackerRouteMetrics.getTrackerRouteMetrics().responseBeforeEnrichmentCount, 1);
    assert.equal(trackerRouteMetrics.getTrackerRouteMetrics().latestPersistSuccessCount, 1);
  });

  test('B: finishTrackerUploadResponse returns 202 when trackerDeferEnrichment is set', () => {
    const req = { headers: {}, trackerDeferEnrichment: true };
    const res = mockRes();
    finishTrackerUploadResponse(req, res, { ok: true, status: 'success', acceptedCount: 2 }, 'user1');
    assert.equal(res.statusCode, 202);
    assert.equal(res.body.deferred, true);
    assert.equal(res.body.route, 'direct-ingest');
    assert.equal(trackerRouteMetrics.getTrackerRouteMetrics().accepted202Count, 1);
    assert.equal(trackerRouteMetrics.getTrackerRouteMetrics().deferredEnrichmentCount, 1);
  });

  test('C: finishTrackerUploadResponse labels web-proxy-fallback route', () => {
    const req = { headers: { 'x-deng-via-web-proxy': '1' }, trackerDeferEnrichment: true };
    const res = mockRes();
    finishTrackerUploadResponse(req, res, { ok: true, status: 'success' }, 'user1');
    assert.equal(res.body.route, 'web-proxy-fallback');
  });

  test('D: wrapTrackerUpload sets defer flag under backlog instead of 503', () => {
    process.env.TRACKER_ENRICHMENT_MAX_CONCURRENT = '1';
    delete require.cache[require.resolve('../src/trackerConcurrencyGate')];
    const gate = require('../src/trackerConcurrencyGate');
    gate._resetForTests();
    gate.scheduleDeferredUploadWork('busy1', () => new Promise(() => {}));
    gate.scheduleDeferredUploadWork('busy2', () => new Promise(() => {}));
    gate.scheduleDeferredUploadWork('busy3', () => new Promise(() => {}));
    const handler = gate.wrapTrackerUpload('test', (req, res) => {
      assert.equal(req.trackerDeferEnrichment, true);
      res.status(200).json({ ok: true });
    });
    const req = { body: { username: 'Tester', type: 'inventory_snapshot' }, headers: {} };
    const res = mockRes();
    handler(req, res);
    assert.equal(res.statusCode, 200);
    assert.equal(trackerRouteMetrics.getTrackerRouteMetrics().hardFail503Count, 0);
    process.env.TRACKER_ENRICHMENT_MAX_CONCURRENT = '4';
    delete require.cache[require.resolve('../src/trackerConcurrencyGate')];
  });

  test('E: wrapTrackerUpload defers when queue is full for new account', () => {
    process.env.TRACKER_QUEUE_MAX = '2';
    process.env.TRACKER_ENRICHMENT_MAX_CONCURRENT = '1';
    delete require.cache[require.resolve('../src/trackerConcurrencyGate')];
    const gate = require('../src/trackerConcurrencyGate');
    gate._resetForTests();
    assert.equal(gate.stats().queueMax, 2);
    gate.scheduleDeferredUploadWork('held', () => new Promise(() => {}));
    gate.scheduleDeferredUploadWork('queued', () => new Promise(() => {}));
    assert.ok(gate.stats().queued >= 2, `expected queued backlog, got ${gate.stats().queued}`);
    const handler = gate.wrapTrackerUpload('test', (req, res) => {
      assert.equal(req.trackerDeferEnrichment, true);
      res.status(200).json({ ok: true });
    });
    const req = { body: { username: 'NewUser', type: 'inventory_snapshot' }, headers: {} };
    const res = mockRes();
    handler(req, res);
    assert.equal(res.statusCode, 200);
    assert.equal(trackerRouteMetrics.getTrackerRouteMetrics().hardFail503Count, 0);
    process.env.TRACKER_QUEUE_MAX = '1000';
    process.env.TRACKER_ENRICHMENT_MAX_CONCURRENT = '4';
    delete require.cache[require.resolve('../src/trackerConcurrencyGate')];
  });

  test('F: coalesced uploads increment coalescedUploadCount', () => {
    trackerConcurrencyGate.scheduleDeferredUploadWork('same', () => {});
    trackerConcurrencyGate.scheduleDeferredUploadWork('same', () => {});
    assert.equal(trackerRouteMetrics.getTrackerRouteMetrics().coalescedUploadCount, 1);
  });

  test('G: stability snapshot serves precomputed payload', () => {
    stabilitySnapshot.refreshStabilitySnapshot();
    const cached = stabilitySnapshot.getCachedStabilityStatus();
    assert.equal(cached.snapshotSource, 'precomputed');
    assert.ok(cached.trackerRoute);
    const json = stabilitySnapshot.getCachedStabilityJson();
    assert.ok(json.includes('"snapshotSource":"precomputed"'));
  });

  test('H: tracker route metrics expose P0 counters', () => {
    trackerRouteMetrics.recordAccepted202();
    trackerRouteMetrics.recordCoalescedUpload();
    trackerRouteMetrics.recordDeferredEnrichment();
    const m = trackerRouteMetrics.getTrackerRouteMetrics();
    assert.equal(m.accepted202Count, 1);
    assert.equal(m.coalescedUploadCount, 1);
    assert.equal(m.deferredEnrichmentCount, 1);
    assert.ok('hardFail503Count' in m);
    assert.ok('responseBeforeEnrichmentCount' in m);
  });

  test('I: shouldReturn202 when per-account pending work exists', () => {
    trackerConcurrencyGate.scheduleDeferredUploadWork('pending-user', () => {});
    assert.equal(shouldReturn202({ headers: {} }, 'pending-user'), true);
  });

  test('J: ecosystem keeps web proxy fallback enabled', () => {
    const fs = require('fs');
    const path = require('path');
    const eco = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'ecosystem.site.json'), 'utf8'));
    const site = eco.apps.find((a) => a.name === 'deng-tool-site');
    assert.equal(site.env.TRACKER_UPLOAD_PROXY, '1');
    assert.equal(site.env.SKIP_TRACKER_UPLOAD_ROUTES, '1');
  });
});
