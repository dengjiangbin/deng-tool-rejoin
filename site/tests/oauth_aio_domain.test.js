'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-oauth-aio-suite';
process.env.TOOL_SITE_STATE_SECRET = 'test-state-secret-that-is-long-enough-for-oauth-aio-suite';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = 'test-discord-client-id';
process.env.DISCORD_CLIENT_SECRET = 'test-discord-client-secret';
process.env.DISCORD_REDIRECT_URI = 'https://aio.deng.my.id/api/aio/auth/callback';
process.env.DISCORD_AIO_WEB_REDIRECT_URI = 'https://aio.deng.my.id/auth/discord/callback';
process.env.DISCORD_AIO_REDIRECT_URI = 'https://aio.deng.my.id/api/aio/auth/callback';
process.env.TOOL_SITE_PUBLIC_URL = 'https://aio.deng.my.id';
process.env.TOOL_SITE_INTERNAL_URL = 'https://tool.deng.my.id';

const fakeAxios = {
  async post() {
    return { data: { access_token: 'discord-access-token' } };
  },
  async get() {
    return {
      data: { id: 'discord-user-1', username: 'DiscordTester', avatar: null, email: null },
    };
  },
};

const crypto = require('node:crypto');
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

require.cache[path.join(__dirname, '..', 'src', 'db.js')] = {
  id: path.join(__dirname, '..', 'src', 'db.js'),
  filename: path.join(__dirname, '..', 'src', 'db.js'),
  loaded: true,
  exports: { from(table) { return new MemoryQuery(table); } },
};
require.cache[require.resolve('axios')] = {
  id: require.resolve('axios'),
  filename: require.resolve('axios'),
  loaded: true,
  exports: fakeAxios,
};

const request = require('supertest');
const oauthStateStore = require('../src/oauthStateStore');
const {
  preferredOAuthCallbackUri,
  alternateOAuthCallbackUri,
  resolveDiscordRedirectUri,
  isOAuthCallbackPath,
} = require('../src/publicDomain');
const { safeReturnPath } = require('../src/auth');

function clearAppCache() {
  delete require.cache[require.resolve('../src/app')];
  delete require.cache[require.resolve('../src/oauthRoutes')];
  delete require.cache[require.resolve('../src/discordOAuthCallback')];
  delete require.cache[require.resolve('../src/auth')];
  delete require.cache[require.resolve('../src/publicDomain')];
}

async function completeOAuth(agent, callbackPath, startLocation) {
  const state = new URL(startLocation).searchParams.get('state');
  assert.ok(state, 'OAuth start must include state');
  return agent
    .get(`${callbackPath}?code=ok&state=${state}`)
    .set('Host', 'aio.deng.my.id');
}

