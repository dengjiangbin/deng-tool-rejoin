'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const express = require('express');
const session = require('express-session');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';

const { toSessionUser } = require('../src/auth');
const trackerRouter = require('../src/fishitTrackerRoutes');
const {
  buildTrackerPageLocals,
  buildInventoryViewer,
} = require('../src/fishitTrackerRoutes');
const {
  BLOCKER10ZTD_INVENTORY_ACCESS_SAFE_RENDER_MARKER,
  BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
} = require('../src/fishitTrackerBuild');

const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

function makeTrackerApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

function makeProductionApp() {
  const prevEnv = process.env.NODE_ENV;
  process.env.NODE_ENV = 'production';
  process.env.TOOL_SITE_COOKIE_SECRET = process.env.TOOL_SITE_COOKIE_SECRET
    || 'test-cookie-secret-at-least-32-chars-long';
  delete require.cache[require.resolve('../src/app')];
  const app = require('../src/app');
  process.env.NODE_ENV = prevEnv;
  return app;
}

function makeStaleDeployApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  app.use(session({
    name: 'deng_sid',
    secret: process.env.TOOL_SITE_COOKIE_SECRET || 'test-cookie-secret-at-least-32-chars-long',
    resave: false,
    saveUninitialized: false,
  }));
  app.use((_req, res) => {
    res.status(404).render('error', { code: 404, message: 'Page not found.' });
  });
  return app;
}

