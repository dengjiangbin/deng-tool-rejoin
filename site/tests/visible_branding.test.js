'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const path = require('node:path');
const fs = require('node:fs');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-branding-suite';
process.env.TOOL_SITE_STATE_SECRET = 'test-state-secret-that-is-long-enough-for-branding-suite';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = 'test-discord-client-id';
process.env.DISCORD_CLIENT_SECRET = 'test-discord-client-secret';
process.env.DISCORD_REDIRECT_URI = 'http://localhost:8791/auth/discord/callback';
process.env.TOOL_SITE_PUBLIC_URL = 'https://aio.deng.my.id';
process.env.TOOL_SITE_INTERNAL_URL = 'https://tool.deng.my.id';
process.env.LINKVERTISE_COMPLETE_URL = 'http://localhost:8791/unlock/linkvertise/complete';
process.env.LOOTLABS_COMPLETE_URL = 'http://localhost:8791/unlock/lootlabs/complete';

const fakeAxios = {
  async post() { return { data: { access_token: 'discord-access-token' } }; },
  async get() {
    return { data: { id: 'discord-user-1', username: 'DiscordTester', avatar: null, email: null } };
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
  _matches(row) { return this.filters.every((f) => row[f.field] === f.value); }
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

const FORBIDDEN_VISIBLE = [
  /DENG Tool\b/i,
  /Tool Rejoin/i,
  /tool\.deng\.my\.id/i,
  /DENG Monitor/i,
  /DENG Tracker/i,
  /Tool Players/i,
  /Rejoin Tools Running/i,
  /Back to DENG Tool/i,
  /DENG All In One: Rejoin/i,
];

function visibleHtml(html) {
  return String(html || '')
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<style[\s\S]*?<\/style>/gi, '');
}

function assertNoLegacyBranding(html, label) {
  const visible = visibleHtml(html);
  for (const pattern of FORBIDDEN_VISIBLE) {
    assert.doesNotMatch(visible, pattern, `${label} must not expose legacy branding ${pattern}`);
  }
}

function clearAppCache() {
  delete require.cache[require.resolve('../src/app')];
}

async function login(agent) {
  const start = await agent.get('/auth/discord');
  const state = new URL(start.headers.location).searchParams.get('state');
  await agent.get(`/auth/discord/callback?code=ok&state=${state}`);
}

describe('visible branding regression', () => {
  beforeEach(() => {
    memoryDb.site_users = [];
    clearAppCache();
  });

  test('PWA manifest uses DENG All In One naming', () => {
    const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'public', 'site.webmanifest'), 'utf8'));
    assert.equal(manifest.name, 'DENG All In One');
    assert.equal(manifest.short_name, 'DENG AIO');
    assert.doesNotMatch(manifest.description, /DENG Tool/i);
    assert.doesNotMatch(JSON.stringify(manifest), /tool\.deng\.my\.id/i);
  });

  test('public pages render without legacy visible branding', async () => {
    const app = require('../src/app');
    const publicPaths = ['/', '/login', '/nope-branding-404'];
    for (const route of publicPaths) {
      const res = await request(app).get(route).set('Host', 'aio.deng.my.id');
      assert.ok([200, 404].includes(res.status), `${route} status`);
      assertNoLegacyBranding(res.text, route);
      if (res.status === 200) {
        assert.match(res.text, /DENG All In One/i, `${route} should show DENG All In One`);
      }
    }
  });

  test('homepage title and meta use DENG All In One', async () => {
    const app = require('../src/app');
    const res = await request(app).get('/').set('Host', 'aio.deng.my.id');
    assert.equal(res.status, 200);
    assert.match(res.text, /<title>DENG All In One - Roblox Automation &amp; Stat Tracker<\/title>/);
    assert.match(res.text, /meta name="application-name" content="DENG All In One"/);
    assert.match(res.text, /meta property="og:site_name" content="DENG All In One"/);
    assert.match(res.text, /link rel="manifest" href="\/public\/site\.webmanifest/);
    assert.match(res.text, /Rejoin agents active/);
    assert.match(res.text, /Platform Players/);
  });

  test('authenticated portal pages render without legacy visible branding', async () => {
    const app = require('../src/app');
    const agent = request.agent(app);
    await login(agent);
    const routes = ['/dashboard', '/license', '/download', '/fishit'];
    for (const route of routes) {
      const res = await agent.get(route).set('Host', 'aio.deng.my.id');
      assert.equal(res.status, 200, route);
      assertNoLegacyBranding(res.text, route);
      assert.match(res.text, /DENG All In One/i, `${route} brand present`);
    }
  });

  test('tracker shell uses DENG All In One branding', () => {
    const tracker = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.match(tracker, /DENG All In One/);
    assert.match(tracker, /Live Tracker/);
    assert.doesNotMatch(tracker, /DENG Tracker/);
  });
});
