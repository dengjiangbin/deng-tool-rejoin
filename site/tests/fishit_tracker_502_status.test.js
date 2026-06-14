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
const { deriveAccountPresenceStatus } = require('../src/trackerAccountPresence');
const { MINIMUM_TRACKER_BUILD } = require('../src/fishitTrackerBuild');
const gate = require('../src/trackerConcurrencyGate');

const liveTrackDB = trackerRoutes.liveTrackDB;
const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');

function makeApp() {
  const app = express();
  app.use(trackerRoutes);
  return app;
}

function iso(msAgo, fromMs) {
  const base = fromMs != null ? fromMs : Date.now();
  return new Date(base - msAgo).toISOString();
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

describe('tracker 502 upload status — preserve online within grace', () => {
  beforeEach(() => {
    gate._resetForTests();
    for (const key of Object.keys(liveTrackDB)) delete liveTrackDB[key];
  });

  test('deriveTrackerUploadAccountStatus stays green/yellow after transient 502 while last success is fresh', () => {
    const nowMs = Date.parse('2026-06-14T12:00:00.000Z');
    const session = {
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      intervalSeconds: 10,
      lastStatus: 'red',
      lastSyncReason: 'full_snapshot',
      lastSuccessfulUploadAt: iso(8000, nowMs),
      lastSuccessfulHeartbeatAt: iso(8000, nowMs),
      lastHeartbeatAt: iso(8000, nowMs),
      snapshotComplete: true,
      inventoryReady: true,
      latestPayloadAccepted: true,
      lastFailureReason: 'server_502_upload_retrying',
      lastUploadFailureIsTransient: true,
    };
    const upload = uploadStatus.deriveTrackerUploadAccountStatus(session, {
      serverNowMs: nowMs,
      expectedTrackerBuild: MINIMUM_TRACKER_BUILD,
      isTrustedBuild: isTrusted,
    });
    const presence = deriveAccountPresenceStatus(session, undefined, nowMs);
    assert.equal(upload.statusColor, 'green');
    assert.equal(upload.statusDecisionReason, 'server_502_upload_retrying');
    assert.equal(presence.accountPresenceLive, true);
    assert.equal(presence.accountStatusReason, 'server_502_upload_retrying');
  });

  test('transient 502 heartbeat does not mark session red on server', async () => {
    const app = makeApp();
    const username = 'Transient502User';
    const key = username.toLowerCase();

    await request(app).post('/api/tracker/update-backpack').send({
      type: 'inventory_snapshot',
      username,
      userId: 991502,
      isOnline: true,
      clientOrigin: 'roblox_tracker',
      trackerBuild: MINIMUM_TRACKER_BUILD,
      intervalSeconds: 10,
      scanCompleted: true,
      replionReady: true,
      leaderstatsReady: true,
      fishScanReady: true,
      stoneScanReady: true,
      inventorySource: 'playerdata_gameitemdb',
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

    assert.equal(liveTrackDB[key].lastStatus, 'green');

    await request(app).post('/api/tracker/update-backpack').send({
      type: 'tracker_status',
      username,
      userId: 991502,
      isOnline: true,
      clientOrigin: 'roblox_tracker',
      trackerBuild: MINIMUM_TRACKER_BUILD,
      intervalSeconds: 10,
      uploadFailed: true,
      failureReason: 'code=502',
      statusCode: 502,
    }).expect(200);

    const session = liveTrackDB[key];
    assert.equal(session.lastStatus, 'green', 'transient 502 must not flip lastStatus red');
    assert.match(session.lastFailureReason, /server_502_upload_retrying/);

    const backpack = await request(app)
      .get(`/api/tracker/get-backpack/${key}`)
      .expect(200);
    assert.equal(backpack.body.accountPresenceLive, true);
    assert.equal(backpack.body.currentStatus, 'green');
    assert.match(String(backpack.body.uploadWarningReason || backpack.body.accountStatusReason), /502/);
  });

  test('account turns offline only after grace expires without successful contact', () => {
    const nowMs = Date.parse('2026-06-14T12:00:00.000Z');
    const session = {
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      intervalSeconds: 10,
      lastStatus: 'green',
      lastSuccessfulUploadAt: iso(120000, nowMs),
      lastSuccessfulHeartbeatAt: iso(120000, nowMs),
      snapshotComplete: true,
      latestPayloadAccepted: true,
      lastFailureReason: 'server_502_upload_retrying',
    };
    const upload = uploadStatus.deriveTrackerUploadAccountStatus(session, {
      serverNowMs: nowMs,
      expectedTrackerBuild: MINIMUM_TRACKER_BUILD,
      isTrustedBuild: isTrusted,
    });
    const presence = deriveAccountPresenceStatus(session, undefined, nowMs);
    assert.equal(upload.statusColor, 'red');
    assert.equal(upload.statusDecisionReason, 'upload_interval_missed');
    assert.equal(presence.accountPresenceLive, false);
    assert.equal(presence.accountPresenceReason, 'account_offline_timeout');
  });

  test('Enchant Stones and Totems subsection titles use white text in tracker source CSS', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /\.items-subsection__title\.stones-section__title[\s\S]*color:#fff/);
    assert.match(source, /\.items-subsection__title\.totems-section__title[\s\S]*color:#fff/);
  });
});
