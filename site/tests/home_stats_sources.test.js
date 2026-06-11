'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-the-site-suite';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = process.env.DISCORD_CLIENT_ID || 'test-discord-client-id';
process.env.DISCORD_CLIENT_SECRET = process.env.DISCORD_CLIENT_SECRET || 'test-discord-client-secret';
process.env.DISCORD_REDIRECT_URI = process.env.DISCORD_REDIRECT_URI || 'http://localhost:8791/auth/discord/callback';

const app = require('../src/app');
const fishitDb = require('../src/fishitDb');
const trackerRoutes = require('../src/fishitTrackerRoutes');

describe('landing stat sources and layout regression', () => {
  test('Fish It public-summary uses DENG Fish It Bot database sources only', async () => {
    const res = await request(app).get('/api/fishit/public-summary');
    assert.equal(res.status, 200);
    assert.ok(res.body.sources, 'expected sources proof object');
    assert.match(String(res.body.sources.totalFish.service), /fishitDb/);
    assert.match(String(res.body.sources.totalFish.store), /deng-fish-it-bot/);
    assert.match(String(res.body.sources.totalSecret.blob), /alltime_fish_cache/);
    assert.match(String(res.body.sources.totalForgotten.blob), /alltime_fish_cache/);
    assert.match(String(res.body.sources.ghostfinnRod.blob), /alltime_rod_cache/);
    assert.match(String(res.body.sources.elementRod.field), /totalElement/);
    assert.match(String(res.body.sources.diamondRod.field), /totalDiamond/);
    assert.deepEqual(
      res.body.rejectedSources,
      ['fishit-tracker', 'liveTrackDB', 'fishitGlobalDb', 'quiz_bot'],
    );
    for (const key of ['totalFish', 'totalSecret', 'totalForgotten', 'ghostfinnRod', 'elementRod', 'diamondRod']) {
      assert.ok(res.body.sources[key], 'missing source proof for ' + key);
      assert.match(String(res.body.sources[key].service), /fishitDb/);
      assert.doesNotMatch(String(res.body.sources[key].service), /fishit-tracker|liveTrackDB|fishitGlobalDb/i);
    }
    assert.equal('trackedFishers' in res.body, false);
    assert.equal('onlineFishers' in res.body, false);
    assert.equal('globalSpecies' in res.body, false);
  });

  test('Fish It public-summary reads bot getGlobal totals when DB is available', () => {
    const original = fishitDb.getGlobal;
    fishitDb.getGlobal = () => ({
      available: true,
      total_fish: 12345,
      secret_fish: 678,
      forgotten_fish: 90,
      last_updated: '2099-01-01T00:00:00.000Z',
      rods: { ghostfinn: 20, element: 15, diamond: 8 },
    });
    try {
      const g = fishitDb.getGlobal();
      assert.equal(g.total_fish, 12345);
      assert.equal(g.rods.ghostfinn, 20);
    } finally {
      fishitDb.getGlobal = original;
    }
  });

  test('tracker public stats stay separate from bot getGlobal totals', () => {
    const original = fishitDb.getGlobal;
    fishitDb.getGlobal = () => ({
      available: true,
      total_players: 999999,
      total_fish: 888888,
      last_updated: '2099-01-01T00:00:00.000Z',
    });
    try {
      const tracker = trackerRoutes.collectPublicFishItTrackerStats();
      assert.notEqual(tracker.trackedFishers, 999999);
      assert.notEqual(tracker.fishTracked, 888888);
    } finally {
      fishitDb.getGlobal = original;
    }
  });

  test('landing page keeps Active Devices inside Live Network row', async () => {
    const res = await request(app).get('/');
    assert.equal(res.status, 200);
    assert.doesNotMatch(res.text, /Rejoin Tool Stats/);
    assert.match(res.text, /Live Network/);
    assert.match(res.text, /data-home-stat-card="rejoinActiveDevices"/);
    assert.match(res.text, /Rejoin Tools Running/);
    assert.doesNotMatch(res.text, /data-home-stat-card="activeAgents"/);
    assert.doesNotMatch(res.text, /Tracker Devices Running/);
    const liveGridMatch = res.text.match(/data-home-live-stats-grid[\s\S]*?<\/div>\s*<p class="deng-home-stats-empty" data-home-live-stats-empty/);
    assert.ok(liveGridMatch, 'expected Live Network stat grid');
    assert.match(liveGridMatch[0], /data-home-stat-card="rejoinActiveDevices"/);
    assert.doesNotMatch(res.text, /data-home-rejoin-stats-grid/);
  });

  test('landing Fish It Stats shows bot catch/rod cards only', async () => {
    const res = await request(app).get('/');
    assert.equal(res.status, 200);
    assert.match(res.text, /Total Fish/);
    assert.match(res.text, /Total Secret/);
    assert.match(res.text, /Total Forgotten/);
    assert.match(res.text, /Ghostfinn Rod/);
    assert.match(res.text, /Element Rod/);
    assert.match(res.text, /Diamond Rod/);
    assert.match(res.text, /Fish caught in DENG Fish It Bot/);
    assert.match(res.text, /data-home-stat-card="totalFish"/);
    assert.match(res.text, /data-home-stat-card="ghostfinnRod"/);
    assert.doesNotMatch(res.text, /data-home-stat-card="trackedFishers"/);
    assert.doesNotMatch(res.text, /data-home-stat-card="onlineFishers"/);
    assert.doesNotMatch(res.text, /data-home-stat-card="inventoriesSynced"/);
    assert.doesNotMatch(res.text, /data-home-stat-card="fishTracked"/);
    assert.doesNotMatch(res.text, /data-home-stat-card="globalSpecies"/);
    assert.doesNotMatch(res.text, /Tracked Fishers/);
    assert.doesNotMatch(res.text, /Global Species/);
  });

  test('landing stat cards use slower count-up duration config', async () => {
    const res = await request(app).get('/');
    assert.match(res.text, /data-count-duration="1800"/);
    assert.doesNotMatch(res.text, /data-count-duration="750"/);
  });
});
