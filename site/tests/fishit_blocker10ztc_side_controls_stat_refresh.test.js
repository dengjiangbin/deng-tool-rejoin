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

const playerStatsStore = require('../src/fishitPlayerStats');
const trackerRouter = require('../src/fishitTrackerRoutes');
const {
  BLOCKER10ZTC_SIDE_CONTROLS_STAT_REFRESH_MARKER,
  BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
} = require('../src/fishitTrackerBuild');

const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZTC side controls + stat refresh contract', () => {
  test('deploy marker points at side controls stat refresh build', () => {
    assert.equal(BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, BLOCKER10ZTC_SIDE_CONTROLS_STAT_REFRESH_MARKER);
  });

  test('hide username uses single icon slot and no dual-eye markup', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /id="hideUsernameIcon"/);
    assert.match(tpl, /hideUsernameIconEl\.innerHTML = hideUsernames \? HIDE_USERNAME_EYE_OFF_SVG : HIDE_USERNAME_EYE_SVG/);
    assert.doesNotMatch(tpl, /data-icon="eye-off"/);
    assert.doesNotMatch(tpl, /theme-toggle-track/);
  });

  test('inventory sidebar has no guest or sign-in UI', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    assert.doesNotMatch(res.text, />Guest</);
    assert.doesNotMatch(res.text, /Sign in to sync profile/);
    assert.doesNotMatch(res.text, />Sign in</);
    assert.doesNotMatch(res.text, /inventory-action-btn--login" title="Sign in"/);
    assert.match(res.text, /inventory-profile-card__name/);
    assert.match(res.text, />Logout</);
    assert.match(res.text, />Script</);
  });

  test('normalizePlayerStatsForApi regenerates stale totalCaughtText from numeric field', () => {
    const raw = playerStatsStore.sanitisePlayerStats({
      source: 'leaderstats',
      build: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
      totalCaught: 58811,
      totalCaughtText: '58.810',
      coins: 100,
      coinsText: '99',
      rarestFishChance: '1/100',
    });
    const normalized = playerStatsStore.normalizePlayerStatsForApi(raw);
    assert.equal(normalized.totalCaughtText, '58.811');
    assert.equal(normalized.coinsText, '100');
    assert.equal(playerStatsStore.displayTotalCaught(normalized), '58.811');
  });

  test('mergePlayerStats keeps totalCaught and rarestFish fresh across partial coin-only uploads', () => {
    let merged = playerStatsStore.mergePlayerStats(null, {
      source: 'leaderstats',
      build: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
      coins: 100,
      totalCaught: 1000,
      rarestFishChance: '1/100',
    }, { isLiveRoblox: true });
    merged = playerStatsStore.mergePlayerStats(merged, {
      source: 'leaderstats',
      build: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
      coins: 200,
    }, { isLiveRoblox: true });
    assert.equal(merged.coins, 200);
    assert.equal(merged.totalCaught, 1000);
    assert.equal(merged.rarestFishChance, '1/100');
    merged = playerStatsStore.mergePlayerStats(merged, {
      source: 'leaderstats',
      build: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
      totalCaught: 2000,
      rarestFishChance: '1/200',
    }, { isLiveRoblox: true });
    assert.equal(playerStatsStore.displayTotalCaught(merged), '2.000');
    assert.equal(playerStatsStore.displayRarestFish(merged), '1/200');
  });

  test('get-backpack returns refreshed coin, total caught, and rarest fish across 3 uploads', async () => {
    const app = makeApp();
    const username = 'ztcstatrefresh';
    const payloads = [
      { coins: 100, totalCaught: 1000, rarestFishChance: '1/100' },
      { coins: 200, totalCaught: 2000, rarestFishChance: '1/200' },
      { coins: 300, totalCaught: 3000, rarestFishChance: '1/300' },
    ];
    const seen = [];
    for (let i = 0; i < payloads.length; i += 1) {
      const p = payloads[i];
      await request(app)
        .post('/api/fishit-tracker/update-backpack')
        .send({
          type: 'inventory_snapshot',
          username,
          userId: 99200 + i,
          isOnline: true,
          clientOrigin: 'roblox_tracker',
          trackerBuild: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
          items: [{ itemId: String(i + 1), name: `Fish${i + 1}`, amount: 1, category: 'fish', rarity: 'Common' }],
          playerStats: {
            coins: p.coins,
            totalCaught: p.totalCaught,
            totalCaughtText: String(p.totalCaught),
            rarestFishChance: p.rarestFishChance,
            source: 'leaderstats',
            build: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
          },
        })
        .expect(200);
      const res = await request(app).get(`/api/fishit-tracker/get-backpack/${username}`).expect(200);
      seen.push({
        coinsText: res.body.playerStats.coinsText,
        totalCaughtText: res.body.playerStats.totalCaughtText,
        rarestFishChance: res.body.playerStats.rarestFishChance,
      });
    }
    assert.deepEqual(seen.map((row) => row.coinsText), ['100', '200', '300']);
    assert.deepEqual(seen.map((row) => row.totalCaughtText), ['1.000', '2.000', '3.000']);
    assert.deepEqual(seen.map((row) => row.rarestFishChance), ['1/100', '1/200', '1/300']);
  });

  test('template normalizes poll player stats and keeps unified 10s pipeline', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /function normalizePollPlayerStats/);
    assert.match(tpl, /const POLL_MS\s*=\s*10000/);
    assert.match(tpl, /function applyInventoryPollPayload/);
    assert.match(tpl, /entry\._statRefreshCycleProof/);
  });
});
