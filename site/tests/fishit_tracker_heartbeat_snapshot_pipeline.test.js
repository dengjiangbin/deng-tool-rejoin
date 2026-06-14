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
process.env.INVENTORY_ACCOUNTS_MEMORY = '1';

const trackerRoutes = require('../src/fishitTrackerRoutes');
const uploadStatus = require('../src/fishitTrackerUploadStatus');
const liveTrackerSerializer = require('../src/fishitLiveTrackerSerializer');
const playerStatsStore = require('../src/fishitPlayerStats');
const inventoryTrackedAccounts = require('../src/inventoryTrackedAccounts');
const {
  MINIMUM_TRACKER_BUILD,
  ALLOWED_TRACKER_CHANNEL,
  ALLOWED_TRACKER_RAW_URL,
} = require('../src/fishitTrackerChannelEnforcement');
const manifest = require('../src/inventoryAssetManifest.json');

const liveTrackDB = trackerRoutes.liveTrackDB;
const gate = require('../src/trackerConcurrencyGate');
const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const INVENTORY_JS = path.join(__dirname, '..', 'public', 'assets', manifest.js);
const OWNER_ID = '123456789012345678';
const USERNAME = 'pipelinesnapuser';

function makeApp() {
  const app = express();
  app.use(trackerRoutes);
  return app;
}

function validProof(extra = {}) {
  return {
    trackerBuild: MINIMUM_TRACKER_BUILD,
    trackerChannel: ALLOWED_TRACKER_CHANNEL,
    scriptSource: ALLOWED_TRACKER_RAW_URL,
    clientOrigin: 'roblox_tracker',
    evidenceSourceMode: 'live_roblox',
    trackerClientProof: {
      trackerBuild: MINIMUM_TRACKER_BUILD,
      trackerChannel: ALLOWED_TRACKER_CHANNEL,
      scriptSource: ALLOWED_TRACKER_RAW_URL,
    },
    ...extra,
  };
}

function fullSnapshot(extra = {}) {
  return validProof({
    type: 'inventory_snapshot',
    username: USERNAME,
    userId: 991234567,
    isOnline: true,
    phase: 'live',
    inventorySource: 'playerdata_gameitemdb',
    scanCompleted: true,
    replionReady: true,
    leaderstatsReady: true,
    fishScanReady: true,
    stoneScanReady: true,
    hasLeaderstatsSnapshot: true,
    hasFishSnapshot: true,
    hasStoneSnapshot: true,
    playerDataGameItemDbProof: { playerDataInventoryCount: 1, gameItemDbBuilt: true },
    fishItems: [{
      itemId: '1',
      name: 'Pipeline Fish',
      quantity: 1,
      type: 'Fish',
      kind: 'fish',
      source: 'playerdata_gameitemdb',
    }],
    stoneItems: [],
    playerStats: {
      coins: 500,
      totalCaught: 25,
      source: 'leaderstats',
      build: MINIMUM_TRACKER_BUILD,
    },
    ...extra,
  });
}

