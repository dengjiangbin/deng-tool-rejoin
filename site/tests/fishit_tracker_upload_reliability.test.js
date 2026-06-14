'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.FISHIT_REQUIRE_TRACKER_PROOF_IN_TEST = '1';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';

const trackerRoutes = require('../src/fishitTrackerRoutes');
const { PRODUCTION_TRACKER_BUILD } = require('../src/fishitTrackerBuild');
const { ALLOWED_TRACKER_RAW_URL, ALLOWED_TRACKER_CHANNEL } = require('../src/fishitTrackerChannelEnforcement');
const coalesce = require('../src/trackerUploadCoalesce');
const { safeOptionalWeight } = require('../src/fishitUploadRowSafety');

const BUILD = PRODUCTION_TRACKER_BUILD;
const RAW = ALLOWED_TRACKER_RAW_URL;

function makeApp() {
  const app = express();
  app.use(trackerRoutes.uploadRouter);
  return app;
}

function baseBody(username, extra = {}) {
  return {
    username,
    userId: 88000 + username.length,
    trackerBuild: BUILD,
    trackerChannel: ALLOWED_TRACKER_CHANNEL,
    scriptSource: RAW,
    clientOrigin: 'roblox_tracker',
    evidenceSourceMode: 'live_roblox',
    isOnline: true,
    ...extra,
  };
}

describe('upload reliability — missing weight + no 429 storms', () => {
  beforeEach(() => {
    coalesce._resetForTests();
  });

  test('safeOptionalWeight never throws on null/undefined rows', () => {
    assert.equal(safeOptionalWeight(null), null);
    assert.equal(safeOptionalWeight(undefined), null);
    assert.equal(safeOptionalWeight({}), null);
    assert.equal(safeOptionalWeight({ weight: 12.5 }), 12.5);
  });

  test('required_leaderstats lane accepts payload without weight fields', async () => {
    const app = makeApp();
    const res = await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(baseBody('WeightSafeLeader', {
        type: 'inventory_snapshot',
        uploadPath: 'playerdata_leaderstats_only',
        leaderstatsOnlyUpload: true,
        playerStats: {
          coins: 500,
          totalCaught: 42,
          rarestFishChance: '1/500',
          source: 'leaderstats',
        },
      }));
    assert.notEqual(res.status, 500, res.body?.message || res.body?.error);
    assert.notEqual(res.status, 429, res.body?.message || res.body?.error);
    assert.ok(res.status === 200 || res.status === 202, `status=${res.status}`);
  });

  test('inventory_snapshot accepts rows missing weight and null holes', async () => {
    const app = makeApp();
    const res = await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(baseBody('WeightSafeInv', {
        type: 'inventory_snapshot',
        inventorySource: 'playerdata_gameitemdb',
        fishItems: [
          null,
          { itemId: '1', name: 'Clownfish', quantity: 2, source: 'playerdata_gameitemdb' },
          { itemId: '2', name: 'Shark', quantity: 1, source: 'playerdata_gameitemdb' },
        ],
        stoneItems: [],
        totemItems: [],
        playerStats: {
          coins: 100,
          totalCaught: 5,
          source: 'leaderstats',
        },
      }));
    assert.notEqual(res.status, 500, res.body?.message || res.body?.error);
    assert.notEqual(res.status, 429);
    assert.ok(res.status === 200 || res.status === 202);
  });

  test('rapid same-user uploads coalesce instead of 429', async () => {
    const app = makeApp();
    const body = baseBody('CoalesceUser', {
      type: 'tracker_status',
      online: true,
      lastSeenAt: new Date().toISOString(),
    });
    const first = await request(app).post('/api/fishit-tracker/update-backpack').send(body);
    assert.ok(first.status === 200 || first.status === 202);
    const second = await request(app).post('/api/fishit-tracker/update-backpack').send(body);
    assert.notEqual(second.status, 429);
    assert.ok(second.status === 200 || second.status === 202);
    if (second.status === 202 && second.body.coalesced) {
      assert.equal(second.body.coalesced, true);
    }
  });

  test('many distinct usernames upload concurrently without 429', async () => {
    const app = makeApp();
    const results = await Promise.all(
      Array.from({ length: 40 }, (_, i) => request(app)
        .post('/api/fishit-tracker/update-backpack')
        .send(baseBody(`BulkUser${i}`, {
          type: 'tracker_status',
          online: true,
        }))),
    );
    const blocked = results.filter((r) => r.status === 429);
    const serverErr = results.filter((r) => r.status >= 500);
    assert.equal(blocked.length, 0, `429 count=${blocked.length}`);
    assert.equal(serverErr.length, 0, `500 count=${serverErr.length}`);
  });
});
