'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';

const trackerRoutes = require('../src/fishitTrackerRoutes');
const snapshotCompleteness = require('../src/fishitSnapshotCompleteness');
const uploadStatus = require('../src/fishitTrackerUploadStatus');
const { MINIMUM_TRACKER_BUILD } = require('../src/fishitTrackerBuild');
const manifest = require('../src/inventoryAssetManifest.json');

const liveTrackDB = trackerRoutes.liveTrackDB;
const gate = require('../src/trackerConcurrencyGate');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const INVENTORY_JS = path.join(__dirname, '..', 'public', 'assets', manifest.js);

function makeApp() {
  const app = express();
  app.use(trackerRoutes);
  return app;
}

function isTrusted(build) {
  return build === MINIMUM_TRACKER_BUILD || String(build).includes('LOADER_REGISTER_LIMIT_FIX');
}

function expectUploadOk() {
  return (res) => {
    if (res.status !== 200 && res.status !== 202) {
      throw new Error(`expected 200 or 202, got ${res.status}`);
    }
  };
}

describe('snapshot completeness — blank first payload handling', () => {
  beforeEach(() => {
    gate._resetForTests();
    for (const key of Object.keys(liveTrackDB)) delete liveTrackDB[key];
  });

  test('blank inventory snapshot without scan proof is rejected on first execution', async () => {
    const app = makeApp();
    const username = 'BlankFirstUser';
    const key = username.toLowerCase();
    await request(app).post('/api/tracker/update-backpack').send({
      type: 'inventory_snapshot',
      username,
      userId: 991100,
      isOnline: true,
      clientOrigin: 'roblox_tracker',
      trackerBuild: MINIMUM_TRACKER_BUILD,
      inventorySource: 'playerdata_gameitemdb',
      fishItems: [],
      stoneItems: [],
      firstExecution: true,
      scanCompleted: false,
      playerStats: { source: 'missing', build: MINIMUM_TRACKER_BUILD },
    }).expect(expectUploadOk());

    const session = liveTrackDB[key];
    assert.ok(session);
    assert.equal(session.snapshotComplete, false);
    assert.equal(session.blankPayloadRejected, true);
    assert.notEqual(session.inventoryDisplayState, 'empty');
  });

  test('heartbeat alone keeps account yellow while snapshot incomplete', async () => {
    const app = makeApp();
    const username = 'HbOnlyUser';
    const key = username.toLowerCase();
    await request(app).post('/api/tracker/update-backpack').send({
      type: 'tracker_status',
      username,
      userId: 991101,
      isOnline: true,
      clientOrigin: 'roblox_tracker',
      trackerBuild: MINIMUM_TRACKER_BUILD,
    }).expect(200);

    const session = liveTrackDB[key];
    assert.ok(session);
    const status = uploadStatus.deriveTrackerUploadAccountStatus(session, {
      expectedTrackerBuild: MINIMUM_TRACKER_BUILD,
      isTrustedBuild: isTrusted,
    });
    assert.equal(status.statusColor, 'yellow');
    assert.equal(status.inventoryReady, false);
    assert.equal(session.snapshotComplete, false);
    assert.ok(session.lastHeartbeatAt || session.lastSuccessfulHeartbeatAt);
  });

  test('full snapshot with fish/stone and stats marks snapshotComplete', async () => {
    const app = makeApp();
    const username = 'FullSnapUser';
    const key = username.toLowerCase();
    const res = await request(app).post('/api/tracker/update-backpack').send({
      type: 'inventory_snapshot',
      username,
      userId: 991102,
      isOnline: true,
      clientOrigin: 'roblox_tracker',
      trackerBuild: MINIMUM_TRACKER_BUILD,
      inventorySource: 'playerdata_gameitemdb',
      scanCompleted: true,
      replionReady: true,
      leaderstatsReady: true,
      fishScanReady: true,
      stoneScanReady: true,
      fishItemCount: 1,
      stoneItemCount: 0,
      fishItems: [{ itemId: '1', name: 'Clownfish', type: 'Fish', quantity: 1, source: 'playerdata_gameitemdb' }],
      stoneItems: [],
      playerDataGameItemDbProof: { playerDataInventoryCount: 1, gameItemDbBuilt: true },
      playerStats: {
        coins: 100,
        totalCaught: 10,
        source: 'leaderstats',
        build: MINIMUM_TRACKER_BUILD,
      },
    }).expect(expectUploadOk());
    assert.equal(res.body.snapshotComplete, true);

    const session = liveTrackDB[key];
    assert.ok(session);
    assert.equal(session.snapshotComplete, true);
    assert.ok(session.firstFullSnapshotAt);
    assert.equal(session.snapshotCompletenessReason, 'full_snapshot_verified');
    assert.ok(session.lastInventoryAt);
  });

  test('blank upload does not overwrite existing good inventory', async () => {
    const app = makeApp();
    const username = 'PreserveGood';
    const key = username.toLowerCase();
    await request(app).post('/api/tracker/update-backpack').send({
      type: 'inventory_snapshot',
      username,
      userId: 991103,
      isOnline: true,
      clientOrigin: 'roblox_tracker',
      trackerBuild: MINIMUM_TRACKER_BUILD,
      inventorySource: 'playerdata_gameitemdb',
      scanCompleted: true,
      replionReady: true,
      leaderstatsReady: true,
      fishScanReady: true,
      stoneScanReady: true,
      fishItems: [{ itemId: '1', name: 'Clownfish', type: 'Fish', quantity: 2, source: 'playerdata_gameitemdb' }],
      stoneItems: [],
      playerDataGameItemDbProof: { playerDataInventoryCount: 1, gameItemDbBuilt: true },
      playerStats: { coins: 50, totalCaught: 5, source: 'leaderstats', build: MINIMUM_TRACKER_BUILD },
    }).expect(expectUploadOk());

    await request(app).post('/api/tracker/update-backpack').send({
      type: 'inventory_snapshot',
      username,
      userId: 991103,
      isOnline: true,
      clientOrigin: 'roblox_tracker',
      trackerBuild: MINIMUM_TRACKER_BUILD,
      inventorySource: 'playerdata_gameitemdb',
      fishItems: [],
      stoneItems: [],
      scanCompleted: false,
      playerStats: { coins: 50, totalCaught: 5, source: 'leaderstats', build: MINIMUM_TRACKER_BUILD },
    }).expect(expectUploadOk());

    const fish = Array.isArray(liveTrackDB[key]?.playerDataFishItems)
      ? liveTrackDB[key].playerDataFishItems
      : [];
    assert.ok(fish.length > 0, 'prior fish inventory must be preserved');
  });

  test('frontend shows syncing text instead of empty inventory when snapshot incomplete', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /function inventoryDisplayState/);
    assert.match(source, /Waiting for inventory snapshot/);
    assert.match(source, /Waiting for snapshot/);
    assert.match(source, /function statsSnapshotReady/);
    const js = fs.readFileSync(INVENTORY_JS, 'utf8');
    assert.match(js, /inventoryDisplayState/);
    assert.match(js, /Waiting for inventory snapshot/);
  });

  test('evaluateSnapshotCompleteness distinguishes scan-not-ready from verified empty', () => {
    const now = new Date().toISOString();
    const notReady = snapshotCompleteness.evaluateSnapshotCompleteness({
      body: {
        username: 'x',
        userId: 1,
        inventorySource: 'playerdata_gameitemdb',
        fishItems: [],
        stoneItems: [],
        playerDataGameItemDbProof: { playerDataInventoryCount: 5, gameItemDbBuilt: true },
        playerStats: { coins: 1, totalCaught: 1, source: 'leaderstats', build: MINIMUM_TRACKER_BUILD },
      },
      existing: null,
      cleanItems: [],
      playerDataFishItems: [],
      playerDataStoneItems: [],
      parseStats: null,
      partialInfo: { isPartial: false },
      isHeartbeat: false,
      now,
    });
    assert.equal(notReady.snapshotComplete, false);
    assert.equal(notReady.provenEmptyInventory, false);
    assert.match(notReady.snapshotCompletenessReason, /unresolved|scan|blank/i);

    const verifiedEmpty = snapshotCompleteness.evaluateSnapshotCompleteness({
      body: {
        username: 'x',
        userId: 1,
        inventorySource: 'playerdata_gameitemdb',
        scanCompleted: true,
        replionReady: true,
        leaderstatsReady: true,
        fishScanReady: true,
        stoneScanReady: true,
        fishItems: [],
        stoneItems: [],
        playerDataGameItemDbProof: { playerDataInventoryCount: 0, gameItemDbBuilt: true },
        playerStats: { coins: 0, totalCaught: 0, source: 'leaderstats', build: MINIMUM_TRACKER_BUILD },
      },
      existing: null,
      cleanItems: [],
      playerDataFishItems: [],
      playerDataStoneItems: [],
      parseStats: null,
      partialInfo: { isPartial: false },
      isHeartbeat: false,
      now,
    });
    assert.equal(verifiedEmpty.snapshotComplete, true);
    assert.equal(verifiedEmpty.provenEmptyInventory, true);
  });

  test('detectPartialZeroFishSnapshot ignores empty cleanItems when playerData rows exist', () => {
    const partialSnapshot = require('../src/fishitPartialSnapshot');
    const info = partialSnapshot.detectPartialZeroFishSnapshot({
      ps: null,
      cleanItems: [],
      existing: { lastGoodFishItems: [{ name: 'Old' }], lastGoodPublicFishCount: 5 },
      priorPublicFishCount: 5,
      playerDataFishCount: 44,
      playerDataStoneCount: 22,
      usesPlayerDataGameItemDb: true,
    });
    assert.equal(info.isPartial, false);
    assert.equal(info.partialSnapshotDetected, false);
  });

  test('fish and stone snapshot without leaderstats marks inventoryReady but not snapshotComplete', () => {
    const now = new Date().toISOString();
    const result = snapshotCompleteness.evaluateSnapshotCompleteness({
      body: {
        username: 'nostats',
        userId: 1,
        inventorySource: 'playerdata_gameitemdb',
        fishItems: [{ itemId: '1', name: 'Clownfish', quantity: 1, type: 'Fish', source: 'playerdata_gameitemdb' }],
        stoneItems: [{ itemId: '2', name: 'Stone', quantity: 1, type: 'Stone', source: 'playerdata_gameitemdb' }],
        playerStats: { source: 'missing', build: MINIMUM_TRACKER_BUILD },
        playerDataGameItemDbProof: { compact: true, uploadPath: 'playerdata_gameitemdb' },
      },
      existing: null,
      cleanItems: [],
      playerDataFishItems: [{ itemId: '1', name: 'Clownfish', quantity: 1, type: 'Fish' }],
      playerDataStoneItems: [{ itemId: '2', name: 'Stone', quantity: 1, kind: 'stone' }],
      parseStats: null,
      partialInfo: { isPartial: false },
      isHeartbeat: false,
      now,
    });
    assert.equal(result.inventoryReady, true);
    assert.equal(result.snapshotComplete, false);
    assert.equal(result.hasFishSnapshot, true);
    assert.equal(result.hasStoneSnapshot, true);
    assert.equal(result.snapshotCompletenessReason, 'inventory_ready_awaiting_leaderstats');
  });

  test('compact gameitemdb upload with fish/stone is not blocked by legacy zero-fish partial path', async () => {
    const app = makeApp();
    const username = 'CompactNotPartial';
    const key = username.toLowerCase();
    await request(app).post('/api/tracker/update-backpack').send({
      type: 'inventory_snapshot',
      username,
      userId: 991201,
      isOnline: true,
      clientOrigin: 'roblox_tracker',
      trackerBuild: MINIMUM_TRACKER_BUILD,
      inventorySource: 'playerdata_gameitemdb',
      scanCompleted: true,
      replionReady: true,
      leaderstatsReady: true,
      fishScanReady: true,
      stoneScanReady: true,
      hasLeaderstatsSnapshot: true,
      hasFishSnapshot: true,
      hasStoneSnapshot: true,
      fishItems: [{ itemId: '1', name: 'Seed Fish', type: 'Fish', quantity: 2, source: 'playerdata_gameitemdb' }],
      stoneItems: [],
      playerDataGameItemDbProof: { playerDataInventoryCount: 1, gameItemDbBuilt: true },
      playerStats: { coins: 10, totalCaught: 1, source: 'leaderstats', build: MINIMUM_TRACKER_BUILD },
    }).expect(expectUploadOk());
    assert.equal(liveTrackDB[key].snapshotComplete, true);

    await request(app).post('/api/tracker/update-backpack').send({
      type: 'inventory_snapshot',
      username,
      userId: 991201,
      isOnline: true,
      clientOrigin: 'roblox_tracker',
      trackerBuild: MINIMUM_TRACKER_BUILD,
      inventorySource: 'playerdata_gameitemdb',
      hasLeaderstatsSnapshot: true,
      hasFishSnapshot: true,
      hasStoneSnapshot: true,
      fishItems: [{ itemId: '1', name: 'Seed Fish', type: 'Fish', quantity: 2, source: 'playerdata_gameitemdb' }],
      stoneItems: [{ itemId: '9', name: 'Luck Stone', type: 'Stone', quantity: 1, kind: 'stone', source: 'playerdata_gameitemdb' }],
      playerStats: { coins: 10, totalCaught: 1, source: 'leaderstats', build: MINIMUM_TRACKER_BUILD },
      playerDataGameItemDbProof: { compact: true, uploadPath: 'playerdata_gameitemdb' },
    }).expect(expectUploadOk());

    assert.equal(liveTrackDB[key].inventoryReady, true);
    assert.equal(liveTrackDB[key].snapshotComplete, true);
    assert.ok(Array.isArray(liveTrackDB[key].playerDataFishItems));
    assert.ok(liveTrackDB[key].playerDataFishItems.length >= 1);
    assert.ok(Array.isArray(liveTrackDB[key].playerDataStoneItems));
    assert.ok(liveTrackDB[key].playerDataStoneItems.length >= 1);
  });
});
