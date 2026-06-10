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
  BLOCKER10ZT6_LIVE_STATS_POLL_SYNC_LAYOUT_MARKER,
  BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
  EXPECTED_CLIENT_TRACKER_BUILD,
} = require('../src/fishitTrackerBuild');

const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZT6 live stats poll, sync status, responsive layout', () => {
  test('UI deploy marker points to BLOCKER10ZT6 while client tracker stays ZT5', () => {
    assert.equal(BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, BLOCKER10ZT6_LIVE_STATS_POLL_SYNC_LAYOUT_MARKER);
    assert.match(EXPECTED_CLIENT_TRACKER_BUILD, /BLOCKER10ZT5/);
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /BLOCKER10ZT6_LIVE_STATS_POLL_SYNC_LAYOUT_2026_06_10/);
  });

  test('frontend uses 10s shared poll and 1s sync tick with applyPollPayload', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /const POLL_MS\s*=\s*10000/);
    assert.match(tpl, /const SYNC_TICK_MS\s*=\s*1000/);
    assert.match(tpl, /function applyPollPayload/);
    assert.match(tpl, /applyPollPayload\(entry, key, data\)/);
    assert.doesNotMatch(tpl, /mergeEntryPlayerStats/);
    assert.doesNotMatch(tpl, /Live · Last sync/);
  });

  test('status format uses card-sync-line with duration and username', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /function formatEntrySyncStatusLine/);
    assert.match(tpl, /Last sync: \$\{duration\} \$\{name\}/);
    assert.match(tpl, /data-card-sync-text/);
    assert.match(tpl, /formatSyncDurationLabel/);
    assert.doesNotMatch(tpl, /\.sync-age \{ display:none;/);
  });

  test('desktop table forced at min-width 769 and mobile stats horizontal', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /@media \(min-width:769px\)[\s\S]*\.accounts-table-wrap \{ display:block !important/);
    assert.match(tpl, /@media \(min-width:769px\)[\s\S]*\.accounts-mobile-list \{ display:none !important/);
    assert.match(tpl, /@media \(max-width:768px\)[\s\S]*\.accounts-mobile-card__grid--stats[\s\S]*flex-direction:row/);
    assert.match(tpl, /\.accounts-mobile-card__grid--stats[\s\S]*grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/);
  });

  test('table hides sync age and mobile cards omit debug rows', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /accounts-status \[data-table-sync-age\] \{ display:none;/);
    assert.doesNotMatch(tpl, /accounts-mobile-card__row-label">Last sync/);
    assert.doesNotMatch(tpl, /accounts-mobile-card__row-label">Fish/);
    assert.doesNotMatch(tpl, /accounts-mobile-card__row-label">Types/);
  });

  test('get-backpack returns playerStats from stored session even when isSessionLive is false', async () => {
    const app = makeApp();
    const username = 'StatsPollProofUser';
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username,
        userId: 99102,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
        clientOrigin: 'roblox_tracker',
        items: [{ itemId: '42', name: 'Deep Sea Crab', amount: 3, category: 'fish', rarity: 'Rare' }],
        playerStats: {
          coins: 2200,
          coinsText: '2.2K',
          totalCaught: 88,
          totalCaughtText: '88',
          rarestFishChance: '1/250',
          source: 'leaderstats',
          build: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
        },
      })
      .expect(200);

    const backpack = await request(app).get(`/api/fishit-tracker/get-backpack/${username}`).expect(200);
    assert.equal(backpack.body.playerStats.coinsText, '2.2K');
    assert.equal(backpack.body.playerStats.totalCaughtText, '88');
    assert.equal(backpack.body.playerStats.rarestFishChance, '1/250');
    assert.equal(backpack.body.connectionIndicatorProof, undefined);

    const { isSessionLive } = require('../src/fishitTrackerRoutes');
    const playerStatsStore = require('../src/fishitPlayerStats');
    const staleSession = {
      lastSeenAt: new Date(Date.now() - 200_000).toISOString(),
      lastInventoryAt: new Date(Date.now() - 200_000).toISOString(),
      playerStats: backpack.body.playerStats,
    };
    assert.equal(isSessionLive(staleSession), false);
    assert.equal(playerStatsStore.displayablePlayerStats(staleSession.playerStats).coinsText, '2.2K');
  });

  test('debug API exposes proof fields only in debug route', async () => {
    const app = makeApp();
    const username = 'DebugProofUser';
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username,
        userId: 99103,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
        fishItems: [{ itemId: '2', name: 'Tuna', quantity: 1, source: 'playerdata_gameitemdb' }],
        playerStats: {
          coins: 500,
          coinsText: '500',
          totalCaught: 10,
          totalCaughtText: '10',
          rarestFishChance: '1/100',
          source: 'leaderstats',
          build: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
        },
      })
      .expect(200);

    const debug = await request(app).get(`/api/fishit-tracker/debug/${username}`).expect(200);
    assert.equal(debug.body.statsPollingProof.publicPollIntervalMs, 10000);
    assert.equal(debug.body.statsPollingProof.syncTickMs, 1000);
    assert.equal(debug.body.uploadIntervalProof.trackerUploadIntervalSeconds, 10);
    assert.equal(debug.body.responsiveLayoutProof.mobileStatsFlexRow, true);
    assert.equal(debug.body.responsiveLayoutProof.desktopLayoutReverted, true);
    assert.equal(debug.body.connectionIndicatorProof.indicatorColor, 'green');

    const backpack = await request(app).get(`/api/fishit-tracker/get-backpack/${username}`).expect(200);
    assert.equal(backpack.body.statsPollingProof, undefined);
    assert.equal(backpack.body.uploadIntervalProof, undefined);
    assert.equal(backpack.body.responsiveLayoutProof, undefined);
    assert.equal(backpack.body.connectionIndicatorProof, undefined);
  });
});
