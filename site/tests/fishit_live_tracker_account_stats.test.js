'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-the-site-suite';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = process.env.DISCORD_CLIENT_ID || 'test-discord-client-id';
process.env.DISCORD_CLIENT_SECRET = process.env.DISCORD_CLIENT_SECRET || 'test-discord-client-secret';
process.env.DISCORD_REDIRECT_URI = process.env.DISCORD_REDIRECT_URI || 'http://localhost:8791/auth/discord/callback';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';

const liveTrackerSerializer = require('../src/fishitLiveTrackerSerializer');
const playerStatsStore = require('../src/fishitPlayerStats');
const trackerRoutes = require('../src/fishitTrackerRoutes');
const { MINIMUM_TRACKER_BUILD } = require('../src/fishitTrackerBuild');
const fs = require('fs');
const path = require('path');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');

function makeAuthedApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use((req, _res, next) => {
    req.session = {
      csrfToken: 'csrf-test',
      user: { discord_user_id: '123456789012345678', id: '123456789012345678' },
    };
    next();
  });
  app.use(trackerRoutes);
  return app;
}

function resolvePlayerStatsForApi(raw) {
  return playerStatsStore.normalizePlayerStatsForApi(raw);
}

describe('live tracker account stats serializer', () => {
  test('serializeLiveTrackerAccountStats returns coin/caught/rare from leaderstats session', () => {
    const stats = liveTrackerSerializer.serializeLiveTrackerAccountStats({
      playerStats: {
        coins: 653200000,
        totalCaught: 3077845,
        rarestFishChance: '1/25M',
        source: 'leaderstats',
        build: MINIMUM_TRACKER_BUILD,
      },
      lastSuccessfulUploadAt: '2026-06-12T10:00:00.000Z',
      runId: 'run-abc',
      uploadSeq: 42,
      statusColor: 'green',
    }, playerStatsStore, resolvePlayerStatsForApi);

    assert.equal(stats.statsProven, true);
    assert.equal(stats.coins, 653200000);
    assert.equal(stats.coinsText, '653.2M');
    assert.equal(stats.totalCaught, 3077845);
    assert.equal(stats.totalCaughtText, '3.077.845');
    assert.equal(stats.rarestFish, '1/25M');
    assert.equal(stats.statsSource, 'leaderstats');
    assert.equal(stats.emptyReason, null);
    assert.equal(stats.runId, 'run-abc');
  });

  test('serializeLiveTrackerAccountStats reports emptyReason when stats missing', () => {
    const stats = liveTrackerSerializer.serializeLiveTrackerAccountStats({
      statusColor: 'green',
      lastSuccessfulUploadAt: '2026-06-12T10:00:00.000Z',
    }, playerStatsStore, resolvePlayerStatsForApi);
    assert.equal(stats.statsProven, false);
    assert.ok(stats.emptyReason);
    assert.equal(stats.coinsText, null);
  });

  test('lite get-backpack includes liveAccountStats separate from dashboard bot DB', async () => {
    const app = makeAuthedApp();
    const username = 'LiveStatsUser1';
    await request(app).post('/api/fishit-tracker/update-backpack').send({
      type: 'inventory_snapshot',
      username,
      userId: 991002,
      isOnline: true,
      clientOrigin: 'roblox_tracker',
      trackerBuild: MINIMUM_TRACKER_BUILD,
      leaderstatsReady: true,
      fishItems: [{ itemId: '1', name: 'Clownfish', type: 'Fish', quantity: 1, source: 'playerdata_gameitemdb', rarity: 'Common' }],
      stoneItems: [],
      playerStats: {
        coins: 1200,
        totalCaught: 450,
        rarestFishChance: '1/4.50K',
        source: 'leaderstats',
        build: MINIMUM_TRACKER_BUILD,
      },
    }).expect(200);

    const lite = await request(app)
      .get(`/api/tracker/get-backpack/${username.toLowerCase()}?lite=1`)
      .expect(200);

    assert.equal(lite.body.lite, true);
    assert.ok(lite.body.liveAccountStats);
    assert.equal(lite.body.liveAccountStats.coinsText, '1.2K');
    assert.equal(lite.body.liveAccountStats.totalCaughtText, '450');
    assert.equal(lite.body.liveAccountStats.rarestFish, '1/4.50K');
    assert.equal(lite.body.liveAccountStats.statsSource, 'leaderstats');
    assert.equal(lite.body.statsSource, 'leaderstats');
    assert.equal(lite.body.globalCatalogProof, undefined);
  });

  test('frontend trusts liveAccountStats from API without snapshotComplete gate', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /liveAccountStatsToPlayerStats/);
    assert.match(source, /__fromApi/);
    assert.match(source, /patchAllVisibleAccountStats/);
    assert.match(source, /patchAccountStatsRow/);
    assert.doesNotMatch(source, /applyLiveSnapshotToPublicUi[\s\S]{0,400}renderAccountsTable\(\)/);
  });
});
