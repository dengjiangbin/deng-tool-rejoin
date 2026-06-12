'use strict';

const { describe, test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const fs = require('fs');
const os = require('os');
const path = require('path');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-the-site-suite';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = process.env.DISCORD_CLIENT_ID || 'test-discord-client-id';
process.env.DISCORD_CLIENT_SECRET = process.env.DISCORD_CLIENT_SECRET || 'test-discord-client-secret';
process.env.DISCORD_REDIRECT_URI = process.env.DISCORD_REDIRECT_URI || 'http://localhost:8791/auth/discord/callback';

const TEST_DISCORD_ID = '123456789012345678';
const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const trackerPerf = require('../src/fishitTrackerPerformance');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  // Fresh router per app so fishitDb picks up env changes when module cache is reset.
  delete require.cache[require.resolve('../src/fishitDb')];
  delete require.cache[require.resolve('../src/fishitDbPath')];
  delete require.cache[require.resolve('../src/fishitTrackerRoutes')];
  const trackerRouter = require('../src/fishitTrackerRoutes');
  app.use(trackerRouter);
  return app;
}

function loadFishitDbFresh() {
  delete require.cache[require.resolve('../src/fishitDb')];
  delete require.cache[require.resolve('../src/fishitDbPath')];
  return require('../src/fishitDb');
}

function createBotDb(tmpPath, fishCache) {
  const { DatabaseSync } = require('node:sqlite');
  if (fs.existsSync(tmpPath)) fs.unlinkSync(tmpPath);
  const db = new DatabaseSync(tmpPath);
  db.exec('CREATE TABLE app_kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)');
  if (fishCache != null) {
    db.prepare('INSERT INTO app_kv (key, value) VALUES (?, ?)').run(
      'alltime_fish_cache',
      JSON.stringify(fishCache),
    );
  }
  if (typeof db.close === 'function') db.close();
}

function makeFishCache(discordId, opts = {}) {
  const secret = opts.secret || [];
  const forgotten = opts.forgotten || [];
  return {
    lastUpdated: opts.lastUpdated || new Date().toISOString(),
    byUser: {
      [discordId]: {
        userId: discordId,
        username: opts.username || 'mock_roblox',
        details: { secret, forgotten },
        byDate: opts.byDate || {},
      },
    },
  };
}

