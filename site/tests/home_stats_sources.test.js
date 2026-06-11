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
  test('Fish It public-summary uses tracker/global catalog sources only', async () => {
    const res = await request(app).get('/api/fishit/public-summary');
    assert.equal(res.status, 200);
    assert.ok(res.body.sources, 'expected sources proof object');
    assert.match(String(res.body.sources.trackedFishers.service), /fishit-tracker/);
    assert.match(String(res.body.sources.onlineFishers.store), /liveTrackDB/);
    assert.match(String(res.body.sources.fishTracked.method), /lastGoodPublicFishCount|visibleFishInstances/);
    assert.match(String(res.body.sources.globalSpecies.service), /fishitGlobalDb/);
    assert.deepEqual(res.body.rejectedSources, ['fishitDb', 'deng_fish_it_bot', 'quiz_bot', '!d']);
    assert.doesNotMatch(JSON.stringify(res.body.sources), /fishitDb|deng_fish_it_bot|quiz_bot|!d/i);
    for (const key of ['trackedFishers', 'onlineFishers', 'inventoriesSynced', 'fishTracked']) {
      assert.ok(res.body.sources[key], 'missing source proof for ' + key);
    }
  });

  test('Fish It public-summary does not call bot getGlobal totals', () => {
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

  test('landing page keeps Rejoin active devices out of Live Network', async () => {
    const res = await request(app).get('/');
    assert.equal(res.status, 200);
    assert.match(res.text, /Rejoin Tool Stats/);
    assert.match(res.text, /data-home-stat-card="rejoinActiveDevices"/);
    assert.match(res.text, /Rejoin Tools Running/);
    assert.doesNotMatch(res.text, /data-home-stat-card="activeAgents"/);
    assert.doesNotMatch(res.text, /Tracker Devices Running/);
  });

  test('landing stat cards use slower count-up duration config', async () => {
    const res = await request(app).get('/');
    assert.match(res.text, /data-count-duration="1800"/);
    assert.doesNotMatch(res.text, /data-count-duration="750"/);
  });
});