describe('OAuth aio domain migration', () => {
  beforeEach(() => {
    memoryDb.site_users = [];
    clearAppCache();
    process.env.DISCORD_REDIRECT_URI = 'https://aio.deng.my.id/api/aio/auth/callback';
    process.env.DISCORD_AIO_WEB_REDIRECT_URI = 'https://aio.deng.my.id/auth/discord/callback';
  });

  test('A: login authorize URL uses aio redirect URI, not tool', async () => {
    const app = require('../src/app');
    const res = await request(app)
      .get('/auth/discord')
      .set('Host', 'aio.deng.my.id');
    assert.equal(res.status, 302);
    assert.match(res.headers.location, /redirect_uri=https%3A%2F%2Faio\.deng\.my\.id%2Fapi%2Faio%2Fauth%2Fcallback/);
    assert.doesNotMatch(res.headers.location, /tool\.deng\.my\.id/);
  });

  test('B/C: both callback routes are public and reach shared handler', async () => {
    const app = require('../src/app');
    const agent = request.agent(app);

    const start = await agent.get('/auth/discord').set('Host', 'aio.deng.my.id');
    const preferredCb = await completeOAuth(agent, '/api/aio/auth/callback', start.headers.location);
    assert.equal(preferredCb.status, 302);
    assert.match(preferredCb.headers.location, /\/dashboard/);
    assert.doesNotMatch(preferredCb.headers.location, /\/login/);

    clearAppCache();
    const app2 = require('../src/app');
    const agent2 = request.agent(app2);
    const state = oauthStateStore.createOAuthState({
      redirectUri: alternateOAuthCallbackUri(),
      authReturnTo: '/tracker',
    });
    const altCb = await agent2
      .get(`/auth/discord/callback?code=ok&state=${state}`)
      .set('Host', 'aio.deng.my.id');
    assert.equal(altCb.status, 302);
    assert.match(altCb.headers.location, /\/tracker/);
    assert.doesNotMatch(altCb.headers.location, /\/login/);
  });

  test('D/E: successful OAuth creates session and persists to protected page', async () => {
    const app = require('../src/app');
    const agent = request.agent(app);
    const start = await agent.get('/auth/discord?return=/tracker').set('Host', 'aio.deng.my.id');
    const cb = await completeOAuth(agent, '/api/aio/auth/callback', start.headers.location);
    assert.equal(cb.status, 302);
    assert.equal(cb.headers.location, '/tracker');

    const tracker = await agent.get('/tracker').set('Host', 'aio.deng.my.id');
    assert.notEqual(tracker.status, 302, 'authenticated user must not bounce to login');
    assert.notEqual(tracker.headers.location, '/login');
  });

  test('F: production session cookie config is secure host-only on aio domain', () => {
    process.env.NODE_ENV = 'production';
    delete process.env.TOOL_SITE_COOKIE_DOMAIN;
    delete require.cache[require.resolve('../src/sessionCookieConfig')];
    const { describeSessionCookieConfig } = require('../src/sessionCookieConfig');
    const app = require('../src/app');
    assert.ok(app);
    const cookie = describeSessionCookieConfig();
    assert.equal(cookie.hostOnly, true);
    assert.equal(cookie.domain, null);
    assert.equal(cookie.secure, 'auto');
    assert.equal(preferredOAuthCallbackUri(), 'https://aio.deng.my.id/api/aio/auth/callback');
    assert.equal(alternateOAuthCallbackUri(), 'https://aio.deng.my.id/auth/discord/callback');
    assert.equal(resolveDiscordRedirectUri({ headers: { host: 'aio.deng.my.id' } }), preferredOAuthCallbackUri());
    assert.equal(isOAuthCallbackPath('/api/aio/auth/callback'), true);
    assert.equal(isOAuthCallbackPath('/auth/discord/callback'), true);
    process.env.NODE_ENV = 'test';
  });

  test('G: login page HTML has no visible tool OAuth links', async () => {
    const app = require('../src/app');
    const res = await request(app)
      .get('/login')
      .set('Host', 'aio.deng.my.id');
    assert.equal(res.status, 200);
    assert.match(res.text, /DENG All In One/);
    assert.match(res.text, /\/auth\/discord/);
    assert.doesNotMatch(res.text, /tool\.deng\.my\.id/);
  });

  test('H: APK auth start uses same public aio callback domain', async () => {
    const app = require('../src/app');
    const res = await request(app)
      .get('/api/aio/auth/start')
      .set('Host', 'aio.deng.my.id');
    assert.equal(res.status, 302);
    assert.match(res.headers.location, /redirect_uri=https%3A%2F%2Faio\.deng\.my\.id%2Fapi%2Faio%2Fauth%2Fcallback/);
    assert.doesNotMatch(res.headers.location, /tool\.deng\.my\.id/);
  });

  test('safeReturnPath rejects external domains and normalizes aio URLs', () => {
    assert.equal(safeReturnPath('/tracker'), '/tracker');
    assert.equal(safeReturnPath('https://aio.deng.my.id/tracker'), '/tracker');
    assert.equal(safeReturnPath('https://tool.deng.my.id/dashboard'), '/dashboard');
    assert.equal(safeReturnPath('https://evil.example/phish'), null);
    assert.equal(safeReturnPath('/login'), null);
    assert.equal(safeReturnPath('https://aio.deng.my.id/login'), null);
  });

  test('callback without code does not require prior login redirect loop', async () => {
    const app = require('../src/app');
    const res = await request(app)
      .get('/api/aio/auth/callback?error=access_denied')
      .set('Host', 'aio.deng.my.id');
    assert.equal(res.status, 302);
    assert.match(res.headers.location, /\/login/);
  });
});