describe('dashboard catch stats — DENG Fish It bot DB', () => {
  let tmpDb;
  let prevFishitPath;

  beforeEach(() => {
    prevFishitPath = process.env.FISHIT_DB_PATH;
    tmpDb = path.join(os.tmpdir(), `fishit-dash-catch-${Date.now()}-${Math.random().toString(36).slice(2)}.sqlite`);
    trackerPerf.clearDashboardCache();
  });

  afterEach(() => {
    try {
      const fishitDb = loadFishitDbFresh();
      fishitDb._resetCache();
    } catch (_) { /* ignore */ }
    try {
      const fishitDbPath = require('../src/fishitDbPath');
      fishitDbPath.invalidateResolvedPath();
    } catch (_) { /* ignore */ }
    if (prevFishitPath === undefined) delete process.env.FISHIT_DB_PATH;
    else process.env.FISHIT_DB_PATH = prevFishitPath;
    try {
      if (tmpDb && fs.existsSync(tmpDb)) fs.unlinkSync(tmpDb);
    } catch (_) { /* Windows may keep SQLite handle briefly */ }
  });

  test('mock DB with Secret + Forgotten catches returns ok statsState and counts', () => {
    const fish = makeFishCache(TEST_DISCORD_ID, {
      secret: [
        { name: 'King Crab', time: '2026-06-10T12:00:00.000Z', weight: 1200 },
        { name: 'Iridesca', time: '2026-06-11T08:00:00.000Z', weight: 800 },
      ],
      forgotten: [
        { name: 'Thunderzilla', time: '2026-06-11T14:00:00.000Z', weight: 500000 },
      ],
      byDate: { '2026-06-10': { total: 1 }, '2026-06-11': { total: 2 } },
    });
    createBotDb(tmpDb, fish);
    process.env.FISHIT_DB_PATH = tmpDb;
    const fishitDb = loadFishitDbFresh();

    const payload = fishitDb.getOwnerDashboard(TEST_DISCORD_ID, [], 'all');
    assert.equal(payload.available, true);
    assert.equal(payload.statsState, 'ok');
    assert.equal(payload.cards.secretCaught, 2);
    assert.equal(payload.cards.forgottenCaught, 1);
    assert.ok(payload.fishCards.length >= 2);
    assert.ok(payload.dailyCaught.some((row) => row.totalCaught > 0));
    assert.equal(payload.debug.identityMatchMode, 'discord_id_direct');
    assert.equal(payload.source, 'deng_fish_it_bot_db_d_command');
  });

  test('mock DB with only Secret catches classifies rarity correctly', () => {
    createBotDb(tmpDb, makeFishCache(TEST_DISCORD_ID, {
      secret: [{ name: 'Alpha Secret', time: '2026-06-01T00:00:00.000Z' }],
      forgotten: [],
    }));
    process.env.FISHIT_DB_PATH = tmpDb;
    const fishitDb = loadFishitDbFresh();
    const payload = fishitDb.getOwnerDashboard(TEST_DISCORD_ID, [], 'all');
    assert.equal(payload.statsState, 'ok');
    assert.equal(payload.cards.secretCaught, 1);
    assert.equal(payload.cards.forgottenCaught, 0);
    assert.equal(payload.fishCards[0].rarity, 'Secret');
  });

  test('mock DB with only Forgotten catches classifies rarity correctly', () => {
    createBotDb(tmpDb, makeFishCache(TEST_DISCORD_ID, {
      secret: [],
      forgotten: [{ name: 'Frostbite Leviathan', time: '2026-06-02T00:00:00.000Z' }],
    }));
    process.env.FISHIT_DB_PATH = tmpDb;
    const fishitDb = loadFishitDbFresh();
    const payload = fishitDb.getOwnerDashboard(TEST_DISCORD_ID, [], 'all');
    assert.equal(payload.statsState, 'ok');
    assert.equal(payload.cards.secretCaught, 0);
    assert.equal(payload.cards.forgottenCaught, 1);
    assert.equal(payload.fishCards[0].rarity, 'Forgotten');
  });

  test('mock empty user (zero catches) returns available true with zeros — not error', () => {
    createBotDb(tmpDb, makeFishCache(TEST_DISCORD_ID, { secret: [], forgotten: [] }));
    process.env.FISHIT_DB_PATH = tmpDb;
    const fishitDb = loadFishitDbFresh();
    const payload = fishitDb.getOwnerDashboard(TEST_DISCORD_ID, [], 'all');
    assert.equal(payload.available, true);
    assert.equal(payload.statsState, 'empty');
    assert.equal(payload.emptyReason, 'no_catch_records_in_bot_db');
    assert.equal(payload.cards.secretCaught, 0);
    assert.equal(payload.cards.forgottenCaught, 0);
    assert.deepEqual(payload.fishCards, []);
    assert.ok(Array.isArray(payload.dailyCaught));
  });

  test('missing fish cache row returns statsState error (bot_db_not_connected path)', () => {
    createBotDb(tmpDb, null);
    process.env.FISHIT_DB_PATH = tmpDb;
    const fishitDb = loadFishitDbFresh();
    const payload = fishitDb.getOwnerDashboard(TEST_DISCORD_ID, [], 'all');
    assert.equal(payload.available, false);
    assert.equal(payload.statsState, 'error');
    assert.equal(payload.emptyReason, 'fish_cache_missing_or_empty');
  });

  test('missing DB file returns statsState error', () => {
    process.env.FISHIT_DB_PATH = path.join(os.tmpdir(), 'nonexistent-fishit-dash.sqlite');
    const fishitDb = loadFishitDbFresh();
    const payload = fishitDb.getOwnerDashboard(TEST_DISCORD_ID, [], 'all');
    assert.equal(payload.available, false);
    assert.equal(payload.statsState, 'error');
    assert.equal(payload.emptyReason, 'bot_db_not_connected');
  });

  test('GET /api/tracker/dashboard returns ok payload from mock catch DB', async () => {
    createBotDb(tmpDb, makeFishCache(TEST_DISCORD_ID, {
      secret: [{ name: 'Beta Secret', time: '2026-06-09T10:00:00.000Z' }],
      forgotten: [{ name: 'Sea Eater', time: '2026-06-09T11:00:00.000Z' }],
    }));
    process.env.FISHIT_DB_PATH = tmpDb;
    const res = await request(makeApp()).get('/api/tracker/dashboard?period=all&debug=1').expect(200);
    assert.equal(res.body.ok, true);
    assert.equal(res.body.statsState, 'ok');
    assert.equal(res.body.available, true);
    assert.equal(res.body.cards.secretCaught, 1);
    assert.equal(res.body.cards.forgottenCaught, 1);
    assert.ok(res.body.fishCards.length >= 1);
  });

  test('GET /api/tracker/dashboard with empty catches returns zeros without error state', async () => {
    createBotDb(tmpDb, makeFishCache(TEST_DISCORD_ID, { secret: [], forgotten: [] }));
    process.env.FISHIT_DB_PATH = tmpDb;
    const res = await request(makeApp()).get('/api/tracker/dashboard?period=all&debug=1').expect(200);
    assert.equal(res.body.ok, true);
    assert.equal(res.body.statsState, 'empty');
    assert.equal(res.body.available, true);
    assert.equal(res.body.cards.secretCaught, 0);
    assert.equal(res.body.cards.forgottenCaught, 0);
    assert.notEqual(res.body.statsState, 'error');
  });

  test('GET /api/tracker/dashboard with missing DB returns controlled error JSON', async () => {
    process.env.FISHIT_DB_PATH = path.join(os.tmpdir(), 'missing-fishit-api.sqlite');
    const res = await request(makeApp()).get('/api/tracker/dashboard?period=all&debug=1').expect(200);
    assert.equal(res.body.ok, true);
    assert.equal(res.body.statsState, 'error');
    assert.equal(res.body.available, false);
    assert.equal(res.body.emptyReason, 'bot_db_not_connected');
    assert.equal(res.body.cards.secretCaught, 0);
  });

  test('frontend distinguishes API failure from valid empty data', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /function dashboardStatsState/);
    assert.match(source, /statsState === 'error'/);
    assert.match(source, /renderDashboardChart\(\(data && data\.dailyCaught\)[^;]+\{ failed \}/);
    assert.match(source, /renderDashboardFishGrid\(\(data && data\.fishCards\)[^;]+\{ failed \}/);
    assert.match(source, /if \(state !== 'error'\) return;/);
    assert.match(source, /failed \? null : Number\(cards\.secretCaught/);
  });
});
