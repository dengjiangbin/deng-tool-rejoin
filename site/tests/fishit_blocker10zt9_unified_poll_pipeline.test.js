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
  BLOCKER10ZT9_UNIFIED_POLL_PIPELINE_MARKER,
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

function extractFn(script, name) {
  const re = new RegExp(`function ${name}\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n  \\}`);
  const m = script.match(re);
  assert.ok(m, `${name} must exist`);
  return m[0];
}

function loadPollSnapshotFns() {
  const tpl = fs.readFileSync(TPL_PATH, 'utf8');
  const script = tpl.slice(tpl.indexOf('<script>') + 8, tpl.indexOf('</script>'));
  const names = [
    'formatCompactStatNumber',
    'formatGroupedCaughtNumber',
    'isTrustedPlayerStats',
    'displayableEntryPlayerStats',
    'extractPlayerStatsFromPayload',
    'buildLiveSnapshotFromPayload',
    'syncEntryFromLiveSnapshot',
    'getEntryPlayerStats',
    'displayCoinsStat',
    'displayTotalCaughtStat',
    'displayRarestFishStat',
  ];
  const fns = names.map((name) => extractFn(script, name));
  const trusted = script.match(/const TRUSTED_PLAYERSTATS_BUILD_MARKS = \[[^\]]+\];/);
  assert.ok(trusted, 'TRUSTED_PLAYERSTATS_BUILD_MARKS must exist');
  return new Function(`
    ${trusted[0]}
    function getPublicFishItems(data) {
      if (Array.isArray(data && data.fishItems)) return data.fishItems;
      return [];
    }
    function getPublicStoneItems(data) {
      if (Array.isArray(data && data.stoneItems)) return data.stoneItems;
      return [];
    }
    ${fns.join('\n')}
    return {
      buildLiveSnapshotFromPayload,
      syncEntryFromLiveSnapshot,
      getEntryPlayerStats,
      displayCoinsStat,
      displayTotalCaughtStat,
      displayRarestFishStat,
    };
  `)();
}

function trustedStats(overrides) {
  return {
    source: 'leaderstats',
    build: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
    ...overrides,
  };
}

