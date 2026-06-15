'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-domain-suite';
process.env.TOOL_SITE_STATE_SECRET = 'test-state-secret-that-is-long-enough-for-domain-suite';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = 'test-discord-client-id';
process.env.DISCORD_CLIENT_SECRET = 'test-discord-client-secret';
process.env.DISCORD_REDIRECT_URI = 'https://aio.deng.my.id/api/aio/auth/callback';
process.env.DISCORD_AIO_WEB_REDIRECT_URI = 'https://aio.deng.my.id/auth/discord/callback';
process.env.TOOL_SITE_PUBLIC_URL = 'https://aio.deng.my.id';
process.env.TOOL_SITE_INTERNAL_URL = 'https://tool.deng.my.id';
process.env.LINKVERTISE_COMPLETE_URL = 'https://tool.deng.my.id/unlock/linkvertise/complete';
process.env.LOOTLABS_COMPLETE_URL = 'https://tool.deng.my.id/unlock/lootlabs/complete';

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

const mockSupabase = { from(table) { return new MemoryQuery(table); } };

require.cache[path.join(__dirname, '..', 'src', 'db.js')] = {
  id: path.join(__dirname, '..', 'src', 'db.js'),
  filename: path.join(__dirname, '..', 'src', 'db.js'),
  loaded: true,
  exports: mockSupabase,
};
require.cache[require.resolve('axios')] = {
  id: require.resolve('axios'),
  filename: require.resolve('axios'),
  loaded: true,
  exports: fakeAxios,
};

const request = require('supertest');
const {
  canonicalPublicUrl,
  internalApiBaseUrl,
  resolveDiscordRedirectUri,
  isLegacyPublicRedirectPath,
  isApiOrInternalPath,
} = require('../src/publicDomain');
const { buildDiscordAuthUrl } = require('../src/auth');

function clearAppCache() {
  delete require.cache[require.resolve('../src/app')];
}

describe('public domain migration', () => {
  beforeEach(() => {
    memoryDb.site_users = [];
    clearAppCache();
  });

  test('canonicalPublicUrl defaults to aio host', () => {
    delete process.env.TOOL_SITE_PUBLIC_URL;
    delete require.cache[require.resolve('../src/publicDomain')];
    const pd = require('../src/publicDomain');
    assert.equal(pd.canonicalPublicUrl(), 'https://aio.deng.my.id');
    process.env.TOOL_SITE_PUBLIC_URL = 'https://aio.deng.my.id';
  });

  test('internalApiBaseUrl stays on tool host for unlock/API assets', () => {
    assert.equal(internalApiBaseUrl(), 'https://tool.deng.my.id');
  });

  test('resolveDiscordRedirectUri uses aio public callback for all hosts', () => {
    const aioReq = { headers: { host: 'aio.deng.my.id' } };
    const toolReq = { headers: { host: 'tool.deng.my.id' } };
    assert.equal(resolveDiscordRedirectUri(aioReq), 'https://aio.deng.my.id/api/aio/auth/callback');
    assert.equal(resolveDiscordRedirectUri(toolReq), 'https://aio.deng.my.id/api/aio/auth/callback');
  });

  test('legacy public paths are redirectable; API paths are not', () => {
    assert.equal(isLegacyPublicRedirectPath('/login'), true);
    assert.equal(isLegacyPublicRedirectPath('/tracker'), true);
    assert.equal(isApiOrInternalPath('/api/fishit-tracker/update-backpack'), true);
    assert.equal(isApiOrInternalPath('/unlock/lootlabs/complete'), true);
    assert.equal(isApiOrInternalPath('/auth/discord/callback'), true);
    assert.equal(isApiOrInternalPath('/health'), true);
    assert.equal(isApiOrInternalPath('/downloads/deng-all-in-one-apk-latest.apk'), true);
  });

  test('tool host redirects safe public pages to aio', async () => {
    const app = require('../src/app');
    const res = await request(app)
      .get('/login')
      .set('Host', 'tool.deng.my.id');
    assert.equal(res.status, 301);
    assert.equal(res.headers.location, 'https://aio.deng.my.id/login');
  });

  test('tool host keeps API endpoints without redirect', async () => {
    const app = require('../src/app');
    const health = await request(app)
      .get('/health')
      .set('Host', 'tool.deng.my.id');
    assert.equal(health.status, 200);
    assert.equal(health.body.service, 'deng-tool-site');
  });

  test('aio host serves public pages without redirect loop', async () => {
    const app = require('../src/app');
    const res = await request(app)
      .get('/')
      .set('Host', 'aio.deng.my.id');
    assert.equal(res.status, 200);
    assert.match(res.text, /aio\.deng\.my\.id/);
    assert.doesNotMatch(res.text, /tool\.deng\.my\.id/);
  });

  test('rendered download page uses canonical aio domain for public copy', async () => {
    const app = require('../src/app');
    const agent = request.agent(app);
    const start = await agent
      .get('/auth/discord')
      .set('Host', 'aio.deng.my.id');
    const state = new URL(start.headers.location).searchParams.get('state');
    assert.match(start.headers.location, /redirect_uri=https%3A%2F%2Faio\.deng\.my\.id%2Fapi%2Faio%2Fauth%2Fcallback/);
    const callback = await agent
      .get(`/api/aio/auth/callback?code=ok&state=${state}`)
      .set('Host', 'aio.deng.my.id');
    assert.equal(callback.status, 302);
    assert.match(callback.headers.location, /\/tracker/);
    const download = await agent.get('/download').set('Host', 'aio.deng.my.id');
    assert.equal(download.status, 200);
    assert.match(download.text, /Only download from https:\/\/aio\.deng\.my\.id\/download/);
    assert.match(download.text, /DENG All In One/);
  });

  test('aio /auth/discord starts OAuth directly with aio callback', async () => {
    const app = require('../src/app');
    const res = await request(app)
      .get('/auth/discord')
      .set('Host', 'aio.deng.my.id');
    assert.equal(res.status, 302);
    assert.match(res.headers.location, /^https:\/\/discord\.com\/api\/v10\/oauth2\/authorize\?/);
    assert.match(res.headers.location, /redirect_uri=https%3A%2F%2Faio\.deng\.my\.id%2Fapi%2Faio%2Fauth%2Fcallback/);
    assert.doesNotMatch(res.headers.location, /tool\.deng\.my\.id/);
  });

  test('tool OAuth start uses aio callback redirect_uri', async () => {
    const app = require('../src/app');
    const res = await request(app)
      .get('/auth/discord?public_return=1')
      .set('Host', 'tool.deng.my.id');
    assert.equal(res.status, 302);
    assert.match(res.headers.location, /redirect_uri=https%3A%2F%2Faio\.deng\.my\.id%2Fapi%2Faio%2Fauth%2Fcallback/);
  });
});
