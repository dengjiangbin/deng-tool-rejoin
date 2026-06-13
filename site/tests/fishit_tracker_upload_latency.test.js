'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';

const gate = require('../src/trackerConcurrencyGate');
const trackerRouter = require('../src/fishitTrackerRoutes');
const {
  MINIMUM_TRACKER_BUILD,
  ALLOWED_TRACKER_CHANNEL,
  ALLOWED_TRACKER_RAW_URL,
} = require('../src/fishitTrackerChannelEnforcement');

function makeApp() {
  const app = express();
  app.use(express.json({ limit: '512kb' }));
  app.use(trackerRouter);
  return app;
}

function inventoryPayload(extra = {}) {
  return {
    username: 'LatencyBurstUser',
    userId: 88001,
    isOnline: true,
    type: 'inventory_snapshot',
    phase: 'live',
    clientOrigin: 'roblox_tracker',
    trackerBuild: MINIMUM_TRACKER_BUILD,
    trackerChannel: ALLOWED_TRACKER_CHANNEL,
    scriptSource: ALLOWED_TRACKER_RAW_URL,
    trackerClientProof: {
      trackerBuild: MINIMUM_TRACKER_BUILD,
      trackerChannel: ALLOWED_TRACKER_CHANNEL,
      scriptSource: ALLOWED_TRACKER_RAW_URL,
    },
    inventorySource: 'playerdata_gameitemdb',
    scanCompleted: true,
    replionReady: true,
    leaderstatsReady: true,
    fishScanReady: true,
    stoneScanReady: true,
    fishItems: [{
      itemId: '1',
      name: 'Burst Fish',
      quantity: 3,
      type: 'Fish',
      source: 'playerdata_gameitemdb',
      rarity: 'Common',
    }],
    stoneItems: [],
    playerStats: {
      coins: 12345,
      totalCaught: 99,
      source: 'leaderstats',
      build: MINIMUM_TRACKER_BUILD,
    },
    ...extra,
  };
}