describe('BLOCKER10ZT9 unified poll pipeline', () => {
  test('unified poll pipeline remains intact after sidebar deploy marker update', () => {
    assert.equal(BLOCKER10ZT9_UNIFIED_POLL_PIPELINE_MARKER, 'BLOCKER10ZT9_UNIFIED_POLL_PIPELINE_2026_06_11');
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /function applyInventoryPollPayload/);
    assert.match(tpl, /entry\.liveSnapshot/);
    assert.match(tpl, /function buildLiveSnapshotFromPayload/);
    assert.match(tpl, /function applyLiveSnapshotToPublicUi/);
    assert.match(tpl, /const POLL_MS\s*=\s*10000/);
  });

  test('displayTotalCaughtStat prefers numeric totalCaught over stale totalCaughtText', () => {
    const fns = loadPollSnapshotFns();
    const stats = trustedStats({
      totalCaught: 58811,
      totalCaughtText: '58.810',
    });
    assert.equal(fns.displayTotalCaughtStat(stats), '58.811');
  });

  test('mergePlayerStats regenerates totalCaughtText when numeric totalCaught changes', () => {
    const existing = playerStatsStore.sanitisePlayerStats(trustedStats({
      totalCaught: 58810,
      totalCaughtText: '58.810',
    }));
    const merged = playerStatsStore.mergePlayerStats(existing, trustedStats({
      totalCaught: 58811,
    }), { isLiveRoblox: true });
    assert.equal(merged.totalCaught, 58811);
    assert.equal(merged.totalCaughtText, '58.811');
    assert.equal(playerStatsStore.displayTotalCaught(merged), '58.811');
  });

  test('liveSnapshot updates coin, total caught, rarest fish, fish, and stone across 3 poll cycles', () => {
    const fns = loadPollSnapshotFns();
    const entry = { liveSnapshot: null, playerStats: null, lastFishList: null, lastStoneList: null };
    const cycles = [
      {
        playerStats: trustedStats({ coins: 100, coinsText: '100', totalCaught: 1000, totalCaughtText: '1.000', rarestFishChance: '1/100' }),
        fishItems: [{ itemId: '1', name: 'Tuna', quantity: 1 }],
        stoneItems: [{ itemId: '10', name: 'Normal Enchant Stone', stoneType: 'Normal', quantity: 1 }],
        expect: { coin: '100', caught: '1.000', rare: '1/100', fish: 1, stone: 1 },
      },
      {
        playerStats: trustedStats({ coins: 200, totalCaught: 2000, rarestFishChance: '1/200' }),
        fishItems: [{ itemId: '1', name: 'Tuna', quantity: 2 }, { itemId: '2', name: 'Salmon', quantity: 1 }],
        stoneItems: [{ itemId: '10', name: 'Normal Enchant Stone', stoneType: 'Normal', quantity: 2 }],
        expect: { coin: '200', caught: '2.000', rare: '1/200', fish: 2, stone: 1 },
      },
      {
        playerStats: trustedStats({ coins: 300, totalCaught: 3000, rarestFishChance: '1/300' }),
        fishItems: [{ itemId: '3', name: 'Shark', quantity: 1 }],
        stoneItems: [],
        expect: { coin: '300', caught: '3.000', rare: '1/300', fish: 1, stone: 0 },
      },
    ];

    const seen = [];
    cycles.forEach((cycle, idx) => {
      const data = {
        playerStats: cycle.playerStats,
        fishItems: cycle.fishItems,
        stoneItems: cycle.stoneItems,
        playerStatsUpdatedAt: `2026-06-11T00:00:0${idx + 1}Z`,
      };
      const pollAt = `2026-06-11T00:00:1${idx}Z`;
      entry.liveSnapshot = fns.buildLiveSnapshotFromPayload(entry, data, pollAt);
      fns.syncEntryFromLiveSnapshot(entry);
      const stats = fns.getEntryPlayerStats(entry);
      seen.push({
        coin: fns.displayCoinsStat(stats),
        caught: fns.displayTotalCaughtStat(stats),
        rare: fns.displayRarestFishStat(stats),
        fish: entry.liveSnapshot.fishCount,
        stone: entry.liveSnapshot.stoneCount,
        pollCount: entry.liveSnapshot.pollCount,
      });
      assert.deepEqual(seen[idx], { ...cycle.expect, pollCount: idx + 1 });
    });

    assert.deepEqual(seen.map((row) => row.coin), ['100', '200', '300']);
    assert.deepEqual(seen.map((row) => row.caught), ['1.000', '2.000', '3.000']);
    assert.deepEqual(seen.map((row) => row.rare), ['1/100', '1/200', '1/300']);
    assert.deepEqual(seen.map((row) => row.fish), [1, 2, 1]);
    assert.deepEqual(seen.map((row) => row.stone), [1, 1, 0]);
  });

  test('get-backpack returns refreshed stats across 3 uploads', async () => {
    const app = makeApp();
    const username = 'unifiedpollusr';
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
          userId: 99120 + i,
          isOnline: true,
          clientOrigin: 'roblox_tracker',
          trackerBuild: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
          items: [{ itemId: String(i + 1), name: `Fish${i + 1}`, amount: 1, category: 'fish', rarity: 'Common' }],
          playerStats: {
            coins: p.coins,
            coinsText: String(p.coins),
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
    assert.deepEqual(seen.map((row) => row.totalCaughtText), ['1000', '2.000', '3.000']);
    assert.deepEqual(seen.map((row) => row.rarestFishChance), ['1/100', '1/200', '1/300']);
  });

  test('debug API exposes unified poll proof fields', async () => {
    const app = makeApp();
    const username = 'UnifiedPollDebugUser';
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username,
        userId: 99130,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
        clientOrigin: 'roblox_tracker',
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
    assert.equal(debug.body.unifiedPollPipelineProof.sharedRefreshFunction, 'applyInventoryPollPayload');
    assert.equal(debug.body.statsHarmonyProof.coinTotalCaughtRarestFromSamePlayerStatsObject, true);
    assert.equal(debug.body.totalCaughtIntervalProof.totalCaughtRefreshesOnEveryPoll, true);
    assert.equal(debug.body.coinIntervalProof.coinRefreshesOnEveryPoll, true);
    assert.equal(debug.body.fishStoneIntervalProof.fishStoneFromSamePollPayload, true);
    assert.equal(debug.body.statsPollingProof.sharedRefreshFunction, 'applyInventoryPollPayload');
  });
});
