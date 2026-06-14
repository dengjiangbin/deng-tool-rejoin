'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const fs = require('fs');
const os = require('os');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-oauth-session-suite';
process.env.TOOL_SITE_STATE_SECRET = 'test-state-secret-that-is-long-enough-for-oauth-session-suite';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = 'test-discord-client-id';
process.env.DISCORD_CLIENT_SECRET = 'test-discord-client-secret';
process.env.DISCORD_REDIRECT_URI = 'https://aio.deng.my.id/api/aio/auth/callback';
process.env.DISCORD_AIO_WEB_REDIRECT_URI = 'https://aio.deng.my.id/auth/discord/callback';
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
  buildSessionCookieOptions,
  describeSessionCookieConfig,
} = require('../src/sessionCookieConfig');

function clearAppCache() {
  delete require.cache[require.resolve('../src/app')];
  delete require.cache[require.resolve('../src/oauthRoutes')];
  delete require.cache[require.resolve('../src/discordOAuthCallback')];
  delete require.cache[require.resolve('../src/auth')];
  delete require.cache[require.resolve('../src/sessionCookieConfig')];
}

function parseSetCookie(headers) {
  const raw = headers['set-cookie'];
  if (!raw) return [];
  return Array.isArray(raw) ? raw : [raw];
}

describe('OAuth session persistence', () => {
  beforeEach(() => {
    memoryDb.site_users = [];
    clearAppCache();
    process.env.NODE_ENV = 'test';
  });

  test('production cookie config is host-only with secure auto and SameSite=Lax', () => {
    process.env.NODE_ENV = 'production';
    delete process.env.TOOL_SITE_COOKIE_DOMAIN;
    clearAppCache();
    const cookie = buildSessionCookieOptions();
    assert.equal(cookie.httpOnly, true);
    assert.equal(cookie.sameSite, 'lax');
    assert.equal(cookie.secure, 'auto');
    assert.equal(cookie.domain, undefined);
    const described = describeSessionCookieConfig();
    assert.equal(described.hostOnly, true);
    assert.equal(described.domain, null);
    process.env.NODE_ENV = 'test';
  });

  test('callback on preferred route sets deng_sid cookie and reaches protected /dashboard', async () => {
    const app = require('../src/app');
    const agent = request.agent(app);
    const start = await agent.get('/auth/discord?return=/dashboard').set('Host', 'aio.deng.my.id');
    const state = new URL(start.headers.location).searchParams.get('state');
    const cb = await agent
      .get(`/api/aio/auth/callback?code=ok&state=${state}`)
      .set('Host', 'aio.deng.my.id')
      .set('X-Forwarded-Proto', 'https');
    assert.equal(cb.status, 302);
    assert.equal(cb.headers.location, '/dashboard');
    const cookies = parseSetCookie(cb.headers);
    assert.ok(cookies.some((c) => c.startsWith('deng_sid=')), 'callback must emit deng_sid Set-Cookie');
    assert.ok(cookies.some((c) => /HttpOnly/i.test(c)), 'cookie must be HttpOnly');
    assert.ok(cookies.some((c) => /SameSite=Lax/i.test(c)), 'cookie must be SameSite=Lax');
    assert.ok(cookies.every((c) => !/Domain=/i.test(c)), 'cookie must be host-only (no Domain attribute)');

    const dashboard = await agent.get('/dashboard').set('Host', 'aio.deng.my.id');
    assert.equal(dashboard.status, 200, 'authenticated /dashboard must load');
  });

  test('alternate callback alias delegates to shared handler', async () => {
    const app = require('../src/app');
    const agent = request.agent(app);
    const state = oauthStateStore.createOAuthState({
      redirectUri: 'https://aio.deng.my.id/auth/discord/callback',
      authReturnTo: '/dashboard',
    });
    const cb = await agent
      .get(`/auth/discord/callback?code=ok&state=${state}`)
      .set('Host', 'aio.deng.my.id')
      .set('X-Forwarded-Proto', 'https');
    assert.equal(cb.status, 302);
    assert.match(cb.headers.location, /\/dashboard/);
    const dash = await agent.get('/dashboard').set('Host', 'aio.deng.my.id');
    assert.equal(dash.status, 200);
  });

  test('proxy HTTPS simulation: req.secure via X-Forwarded-Proto on auth-probe', async () => {
    process.env.TOOL_SITE_TRUST_PROXY = '1';
    clearAppCache();
    const app = require('../src/app');
    const res = await request(app)
      .get('/api/internal/auth-probe')
      .set('Host', 'aio.deng.my.id')
      .set('X-Forwarded-Proto', 'https');
    assert.equal(res.status, 200);
    assert.equal(res.body.secure, true);
    assert.equal(res.body.protocol, 'https');
    assert.equal(res.body.host, 'aio.deng.my.id');
    assert.equal(res.body.cookieConfig.hostOnly, true);
    delete process.env.TOOL_SITE_TRUST_PROXY;
    clearAppCache();
  });

  test('file-backed oauth state survives module reload between start and callback', () => {
    process.env.NODE_ENV = 'production';
    process.env.OAUTH_STATE_DIR = path.join(os.tmpdir(), `oauth-state-test-${Date.now()}`);
    delete require.cache[require.resolve('../src/oauthStateStore')];
    const store = require('../src/oauthStateStore');
    const state = store.createOAuthState({
      redirectUri: 'https://aio.deng.my.id/api/aio/auth/callback',
      authReturnTo: '/dashboard',
    });
    delete require.cache[require.resolve('../src/oauthStateStore')];
    const store2 = require('../src/oauthStateStore');
    const row = store2.consumeOAuthState(state);
    assert.ok(row);
    assert.equal(row.redirectUri, 'https://aio.deng.my.id/api/aio/auth/callback');
    process.env.NODE_ENV = 'test';
    delete process.env.OAUTH_STATE_DIR;
    delete require.cache[require.resolve('../src/oauthStateStore')];
  });
});
