'use strict';

const { describe, test } = require('node:test');
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
const playerStatsStore = require('../src/fishitPlayerStats');
const {
  CLEAN_TRACKER_LOADSTRING,
  buildCleanTrackerLoader,
  PROTECTED_TRACKER_RAW_URL,
} = require('../src/fishitTrackerLoadstring');
const { MINIMUM_TRACKER_BUILD } = require('../src/fishitTrackerBuild');
const manifest = require('../src/inventoryAssetManifest.json');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const INVENTORY_JS = path.join(__dirname, '..', 'public', 'assets', manifest.js);

const FISH_IT_LOADER = 'loadstring(game:HttpGet("https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua"))()';

const {
  deriveConnectionStatus,
  applyUploadSyncSuccess,
  applyUploadSyncFailure,
  UPLOAD_INTERVAL_SECONDS,
  UPLOAD_GRACE_SECONDS,
} = trackerRoutes;

function makeApp() {
  const app = express();
  app.use(trackerRoutes);
  return app;
}

function statsPayload(coins, totalCaught, rarestFishChance) {
  return {
    coins,
    totalCaught,
    rarestFishChance,
    source: 'leaderstats',
    build: MINIMUM_TRACKER_BUILD,
  };
}

function uploadBody(username, stats, extra = {}) {
  return {
    type: 'inventory_snapshot',
    username,
    userId: 88001,
    isOnline: true,
    clientOrigin: 'roblox_tracker',
    evidenceSourceMode: 'live_roblox',
    trackerBuild: MINIMUM_TRACKER_BUILD,
    fishItems: [{ itemId: '1', name: 'Clownfish', quantity: 1, source: 'playerdata_gameitemdb' }],
    playerStats: stats,
    ...extra,
  };
}

