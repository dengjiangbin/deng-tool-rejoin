'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const path = require('node:path');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-the-site-suite';
process.env.TOOL_SITE_STATE_SECRET = 'test-state-secret-that-is-long-enough-for-challenge-suite';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = process.env.DISCORD_CLIENT_ID || 'test-discord-client-id';
process.env.DISCORD_CLIENT_SECRET = process.env.DISCORD_CLIENT_SECRET || 'test-discord-client-secret';
process.env.DISCORD_REDIRECT_URI = process.env.DISCORD_REDIRECT_URI || 'http://localhost:8791/auth/discord/callback';

const fakeAxios = {
  async post() {
    return { data: { access_token: 'discord-access-token' } };
  },
  async get() {
    return {
      data: {
        id: 'discord-user-1',
        username: 'DiscordTester',
        avatar: null,
        email: null,
      },
    };
  },
};

const memoryDb = { site_users: [] };

class MemoryQuery {
  constructor(table) {
    this.table = table;
    this.filters = [];
    this.action = 'select';
    this.payload = null;
  }

  select() { return this; }
  insert(payload) { this.action = 'insert'; this.payload = Array.isArray(payload) ? payload : [payload]; return this; }
  update(payload) { this.action = 'update'; this.payload = payload; return this; }
  eq(field, value) { this.filters.push({ field, value }); return this; }
  maybeSingle() { return this._run().then(({ data }) => ({ data: data[0] || null, error: null })); }
  single() { return this._run().then(({ data }) => (data[0] ? { data: data[0], error: null } : { data: null, error: { message: 'no rows' } })); }
  then(resolve, reject) { return this._run().then(resolve, reject); }

  _matches(row) {
    return this.filters.every((f) => row[f.field] === f.value);
  }

  async _run() {
    const rows = memoryDb[this.table] || (memoryDb[this.table] = []);
    const now = new Date().toISOString();
    if (this.action === 'insert') {
      const inserted = this.payload.map((row) => {
        const next = { id: row.id || crypto.randomUUID(), created_at: now, updated_at: now, ...row };
        rows.push(next);
        return next;
      });
      return { data: inserted, error: null };
    }
    if (this.action === 'update') {
      const updated = [];
      for (const row of rows) {
        if (this._matches(row)) {
          Object.assign(row, this.payload, { updated_at: now });
          updated.push(row);
        }
      }
      return { data: updated, error: null };
    }
    return { data: rows.filter((row) => this._matches(row)), error: null };
  }
}

const mockSupabase = { from(table) { return new MemoryQuery(table); } };

const dbPath = path.join(__dirname, '..', 'src', 'db.js');
require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true, exports: mockSupabase };
require.cache[require.resolve('axios')] = {
  id: require.resolve('axios'),
  filename: require.resolve('axios'),
  loaded: true,
  exports: fakeAxios,
};

const request = require('supertest');
const app = require('../src/app');

async function login(agent) {
  const start = await agent.get('/auth/discord');
  assert.equal(start.status, 302);
  const state = new URL(start.headers.location).searchParams.get('state');
  const res = await agent.get(`/auth/discord/callback?code=ok&state=${state}`);
  assert.equal(res.status, 302);
  assert.equal(res.headers.location, '/tracker');
}