describe('tracker heartbeat + snapshot pipeline', () => {
  beforeEach(() => {
    inventoryTrackedAccounts.resetMemoryStoreForTests();
    gate._resetForTests();
    for (const key of Object.keys(liveTrackDB)) delete liveTrackDB[key];
  });

  test('heartbeat-only registered account shows online/syncing on account-status', async () => {
    const added = await inventoryTrackedAccounts.addTrackedAccounts(OWNER_ID, [USERNAME]);
    assert.equal(added.accounts.length, 1);
    const app = makeApp();
    await request(app).post('/api/fishit-tracker/update-backpack').send(validProof({
      type: 'tracker_status',
      username: USERNAME,
      userId: 991234567,
      isOnline: true,
      phase: 'startup',
    })).expect(200);

    const session = liveTrackDB[USERNAME];
    assert.ok(session && session.lastHeartbeatAt);
    assert.notEqual(session.snapshotComplete, true);
    const presence = trackerRoutes.deriveAccountPresenceStatus(session);
    assert.equal(presence.accountPresenceLive, true);

    const status = await request(app).get('/api/tracker/account-status').expect(200);
    assert.equal(status.body.trackedCount, 1);
    assert.equal(status.body.onlineCount, 1);
    const row = status.body.accounts[0];
    assert.ok(row);
    assert.notEqual(row.statusColor, 'red');
    assert.notEqual(row.snapshotComplete, true);
  });

  test('heartbeat-only does not create fake inventory', async () => {
    await inventoryTrackedAccounts.addTrackedAccounts(OWNER_ID, [USERNAME]);
    const app = makeApp();
    await request(app).post('/api/fishit-tracker/update-backpack').send(validProof({
      type: 'tracker_status',
      username: USERNAME,
      userId: 991234567,
      isOnline: true,
      phase: 'startup',
    })).expect(200);

    const session = liveTrackDB[USERNAME];
    assert.ok(session);
    assert.notEqual(session.snapshotComplete, true);
    assert.ok(!session.playerDataFishItems || session.playerDataFishItems.length === 0);
    assert.ok(!session.fishItems || session.fishItems.length === 0);
  });

  test('later full snapshot for same username populates stats/fish/stones', async () => {
    await inventoryTrackedAccounts.addTrackedAccounts(OWNER_ID, [USERNAME]);
    const app = makeApp();
    await request(app).post('/api/fishit-tracker/update-backpack').send(validProof({
      type: 'tracker_status',
      username: USERNAME,
      userId: 991234567,
      isOnline: true,
      phase: 'startup',
    })).expect(200);

    const snap = await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(fullSnapshot())
      .expect((res) => {
        if (res.status !== 200 && res.status !== 202) {
          throw new Error(`expected 200 or 202, got ${res.status}`);
        }
      });
    assert.equal(snap.body.snapshotComplete, true);
    if (snap.status === 200) {
      assert.equal(snap.body.inventoryReady, true);
    }
    assert.equal(liveTrackDB[USERNAME].snapshotComplete, true);
    assert.ok(Array.isArray(liveTrackDB[USERNAME].playerDataFishItems));
    assert.ok(liveTrackDB[USERNAME].playerDataFishItems.length >= 1);
    assert.ok(liveTrackDB[USERNAME].playerStats);
    assert.ok(liveTrackDB[USERNAME].lastInventoryAt);
    assert.equal(liveTrackDB[USERNAME].hasLeaderstatsSnapshot, true);
    assert.equal(liveTrackDB[USERNAME].hasFishSnapshot, true);
  });

  test('discordOwnerId null resolves through registered account owner lookup', async () => {
    await inventoryTrackedAccounts.addTrackedAccounts(OWNER_ID, [USERNAME]);
    const app = makeApp();
    await request(app).post('/api/fishit-tracker/update-backpack').send(validProof({
      type: 'tracker_status',
      username: USERNAME,
      userId: 991234567,
      isOnline: true,
      phase: 'startup',
      discordOwnerId: null,
    })).expect(200);

    assert.equal(liveTrackDB[USERNAME].discordOwnerId, OWNER_ID);
  });

  test('startup/discovery heartbeat followed by live snapshot works', async () => {
    const added = await inventoryTrackedAccounts.addTrackedAccounts(OWNER_ID, [USERNAME]);
    assert.equal(added.accounts.length, 1);
    const app = makeApp();
    await request(app).post('/api/fishit-tracker/update-backpack').send(validProof({
      type: 'tracker_status',
      username: USERNAME,
      userId: 991234567,
      isOnline: true,
      phase: 'replion_missing',
    })).expect(200);

    await request(app).post('/api/fishit-tracker/update-backpack').send(validProof({
      type: 'tracker_status',
      username: USERNAME,
      userId: 991234567,
      isOnline: true,
      phase: 'player_data_selected',
    })).expect(200);

    const snap = await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(fullSnapshot({ phase: 'live' }))
      .expect((res) => {
        if (res.status !== 200 && res.status !== 202) {
          throw new Error(`expected 200 or 202, got ${res.status}`);
        }
      });
    assert.equal(snap.body.snapshotComplete, true);

    const status = await request(app).get('/api/tracker/account-status').expect(200);
    assert.equal(status.body.trackedCount, 1);
    const row = status.body.accounts.find((a) => String(a.canonicalKey || a.username || '').toLowerCase() === USERNAME)
      || status.body.accounts[0];
    assert.ok(row, 'expected account-status row after registered snapshot');
    assert.equal(liveTrackDB[USERNAME].snapshotComplete, true);
    assert.ok(['green', 'yellow'].includes(row.statusColor), row.statusColor);
  });

  test('old invalid payload still rejected', async () => {
    const app = makeApp();
    const res = await request(app).post('/api/fishit-tracker/update-backpack').send({
      type: 'tracker_status',
      username: USERNAME,
      userId: 991234567,
      isOnline: true,
      trackerBuild: 'BLOCKER10ZL_OBSOLETE_BUILD',
      trackerChannel: 'wrong-channel',
      scriptSource: 'https://example.com/old-tracker.lua',
    }).expect(403);
    assert.equal(res.body.error, 'OUTDATED_TRACKER_BUILD');
  });

  test('accepted snapshot updates lastInventoryAt via hasInventory flag', () => {
    const syncEval = uploadStatus.evaluateAcceptedSnapshotSync({
      completenessEval: { snapshotComplete: true, preserveExistingInventory: false },
      acceptedCount: 1,
      body: { fishItems: [{ itemId: '1' }] },
      playerDataFishItems: [{ itemId: '1' }],
      playerDataStoneItems: [],
      playerDataTotemItems: [],
      nextPlayerStatsFields: {},
      uploadRejected: false,
      now: new Date().toISOString(),
    });
    assert.equal(syncEval.hasInventory, true);
    assert.equal(syncEval.accepted, true);
  });

  test('heartbeat-only live stats serializer returns awaiting_inventory_snapshot', () => {
    const stats = liveTrackerSerializer.serializeLiveTrackerAccountStats({
      username: USERNAME,
      statusColor: 'yellow',
      accountPresenceLive: true,
      lastSuccessfulHeartbeatAt: new Date().toISOString(),
      snapshotComplete: false,
    }, playerStatsStore, playerStatsStore.normalizePlayerStatsForApi);
    assert.equal(stats.emptyReason, 'awaiting_inventory_snapshot');
  });

  test('frontend shows waiting-for-snapshot copy and account-status refresh hooks', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /Waiting for inventory snapshot/);
    assert.match(source, /WAITING_SNAPSHOT_STAT/);
    assert.match(source, /applyLiveSnapshotToPublicUi\(entry, key, entry\.lastData\)/);
  });
});
