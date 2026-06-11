'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
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
    assert.match(String(res.body.sources.caught24Hours.service), /fishitDb/);
    assert.match(String(res.body.sources.caught24Hours.store), /deng-fish-it-bot/);
    assert.equal(res.body.sources.caught24Hours.period, 'yesterday');
    assert.match(String(res.body.sources.caught24Hours.windowFrom), /^\d{4}-\d{2}-\d{2}T/);
    assert.match(String(res.body.sources.caught24Hours.windowTo), /^\d{4}-\d{2}-\d{2}T/);
    assert.equal(res.body.sources.caught24Hours.timezone, 'Asia/Jakarta');
    assert.ok(res.body.catchWindow, 'expected catchWindow proof object');
    assert.equal(res.body.catchWindow.period, 'yesterday');
    assert.equal(res.body.catchWindow.timezone, 'Asia/Jakarta');
    assert.match(String(res.body.sources.totalSecret.blob), /alltime_fish_cache/);
    assert.match(String(res.body.sources.totalForgotten.blob), /alltime_fish_cache/);
    assert.match(String(res.body.sources.ghostfinnRod.blob), /alltime_rod_cache/);
    assert.match(String(res.body.sources.elementRod.field), /totalElement/);
    assert.match(String(res.body.sources.diamondRod.field), /totalDiamond/);
    assert.deepEqual(
      res.body.rejectedSources,
      ['fishit-tracker', 'liveTrackDB', 'fishitGlobalDb', 'quiz_bot'],
    );
    for (const key of ['caught24Hours', 'totalSecret', 'totalForgotten', 'ghostfinnRod', 'elementRod', 'diamondRod']) {
      assert.ok(res.body.sources[key], 'missing source proof for ' + key);
      assert.match(String(res.body.sources[key].service), /fishitDb/);
      assert.doesNotMatch(String(res.body.sources[key].service), /fishit-tracker|liveTrackDB|fishitGlobalDb/i);
    }
    assert.equal('totalFish' in res.body, false);
    assert.equal('trackedFishers' in res.body, false);
    assert.equal('onlineFishers' in res.body, false);
    assert.equal('globalSpecies' in res.body, false);
  });

  test('Fish It public-summary yesterday caught uses bot byDate window not lifetime total', () => {
    const originalGetGlobal = fishitDb.getGlobal;
    const originalGetPeriod = fishitDb.getGlobalPeriodCaught;
    fishitDb.getGlobal = () => ({
      available: true,
      total_fish: 128823,
      secret_fish: 678,
      forgotten_fish: 90,
      last_updated: '2099-01-01T00:00:00.000Z',
      rods: { ghostfinn: 20, element: 15, diamond: 8 },
    });
    fishitDb.getGlobalPeriodCaught = () => ({
      period: 'yesterday',
      periodLabel: 'Yesterday',
      timezone: 'Asia/Jakarta',
      windowFrom: '2099-06-10T17:00:00.000Z',
      windowTo: '2099-06-11T17:00:00.000Z',
      count: 4217,
    });
    try {
      const g = fishitDb.getGlobal();
      const caught = fishitDb.getGlobalPeriodCaught('yesterday');
      assert.equal(g.total_fish, 128823);
      assert.equal(caught.count, 4217);
      assert.notEqual(caught.count, g.total_fish);
    } finally {
      fishitDb.getGlobal = originalGetGlobal;
      fishitDb.getGlobalPeriodCaught = originalGetPeriod;
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
    assert.match(res.text, /24 Hours Caught/);
    assert.doesNotMatch(res.text, /Total Fish/);
    assert.match(res.text, /Total Secret/);
    assert.match(res.text, /Total Forgotten/);
    assert.match(res.text, /Ghostfinn Rod/);
    assert.match(res.text, /Element Rod/);
    assert.match(res.text, /Diamond Rod/);
    assert.match(res.text, /Fish caught yesterday/);
    assert.doesNotMatch(res.text, /Fish caught in DENG Fish It Bot/);
    assert.match(res.text, /data-home-stat-card="caught24Hours"/);
    assert.match(res.text, /data-home-stat-card="ghostfinnRod"/);
    assert.doesNotMatch(res.text, /data-home-stat-card="trackedFishers"/);
    assert.doesNotMatch(res.text, /data-home-stat-card="onlineFishers"/);
    assert.doesNotMatch(res.text, /data-home-stat-card="inventoriesSynced"/);
    assert.doesNotMatch(res.text, /data-home-stat-card="fishTracked"/);
    assert.doesNotMatch(res.text, /data-home-stat-card="globalSpecies"/);
    assert.doesNotMatch(res.text, /Tracked Fishers/);
    assert.doesNotMatch(res.text, /Global Species/);
  });

  test('landing stat cards use Winter HUB count-up duration config', async () => {
    const res = await request(app).get('/');
    assert.match(res.text, /data-count-duration="1200"/);
    assert.doesNotMatch(res.text, /data-count-duration="750"/);
  });

  test('landing page omits removed DENG Tools network section', async () => {
    const res = await request(app).get('/');
    assert.doesNotMatch(res.text, /DENG Tools/);
    assert.doesNotMatch(res.text, /id="tools"/);
    assert.doesNotMatch(res.text, /deng-home-network-panel/);
    assert.doesNotMatch(res.text, /deng-home-node-grid/);
    assert.match(res.text, /id="about"/);
    assert.match(res.text, /One platform\. Multiple tools\./);
  });

  test('mobile homepage keeps Home Statistic About nav links in one row', () => {
    const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'css', 'home.css'), 'utf8');
    assert.match(css, /@media \(max-width: 860px\)[\s\S]*\.deng-home-nav__inner[\s\S]*display:\s*flex[\s\S]*flex-wrap:\s*nowrap/);
    assert.match(css, /@media \(max-width: 860px\)[\s\S]*\.deng-home-nav__links[\s\S]*flex-wrap:\s*nowrap/);
    assert.doesNotMatch(css, /@media \(max-width: 860px\)[\s\S]*\.deng-home-nav__links[\s\S]*grid-column:\s*1\s*\/\s*-1/);
    assert.doesNotMatch(css, /@media \(max-width: 860px\)[\s\S]*\.deng-home-nav__links[\s\S]*order:\s*3/);
  });
});