describe('BLOCKER10ZTD inventory access safe render', () => {
  test('deploy marker points at inventory access safe render build', () => {
    const { BLOCKER10ZTF_STAT_INTERVAL_SOURCE_HARDENING_MARKER } = require('../src/fishitTrackerBuild');
    assert.equal(BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, BLOCKER10ZTF_STAT_INTERVAL_SOURCE_HARDENING_MARKER);
  });

  test('buildInventoryViewer supports production-like session and missing optional fields', () => {
    const full = buildInventoryViewer(toSessionUser({
      id: 'site-user-1',
      username: 'denghub2',
      discord_user_id: '123456789012345678',
      discord_username: 'denghub2',
      discord_avatar: 'abc123',
      email: 'denghub2@example.com',
    }));
    assert.equal(full.name, 'denghub2');
    assert.equal(full.discordId, '123456789012345678');
    assert.match(full.avatarUrl, /cdn\.discordapp\.com\/avatars/);

    const sparse = buildInventoryViewer({ username: 'denghub2' });
    assert.equal(sparse.name, 'denghub2');
    assert.equal(sparse.initial, 'D');
    assert.equal(sparse.hasDiscordAvatar, false);
    assert.equal(sparse.avatarUrl, '');

    const empty = buildInventoryViewer(null);
    assert.equal(empty.name, 'Account');
    assert.equal(empty.initial, 'A');
  });

  test('buildTrackerPageLocals always includes safe viewer, scriptUrl, and logoutUrl', () => {
    const locals = buildTrackerPageLocals({
      session: {
        user: { username: 'denghub2', discord_user_id: '99', discord_avatar: 'av' },
        csrfToken: 'csrf-1',
      },
    });
    assert.ok(locals.viewer);
    assert.equal(locals.viewer.name, 'denghub2');
    assert.equal(locals.scriptUrl, '/inventory');
    assert.equal(locals.logoutUrl, '/auth/logout');
    assert.equal(locals.inventoryAccessProof.safeViewerLocals, true);
  });

  test('template uses viewer locals instead of raw user.discord_* access', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /viewer\.hasDiscordAvatar/);
    assert.match(tpl, /viewer\.name/);
    assert.doesNotMatch(tpl, /user\.discord_avatar/);
    assert.doesNotMatch(tpl, /user\.discord_user_id/);
  });

  test('authenticated /inventory and /inventory/ return 200 with inventory header', async () => {
    const app = makeTrackerApp();
    for (const route of ['/inventory', '/inventory/']) {
      const res = await request(app).get(route).expect(200);
      assert.match(res.text, /DENG Inventory Tracker/);
      assert.match(res.text, /Track Your Fish It Accounts/);
      assert.doesNotMatch(res.text, /An unexpected error occurred/);
      assert.doesNotMatch(res.text, />Guest</);
      assert.doesNotMatch(res.text, />Sign in</);
      assert.match(res.text, /inventory-profile-card__name/);
      assert.match(res.text, />Logout</);
    }
  });

  test('unauthenticated production /inventory redirects cleanly instead of 500', async () => {
    const prevEnv = process.env.NODE_ENV;
    process.env.NODE_ENV = 'production';
    const app = makeProductionApp();
    const res = await request(app).get('/inventory');
    assert.equal(res.status, 302);
    assert.match(res.headers.location, /^\/login\?return=%2Finventory$/);
    assert.doesNotMatch(res.text || '', /An unexpected error occurred/);
    process.env.NODE_ENV = prevEnv;
  });

  test('unauthenticated production /inventory/ redirects cleanly instead of 500', async () => {
    const prevEnv = process.env.NODE_ENV;
    process.env.NODE_ENV = 'production';
    const app = makeProductionApp();
    const res = await request(app).get('/inventory/');
    assert.equal(res.status, 302);
    assert.match(res.headers.location, /^\/login\?return=%2Finventory%2F$/);
    process.env.NODE_ENV = prevEnv;
  });

  test('stale tracker-before-session deploy still redirects instead of 500', async () => {
    const prevEnv = process.env.NODE_ENV;
    process.env.NODE_ENV = 'production';
    const app = makeStaleDeployApp();
    const res = await request(app).get('/inventory');
    assert.equal(res.status, 302);
    assert.match(res.headers.location, /^\/login\?return=%2Finventory$/);
    assert.notEqual(res.status, 500);
    process.env.NODE_ENV = prevEnv;
  });

  test('missing optional profile fields render inventory page without 500', async () => {
    const app = express();
    app.set('view engine', 'ejs');
    app.set('views', path.join(__dirname, '..', 'views'));
    app.use(session({
      secret: 'test-cookie-secret-at-least-32-chars-long',
      resave: false,
      saveUninitialized: false,
    }));
    app.use((req, _res, next) => {
      req.session.user = { username: 'denghub2' };
      req.session.csrfToken = 'csrf-sparse';
      next();
    });
    app.use(trackerRouter);
    app.use((err, _req, res, _next) => {
      res.status(500).send(String(err.message || err));
    });

    const res = await request(app).get('/inventory').expect(200);
    assert.match(res.text, /inventory-profile-card__name/);
    assert.match(res.text, /denghub2/);
    assert.doesNotMatch(res.text, /An unexpected error occurred/);
  });

  test('get-backpack returns refreshed coin, total caught, and rarest fish across 3 uploads', async () => {
    const app = makeTrackerApp();
    const username = 'ztdinventoryaccess';
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
          userId: 99300 + i,
          isOnline: true,
          clientOrigin: 'roblox_tracker',
          trackerBuild: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
          items: [{ itemId: String(i + 1), name: `Fish${i + 1}`, amount: 1, category: 'fish', rarity: 'Common' }],
          playerStats: {
            coins: p.coins,
            totalCaught: p.totalCaught,
            rarestFishChance: p.rarestFishChance,
            source: 'leaderstats',
            build: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
          },
        })
        .expect(200);
      const poll = await request(app)
        .get(`/api/fishit-tracker/get-backpack/${username}`)
        .expect(200);
      seen.push({
        coinsText: poll.body.playerStats && poll.body.playerStats.coinsText,
        totalCaughtText: poll.body.playerStats && poll.body.playerStats.totalCaughtText,
        rarestFishChance: poll.body.playerStats && poll.body.playerStats.rarestFishChance,
      });
    }
    assert.deepEqual(seen.map((row) => row.coinsText), ['100', '200', '300']);
    assert.deepEqual(seen.map((row) => row.totalCaughtText), ['1.000', '2.000', '3.000']);
    assert.deepEqual(seen.map((row) => row.rarestFishChance), ['1/100', '1/200', '1/300']);
  });
});