describe('tracker upload latency — fast path + coalesced enrichment', () => {
  beforeEach(() => {
    gate._resetForTests();
  });

  test('inventory uploads bypass global slot queue (gate_wait_ms=0)', () => {
    const source = require('fs').readFileSync(
      require('path').join(__dirname, '..', 'src', 'trackerConcurrencyGate.js'),
      'utf8',
    );
    assert.doesNotMatch(source, /acquireSlot/);
    assert.doesNotMatch(source, /server_busy/);
    assert.match(source, /scheduleDeferredUploadWork/);
  });

  test('1 — 20 rapid POSTs for same user return 200 and latest snapshot wins', async () => {
    const app = makeApp();
    const started = Date.now();
    for (let i = 0; i < 20; i += 1) {
      const res = await request(app)
        .post('/api/fishit-tracker/update-backpack')
        .send(inventoryPayload({
          username: 'CoalesceUser',
          playerStats: {
            coins: 1000 + i,
            totalCaught: 10 + i,
            source: 'leaderstats',
            build: MINIMUM_TRACKER_BUILD,
          },
        }));
      assert.equal(res.status, 200, 'must never return fake 202');
      assert.notEqual(res.body.status, 'queued');
    }
    const elapsed = Date.now() - started;
    const dbg = await request(app)
      .get('/api/fishit-tracker/debug/CoalesceUser')
      .expect(200);

    assert.equal(dbg.body.playerStats.coins, 1019);
    assert.equal(dbg.body.playerStats.totalCaught, 29);
    assert.ok(dbg.body.lastUploadReceivedAt);
    assert.ok(dbg.body.latestSuccessfulUploadAt);
    assert.ok(elapsed < 15_000, `20 sequential uploads should finish quickly, took ${elapsed}ms`);
  });

  test('2 — raw snapshot timestamps update before deferred enrichment completes', async () => {
    const app = makeApp();
    const before = Date.now();
    const res = await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(inventoryPayload({ username: 'FastPersistUser', userId: 88002 }))
      .expect(200);

    assert.equal(res.status, 200);
    assert.ok(res.body.lastInventoryAt);
    const receivedMs = new Date(res.body.lastInventoryAt).getTime();
    assert.ok(receivedMs >= before - 1000);
    assert.ok(Date.now() - receivedMs < 5000);

    const dbg = await request(app)
      .get('/api/fishit-tracker/debug/FastPersistUser')
      .expect(200);
    assert.equal(dbg.body.lastUploadStatusCodeReturned, 200);
    assert.ok(dbg.body.latestSuccessfulUploadAt);
  });

  test('3 — concurrent users do not get 202 and persist independently', async () => {
    const app = makeApp();
    const users = Array.from({ length: 8 }, (_, i) => `ConcurrentUser${i}`);
    const started = Date.now();
    const results = await Promise.all(users.map((username, i) => request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(inventoryPayload({
        username,
        userId: 89000 + i,
        playerStats: {
          coins: 500 + i,
          totalCaught: i,
          source: 'leaderstats',
          build: MINIMUM_TRACKER_BUILD,
        },
      }))));

    for (const res of results) {
      assert.equal(res.status, 200);
    }
    assert.ok(Date.now() - started < 20_000);

    for (let i = 0; i < users.length; i += 1) {
      const dbg = await request(app).get(`/api/fishit-tracker/debug/${users[i]}`).expect(200);
      assert.equal(dbg.body.playerStats.coins, 500 + i);
      assert.ok(dbg.body.latestSuccessfulUploadAt);
    }
  });

  test('4 — get-backpack reflects latest snapshot quickly after POST', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(inventoryPayload({ username: 'PollFreshUser', userId: 88003 }))
      .expect(200);

    const bp = await request(app)
      .get('/api/fishit-tracker/get-backpack/PollFreshUser?lite=1')
      .expect(200);

    assert.ok(Array.isArray(bp.body.fishItems));
    assert.equal(bp.body.fishItems.length, 1);
    assert.equal(bp.body.fishItems[0].name, 'Burst Fish');
    assert.ok(bp.body.serverReceivedAt || bp.body.lastInventoryAt);
  });

  test('5 — heartbeat and inventory share fresh lastSuccessfulUploadAt after snapshot', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(inventoryPayload({ username: 'StatusFreshUser', userId: 88004 }))
      .expect(200);

    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'StatusFreshUser',
        userId: 88004,
        isOnline: true,
        type: 'tracker_status',
        trackerBuild: MINIMUM_TRACKER_BUILD,
        trackerChannel: ALLOWED_TRACKER_CHANNEL,
        scriptSource: ALLOWED_TRACKER_RAW_URL,
        trackerClientProof: {
          trackerBuild: MINIMUM_TRACKER_BUILD,
          trackerChannel: ALLOWED_TRACKER_CHANNEL,
          scriptSource: ALLOWED_TRACKER_RAW_URL,
        },
      })
      .expect(200);

    const dbg = await request(app)
      .get('/api/fishit-tracker/debug/StatusFreshUser')
      .expect(200);

    const uploadAt = new Date(dbg.body.latestSuccessfulUploadAt).getTime();
    const ageSec = dbg.body.syncProof?.ageSeconds;
    assert.ok(Number.isFinite(uploadAt));
    assert.ok(ageSec == null || ageSec < 120, 'status should not show multi-minute stale age right after upload');
  });

  test('6 — deferred enrichment coalesces per account pending count', async () => {
    gate._resetForTests();
    let runs = 0;
    gate.scheduleDeferredUploadWork('alpha', () => new Promise((resolve) => {
      setTimeout(resolve, 80);
    }));
    gate.scheduleDeferredUploadWork('alpha', () => { runs += 1; });
    gate.scheduleDeferredUploadWork('alpha', () => { runs += 1; });
    assert.ok(gate.stats().deferredSuperseded >= 2);
    assert.ok(gate.perAccountPendingCount('alpha') >= 1);
    await new Promise((r) => setTimeout(r, 120));
    assert.equal(runs, 1, 'only latest deferred job should run');
  });
});