describe('public home landing page', () => {
  test('GET / logged out returns landing page, not login', async () => {
    const res = await request(app).get('/');
    assert.equal(res.status, 200);
    assert.match(res.text, /<title>DENG All In One - Roblox Automation &amp; Stat Tracker<\/title>/);
    assert.match(res.text, /class="deng-home"/);
    assert.match(res.text, /Live Network/);
    assert.match(res.text, /Platform Stats/);
    assert.match(res.text, /Fish It Stats/);
    assert.match(res.text, /Tracked Players/);
    assert.doesNotMatch(res.text, /Rejoin Tool Stats/);
    assert.match(res.text, /Active Devices/);
    assert.match(res.text, /Rejoin agents active/);
    assert.match(res.text, /One platform\. Multiple modules\./);
    assert.match(res.text, /deng-home-nav-wrap--fixed/);
    assert.match(res.text, /href="#home"[^>]*>Home<\/a>/);
    assert.match(res.text, /href="#statistic">Statistic<\/a>/);
    assert.match(res.text, /href="#about">About<\/a>/);
    assert.match(res.text, /data-home-live-stats-grid/);
    const liveGridMatch = res.text.match(/data-home-live-stats-grid[\s\S]*?<\/div>\s*<p class="deng-home-stats-empty" data-home-live-stats-empty/);
    assert.ok(liveGridMatch, 'expected Live Network stat grid');
    assert.match(liveGridMatch[0], /data-home-stat-card="trackedPlayers"/);
    assert.match(liveGridMatch[0], /data-home-stat-card="onlineNow"/);
    // Active Devices was moved out of Live Network into Platform Stats.
    assert.doesNotMatch(liveGridMatch[0], /data-home-stat-card="rejoinActiveDevices"/);
    const platformGridMatch = res.text.match(/data-home-platform-stats-grid[\s\S]*?<\/div>\s*<p class="deng-home-stats-empty" data-home-platform-stats-empty/);
    assert.ok(platformGridMatch, 'expected Platform Stats grid');
    assert.match(platformGridMatch[0], /data-home-stat-card="rejoinActiveDevices"/);
    // Redeemed Keys card was removed from the homepage.
    assert.doesNotMatch(platformGridMatch[0], /data-home-stat-card="redeemedKeys"/);
    assert.match(res.text, /data-home-stat-card="rejoinActiveDevices"/);
    assert.match(res.text, /data-home-stat-card="caught24Hours"/);
    assert.match(res.text, /data-home-stat-card="ghostfinnRod"/);
    assert.match(res.text, /data-home-stat-meta="onlineNow"/);
    assert.match(res.text, /Rejoin agents active/);
    assert.match(res.text, /deng-home-section--tight/);
    assert.match(res.text, /class="[^"]*js-count-up[^"]*"[^>]*data-home-stat-value="trackedPlayers"/);
    assert.match(res.text, /data-home-stat-value="rejoinActiveDevices"/);
    assert.match(res.text, /data-home-stat-value="rejoinTotalDevices"/);
    assert.doesNotMatch(res.text, /data-home-stat-card="activeAgents"/);
    assert.doesNotMatch(res.text, /data-home-stat-value="activeAgents"/);
    assert.doesNotMatch(res.text, /Tracker Devices Running/);
    assert.match(res.text, /count-up-stats\.js/);
    assert.match(res.text, /hero-wordmark/);
    assert.match(res.text, /aria-label="Go to home"/);
    assert.doesNotMatch(res.text, /Welcome back/);
    assert.doesNotMatch(res.text, /Sign in with Discord/);
    assert.doesNotMatch(res.text, /login-page--split/);
    assert.doesNotMatch(res.text, /Purchase via Discord/i);
    assert.doesNotMatch(res.text, /href="\/download"/);
    assert.doesNotMatch(res.text, /login-page__card/);
    assert.doesNotMatch(res.text, /theme-toggle-floating/);
    assert.doesNotMatch(res.text, /BLOCKER|DEBUG/i);
    assert.doesNotMatch(res.text, /Active devices online/i);
    assert.match(res.headers['cache-control'], /no-store/i);
  });

  test('GET /login logged out returns dedicated login page', async () => {
    const res = await request(app).get('/login');
    assert.equal(res.status, 200);
    assert.match(res.text, /<title>Sign In - DENG All In One<\/title>/);
    assert.match(res.text, /DENG All In One/);
    assert.doesNotMatch(res.text, /DENG Tool\b/);
    assert.match(res.text, /class="login-page login-page--split"/);
    assert.match(res.text, /Welcome back/);
    assert.match(res.text, /Sign in with Discord/);
    assert.match(res.text, /href="\/auth\/discord"/);
    assert.match(res.text, /login-page__back" href="\/">.*Back to Home<\/a>/);
    assert.doesNotMatch(res.text, /class="deng-home"/);
    assert.doesNotMatch(res.text, /data-home-live-stats-grid/);
    assert.doesNotMatch(res.text, /Purchase via Discord/i);
  });

  test('/login does not redirect to / when logged out', async () => {
    const res = await request(app).get('/login');
    assert.equal(res.status, 200);
    assert.notEqual(res.headers.location, '/');
  });

  test('/ does not redirect to /login when logged out', async () => {
    const res = await request(app).get('/');
    assert.equal(res.status, 200);
    assert.notEqual(res.headers.location, '/login');
  });

  test('GET /download logged out redirects to login with return path', async () => {
    const res = await request(app).get('/download');
    assert.equal(res.status, 302);
    assert.match(res.headers.location, /^\/login\?return=%2Fdownload$/);
  });

  test('protected /dashboard redirects unauthenticated users to /login', async () => {
    const res = await request(app).get('/dashboard');
    assert.equal(res.status, 302);
    assert.match(res.headers.location, /^\/login(\?return=%2Fdashboard)?$/);
  });

  test('public CTAs use login return links instead of direct private routes', async () => {
    const res = await request(app).get('/');
    assert.match(res.text, /href="\/login\?return=\/download">Visit agent/);
    assert.match(res.text, /href="\/login\?return=\/inventory">/);
    assert.match(res.text, /href="\/login\?return=\/dashboard">/);
  });

  test('home page uses home.js for live stats fetch', async () => {
    const res = await request(app).get('/');
    assert.match(res.text, /home\.js/);
    assert.doesNotMatch(res.text, /fishit-home\.js/);
  });
});

describe('authenticated logout wiring', () => {
  for (const route of ['/dashboard', '/license', '/fishit', '/download']) {
    test(`logged-in ${route} includes shared logout confirm assets`, async () => {
      const agent = request.agent(app);
      await login(agent);
      const res = await agent.get(route);
      assert.equal(res.status, 200);
      assert.match(res.text, /logoutConfirm\.js/);
      assert.match(res.text, /logoutConfirm\.css/);
      assert.match(res.text, /data-logout-confirm/);
      assert.match(res.text, /action="\/auth\/logout"/);
    });
  }

  test('logout from dashboard clears session and blocks protected pages', async () => {
    const agent = request.agent(app);
    await login(agent);
    const page = await agent.get('/dashboard');
    const csrfMatch = page.text.match(/name="_csrf" value="([^"]+)"/);
    assert.ok(csrfMatch, 'csrf token missing');
    const out = await agent.post('/auth/logout').type('form').send({ _csrf: csrfMatch[1] });
    assert.equal(out.status, 302);
    const blocked = await agent.get('/dashboard');
    assert.equal(blocked.status, 302);
    assert.match(blocked.headers.location, /^\/login/);
  });
});
