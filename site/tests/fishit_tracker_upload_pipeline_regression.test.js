'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');
const fs = require('fs');
const path = require('path');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';

const gate = require('../src/trackerConcurrencyGate');
const trackerRouter = require('../src/fishitTrackerRoutes');
const aioRoutes = require('../src/aioRoutes');
const {
  MINIMUM_TRACKER_BUILD,
  ALLOWED_TRACKER_CHANNEL,
  ALLOWED_TRACKER_RAW_URL,
} = require('../src/fishitTrackerChannelEnforcement');
const {
  CLEAN_TRACKER_LOADSTRING,
  PROTECTED_TRACKER_RAW_URL,
} = require('../src/fishitTrackerLoadstring');

const TRACKER_URL = 'https://tool.deng.my.id/api/fishit-tracker/update-backpack';
const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');

function makeTrackerApp() {
  const app = express();
  app.use(express.json({ limit: '512kb' }));
  app.use(trackerRouter);
  return app;
}

function makeFullApp() {
  const app = express();
  app.use(express.json({ limit: '512kb' }));
  app.use(trackerRouter);
  app.use(aioRoutes);
  return app;
}

function validProof(extra = {}) {
  return {
    trackerBuild: MINIMUM_TRACKER_BUILD,
    trackerChannel: ALLOWED_TRACKER_CHANNEL,
    scriptSource: ALLOWED_TRACKER_RAW_URL,
    trackerClientProof: {
      trackerBuild: MINIMUM_TRACKER_BUILD,
      trackerChannel: ALLOWED_TRACKER_CHANNEL,
      scriptSource: ALLOWED_TRACKER_RAW_URL,
    },
    ...extra,
  };
}

function inventorySnapshot(extra = {}) {
  return validProof({
    username: 'UploadRegressionUser',
    userId: 424242,
    isOnline: true,
    type: 'inventory_snapshot',
    phase: 'live',
    clientOrigin: 'roblox_tracker',
    evidenceSourceMode: 'live_roblox',
    inventorySource: 'playerdata_gameitemdb',
    scanCompleted: true,
    replionReady: true,
    leaderstatsReady: true,
    fishScanReady: true,
    stoneScanReady: true,
    fishItems: [{
      itemId: '1',
      name: 'Regression Test Fish',
      quantity: 2,
      type: 'Fish',
      kind: 'fish',
      rarity: 'Common',
      identityVerified: true,
      source: 'playerdata_gameitemdb',
    }],
    stoneItems: [],
    playerStats: {
      coins: 99999,
      totalCaught: 12,
      source: 'leaderstats',
      build: MINIMUM_TRACKER_BUILD,
    },
    ...extra,
  });
}

