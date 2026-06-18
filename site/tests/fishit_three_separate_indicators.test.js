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
const { MINIMUM_TRACKER_BUILD } = require('../src/fishitTrackerBuild');
const manifest = require('../src/inventoryAssetManifest.json');
const {
  deriveAccountPresenceStatus,
  deriveStatsUploadStatus,
  deriveInventoryUploadStatus,
  inventoryUploadGraceSeconds,
  inventoryUploadStaleAfterSeconds,
  UPLOAD_INTERVAL_SECONDS,
  UPLOAD_GRACE_SECONDS,
} = trackerRoutes;

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

describe('three separate indicators regression', () => {
  test('frontend keeps stats sub, presence dot, and inventory upload indicator separate', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /data-stats-sync-sub/);
    assert.match(source, /data-caught-activity-sub/);
    assert.match(source, /data-inventory-upload-indicator/);
    assert.match(source, /function isAccountPresent/);
    assert.match(source, /function isStatsUploadFresh/);
    assert.match(source, /function isInventoryUploadFresh/);
    assert.match(source, /function inventoryUploadStaleAfterSeconds/);
    assert.doesNotMatch(source, /data-card-sync-text/);
    assert.match(source, /function formatStatsSyncAgeSub/);
    assert.match(source, /function formatPresenceStatusText/);
    assert.match(source, /function formatCaughtActivitySub/);
    assert.match(source, /function touchCaughtActivityState/);
    assert.match(source, /function entryPresenceTimestamp/);
    assert.doesNotMatch(source, /data-table-sync-text/);
    assert.match(source, /buildAccountStatusHtml[\s\S]*formatPresenceStatusText\(entry\)/);
    assert.match(source, /patchAccountStatusDom[\s\S]*formatPresenceStatusText\(entry\)/);
    const statusFn = source.match(/function buildAccountStatusHtml\(entry\)\s*\{[\s\S]*?\n  \}/);
    assert.ok(statusFn, 'buildAccountStatusHtml must exist');
    assert.doesNotMatch(statusFn[0], /formatEntrySyncStatusText/);
    assert.match(source, /formatStatsSyncAgeSub[\s\S]*formatCaughtActivitySub\(entry\)/);
    assert.match(source, /formatEntrySyncStatusText[\s\S]*isInventoryUploadFresh\(entry\)/);
    assert.match(source, /applyInventoryPollPayload[\s\S]*touchCaughtActivityState/);
  });

  test('backend splits presence, stats upload, and inventory upload freshness', () => {
    const now = Date.now();
    const session = {
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      lastHeartbeatAt: iso(5000),
      lastSeenAt: iso(5000),
      lastStatsUploadAt: iso(4000),
      lastSnapshotUploadAt: iso(160000),
      lastInventoryAt: iso(160000),
      leaderstatsUploadOk: true,
      intervalSeconds: UPLOAD_INTERVAL_SECONDS,
      graceSeconds: UPLOAD_GRACE_SECONDS,
    };
    const presence = deriveAccountPresenceStatus(session);
    const stats = deriveStatsUploadStatus(session);
    const inventory = deriveInventoryUploadStatus(session);
    assert.equal(presence.accountPresenceLive, true);
    assert.equal(stats.statsUploadFresh, true);
    assert.equal(inventory.inventoryUploadFresh, false);
    assert.equal(presence.accountPresenceStatus, 'online');
    assert.equal(inventory.inventorySyncStatus || inventory.inventoryUploadStatus, 'stale');
  });

  test('in-game account stays online when fish/stone upload is stale', () => {
    const session = {
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      lastHeartbeatAt: iso(8000),
      lastSeenAt: iso(8000),
      lastSnapshotUploadAt: iso(180000),
      lastInventoryAt: iso(180000),
      intervalSeconds: UPLOAD_INTERVAL_SECONDS,
      graceSeconds: UPLOAD_GRACE_SECONDS,
    };
    const presence = deriveAccountPresenceStatus(session);
    const inventory = deriveInventoryUploadStatus(session);
    assert.equal(presence.accountPresenceLive, true);
    assert.equal(inventory.inventoryUploadFresh, false);
  });

  test('warehouse unchanged stats upload stays fresh and displays zero', async () => {
    const app = makeApp();
    const username = 'WarehouseZeroUser';
    const stats = {
      coins: 0,
      totalCaught: 0,
      rarestFishChance: '1/1000',
      source: 'leaderstats',
      build: MINIMUM_TRACKER_BUILD,
    };
    const body = {
      type: 'inventory_snapshot',
      username,
      userId: 88002,
      isOnline: true,
      clientOrigin: 'roblox_tracker',
      evidenceSourceMode: 'live_roblox',
      trackerBuild: MINIMUM_TRACKER_BUILD,
      fishItems: [{ itemId: '1', name: 'Clownfish', quantity: 1, source: 'playerdata_gameitemdb' }],
      playerStats: stats,
    };
    await request(app).post('/api/fishit-tracker/update-backpack').send(body).expect((res) => {
      assert.ok(res.status === 200 || res.status === 202);
    });
    await request(app).post('/api/fishit-tracker/update-backpack').send(body).expect((res) => {
      assert.ok(res.status === 200 || res.status === 202);
    });

    const backpack = await request(app).get(`/api/fishit-tracker/get-backpack/${username.toLowerCase()}`).expect(200);
    assert.equal(backpack.body.playerStats.totalCaught, 0);
    assert.equal(backpack.body.playerStats.coins, 0);
    assert.equal(backpack.body.statsUploadFresh, true);
    assert.equal(backpack.body.inventoryUploadFresh, true);
    assert.equal(backpack.body.accountPresenceLive, true);
  });

  test('inventory upload freshness uses grace before turning stale', () => {
    const interval = UPLOAD_INTERVAL_SECONDS;
    const grace = inventoryUploadGraceSeconds(interval);
    const staleAfter = inventoryUploadStaleAfterSeconds(interval);
    assert.equal(grace, 90);
    assert.equal(staleAfter, 150);

    const freshInsideGrace = deriveInventoryUploadStatus({
      lastSnapshotUploadAt: iso(120000),
      intervalSeconds: interval,
    });
    assert.equal(freshInsideGrace.inventoryUploadFresh, true);

    const staleBeyondGrace = deriveInventoryUploadStatus({
      lastSnapshotUploadAt: iso(160000),
      intervalSeconds: interval,
    });
    assert.equal(staleBeyondGrace.inventoryUploadFresh, false);
  });

  test('compiled bundle includes shared indicator helpers', () => {
    const js = fs.readFileSync(INVENTORY_JS, 'utf8');
    assert.match(js, /data-stats-sync-sub/);
    assert.match(js, /data-caught-activity-sub/);
    assert.match(js, /data-inventory-upload-indicator/);
    assert.match(js, /function updateInventoryUploadIndicator/);
    assert.match(js, /function inventoryUploadStaleAfterSeconds/);
    assert.match(js, /function formatPresenceStatusText/);
    assert.match(js, /function touchCaughtActivityState/);
    assert.doesNotMatch(js, /data-card-sync-text/);
  });

  test('stats timer is the authoritative "<age> ago" backend age, never stat-delta labels', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.doesNotMatch(source, /No stat change/);
    assert.match(source, /function formatStatsUploadDurationText/);
    assert.match(source, /function formatCaughtActivitySub[\s\S]*formatStatsUploadDurationText\(entry\)/);
    // The visible leaderstats timer is the true backend leaderstats age rendered
    // as "<age> ago" (authoritative) — NOT the per-session frontend-receive time,
    // so it never resets to "1s" on refresh/new session.
    assert.match(source, /function formatStatsUploadDurationText\(entry\) \{[\s\S]*?return formatAgeAgoSeconds\(backendStatsAgeSeconds\(entry\)\);/);

    // Run the real authoritative helpers under a controllable clock.
    const open = source.indexOf('  function formatAgeAgo(ms) {');
    const close = source.indexOf('  function syncAgeSeconds(timestamp) {');
    assert.ok(open > 0 && close > open, 'formatAgeAgo helper block missing');
    const block = source.slice(open, close);
    const fns = new Function('Math', 'Number', 'Date', `
      ${block}
      return { formatAgeAgoSeconds };
    `)(Math, Number, { now: () => 100000 });
    // 8m-old backend leaderstats age -> "8m ago" (does NOT reset to "1s").
    assert.equal(fns.formatAgeAgoSeconds(8 * 60), '8m ago');
    // No authoritative timestamp -> blank, never a fake "1s".
    assert.equal(fns.formatAgeAgoSeconds(null), '');
  });

  test('backend records stats value-change timestamp only when a value changes', async () => {
    const app = makeApp();
    const username = 'StatsChangeUser';
    const baseBody = {
      type: 'inventory_snapshot',
      username,
      userId: 88003,
      isOnline: true,
      clientOrigin: 'roblox_tracker',
      evidenceSourceMode: 'live_roblox',
      trackerBuild: MINIMUM_TRACKER_BUILD,
      fishItems: [{ itemId: '1', name: 'Clownfish', quantity: 1, source: 'playerdata_gameitemdb' }],
      playerStats: { coins: 100, totalCaught: 10, rarestFishChance: '1/1000', source: 'leaderstats', build: MINIMUM_TRACKER_BUILD },
    };
    await request(app).post('/api/fishit-tracker/update-backpack').send(baseBody).expect((res) => {
      assert.ok(res.status === 200 || res.status === 202);
    });
    const first = await request(app).get(`/api/fishit-tracker/get-backpack/${username.toLowerCase()}`).expect(200);
    const firstChangeAt = first.body.lastStatsChangeAt;
    assert.ok(firstChangeAt, 'first upload should set lastStatsChangeAt');

    // Unchanged stats -> timestamp must NOT advance.
    await new Promise((r) => setTimeout(r, 15));
    await request(app).post('/api/fishit-tracker/update-backpack').send(baseBody).expect((res) => {
      assert.ok(res.status === 200 || res.status === 202);
    });
    const same = await request(app).get(`/api/fishit-tracker/get-backpack/${username.toLowerCase()}`).expect(200);
    assert.equal(same.body.lastStatsChangeAt, firstChangeAt);

    // Changed stats -> timestamp advances.
    await new Promise((r) => setTimeout(r, 15));
    await request(app).post('/api/fishit-tracker/update-backpack')
      .send({ ...baseBody, playerStats: { ...baseBody.playerStats, totalCaught: 11 } }).expect((res) => {
        assert.ok(res.status === 200 || res.status === 202);
      });
    const changed = await request(app).get(`/api/fishit-tracker/get-backpack/${username.toLowerCase()}`).expect(200);
    assert.notEqual(changed.body.lastStatsChangeAt, firstChangeAt);
  });
});
