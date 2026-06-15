'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-oauth-suite';
process.env.TOOL_SITE_STATE_SECRET = 'test-state-secret-that-is-long-enough-for-oauth-suite';
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
const { resolveDiscordRedirectUri, preferredOAuthCallbackUri } = require('../src/publicDomain');

function clearAppCache() {
  delete require.cache[require.resolve('../src/app')];
  delete require.cache[require.resolve('../src/oauthRoutes')];
  delete require.cache[require.resolve('../src/discordOAuthCallback')];
  delete require.cache[require.resolve('../src/auth')];
}

describe('Discord OAuth fast path', () => {
  beforeEach(() => {
    memoryDb.site_users = [];
    clearAppCache();
  });

  test('redirect URI uses aio public callback domain', () => {
    const aioReq = { headers: { host: 'aio.deng.my.id' } };
    assert.equal(resolveDiscordRedirectUri(aioReq), 'https://aio.deng.my.id/api/aio/auth/callback');
    assert.equal(preferredOAuthCallbackUri(), 'https://aio.deng.my.id/api/aio/auth/callback');
  });

  test('aio login start redirects to Discord without tool hop', async () => {
    const app = require('../src/app');
    const t0 = Date.now();
    const res = await request(app)
      .get('/auth/discord')
      .set('Host', 'aio.deng.my.id');
    assert.equal(res.status, 302);
    assert.match(res.headers.location, /discord\.com\/api\/v10\/oauth2\/authorize/);
    assert.match(res.headers.location, /redirect_uri=https%3A%2F%2Faio\.deng\.my\.id%2Fapi%2Faio%2Fauth%2Fcallback/);
    assert.ok(Date.now() - t0 < 3000, 'OAuth start should not block on session file write');
  });

  test('callback validates server-side state and completes same-host session', async () => {
    const app = require('../src/app');
    const agent = request.agent(app);
    const start = await agent
      .get('/auth/discord?public_return=1')
      .set('Host', 'aio.deng.my.id');
    const state = new URL(start.headers.location).searchParams.get('state');
    assert.ok(state);
    const callback = await agent
      .get(`/api/aio/auth/callback?code=ok&state=${state}`)
      .set('Host', 'aio.deng.my.id');
    assert.equal(callback.status, 302);
    assert.match(callback.headers.location, /\/tracker/);
    assert.doesNotMatch(callback.headers.location, /\/login/);
  });

  test('oauth state is single-use', () => {
    const state = oauthStateStore.createOAuthState({
      redirectUri: 'https://aio.deng.my.id/api/aio/auth/callback',
      returnPublicUrl: 'https://aio.deng.my.id',
    });
    assert.ok(oauthStateStore.consumeOAuthState(state));
    assert.equal(oauthStateStore.consumeOAuthState(state), null);
  });
});
