'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.TRACKER_INGEST_MODE = '1';
process.env.SKIP_TRACKER_UPLOAD_ROUTES = '0';
process.env.FISHIT_TEST_FIXTURE = '1';
process.env.FISHIT_SESSION_SYNC_SAVE = '1';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';
process.env.INVENTORY_ACCOUNTS_MEMORY = '1';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-ingest-suite';

const uploadMetrics = require('../src/trackerUploadRequestMetrics');
const trackerConcurrencyGate = require('../src/trackerConcurrencyGate');
const { MINIMUM_TRACKER_BUILD } = require('../src/fishitTrackerBuild');

function makeIngestApp() {
  delete require.cache[require.resolve('../src/fishitTrackerRoutes')];
  delete require.cache[require.resolve('../src/trackerIngestApp')];
  return require('../src/trackerIngestApp');
}

describe('tracker ingest upload reliability', () => {
  beforeEach(() => {
    uploadMetrics._resetForTests();
    trackerConcurrencyGate._resetForTests();
  });

  test('oversized upload payload returns 413 JSON not 502', async () => {
    const app = makeIngestApp();
    const big = 'x'.repeat(600 * 1024);
    const res = await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .set('Content-Type', 'application/json')
      .send(`{"type":"inventory_snapshot","username":"biguser","payload":"${big}"}`);
    assert.equal(res.status, 413);
    assert.equal(res.body.error, 'payload_too_large');
  });

  test('heartbeat upload returns quickly under duplicate saves', async () => {
    const app = makeIngestApp();
    const body = {
      type: 'tracker_status',
      username: 'speeduser',
      userId: 42,
      isOnline: true,
      trackerBuild: MINIMUM_TRACKER_BUILD,
      trackerChannel: 'fish-it-main',
      scriptSource: 'https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua',
      clientOrigin: 'roblox_tracker',
      evidenceSourceMode: 'live_roblox',
      phase: 'ready',
    };
    const durations = [];
    for (let i = 0; i < 5; i += 1) {
      const start = Date.now();
      const res = await request(app)
        .post('/api/fishit-tracker/update-backpack')
        .send(body);
      durations.push(Date.now() - start);
      assert.ok([200, 202].includes(res.status), `unexpected status ${res.status} body=${JSON.stringify(res.body)}`);
    }
    const p95 = durations.sort((a, b) => a - b)[Math.floor(durations.length * 0.95)] || 0;
    assert.ok(p95 < 2000, `p95 heartbeat duration ${p95}ms too high`);
    const metrics = uploadMetrics.snapshotUploadMetrics();
    assert.ok((metrics.byPayloadType.tracker_status || {}).count >= 5);
  });
});
