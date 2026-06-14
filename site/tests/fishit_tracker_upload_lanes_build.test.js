'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

process.env.FISHIT_REQUIRE_TRACKER_PROOF_IN_TEST = '1';
process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';
process.env.INVENTORY_ACCOUNTS_MEMORY = '1';

const trackerRoutes = require('../src/fishitTrackerRoutes');
const gate = require('../src/trackerConcurrencyGate');
const {
  PRODUCTION_TRACKER_BUILD,
} = require('../src/fishitTrackerBuild');
const {
  ALLOWED_TRACKER_CHANNEL,
  ALLOWED_TRACKER_RAW_URL,
} = require('../src/fishitTrackerChannelEnforcement');

const liveTrackDB = trackerRoutes.liveTrackDB;
const USER = 'BuildLaneUser';

function makeApp() {
  const app = express();
  app.use(trackerRoutes);
  return app;
}

function clientProof(extra = {}) {
  return {
    trackerBuild: PRODUCTION_TRACKER_BUILD,
    trackerChannel: ALLOWED_TRACKER_CHANNEL,
    scriptSource: ALLOWED_TRACKER_RAW_URL,
    clientOrigin: 'roblox_tracker',
    trackerClientProof: {
      trackerBuild: PRODUCTION_TRACKER_BUILD,
      trackerChannel: ALLOWED_TRACKER_CHANNEL,
      scriptSource: ALLOWED_TRACKER_RAW_URL,
    },
    username: USER,
    userId: 88001,
    isOnline: true,
    ...extra,
  };
}

function expectUploadOk(res) {
  if (res.status !== 200 && res.status !== 202) {
    throw new Error(`expected 200/202 got ${res.status} body=${JSON.stringify(res.body)}`);
  }
}

describe('upload lanes accept current public tracker build', () => {
  beforeEach(() => {
    gate._resetForTests();
    for (const key of Object.keys(liveTrackDB)) delete liveTrackDB[key];
  });

  test('tracker_status lane accepts current build', async () => {
    const app = makeApp();
    const res = await request(app).post('/api/fishit-tracker/update-backpack').send(clientProof({
      type: 'tracker_status',
      phase: 'live',
    }));
    expectUploadOk(res);
    assert.notEqual(res.body?.error, 'OUTDATED_TRACKER_BUILD');
  });

  test('inventory_snapshot lane accepts current build', async () => {
    const app = makeApp();
    const res = await request(app).post('/api/fishit-tracker/update-backpack').send(clientProof({
      type: 'inventory_snapshot',
      inventorySource: 'playerdata_gameitemdb',
      scanCompleted: true,
      replionReady: true,
      leaderstatsReady: true,
      fishScanReady: true,
      stoneScanReady: true,
      playerDataGameItemDbProof: { playerDataInventoryCount: 1, gameItemDbBuilt: true },
      fishItems: [{ itemId: '1', name: 'Clownfish', type: 'Fish', quantity: 1, source: 'playerdata_gameitemdb' }],
      stoneItems: [],
      playerStats: {
        coins: 100,
        totalCaught: 5,
        source: 'leaderstats',
        build: PRODUCTION_TRACKER_BUILD,
      },
    }));
    expectUploadOk(res);
    assert.notEqual(res.body?.error, 'OUTDATED_TRACKER_BUILD');
  });

  test('required leaderstats via playerStats on inventory lane accepts current build', async () => {
    const app = makeApp();
    const res = await request(app).post('/api/fishit-tracker/update-backpack').send(clientProof({
      type: 'inventory_snapshot',
      inventorySource: 'playerdata_gameitemdb',
      hasLeaderstatsSnapshot: true,
      leaderstatsReady: true,
      playerStats: {
        coins: 250,
        totalCaught: 12,
        rarestFishChance: '1/100',
        source: 'leaderstats',
        build: PRODUCTION_TRACKER_BUILD,
      },
      fishItems: [],
      stoneItems: [],
    }));
    expectUploadOk(res);
    assert.notEqual(res.body?.error, 'OUTDATED_TRACKER_BUILD');
    const session = liveTrackDB[USER.toLowerCase()];
    assert.ok(session?.playerStats || session?.lastStatsUploadAt || session?.lastSuccessfulUploadAt);
  });

  test('rejects stale build with OUTDATED_TRACKER_BUILD on all lanes', async () => {
    const app = makeApp();
    const stale = clientProof({
      type: 'inventory_snapshot',
      trackerBuild: 'BLOCKER10ZT3_SYNC_STATUS_COIN_MOBILE_TABLE_2026_06_10',
      trackerClientProof: {
        trackerBuild: 'BLOCKER10ZT3_SYNC_STATUS_COIN_MOBILE_TABLE_2026_06_10',
        trackerChannel: ALLOWED_TRACKER_CHANNEL,
        scriptSource: ALLOWED_TRACKER_RAW_URL,
      },
    });
    const res = await request(app).post('/api/fishit-tracker/update-backpack').send(stale);
    assert.equal(res.status, 403);
    assert.equal(res.body.error, 'OUTDATED_TRACKER_BUILD');
  });

  test('public raw URL constant matches enforcement module', () => {
    const { PUBLIC_TRACKER_RAW_URL } = require('../src/fishitPublicTrackerBuild');
    assert.equal(ALLOWED_TRACKER_RAW_URL, PUBLIC_TRACKER_RAW_URL);
    assert.match(ALLOWED_TRACKER_RAW_URL, /raw\.githubusercontent\.com\/dengjiangbin\/fish-it\/main\/tracker\.lua$/);
  });
});
