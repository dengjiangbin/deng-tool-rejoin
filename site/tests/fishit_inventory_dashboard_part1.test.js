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

const fishitDb = require('../src/fishitDb');
const trackerRouter = require('../src/fishitTrackerRoutes');
const SOURCE_PATH = require('path').join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const fs = require('fs');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', require('path').join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('tracker dashboard part 1', () => {
  test('normalizeDashboardPeriod maps quick presets', () => {
    assert.equal(fishitDb.normalizeDashboardPeriod('ALL TIME'), 'all');
    assert.equal(fishitDb.normalizeDashboardPeriod('7d'), '7d');
    assert.equal(fishitDb.normalizeDashboardPeriod('30d'), '30d');
    assert.equal(fishitDb.normalizeDashboardPeriod('YTD'), 'ytd');
    assert.equal(fishitDb.normalizeDashboardPeriod('TDY'), 'tdy');
    assert.equal(fishitDb.normalizeDashboardPeriod('custom'), 'custom');
    assert.equal(fishitDb.normalizeDashboardPeriod(undefined), 'all');
  });

  test('getOwnerDashboard queries bot DB by Discord ID (same as !d / !s)', () => {
    const payload = fishitDb.getOwnerDashboard('123456789012345678', [], 'all');
    assert.equal(payload.scope, 'owner');
    assert.equal(payload.discordUserId, '123456789012345678');
    assert.equal(payload.trackedAccountCount, 0);
    assert.equal(payload.period, 'all');
    assert.equal(payload.available, false);
    assert.equal(payload.statsState, 'error');
    assert.equal(payload.emptyReason, 'bot_db_not_connected');
    assert.equal(payload.cards.secretCaught, 0);
    assert.equal(payload.cards.forgottenCaught, 0);
    assert.deepEqual(payload.fishCards, []);
  });

  test('sortFishCardsByRarity orders Secret before Forgotten then by count', () => {
    const sorted = fishitDb.sortFishCardsByRarity([
      { name: 'Alpha', rarity: 'Common', count: 99 },
      { name: 'Beta', rarity: 'Forgotten', count: 2 },
      { name: 'Gamma', rarity: 'Secret', count: 1 },
      { name: 'Delta', rarity: 'Secret', count: 5 },
    ]);
    assert.deepEqual(sorted.map((row) => `${row.rarity}:${row.name}`), [
      'Secret:Delta',
      'Secret:Gamma',
      'Forgotten:Beta',
      'Common:Alpha',
    ]);
  });

  test('GET /api/tracker/dashboard defaults to all-time range', async () => {
    const res = await request(makeApp()).get('/api/tracker/dashboard').expect(200);
    assert.equal(res.body.ok, true);
    assert.equal(res.body.period, 'all');
    assert.equal(res.body.debug, undefined);

    const debugRes = await request(makeApp()).get('/api/tracker/dashboard?debug=1').expect(200);
    assert.equal(debugRes.body.debug && debugRes.body.debug.selectedRange, 'all');
  });

  test('tracker page defaults dashboard period to ALL TIME', async () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /let dashboardPeriod = 'all'/);
    assert.match(source, /initDashboardDefaultPeriod/);
    assert.match(source, /data-dashboard-period="all"/);
    assert.match(source, /dashboard-period-filter__btn is-active" data-dashboard-period="all"/);

    const res = await request(makeApp()).get('/tracker').expect(200);
    assert.match(res.text, /data-dashboard-period="all"/);
    assert.match(res.text, /dashboard-period-filter__btn is-active" data-dashboard-period="all"/);
    assert.doesNotMatch(res.text, /dashboard-period-filter__btn is-active" data-dashboard-period="30d"/);
  });

  test('GET /api/tracker/dashboard returns owner-scoped payload', async () => {
    const res = await request(makeApp()).get('/api/tracker/dashboard?period=7d').expect(200);
    assert.equal(res.body.ok, true);
    assert.equal(res.body.period, '7d');
    assert.equal(res.body.scope, 'owner');
    assert.equal(res.body.discordUserId, '123456789012345678');
    assert.ok(res.body.cards);
    assert.ok(Array.isArray(res.body.fishCards));
    assert.ok(Array.isArray(res.body.dailyCaught));
  });

  test('dashboard period filter changes date range on API', async () => {
    const res7 = await request(makeApp()).get('/api/tracker/dashboard?period=7d').expect(200);
    const resAll = await request(makeApp()).get('/api/tracker/dashboard?period=all').expect(200);
    assert.equal(res7.body.period, '7d');
    assert.equal(resAll.body.period, 'all');
    assert.notEqual(res7.body.from, resAll.body.from);
  });

  test('custom dashboard period rejects invalid range', async () => {
    const res = await request(makeApp()).get('/api/tracker/dashboard?period=custom&from=2026-06-12&to=2026-06-01').expect(400);
    assert.equal(res.body.ok, false);
    assert.equal(res.body.error, 'invalid_custom_range');
  });

  test('tracker page includes compact top nav, line chart, and owner dashboard API', async () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /inventory-sidebar__top/);
    assert.match(source, /inventory-main-nav/);
    assert.match(source, /Live Tracker/);
    assert.match(source, /dashboard-chart-line/);
    assert.match(source, /dashboard-chart-point/);
    assert.match(source, /\/api\/tracker\/dashboard/);
    assert.match(source, /id="dashboardFishGrid"/);
    assert.doesNotMatch(source, /\/api\/inventory\/dashboard/);
    assert.doesNotMatch(source, /Total Fish Caught/);
    assert.match(source, /dashboardStatusNotice/);
    assert.match(source, /renderDashboardStatusNotice/);
    assert.match(source, /emptyReason/);

    const res = await request(makeApp()).get('/tracker').expect(200);
    assert.match(res.text, /Dashboard/);
    assert.match(res.text, /Live Tracker/);
    assert.match(res.text, /inventory-sidebar__top/);
    assert.match(res.text, /dashboardFishGrid/);
  });
});