describe('sync duration + stats interval hotfix regression', () => {
  test('A/B public copy script is exactly clean fish-it loader without ?v=', () => {
    assert.equal(CLEAN_TRACKER_LOADSTRING, FISH_IT_LOADER);
    assert.equal(CLEAN_TRACKER_LOADSTRING, buildCleanTrackerLoader(PROTECTED_TRACKER_RAW_URL));
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /\?v=/);
  });

  test('C/D frontend renders minimal duration only for green and red', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /function formatMinimalSyncDuration/);
    assert.match(source, /isEntryStatusGreen\(entry\)\) return formatMinimalSyncDuration\(entrySuccessUploadAt\(entry\)\)/);
    assert.match(source, /formatMinimalSyncDuration\(entryRedSince\(entry\)\)/);
    assert.doesNotMatch(source, /Last sync success/i);
    assert.doesNotMatch(source, /Sync failed/i);
    assert.doesNotMatch(source, /stale for/i);
    assert.doesNotMatch(source, /Fresh stats updated/i);
    assert.doesNotMatch(source, /Heartbeat only/i);
    assert.doesNotMatch(source, /\sago\b/i);
  });

  test('E green status uses lastSuccessfulUploadAt only', () => {
    const now = Date.now();
    const iso = (ms) => new Date(ms).toISOString();
    const st = deriveConnectionStatus({
      trackerBuild: MINIMUM_TRACKER_BUILD,
      lastSuccessfulUploadAt: iso(now - 3000),
      lastHeartbeatAt: iso(now - 120000),
      intervalSeconds: UPLOAD_INTERVAL_SECONDS,
      graceSeconds: UPLOAD_GRACE_SECONDS,
    });
    assert.equal(st.currentStatus, 'green');
    assert.equal(st.connectionStatusReason, 'fresh_upload');
  });

  test('F heartbeat-only cannot make green', () => {
    const now = Date.now();
    const iso = (ms) => new Date(ms).toISOString();
    const st = deriveConnectionStatus({
      trackerBuild: MINIMUM_TRACKER_BUILD,
      lastHeartbeatAt: iso(now - 1000),
      lastSeenAt: iso(now - 1000),
    });
    assert.equal(st.currentStatus, 'red');
    assert.notEqual(st.connectionStatus, 'live');
  });

  test('G missed interval after interval+grace becomes red', () => {
    const now = Date.now();
    const iso = (ms) => new Date(ms).toISOString();
    const st = deriveConnectionStatus({
      trackerBuild: MINIMUM_TRACKER_BUILD,
      lastSuccessfulUploadAt: iso(now - (UPLOAD_INTERVAL_SECONDS + UPLOAD_GRACE_SECONDS + 3) * 1000),
      intervalSeconds: UPLOAD_INTERVAL_SECONDS,
      graceSeconds: UPLOAD_GRACE_SECONDS,
    });
    assert.equal(st.currentStatus, 'red');
    assert.equal(st.connectionStatusReason, 'upload_interval_missed');
  });

  test('H red duration increases over time', () => {
    const now = Date.now();
    const redSince = new Date(now - 28000).toISOString();
    const st1 = deriveConnectionStatus({
      trackerBuild: MINIMUM_TRACKER_BUILD,
      lastSuccessfulUploadAt: new Date(now - 60000).toISOString(),
      redSince,
      intervalSeconds: UPLOAD_INTERVAL_SECONDS,
      graceSeconds: UPLOAD_GRACE_SECONDS,
    });
    const st2 = deriveConnectionStatus({
      ...st1,
      redSince,
    });
    assert.ok(st1.redDurationSeconds >= 27);
    assert.equal(st2.redDurationSeconds, st1.redDurationSeconds);
  });

  test('I recovery success clears red and returns green', () => {
    const now = new Date().toISOString();
    let session = applyUploadSyncFailure({}, now, 'upload_failed');
    assert.equal(session.currentStatus, 'red');
    session = applyUploadSyncSuccess(session, now, { payloadHash: 'abc' });
    assert.equal(session.currentStatus, 'green');
    assert.equal(session.redSince, null);
    assert.ok(session.lastSuccessfulUploadAt);
  });

  test('J repeated stats sync updates coin, total caught, and rarest fish', async () => {
    const app = makeApp();
    const username = 'StatIntervalUser';
    const key = username.toLowerCase();

    await request(app).post('/api/fishit-tracker/update-backpack')
      .send(uploadBody(username, statsPayload(1000, 50, '1/500')))
      .expect(200);
    await request(app).post('/api/fishit-tracker/update-backpack')
      .send(uploadBody(username, statsPayload(2500, 75, '1/400')))
      .expect(200);
    await request(app).post('/api/fishit-tracker/update-backpack')
      .send(uploadBody(username, statsPayload(3900, 90, '1/300')))
      .expect(200);

    const backpack = await request(app).get(`/api/fishit-tracker/get-backpack/${key}`).expect(200);
    assert.equal(backpack.body.playerStats.coins, 3900);
    assert.equal(backpack.body.playerStats.totalCaught, 90);
    assert.equal(backpack.body.playerStats.rarestFishChance, '1/300');
    assert.ok(backpack.body.lastSuccessfulUploadAt);
  });

  test('K compiled inventory bundle polls and patches stats on every payload', () => {
    const js = fs.readFileSync(INVENTORY_JS, 'utf8');
    assert.match(js, /function applyInventoryPollPayload/);
    assert.match(js, /function patchAccountStatsRow/);
    assert.match(js, /patchAccountStatsRow\(entry, key\)/);
    assert.match(js, /function formatEntrySyncStatusText/);
    assert.match(js, /function isTrustedPlayerStatsBuild/);
    assert.match(js, /LOADER_REGISTER_LIMIT_FIX/);
  });

  test('playerStats trust accepts LOADER_REGISTER_LIMIT_FIX build marker', () => {
    const stats = statsPayload(1200, 44, '1/600');
    assert.equal(playerStatsStore.isTrustedPlayerStats(stats), true);
    const merged = playerStatsStore.mergePlayerStats(null, stats);
    assert.equal(merged.coins, 1200);
    const merged2 = playerStatsStore.mergePlayerStats(merged, statsPayload(1500, 55, '1/500'));
    assert.equal(merged2.coins, 1500);
    assert.equal(merged2.totalCaught, 55);
    assert.equal(merged2.rarestFishChance, '1/500');
  });
});
