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
const uploadStatus = require('../src/fishitTrackerUploadStatus');
const { MINIMUM_TRACKER_BUILD } = require('../src/fishitTrackerBuild');
const manifest = require('../src/inventoryAssetManifest.json');
const {
  deriveAccountPresenceStatus,
  deriveConnectionStatus,
  deriveInventoryUploadStatus,
  resolveLastAccountSeenAt,
  isSessionLive,
  ACCOUNT_PRESENCE_GRACE_MS,
} = trackerRoutes;

const liveTrackDB = trackerRoutes.liveTrackDB;
const gate = require('../src/trackerConcurrencyGate');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const INVENTORY_JS = path.join(__dirname, '..', 'public', 'assets', manifest.js);

function makeApp() {
  const app = express();
  app.use(trackerRoutes);
  return app;
}

function iso(msAgo) {
  return new Date(Date.now() - msAgo).toISOString();
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

describe('account upload status — server proof vs legacy presence fields', () => {
  beforeEach(() => {
    gate._resetForTests();
    for (const key of Object.keys(liveTrackDB)) delete liveTrackDB[key];
  });

  test('resolveLastAccountSeenAt prefers freshest loader contact timestamp', () => {
    const session = {
      lastHeartbeatAt: iso(90000),
      lastSeenAt: iso(5000),
      lastSnapshotUploadAt: iso(3000),
    };
    assert.equal(resolveLastAccountSeenAt(session), session.lastSnapshotUploadAt);
  });

  test('fresh heartbeat keeps account online while snapshot incomplete even when lastSuccessfulUploadAt is stale', () => {
    const session = {
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      lastHeartbeatAt: iso(5000),
      lastSuccessfulHeartbeatAt: iso(5000),
      lastSeenAt: iso(5000),
      lastSnapshotUploadAt: iso(90000),
      lastInventoryAt: iso(90000),
      lastSuccessfulUploadAt: iso(90000),
      snapshotComplete: false,
      intervalSeconds: 10,
      graceSeconds: 5,
    };
    const upload = uploadStatus.deriveTrackerUploadAccountStatus(session, {
      expectedTrackerBuild: MINIMUM_TRACKER_BUILD,
      isTrustedBuild: isTrusted,
    });
    const presence = deriveAccountPresenceStatus(session);
    const inventory = deriveInventoryUploadStatus(session);
    assert.equal(isSessionLive(session), true);
    assert.equal(upload.statusColor, 'yellow');
    assert.equal(presence.accountPresenceLive, true);
    assert.equal(inventory.inventoryUploadFresh, true);
  });

  test('stale heartbeat does not turn upload status red when lastSuccessfulUploadAt is fresh', () => {
    const session = {
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      lastHeartbeatAt: iso(90000),
      lastSeenAt: iso(8000),
      lastSnapshotUploadAt: iso(8000),
      lastInventoryAt: iso(8000),
      lastSuccessfulUploadAt: iso(8000),
      lastStatus: 'green',
      lastSyncReason: 'fish_snapshot',
      snapshotComplete: true,
      intervalSeconds: 10,
      graceSeconds: 5,
    };
    const upload = uploadStatus.deriveTrackerUploadAccountStatus(session, {
      expectedTrackerBuild: MINIMUM_TRACKER_BUILD,
      isTrustedBuild: isTrusted,
    });
    const inventory = deriveInventoryUploadStatus(session);
    assert.equal(upload.statusColor, 'green');
    assert.equal(inventory.inventoryUploadFresh, true);
  });

  test('account stays online within 10-minute grace when heartbeat is stale but last success is recent', () => {
    const session = {
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      lastSuccessfulHeartbeatAt: iso(120000),
      lastHeartbeatAt: iso(120000),
      lastSuccessfulUploadAt: iso(8000),
      lastStatus: 'green',
      snapshotComplete: true,
      intervalSeconds: 10,
      graceSeconds: 5,
    };
    const upload = uploadStatus.deriveTrackerUploadAccountStatus(session, {
      expectedTrackerBuild: MINIMUM_TRACKER_BUILD,
      isTrustedBuild: isTrusted,
    });
    assert.equal(upload.statusColor, 'green');
    assert.equal(isSessionLive(session), true);
    assert.equal(deriveAccountPresenceStatus(session).accountPresenceLive, true);
  });

  test('account turns offline when grace expires beyond 10 minutes', () => {
    const session = {
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      lastSuccessfulHeartbeatAt: iso(620000),
      lastHeartbeatAt: iso(620000),
      lastSuccessfulUploadAt: iso(620000),
      snapshotComplete: true,
      intervalSeconds: 10,
      graceSeconds: 5,
    };
    const upload = uploadStatus.deriveTrackerUploadAccountStatus(session, {
      expectedTrackerBuild: MINIMUM_TRACKER_BUILD,
      isTrustedBuild: isTrusted,
    });
    assert.equal(upload.statusColor, 'red');
    assert.equal(isSessionLive(session), false);
  });

  test('get-backpack exposes upload status proof fields', async () => {
    const app = makeApp();
    const username = 'PresenceSplitUser';
    const key = username.toLowerCase();
    await request(app).post('/api/fishit-tracker/update-backpack').send({
      type: 'inventory_snapshot',
      username,
      userId: 88056,
      isOnline: true,
      clientOrigin: 'roblox_tracker',
      trackerBuild: MINIMUM_TRACKER_BUILD,
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
        totalCaught: 10,
        source: 'leaderstats',
        build: MINIMUM_TRACKER_BUILD,
      },
    }).expect(expectUploadOk());

    const session = liveTrackDB[key];
    assert.ok(session);
    const upload = uploadStatus.deriveTrackerUploadAccountStatus(session, {
      expectedTrackerBuild: MINIMUM_TRACKER_BUILD,
      isTrustedBuild: isTrusted,
    });
    const inventoryUpload = deriveInventoryUploadStatus(session);
    assert.equal(upload.statusColor, 'green');
    assert.equal(upload.status, 'online');
    assert.ok(session.lastSuccessfulUploadAt);
    assert.ok(upload.secondsSinceLastSuccess != null);
    assert.equal(inventoryUpload.inventoryUploadStatus, 'fresh');
  });

  test('frontend polls canonical account-status endpoint for row status', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /function pollAccountStatuses/);
    assert.match(source, /function applyAccountStatusPayload/);
    assert.match(source, /function entryConnectionFreshness[\s\S]*entryUploadStatus\(entry\)/);
    assert.match(source, /function refetchAllAccountStatus/);
    assert.match(source, /visibilitychange/);
    assert.match(source, /forceFresh/);
    assert.match(source, /Cache-Control.*no-cache/);
  });

  test('compiled bundle keeps canonical upload status polling', () => {
    const js = fs.readFileSync(INVENTORY_JS, 'utf8');
    assert.match(js, /pollAccountStatuses/);
    assert.match(js, /applyAccountStatusPayload/);
    assert.match(js, /refetchAllAccountStatus/);
  });
});
