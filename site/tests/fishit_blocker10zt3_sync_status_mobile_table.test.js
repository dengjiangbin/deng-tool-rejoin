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

const trackerRouter = require('../src/fishitTrackerRoutes');
const {
  BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_MARKER,
  BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
} = require('../src/fishitTrackerBuild');

const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');
const RAW_LUA = path.join(__dirname, '..', '..', '..', 'DENG PRIVATE SOURCE', 'fishtracker', 'tracker.lua');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZT3 sync status + coin probe + mobile account cards', () => {
  test('UI deploy marker points to BLOCKER10ZT3A hotfix', () => {
    const { BLOCKER10ZT6_LIVE_STATS_POLL_SYNC_LAYOUT_MARKER } = require('../src/fishitTrackerBuild');
    assert.equal(BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, BLOCKER10ZT6_LIVE_STATS_POLL_SYNC_LAYOUT_MARKER);
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /BLOCKER10ZT6_LIVE_STATS_POLL_SYNC_LAYOUT_2026_06_10/);
  });

  test('frontend uses freshest session timestamp for connection', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /function bestSyncTimestamp/);
    assert.match(tpl, /function syncTimestamp[\s\S]*bestSyncTimestamp\(data\)/);
  });

  test('pollUser uses shared applyPollPayload and connection freshness', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /function isEntryConnectionLive/);
    assert.match(tpl, /function applyPollPayload/);
    assert.match(tpl, /applyPollPayload\(entry, key, data\)/);
    assert.doesNotMatch(tpl, /if \(data\.isOnline === false\)/);
  });

  test('mobile uses stacked account cards instead of compact desktop table', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /buildAccountMobileCardHtml/);
    assert.match(tpl, /accounts-mobile-list/);
    assert.match(tpl, /@media \(max-width:768px\)[\s\S]*\.accounts-table-wrap \{ display:none/);
    assert.match(tpl, /@media \(max-width:768px\)[\s\S]*\.accounts-mobile-list \{ display:flex/);
    assert.match(tpl, /\.accounts-mobile-card__username[\s\S]*overflow-wrap:anywhere/);
    assert.doesNotMatch(tpl, /@media \(max-width:768px\)[\s\S]*table-layout:fixed/);
  });

  test('inventory view keeps 2-column mobile grid', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /@media \(max-width:768px\)[\s\S]*\.inventory-grid[\s\S]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  });

  test('tracker_status updates lastSeenAt and debug exposes syncProof', async () => {
    const app = makeApp();
    const username = 'SyncProofUser';
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username,
        userId: 4421,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZW_PLAYERSTATS_REAL_ONLY_2026_06_10',
        phase: 'live',
      })
      .expect(200);

    const debug = await request(app).get(`/api/fishit-tracker/debug/${username}`).expect(200);
    assert.equal(debug.body.ok, true);
    assert.ok(debug.body.lastSeenAt);
    assert.ok(debug.body.lastUploadReceivedAt);
    assert.ok(debug.body.lastUploadAcceptedAt);
    assert.equal(debug.body.lastUploadRejectedAt, null);
    assert.equal(debug.body.lastUploadRejectReason, null);
    assert.equal(debug.body.lastUploadPayloadType, 'tracker_status');
    assert.equal(debug.body.syncProof.isOnline, true);
    assert.equal(debug.body.syncProof.statusColor, 'green');
    assert.ok(debug.body.syncProof.ageSeconds != null && debug.body.syncProof.ageSeconds < 30);
  });

  test('fresh heartbeat shows online even when lastInventoryAt is stale', async () => {
    const app = makeApp();
    const username = 'StaleInvFreshSeen';
    const stale = new Date(Date.now() - 3600_000).toISOString();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username,
        userId: 4422,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10',
        clientOrigin: 'roblox_tracker',
        items: [{ itemId: '1', name: 'Test Fish', amount: 1, category: 'fish' }],
        playerStats: {
          totalCaught: 100,
          totalCaughtText: '100',
          rarestFishChance: '1/1K',
          source: 'leaderstats',
          build: 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10',
        },
      })
      .expect(200);

    const backpack1 = await request(app).get(`/api/fishit-tracker/get-backpack/${username}`).expect(200);
    assert.equal(backpack1.body.isOnline, true);

    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username,
        userId: 4422,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZW_PLAYERSTATS_REAL_ONLY_2026_06_10',
        phase: 'live',
      })
      .expect(200);

    const backpack2 = await request(app).get(`/api/fishit-tracker/get-backpack/${username}`).expect(200);
    assert.equal(backpack2.body.isOnline, true);
    assert.ok(backpack2.body.lastSeenAt);
    assert.ok(backpack2.body.lastInventoryAt);
    const seenMs = new Date(backpack2.body.lastSeenAt).getTime();
    const invMs = new Date(backpack2.body.lastInventoryAt).getTime();
    assert.ok(seenMs > invMs);
    const debug = await request(app).get(`/api/fishit-tracker/debug/${username}`).expect(200);
    assert.equal(debug.body.syncProof.isOnline, true);
  });

  test('missing coins does not force offline when sync is fresh', async () => {
    const app = makeApp();
    const username = 'NoCoinsStillLive';
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username,
        userId: 4423,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10',
        clientOrigin: 'roblox_tracker',
        items: [],
        playerStats: {
          totalCaught: 500,
          totalCaughtText: '500',
          rarestFishChance: '1/4M',
          source: 'leaderstats',
          build: 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10',
        },
        playerStatsDebug: {
          enabled: true,
          build: 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10',
          coinProbe: {
            source: 'missing',
            candidateKeys: ['Caught', 'Rarest Fish'],
            leaderstatsChildren: [{ name: 'Caught', value: '500' }, { name: 'Rarest Fish', value: '1/4M' }],
          },
        },
      })
      .expect(200);

    const res = await request(app).get(`/api/fishit-tracker/get-backpack/${username}`).expect(200);
    assert.equal(res.body.isOnline, true);
    assert.equal(res.body.playerStats.coinsText, undefined);
    assert.equal(res.body.playerStats.totalCaughtText, '500');
    assert.equal(res.body.playerStats.rarestFishChance, '1/4M');
  });

  test('coinProbe sanitiser keeps leaderstatsChildren', () => {
    const { sanitisePlayerStatsDebug } = require('../src/fishitPlayerStats');
    const out = sanitisePlayerStatsDebug({
      enabled: true,
      build: 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10',
      coinProbe: {
        source: 'missing',
        candidateKeys: ['Coins'],
        leaderstatsChildren: [{ name: 'Caught', value: '68,885' }],
      },
    });
    assert.deepEqual(out.coinProbe.leaderstatsChildren, [{ name: 'Caught', value: '68,885' }]);
  });

  test('private Lua includes leaderstatsChildren and sync debug prints', () => {
    if (!fs.existsSync(RAW_LUA)) return;
    const raw = fs.readFileSync(RAW_LUA, 'utf8');
    assert.match(raw, /leaderstatsChildren = collectLeaderstatsChildren\(\)/);
    assert.match(raw, /print\("\[FishTracker\] build", TRACKER_BUILD\)/);
    assert.match(raw, /print\("\[FishTracker\] sync endpoint", TRACKER_URL\)/);
    assert.match(raw, /print\("\[FishTracker\] upload status"/);
  });
});
