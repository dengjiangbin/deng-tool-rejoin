'use strict';
/**
 * Tests for the DENG AIO APK backend API (aioRoutes.js + aioSessionStore.js).
 *
 * Covers the browser-OAuth -> deep-link -> one-time-code -> APK-token flow,
 * bearer-scoped data sync, per-user isolation, and APK update metadata.
 *
 * Mirrors monitor.test.js: ./db (Supabase) and axios are mocked so route logic
 * runs without external services. The AIO session store is in-memory under
 * NODE_ENV=test, so no disk writes occur.
 */

const { describe, test, before, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');

// ── Env required by app.js ──────────────────────────────────────────────────
process.env.TOOL_SITE_COOKIE_SECRET = 'aio-test-cookie-secret-long-enough-yes!!';
process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.TOOL_SITE_PUBLIC_URL = 'http://localhost:8791';
process.env.TOOL_SITE_INTERNAL_URL = 'http://localhost:8791';
process.env.DISCORD_AIO_REDIRECT_URI = 'http://localhost:8791/api/aio/auth/callback';
process.env.DISCORD_CLIENT_ID = 'x';
process.env.DISCORD_CLIENT_SECRET = 'x';
process.env.DISCORD_REDIRECT_URI = 'http://localhost:8791/auth/discord/callback';
process.env.TOOL_SITE_STATE_SECRET = 'aio-test-state-secret-long-enough-yes!!!';

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

// Minimal Supabase mock — every query resolves to "no row" so upsert falls back
// to a Discord-only session user (the AIO path tolerates a missing portal DB).
function makeStubQuery() {
  const q = {
    select() { return q; },
    eq() { return q; },
    in() { return q; },
    order() { return q; },
    limit() { return q; },
    maybeSingle() { return Promise.resolve({ data: null, error: null }); },
    single() { return Promise.resolve({ data: null, error: { message: 'not found' } }); },
    insert() { return q; },
    update() { return q; },
    upsert() { return q; },
    then(resolve) { return Promise.resolve({ data: [], error: null }).then(resolve); },
  };
  return q;
}
const mockSupabase = { from() { return makeStubQuery(); } };

const dbPath = path.join(__dirname, '..', 'src', 'db.js');
require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true, exports: mockSupabase };
require.cache[require.resolve('axios')] = {
  id: require.resolve('axios'),
  filename: require.resolve('axios'),
  loaded: true,
  exports: fakeAxios,
};

const request = require('supertest');
let app;
let aioSessionStore;
let trackedAccounts;

before(() => {
  app = require('../src/app');
  aioSessionStore = require('../src/aioSessionStore');
  trackedAccounts = require('../src/inventoryTrackedAccounts');
});

beforeEach(() => {
  aioSessionStore._reset();
  if (trackedAccounts.resetMemoryStoreForTests) trackedAccounts.resetMemoryStoreForTests();
});

// Mint an APK session token directly (bypassing the browser OAuth dance).
function mintToken(discordUserId, username = 'tester') {
  const { token } = aioSessionStore.createSession({ discordUserId, username });
  return token;
}

// ── OAuth / deep-link flow ───────────────────────────────────────────────────

