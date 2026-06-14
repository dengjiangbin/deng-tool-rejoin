'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const os = require('os');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';
process.env.INVENTORY_ACCOUNTS_MEMORY = '1';

const trackerRoutes = require('../src/fishitTrackerRoutes');
const sessionStore = require('../src/fishitSessionStore');
const leaderstatsUpload = require('../src/fishitLeaderstatsUpload');
const { finishTrackerUploadResponse } = require('../src/trackerUploadResponse');
const { MINIMUM_TRACKER_BUILD } = require('../src/fishitTrackerBuild');
const { RAW_TRACKER_LUA, testIfRawTracker } = require('./helpers/trackerRawSource');

const liveTrackDB = trackerRoutes.liveTrackDB;

function makeApp() {
  const app = express();
  app.use(express.json({ limit: '512kb' }));
  app.use((req, _res, next) => {
    req.inventoryOwnerDiscordId = '123456789012345678';
    next();
  });
  app.use(trackerRoutes);
  return app;
}

function iso(msAgo) {
  return new Date(Date.now() - msAgo).toISOString();
}

describe('tracker upload pipeline 202 success + snapshot promotion', () => {
  testIfRawTracker('Lua treats HTTP 202 and plain ok body as upload success', () => {
    const lua = fs.readFileSync(RAW_TRACKER_LUA, 'utf8');
    assert.match(lua, /UPLOAD_HTTP_2XX_SUCCESS_FIX_2026_06_14/);
    const fn = lua.match(/function HttpDash\.uploadOkFromResult\([\s\S]*?^end/m);
    assert.ok(fn, 'uploadOkFromResult must exist');
    assert.match(fn[0], /HttpDash\.isHttpSuccess/);
    assert.match(fn[0], /http_/);
    const postReq = lua.match(/function HttpDash\.postRequiredLeaderstats\([\s\S]*?^end/m);
    assert.ok(postReq);
    assert.match(postReq[0], /REQUIRED_LEADERSTATS_UPLOAD_OK status=/);
    assert.doesNotMatch(postReq[0], /statusCode == "200"/);
    assert.match(lua, /playerdata_direct/);
    assert.match(lua, /DASHBOARD_RESPONSE inventory_snapshot success=%s status=%s/);
    assert.doesNotMatch(lua, /local ok200\s*=\s*\(tostring\(code\) == "200"\)/);
  });

  test('sanitiseSession roundtrips leaderstats upload fields', () => {
    const now = iso(0);
    const row = sessionStore.sanitiseSession('proofuser', {
      username: 'ProofUser',
      userId: 42,
      leaderstatsUploadOk: true,
      leaderstatsUploadedAt: now,
      leaderstatsUploadSeq: 7,
      lastStatsUploadAt: now,
      lastSnapshotUploadAt: now,
      lastValidLeaderstats: { coins: 100, totalCaught: 50, rarestFishChance: '1/100' },
      playerStats: {
        coins: 100,
        totalCaught: 50,
        rarestFishChance: '1/100',
        source: 'leaderstats',
        statsAt: now,
      },
      requiredOk: true,
      intervalSeconds: 60,
    });
    assert.equal(row.leaderstatsUploadOk, true);
    assert.equal(row.leaderstatsUploadSeq, 7);
    assert.equal(row.lastStatsUploadAt, now);
    assert.equal(row.lastSnapshotUploadAt, now);
    assert.equal(row.requiredOk, true);
    assert.equal(row.lastValidLeaderstats.totalCaught, 50);
  });

  test('leaderstats timestamp survives disk rehydrate without leaderstatsUploadOk flag', () => {
    const now = iso(5000);
    const stats = {
      coins: 10,
      totalCaught: 5,
      rarestFishChance: '1/50',
      source: 'leaderstats',
      statsAt: now,
      build: MINIMUM_TRACKER_BUILD,
    };
    const ts = leaderstatsUpload.leaderstatsUploadTimestamp({
      lastStatsUploadAt: now,
      playerStats: stats,
    });
    assert.equal(ts, now);
    const status = leaderstatsUpload.deriveLeaderstatsUploadStatus({
      lastStatsUploadAt: now,
      playerStats: stats,
      intervalSeconds: 60,
    }, { serverNowMs: Date.now() });
    assert.equal(status.statsUploadFresh, true);
    assert.equal(status.lastStatsUploadAt, now);
  });

  test('202 accepted upload updates read API timestamps and stats', async () => {
    const key = 'pipe202user';
    const username = 'Pipe202User';
    const now = iso(0);
    liveTrackDB[key] = {
      username,
      userId: 77,
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      lastAccountSeenAt: now,
      lastSuccessfulUploadAt: now,
    };

    const app = makeApp();
    const body = {
      type: 'inventory_snapshot',
      username,
      userId: 77,
      trackerBuild: MINIMUM_TRACKER_BUILD,
      clientOrigin: 'roblox_tracker',
      evidenceSourceMode: 'live_roblox',
      intervalSeconds: 60,
      syncIntervalSeconds: 60,
      isOnline: true,
      online: true,
      playerStats: {
        coins: 999,
        totalCaught: 1234,
        rarestFishChance: '1/200',
        source: 'leaderstats',
        build: MINIMUM_TRACKER_BUILD,
      },
      fishItems: [{ itemId: '1', name: 'Test Fish', quantity: 1, source: 'playerdata_gameitemdb' }],
      stoneItems: [],
      totemItems: [],
    };
    const uploadRes = await request(app).post('/api/fishit-tracker/update-backpack').send(body);
    assert.ok([200, 202].includes(uploadRes.status), `upload status ${uploadRes.status} body=${JSON.stringify(uploadRes.body)}`);

    const backpack = await request(app).get(`/api/tracker/get-backpack/${key}`).expect(200);
    assert.ok(backpack.body.statusLastSuccessAt, 'statusLastSuccessAt required');
    assert.ok(backpack.body.inventoryLastSuccessAt, 'inventoryLastSuccessAt required');
    if (backpack.body.leaderstatsLastSuccessAt) {
      assert.equal(typeof backpack.body.secondsSinceLastLeaderstatsSuccess, 'number');
    }
  });

  test('finishTrackerUploadResponse 202 still promotes latest heartbeat state', () => {
    const key = 'hb202';
    const now = iso(0);
    liveTrackDB[key] = {
      username: 'Hb202',
      userId: 88,
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      lastAccountSeenAt: now,
      leaderstatsUploadOk: true,
      lastStatsUploadAt: now,
      lastSnapshotUploadAt: now,
      playerStats: {
        coins: 1,
        totalCaught: 2,
        rarestFishChance: '1/10',
        source: 'leaderstats',
        statsAt: now,
        build: MINIMUM_TRACKER_BUILD,
      },
    };
    const req = { headers: {}, trackerDeferEnrichment: true };
    const res = {
      statusCode: 200,
      body: null,
      status(code) { this.statusCode = code; return this; },
      json(payload) { this.body = payload; return this; },
    };
    finishTrackerUploadResponse(req, res, {
      ok: true,
      status: 'success',
      acceptedCount: 1,
      lastSeenAt: now,
      serverTime: now,
    }, key);
    assert.equal(res.statusCode, 202);
    assert.equal(res.body.ok, true);
    const sanitized = sessionStore.sanitiseSession(key, liveTrackDB[key]);
    assert.equal(sanitized.leaderstatsUploadOk, true);
    assert.equal(sanitized.lastStatsUploadAt, now);
  });
});
