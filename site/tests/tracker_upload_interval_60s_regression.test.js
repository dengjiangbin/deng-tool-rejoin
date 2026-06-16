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
const uploadRateLimit = require('../src/trackerUploadRateLimit');
const { MINIMUM_TRACKER_BUILD } = require('../src/fishitTrackerBuild');
const manifest = require('../src/inventoryAssetManifest.json');
const { RAW_TRACKER_LUA, testIfRawTracker } = require('./helpers/trackerRawSource');
const {
  UPLOAD_INTERVAL_SECONDS,
  UPLOAD_GRACE_SECONDS,
  inventoryUploadGraceSeconds,
  inventoryUploadStaleAfterSeconds,
} = trackerRoutes;

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const INVENTORY_JS = path.join(__dirname, '..', 'public', 'assets', manifest.js);
const AIO_UPLOAD_URL = 'https://aio.deng.my.id/api/fishit-tracker/update-backpack';

function makeApp() {
  const app = express();
  app.use(express.json({ limit: '512kb' }));
  app.use(trackerRoutes);
  return app;
}

function iso(msAgo) {
  return new Date(Date.now() - msAgo).toISOString();
}

describe('tracker upload interval 60s + aio domain regression', () => {
  test('server upload interval constants are 60 seconds with 10 minute public grace', () => {
    assert.equal(UPLOAD_INTERVAL_SECONDS, 60);
    assert.equal(UPLOAD_GRACE_SECONDS, 15);
    assert.equal(inventoryUploadGraceSeconds(60), 600);
    assert.equal(inventoryUploadStaleAfterSeconds(60), 600);
  });

  testIfRawTracker('private tracker uses 60s light sync and aio upload domain', () => {
    const lua = fs.readFileSync(RAW_TRACKER_LUA, 'utf8');
    assert.match(lua, /lightSyncIntervalSeconds = 60/);
    assert.match(lua, new RegExp(AIO_UPLOAD_URL.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    assert.doesNotMatch(lua, /https:\/\/tool\.deng\.my\.id\/api\/fishit-tracker\/update-backpack/);
    assert.match(lua, /intervalSeconds = LiveSafe\.lightSyncIntervalSeconds or 60/);
    assert.match(lua, /UPLOAD_HTML_530_GATEWAY_DIAG_2026_06_15/);
    assert.match(lua, /DEBUG_UPLOAD_DISABLED_PRODUCTION/);
    assert.match(lua, /DENG_TRACKER_RUNNING/);
    assert.match(lua, /UPLOAD_SKIP_COOLDOWN/);
  });

  test('upload rate limit allows normal 60s three-lane cadence', async () => {
    const app = makeApp();
    const username = 'RateLimit60User';
    const body = {
      type: 'inventory_snapshot',
      username,
      userId: 99001,
      isOnline: true,
      clientOrigin: 'roblox_tracker',
      evidenceSourceMode: 'live_roblox',
      trackerBuild: MINIMUM_TRACKER_BUILD,
      intervalSeconds: 60,
      fishItems: [{ itemId: '1', name: 'Clownfish', quantity: 1, source: 'playerdata_gameitemdb' }],
      playerStats: {
        coins: 100,
        totalCaught: 5,
        rarestFishChance: '1/1000',
        source: 'leaderstats',
        build: MINIMUM_TRACKER_BUILD,
      },
    };
    const lanes = [
      { ...body, type: 'tracker_status', fishItems: [], stoneItems: [], totemItems: [] },
      { ...body, uploadPath: 'playerdata_leaderstats_only', leaderstatsOnlyUpload: true },
      body,
    ];
    for (const lane of lanes) {
      const res = await request(app).post('/api/fishit-tracker/update-backpack').send(lane);
      assert.notEqual(res.status, 429, `lane ${lane.type || lane.uploadPath} should not be rate limited`);
      assert.ok(res.status === 200 || res.status === 202, `lane status ${res.status}`);
    }
  });

  test('account-status exposes per-lane last-success timestamps', async () => {
    const app = makeApp();
    const username = 'LaneTsUser';
    const body = {
      type: 'inventory_snapshot',
      username,
      userId: 99002,
      isOnline: true,
      clientOrigin: 'roblox_tracker',
      evidenceSourceMode: 'live_roblox',
      trackerBuild: MINIMUM_TRACKER_BUILD,
      intervalSeconds: 60,
      fishItems: [{ itemId: '1', name: 'Clownfish', quantity: 1, source: 'playerdata_gameitemdb' }],
      playerStats: {
        coins: 50,
        totalCaught: 3,
        rarestFishChance: '1/500',
        source: 'leaderstats',
        build: MINIMUM_TRACKER_BUILD,
      },
    };
    await request(app).post('/api/fishit-tracker/update-backpack').send(body).expect((res) => {
      assert.ok(res.status === 200 || res.status === 202);
    });
    const backpack = await request(app).get(`/api/tracker/get-backpack/${username.toLowerCase()}`).expect(200);
    assert.ok(backpack.body.statusLastSuccessAt, 'statusLastSuccessAt required');
    assert.ok(backpack.body.inventoryLastSuccessAt, 'inventoryLastSuccessAt required');
    assert.equal(typeof backpack.body.secondsSinceLastStatusSuccess, 'number');
    assert.equal(typeof backpack.body.secondsSinceLastInventorySuccess, 'number');
    if (backpack.body.leaderstatsLastSuccessAt) {
      assert.equal(typeof backpack.body.secondsSinceLastLeaderstatsSuccess, 'number');
    }
  });

  test('frontend binds timer and color to the same lane state helpers', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /function liveSecondsSinceStatusSuccess/);
    assert.match(source, /function liveSecondsSinceStatsSuccess/);
    assert.match(source, /function liveSecondsSinceInventorySuccess/);
    assert.match(source, /function formatStatsUploadDurationText/);
    // Presence/Status sync text now follows the FRONTEND refresh time (visible
    // UX timer); the backend status lane stays available via backendPresenceAgeSeconds.
    assert.match(source, /formatPresenceStatusText[\s\S]*return formatFrontendRefreshAgeText\(entry\)/);
    assert.match(source, /function backendPresenceAgeSeconds[\s\S]*liveSecondsSinceStatusSuccess/);
    assert.match(source, /formatCaughtActivitySub[\s\S]*formatStatsUploadDurationText/);
    // Leaderstats + inventory visible timers now follow the FRONTEND refresh time
    // too; the backend lanes stay available via backendStatsAgeSeconds /
    // backendInventoryAgeSeconds for debug/proof.
    assert.match(source, /formatStatsUploadDurationText[\s\S]*?return formatLeaderstatsRefreshAgeText\(entry\)/);
    assert.match(source, /formatEntrySyncStatusText[\s\S]*?return formatInventoryRefreshAgeText\(entry\)/);
    assert.match(source, /function backendStatsAgeSeconds[\s\S]*?liveSecondsSinceStatsSuccess/);
    assert.match(source, /function backendInventoryAgeSeconds[\s\S]*?liveSecondsSinceInventorySuccess/);
    assert.match(source, /patchAccountStatusDom[\s\S]*entryConnectionFreshness[\s\S]*formatPresenceStatusText/);
    assert.match(source, /patchInventoryUploadIndicatorDom[\s\S]*isInventoryUploadFresh[\s\S]*formatEntrySyncStatusText/);
    assert.match(source, /DEFAULT_UPLOAD_INTERVAL_SEC = 60/);
    assert.match(source, /statusLastSuccessAt/);
    assert.match(source, /leaderstatsLastSuccessAt/);
    assert.match(source, /inventoryLastSuccessAt/);
  });

  test('page refresh: status, leaderstats AND inventory timers all follow the per-section frontend refresh time', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    const names = [
      'getEntryFrontendRefreshAgeMs',
      'formatFrontendRefreshAgeText',
      'getEntryLeaderstatsRefreshAgeMs',
      'formatLeaderstatsRefreshAgeText',
      'getEntryInventoryRefreshAgeMs',
      'formatInventoryRefreshAgeText',
      'formatPresenceStatusText',
      'formatStatsUploadDurationText',
      'formatEntrySyncStatusText',
    ];
    names.forEach((name) => assert.match(source, new RegExp(`function ${name}`)));
    const blocks = names.map((name) => source.match(new RegExp(`function ${name}\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n  \\}`)));
    const fns = new Function(`
      function pad2(v) { return String(Math.max(0, Math.floor(Number(v) || 0))).padStart(2, '0'); }
      function formatPresenceDurationLabel(secs) {
        if (secs == null) return '';
        if (secs < 60) return Math.max(1, secs) + 's';
        return '1m ' + pad2(secs % 60) + 's';
      }
      ${blocks.map((b) => b[0]).join('\n')}
      return {
        formatPresenceStatusText,
        formatStatsUploadDurationText,
        formatEntrySyncStatusText,
      };
    `)();
    // Each section carries its OWN frontend-receive timestamp; the backend
    // snapshot age (45s) must NOT leak into any visible timer.
    const entry = {
      uploadStatus: {
        secondsSinceLastStatusSuccess: 45,
        secondsSinceLastLeaderstatsSuccess: 45,
        secondsSinceLastInventorySuccess: 45,
      },
      _uploadStatusFetchedAtMs: Date.now(),
      _frontendRefreshAt: Date.now() - 3000,
      _leaderstatsFrontendRefreshAt: Date.now() - 2000,
      _inventoryFrontendRefreshAt: Date.now() - 4000,
      lastData: {},
    };
    assert.equal(fns.formatPresenceStatusText(entry), '3s');
    assert.equal(fns.formatStatsUploadDurationText(entry), '2s');
    assert.equal(fns.formatEntrySyncStatusText(entry), '4s');
  });

  test('compiled bundle includes lane timer helpers', () => {
    const js = fs.readFileSync(INVENTORY_JS, 'utf8');
    assert.match(js, /liveSecondsSinceStatusSuccess/);
    assert.match(js, /formatStatsUploadDurationText/);
    assert.match(js, /DEFAULT_UPLOAD_INTERVAL_SEC = 60/);
  });

  test('upload coalesce replaces hard 429 rate limiter', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'trackerUploadRateLimit.js'), 'utf8');
    assert.match(src, /deprecated/i);
    assert.match(src, /next\(\)/);
    const coalesceSrc = fs.readFileSync(path.join(__dirname, '..', 'src', 'trackerUploadCoalesce.js'), 'utf8');
    assert.match(coalesceSrc, /duplicate_lane_upload_coalesced/);
    assert.doesNotMatch(coalesceSrc, /status\(429\)/);
  });
});