describe('AIO auth flow', () => {
  test('auth start redirects to Discord with AIO redirect_uri and state', async () => {
    const res = await request(app).get('/api/aio/auth/start');
    assert.equal(res.status, 302);
    const url = new URL(res.headers.location);
    assert.equal(url.origin + url.pathname, 'https://discord.com/api/v10/oauth2/authorize');
    assert.equal(url.searchParams.get('redirect_uri'), 'http://localhost:8791/api/aio/auth/callback');
    assert.ok(url.searchParams.get('state'));
    assert.equal(url.searchParams.get('response_type'), 'code');
  });

  test('auth start uses DISCORD_AIO_REDIRECT_URI when configured (production smoke)', async () => {
    const prev = process.env.DISCORD_AIO_REDIRECT_URI;
    const prevScheme = process.env.DENG_AIO_APP_SCHEME;
    process.env.DISCORD_AIO_REDIRECT_URI = 'https://tool.deng.my.id/api/aio/auth/callback';
    process.env.DENG_AIO_APP_SCHEME = 'deng-aio';
    try {
      delete require.cache[require.resolve('../src/aioRoutes')];
      const freshApp = require('../src/app');
      const res = await request(freshApp).get('/api/aio/auth/start');
      assert.equal(res.status, 302);
      const url = new URL(res.headers.location);
      assert.equal(
        url.searchParams.get('redirect_uri'),
        'https://tool.deng.my.id/api/aio/auth/callback',
        'redirect_uri must match DISCORD_AIO_REDIRECT_URI exactly',
      );
      assert.doesNotMatch(res.text || '', /client_secret|DISCORD_CLIENT_SECRET/i);
    } finally {
      if (prev == null) delete process.env.DISCORD_AIO_REDIRECT_URI;
      else process.env.DISCORD_AIO_REDIRECT_URI = prev;
      if (prevScheme == null) delete process.env.DENG_AIO_APP_SCHEME;
      else process.env.DENG_AIO_APP_SCHEME = prevScheme;
      delete require.cache[require.resolve('../src/aioRoutes')];
      delete require.cache[require.resolve('../src/app')];
    }
  });

  test('callback bounces to app deep link carrying only a one-time code (no token)', async () => {
    const agent = request.agent(app);
    const start = await agent.get('/api/aio/auth/start');
    const state = new URL(start.headers.location).searchParams.get('state');
    const cb = await agent.get(`/api/aio/auth/callback?code=ok&state=${state}`);
    assert.equal(cb.status, 200);
    // Deep link present, and it must NOT contain any Discord/access token.
    const m = /deng-aio:\/\/auth\/callback\?code=([^"'\s&]+)/.exec(cb.text);
    assert.ok(m, 'deep link with code present');
    assert.doesNotMatch(cb.text, /discord-access-token/);
    assert.doesNotMatch(cb.text, /access_token/);

    const loginCode = decodeURIComponent(m[1]);
    const ex = await agent.post('/api/aio/auth/exchange').send({ code: loginCode, device_name: 'Pixel' });
    assert.equal(ex.status, 200);
    assert.equal(ex.body.ok, true);
    assert.ok(ex.body.appSessionToken, 'returns session token');
    assert.equal(ex.body.user.discordUserId, 'discord-user-1');
    assert.equal(ex.body.bootstrapRequired, true);
  });

  test('callback rejects mismatched state', async () => {
    const res = await request(app).get('/api/aio/auth/callback?code=ok&state=bogus');
    assert.equal(res.status, 302);
    assert.match(res.headers.location, /^deng-aio:\/\/auth\/callback\?error=/);
  });

  test('exchange rejects missing code', async () => {
    const res = await request(app).post('/api/aio/auth/exchange').send({});
    assert.equal(res.status, 400);
  });

  test('exchange rejects invalid/expired code', async () => {
    const res = await request(app).post('/api/aio/auth/exchange').send({ code: 'not-a-real-code' });
    assert.equal(res.status, 401);
  });

  test('one-time code cannot be redeemed twice', async () => {
    const { code } = aioSessionStore.createLoginCode({ discordUserId: 'discord-user-1', username: 'a' });
    const first = await request(app).post('/api/aio/auth/exchange').send({ code });
    assert.equal(first.status, 200);
    const second = await request(app).post('/api/aio/auth/exchange').send({ code });
    assert.equal(second.status, 401);
  });

  test('logout revokes the session token', async () => {
    const token = mintToken('discord-user-1');
    const me1 = await request(app).get('/api/aio/me').set('Authorization', `Bearer ${token}`);
    assert.equal(me1.status, 200);
    await request(app).post('/api/aio/auth/logout').set('Authorization', `Bearer ${token}`);
    const me2 = await request(app).get('/api/aio/me').set('Authorization', `Bearer ${token}`);
    assert.equal(me2.status, 401);
  });
});

// ── Bearer auth + scoping ─────────────────────────────────────────────────────

describe('AIO data endpoints require bearer auth', () => {
  for (const url of ['/api/aio/me', '/api/aio/bootstrap', '/api/aio/sync/manifest', '/api/aio/sync/full?dataset=app']) {
    test(`401 without token: ${url}`, async () => {
      const res = await request(app).get(url);
      assert.equal(res.status, 401);
    });
  }

  test('bootstrap returns profile, dataset versions, and update meta', async () => {
    const token = mintToken('discord-user-1', 'DiscordTester');
    const res = await request(app).get('/api/aio/bootstrap').set('Authorization', `Bearer ${token}`);
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
    assert.equal(res.body.profile.discordUserId, 'discord-user-1');
    assert.ok(res.body.datasets.dashboard.version);
    assert.ok(res.body.datasets.profile.version);
    assert.deepEqual(res.body.requiredDatasets, ['profile', 'dashboard', 'accounts', 'tracker', 'app']);
    assert.equal(typeof res.body.app.forceUpdate, 'boolean');
  });

  test('sync full + delta cursor model (unchanged returns changed:false)', async () => {
    const token = mintToken('discord-user-1');
    const full = await request(app).get('/api/aio/sync/full?dataset=app').set('Authorization', `Bearer ${token}`);
    assert.equal(full.status, 200);
    assert.ok(full.body.cursor);
    const delta = await request(app)
      .get(`/api/aio/sync/delta?dataset=app&since=${encodeURIComponent(full.body.cursor)}`)
      .set('Authorization', `Bearer ${token}`);
    assert.equal(delta.status, 200);
    assert.equal(delta.body.changed, false);
  });

  test('unknown dataset is rejected', async () => {
    const token = mintToken('discord-user-1');
    const res = await request(app).get('/api/aio/sync/full?dataset=secrets').set('Authorization', `Bearer ${token}`);
    assert.equal(res.status, 400);
  });

  test('accounts dataset is scoped to the token owner (no cross-user leak)', async () => {
    await trackedAccounts.addTrackedAccounts('111111111111111111', ['alice_fish']);
    await trackedAccounts.addTrackedAccounts('222222222222222222', ['bob_fish']);

    const tokenA = mintToken('111111111111111111');
    const tokenB = mintToken('222222222222222222');

    const a = await request(app).get('/api/aio/sync/full?dataset=accounts').set('Authorization', `Bearer ${tokenA}`);
    const b = await request(app).get('/api/aio/sync/full?dataset=accounts').set('Authorization', `Bearer ${tokenB}`);
    assert.equal(a.status, 200);
    assert.equal(b.status, 200);
    const usernamesA = a.body.data.accounts.map((x) => x.username);
    const usernamesB = b.body.data.accounts.map((x) => x.username);
    assert.deepEqual(usernamesA, ['alice_fish']);
    assert.deepEqual(usernamesB, ['bob_fish']);
  });

  test('profile dataset reflects the token owner only', async () => {
    const tokenA = mintToken('111111111111111111', 'Alice');
    const tokenB = mintToken('222222222222222222', 'Bob');
    const a = await request(app).get('/api/aio/sync/full?dataset=profile').set('Authorization', `Bearer ${tokenA}`);
    const b = await request(app).get('/api/aio/sync/full?dataset=profile').set('Authorization', `Bearer ${tokenB}`);
    assert.equal(a.body.data.discordUserId, '111111111111111111');
    assert.equal(b.body.data.discordUserId, '222222222222222222');
  });

  test('tracker dataset returns account rows with 3-indicator fields', async () => {
    await trackedAccounts.addTrackedAccounts('111111111111111111', ['tracker_test_user']);
    const token = mintToken('111111111111111111');
    const res = await request(app).get('/api/aio/sync/full?dataset=tracker').set('Authorization', `Bearer ${token}`);
    assert.equal(res.status, 200);
    assert.ok(Array.isArray(res.body.data.accounts));
    assert.equal(typeof res.body.data.serverNow, 'string');
    const row = res.body.data.accounts.find((a) => a.username === 'tracker_test_user');
    assert.ok(row, 'tracked account row present');
    assert.equal(typeof row.accountPresenceLive, 'boolean');
    assert.equal(typeof row.statsUploadFresh, 'boolean');
    assert.equal(typeof row.inventoryUploadFresh, 'boolean');
    assert.ok('canonicalKey' in row);
  });
});

// ── APK auto-update metadata ──────────────────────────────────────────────────

describe('AIO app/latest update metadata', () => {
  test('returns version metadata and is reachable without auth', async () => {
    const res = await request(app).get('/api/aio/app/latest');
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
    assert.ok('versionName' in res.body);
    assert.ok('sha256' in res.body);
    assert.ok('apkUrl' in res.body);
    assert.equal(typeof res.body.forceUpdate, 'boolean');
    assert.ok(Array.isArray(res.body.changelog));
  });
});

// ── Existing monitor (Rejoin/device) API accepts the AIO bearer (reuse) ───────
// NOTE: /api/tracker/* cannot be exercised for bearer here because the tracker
// router auto-injects a default test session for those paths. The monitor
// surface has no such shim, so it cleanly proves the requireAppAuth fallback.

describe('AIO bearer reuses existing monitor API (no logic duplication)', () => {
  test('GET /api/monitor/devices rejects requests with no token', async () => {
    const res = await request(app).get('/api/monitor/devices');
    assert.equal(res.status, 401);
  });

  test('GET /api/monitor/devices rejects a bogus bearer', async () => {
    const res = await request(app).get('/api/monitor/devices').set('Authorization', 'Bearer totally-invalid');
    assert.equal(res.status, 401);
  });

  test('GET /api/monitor/devices accepts a valid AIO bearer (auth passes)', async () => {
    const token = mintToken('discord-user-1');
    const res = await request(app).get('/api/monitor/devices').set('Authorization', `Bearer ${token}`);
    assert.notEqual(res.status, 401);
  });
});
