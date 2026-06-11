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
const inventoryAssets = require('../src/inventoryAssets');
const {
  BLOCKER10ZTF_STAT_INTERVAL_SOURCE_HARDENING_MARKER,
  BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
} = require('../src/fishitTrackerBuild');

const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');
const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const ASSET_JS_PATH = path.join(__dirname, '..', 'public', 'assets', inventoryAssets.loadManifest().js);

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZTF stat interval + source hardening + Lucide fish icon', () => {
  test('deploy marker points at BLOCKER10ZTF build', () => {
    assert.equal(BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, BLOCKER10ZTF_STAT_INTERVAL_SOURCE_HARDENING_MARKER);
  });

  test('parseIntegerStat accepts grouped caught strings', () => {
    assert.equal(playerStatsStore.parseIntegerStat('61,984'), 61984);
    assert.equal(playerStatsStore.parseIntegerStat('61.984'), 61984);
    assert.equal(playerStatsStore.parseIntegerStat(61984), 61984);
  });

  test('enrichIncomingPlayerStats prefers fresh leaderstats caught over stale replion numeric', () => {
    const enriched = playerStatsStore.enrichIncomingPlayerStats({
      coins: 41325061,
      totalCaught: 61984,
      source: 'replion',
      build: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
    }, {
      trackerBuild: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
      isLiveRoblox: true,
      playerStatsDebug: {
        enabled: true,
        build: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
        coinProbe: {
          leaderstatsChildren: [
            { name: 'Caught', value: '62,211' },
            { name: 'Rarest Fish', value: '1/2M' },
          ],
        },
      },
    });
    assert.equal(enriched.totalCaught, 62211);
    assert.equal(enriched.rarestFishChance, '1/2M');
  });

  test('get-backpack refreshes total caught across coin-only uploads with leaderstats debug', async () => {
    const app = makeApp();
    const username = 'ztfleaderstats';
    const base = {
      type: 'inventory_snapshot',
      username,
      userId: 99301,
      isOnline: true,
      clientOrigin: 'roblox_tracker',
      trackerBuild: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
      items: [{ itemId: '1', name: 'Fish1', amount: 1, category: 'fish', rarity: 'Common' }],
    };
    const seq = [
      { coins: 100, caught: 1000, rare: '1/100' },
      { coins: 200, caught: 2000, rare: '1/200' },
      { coins: 300, caught: 3000, rare: '1/300' },
      { coins: 400, caught: 4000, rare: '1/400' },
    ];
    const seen = [];
    for (let i = 0; i < seq.length; i += 1) {
      const row = seq[i];
      await request(app)
        .post('/api/fishit-tracker/update-backpack')
        .send({
          ...base,
          playerStats: {
            coins: row.coins,
            source: 'replion',
            build: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
          },
          playerStatsDebug: {
            enabled: true,
            build: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
            coinProbe: {
              leaderstatsChildren: [
                { name: 'Caught', value: String(row.caught) },
                { name: 'Rarest Fish', value: row.rare },
              ],
            },
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
    assert.deepEqual(seen.map((row) => row.coinsText), ['100', '200', '300', '400']);
    assert.deepEqual(seen.map((row) => row.totalCaughtText), ['1.000', '2.000', '3.000', '4.000']);
    assert.deepEqual(seen.map((row) => row.rarestFishChance), ['1/100', '1/200', '1/300', '1/400']);
  });

  test('compiled inventory JS patches caught/coin/rare on each poll cycle', () => {
    const js = fs.readFileSync(ASSET_JS_PATH, 'utf8');
    assert.match(js, /function patchAccountStatsRow/);
    assert.match(js, /function applyInventoryPollPayload/);
    assert.match(js, /patchAccountStatsRow\(entry, key\)/);
    assert.match(js, /caughtEl\.textContent = caughtText/);
    assert.match(js, /coinsEl\.textContent = coinsText/);
    assert.match(js, /rareEl\.textContent = rareText/);
    assert.match(js, /const POLL_MS\s*=\s*10000/);
  });

  test('fish grid toolbar uses exact Lucide fish SVG path', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /M6\.5 12c\.94-3\.46 4\.94-6 8\.5-6/);
    assert.doesNotMatch(tpl, /M6 12c2-3\.2 5\.6-4\.4 8\.4-4\.4/);
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /class="lucide lucide-fish"/);
  });

  test('production inventory HTML uses external assets and no giant inline app code', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    const html = res.text;
    const manifest = inventoryAssets.loadManifest();
    assert.match(html, new RegExp(`/public/assets/${manifest.css.replace('.', '\\.')}`));
    assert.match(html, new RegExp(`/public/assets/${manifest.js.replace('.', '\\.')}`));
    assert.match(html, /id="inventory-runtime"/);
    assert.doesNotMatch(html, /<style>[\s\S]{500,}<\/style>/);
    assert.doesNotMatch(html, /<script>\s*\(function \(\)/);
    assert.doesNotMatch(html, /BLOCKER10ZTA_INVENTORY_DESKTOP_SIDEBAR/);
    assert.doesNotMatch(html, /window\.__fishitDebugProof/);
    assert.doesNotMatch(html, /function applyInventoryPollPayload/);
  });
});
