'use strict';

/**
 * Production-shaped OAuth E2E: cookie jar, HTTPS proxy headers, protected routes.
 * Simulates Discord token exchange via axios mock (same callback handler as production).
 */

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const crypto = require('crypto');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-oauth-e2e-suite';
process.env.TOOL_SITE_STATE_SECRET = 'test-state-secret-that-is-long-enough-for-oauth-e2e-suite';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = 'test-discord-client-id';
process.env.DISCORD_CLIENT_SECRET = 'test-discord-client-secret';
process.env.DISCORD_REDIRECT_URI = 'https://aio.deng.my.id/api/aio/auth/callback';
process.env.TOOL_SITE_PUBLIC_URL = 'https://aio.deng.my.id';
process.env.TOOL_SITE_TRUST_PROXY = '1';

const fakeAxios = {
  async post() { return { data: { access_token: 'discord-access-token' } }; },
  async get() {
    return { data: { id: '505185072211689472', username: 'ProdE2E', avatar: null, email: null } };
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

function clearAppCache() {
  delete require.cache[require.resolve('../src/app')];
}

function parseSetCookie(headers) {
  const raw = headers['set-cookie'];
  if (!raw) return [];
  return Array.isArray(raw) ? raw : [raw];
}

describe('OAuth production E2E (cookie jar + tracker API session)', () => {
  beforeEach(() => {
    memoryDb.site_users = [];
    clearAppCache();
  });

  test('callback Set-Cookie → /dashboard → /tracker → /api/tracker/summary authenticated', async () => {
    const app = require('../src/app');
    const agent = request.agent(app);
    const headers = {
      Host: 'aio.deng.my.id',
      'X-Forwarded-Proto': 'https',
    };

    const start = await agent.get('/auth/discord?return=/tracker').set(headers);
    assert.equal(start.status, 302);
    const authUrl = new URL(start.headers.location);
    assert.equal(authUrl.searchParams.get('redirect_uri'), 'https://aio.deng.my.id/api/aio/auth/callback');

    const state = authUrl.searchParams.get('state');
    const cb = await agent
      .get(`/api/aio/auth/callback?code=ok&state=${state}`)
      .set(headers);
    assert.equal(cb.status, 302);
    const cookies = parseSetCookie(cb.headers);
    assert.ok(cookies.some((c) => c.startsWith('deng_sid=')), 'callback must Set-Cookie deng_sid');
    assert.ok(cookies.every((c) => !/Domain=/i.test(c)), 'host-only cookie');
    if (process.env.NODE_ENV === 'production') {
      assert.ok(cookies.some((c) => /Secure/i.test(c)), 'Secure on HTTPS callback');
    }

    const dashboard = await agent.get('/dashboard').set(headers);
    assert.equal(dashboard.status, 200, 'dashboard must render authenticated');

    const tracker = await agent.get('/tracker').set(headers);
    assert.equal(tracker.status, 200, 'tracker page must render authenticated');

    const summary = await agent.get('/api/tracker/summary').set(headers);
    assert.equal(summary.status, 200, 'tracker summary must not 401 when session cookie sent');
    assert.equal(summary.body.ok, true);

    const debug = await agent.get('/api/internal/auth-debug').set(headers);
    assert.equal(debug.status, 200);
    assert.equal(debug.body.session.authenticated, true);
    assert.equal(debug.body.cookie.received, true);
    assert.equal(debug.body.session.authFailureReason, null);
  });
});