describe('P0 tracker upload pipeline regression', () => {
  beforeEach(() => {
    gate._resetForTests();
  });

  test('A — synthetic POST to canonical TRACKER_URL path persists latest snapshot', async () => {
    const app = makeTrackerApp();
    const payload = inventorySnapshot({ username: 'P0PersistUser' });
    const res = await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(payload)
      .expect(200);

    assert.equal(res.body.ok, true);
    assert.equal(res.body.status, 'success');
    assert.notEqual(res.statusCode, 202, 'inventory uploads must never be dropped with HTTP 202');

    const dbg = await request(app)
      .get('/api/fishit-tracker/debug/P0PersistUser')
      .expect(200);

    assert.equal(dbg.body.username, 'P0PersistUser');
    assert.ok(dbg.body.lastUploadReceivedAt, 'server should record upload arrival');
    assert.ok(dbg.body.lastInventoryAt, 'latest snapshot timestamp should be set');
    assert.equal(dbg.body.lastUploadStatusCodeReturned, 200);
    assert.ok(dbg.body.lastPayloadType);

    const backpack = await request(app)
      .get('/api/fishit-tracker/get-backpack/P0PersistUser?lite=1')
      .expect(200);
    assert.ok(Array.isArray(backpack.body.fishItems));
    assert.equal(backpack.body.fishItems.length, 1);
  });

  test('B — after POST, latest debug shows fresh serverReceivedAt within seconds', async () => {
    const app = makeTrackerApp();
    const before = Date.now();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(inventorySnapshot({ username: 'P0FreshUser', userId: 515151 }))
      .expect(200);

    const dbg = await request(app)
      .get('/api/fishit-tracker/debug/P0FreshUser')
      .expect(200);

    const receivedMs = new Date(dbg.body.lastUploadReceivedAt).getTime();
    assert.ok(Number.isFinite(receivedMs));
    assert.ok(receivedMs >= before - 2000);
    assert.ok(Date.now() - receivedMs < 15_000);
    assert.ok(dbg.body.uploadPipelineDiagnostics);
    assert.equal(dbg.body.uploadPipelineDiagnostics.lastUploadStatusCodeReturned, 200);
    assert.equal(dbg.body.uploadPipelineDiagnostics.aioCacheRefresh, 'scheduled_on_accept');
  });

  test('C/D/E — upload works independent of dashboard/live tracker browser state', async () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /ensureBackgroundPolling/);
    assert.match(source, /stopLiveTrackerPolling/);
    assert.doesNotMatch(
      source,
      /activeInventorySection[\s\S]{0,120}update-backpack/,
      'frontend tab state must not gate backend upload route',
    );

    const app = makeTrackerApp();
    for (const username of ['P0NoBrowserUser', 'P0DashboardTabUser', 'P0LiveTabUser']) {
      const res = await request(app)
        .post('/api/tracker/update-backpack')
        .send(inventorySnapshot({ username }))
        .expect(200);
      assert.equal(res.body.ok, true);
      assert.notEqual(res.statusCode, 202);
    }
  });

  test('F — frontend auto-poll contract still present after upload fix', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /ensureBackgroundPolling/);
    assert.match(source, /POLL_MS\s*=\s*10000/);
    assert.match(source, /applyInventoryPollPayload/);
    assert.match(source, /activeInventorySection === 'dashboard'/);
  });

  test('G — public loadstring targets fish-it tracker.lua and backend accepts its proof', () => {
    assert.equal(
      CLEAN_TRACKER_LOADSTRING,
      'loadstring(game:HttpGet("https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua"))()',
    );
    assert.equal(PROTECTED_TRACKER_RAW_URL, ALLOWED_TRACKER_RAW_URL);
    assert.equal(TRACKER_URL, 'https://tool.deng.my.id/api/fishit-tracker/update-backpack');
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /loadstring\(loadstring/);
  });

  test('legacy /api/tracker/update-backpack alias accepts same payload as canonical route', async () => {
    const app = makeTrackerApp();
    const res = await request(app)
      .post('/api/tracker/update-backpack')
      .send(inventorySnapshot({ username: 'P0AliasUser' }))
      .expect(200);
    assert.equal(res.body.status, 'success');
    assert.notEqual(res.statusCode, 202);
  });

  test('wrong tracker build is rejected with explicit 403 (not silent drop)', async () => {
    const app = makeTrackerApp();
    const res = await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(inventorySnapshot({
        username: 'P0BadBuildUser',
        trackerBuild: 'LOADER_FIX_REGISTER_LIMIT_2026_06_11',
        trackerClientProof: {
          trackerBuild: 'LOADER_FIX_REGISTER_LIMIT_2026_06_11',
          trackerChannel: ALLOWED_TRACKER_CHANNEL,
          scriptSource: ALLOWED_TRACKER_RAW_URL,
        },
      }))
      .expect(403);
    assert.equal(res.body.error, 'tracker_client_rejected');
    assert.match(res.body.reasons.join(','), /old_tracker_build/);
  });
});

describe('trackerConcurrencyGate — inventory uploads never dropped', () => {
  beforeEach(() => {
    gate._resetForTests();
  });

  test('status-only uploads bypass concurrency gate', () => {
    const source = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'trackerConcurrencyGate.js'),
      'utf8',
    );
    assert.match(source, /tracker_status/);
    assert.match(source, /isStatusOnlyUpload/);
    assert.doesNotMatch(source, /status:\s*'queued'/);
    assert.match(source, /server_busy/);
  });

  test('saturated gate still returns 200 for inventory uploads (never HTTP 202)', async () => {
    gate._resetForTests();
    const holdMs = 250;
    const max = gate.stats().max;
    const app = express();
    app.use(express.json({ limit: '512kb' }));
    app.post('/api/fishit-tracker/update-backpack', gate.wrapTrackerUpload('test-hold', (req, res) => {
      setTimeout(() => {
        res.status(200).json({ ok: true, user: req.body.username });
      }, holdMs);
    }));

    const payloads = Array.from({ length: max + 8 }, (_, i) => ({
      username: `GateUser${i}`,
      type: 'inventory_snapshot',
    }));

    const results = await Promise.all(
      payloads.map((body) => request(app)
        .post('/api/fishit-tracker/update-backpack')
        .send(body)),
    );

    for (const res of results) {
      assert.notEqual(res.status, 202, 'inventory upload must not be dropped while gate is saturated');
      assert.equal(res.status, 200);
      assert.equal(res.body.ok, true);
    }
  });

  test('tracker_status bypasses gate even when inventory slots are held', async () => {
    gate._resetForTests();
    const app = express();
    app.use(express.json());
    app.post('/api/fishit-tracker/update-backpack', gate.wrapTrackerUpload('test-hold', (_req, res) => {
      setTimeout(() => res.status(200).json({ ok: true, kind: 'inventory' }), 300);
    }));

    const inventory = Array.from({ length: gate.stats().max }, () => request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({ type: 'inventory_snapshot', username: 'hold' }));

    const status = await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({ type: 'tracker_status', username: 'heartbeat' });

    assert.equal(status.status, 200);
    await Promise.all(inventory);
  });
});
