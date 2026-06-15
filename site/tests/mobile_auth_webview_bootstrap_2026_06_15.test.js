'use strict';

/**
 * Mobile-auth first-party WebView session bootstrap.
 *
 * Proves the replacement for the broken native cookie-injection handoff:
 *   POST /api/aio/mobile-auth/start  -> transaction + Discord OAuth URL
 *   GET  /api/aio/auth/callback       -> binds user, mints single-use consume code
 *   GET  /api/aio/mobile-auth/status  -> polling fallback yields the consume URL
 *   GET  /mobile-auth/consume          -> first-party Set-Cookie + 303 /tracker
 *   GET  /api/aio/auth/me              -> WebView identity check succeeds
 */

const { describe, test, before, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const request = require('supertest');

process.env.TOOL_SITE_COOKIE_SECRET = 'mobile-auth-test-cookie-secret-long!!';
process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = 'test-client-id';
process.env.DISCORD_CLIENT_SECRET = 'test-client-secret';
process.env.DISCORD_REDIRECT_URI = 'https://aio.deng.my.id/api/aio/auth/callback';
process.env.DISCORD_AIO_REDIRECT_URI = 'https://aio.deng.my.id/api/aio/auth/callback';
process.env.TOOL_SITE_PUBLIC_URL = 'https://aio.deng.my.id';
process.env.TOOL_SITE_INTERNAL_URL = 'https://aio.deng.my.id';
process.env.TOOL_SITE_STATE_SECRET = 'mobile-auth-test-state-secret-long!!!';

const fakeAxios = {
  async post() {
    return { data: { access_token: 'discord-access-token' } };
  },
  async get() {
    return {
      data: { id: 'discord-user-77', username: 'MobileTester', avatar: null, email: null },
    };
  },
};

function makeStubQuery() {
  let pendingInsert = false;
  const q = {
    select() { return q; },
    eq() { return q; },
    in() { return q; },
    order() { return q; },
    limit() { return q; },
    maybeSingle() { return Promise.resolve({ data: null, error: null }); },
    single() {
      if (pendingInsert) {
        pendingInsert = false;
        return Promise.resolve({
          data: {
            id: 77,
            discord_user_id: 'discord-user-77',
            discord_username: 'MobileTester',
            username: 'MobileTester',
          },
          error: null,
        });
      }
      return Promise.resolve({ data: null, error: { message: 'not found' } });
    },
    insert() { pendingInsert = true; return q; },
    update() { return q; },
    upsert() { return q; },
    then(resolve) { return Promise.resolve({ data: [], error: null }).then(resolve); },
  };
  return q;
}
const mockSupabase = { from() { return makeStubQuery(); } };

const path = require('path');
const dbPath = path.join(__dirname, '..', 'src', 'db.js');
require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true, exports: mockSupabase };
require.cache[require.resolve('axios')] = {
  id: require.resolve('axios'),
  filename: require.resolve('axios'),
  loaded: true,
  exports: fakeAxios,
};

let app;
let aioSessionStore;

before(() => {
  app = require('../src/app');
  aioSessionStore = require('../src/aioSessionStore');
});

beforeEach(() => {
  aioSessionStore._reset();
});

function oauthStateFromAuthUrl(authUrl) {
  return new URL(authUrl).searchParams.get('state');
}

async function runHappyPath(agent) {
  const start = await agent.post('/api/aio/mobile-auth/start').send({ target: '/tracker' });
  assert.equal(start.status, 200);
  assert.equal(start.body.ok, true);
  assert.ok(start.body.transactionId, 'start returns transactionId');
  assert.ok(start.body.state, 'start returns state nonce');
  assert.match(start.body.authUrl, /discord\.com\/.+\/authorize\?/);
  assert.equal(start.body.target, '/tracker');

  const oauthState = oauthStateFromAuthUrl(start.body.authUrl);
  const cb = await agent.get(`/api/aio/auth/callback?code=ok&state=${oauthState}`);
  assert.equal(cb.status, 302);
  assert.match(cb.headers.location, /\/auth\/apk-open\?code=/);
  assert.match(cb.headers.location, /state=/);
  return { start, callbackLocation: cb.headers.location };
}

describe('mobile-auth WebView first-party bootstrap', () => {
  test('start creates a transaction and Discord OAuth URL with a state', async () => {
    const agent = request.agent(app);
    const start = await agent.post('/api/aio/mobile-auth/start').send({});
    assert.equal(start.status, 200);
    assert.ok(start.body.transactionId);
    assert.ok(start.body.state);
    assert.ok(oauthStateFromAuthUrl(start.body.authUrl), 'authUrl carries Discord state');
    assert.equal(start.body.target, '/tracker');
  });

  test('status polling returns a consume URL after callback binds the user', async () => {
    const agent = request.agent(app);
    const { start } = await runHappyPath(agent);
    const status = await agent.get(
      `/api/aio/mobile-auth/status?transactionId=${encodeURIComponent(start.body.transactionId)}&state=${encodeURIComponent(start.body.state)}`,
    );
    assert.equal(status.status, 200);
    assert.equal(status.body.status, 'complete');
    assert.match(status.body.consumeUrl, /^https:\/\/aio\.deng\.my\.id\/mobile-auth\/consume\?code=/);
    assert.match(status.body.consumeUrl, /state=/);
    assert.match(status.body.consumeUrl, /target=%2Ftracker/);
  });

  test('consume sets deng_sid first-party and serves the auth/me bridge page', async () => {
    const agent = request.agent(app);
    const { start } = await runHappyPath(agent);
    const status = await agent.get(
      `/api/aio/mobile-auth/status?transactionId=${encodeURIComponent(start.body.transactionId)}&state=${encodeURIComponent(start.body.state)}`,
    );
    const consumePath = status.body.consumeUrl.replace('https://aio.deng.my.id', '');
    const consume = await agent.get(consumePath);
    // HTML bridge (NOT a blind 303) that gates /tracker on /api/aio/auth/me.
    assert.equal(consume.status, 200, 'consume serves the bridge page');
    assert.match(consume.headers['content-type'] || '', /text\/html/);
    assert.match(consume.text, /Signing you in/);
    assert.match(consume.text, /\/api\/aio\/auth\/me/);
    assert.match(consume.text, /location\.replace/);
    const cookies = consume.headers['set-cookie'] || [];
    assert.ok(cookies.some((c) => c.startsWith('deng_sid=')), 'consume sets deng_sid on the first-party response');
    assert.match(consume.headers['cache-control'] || '', /no-store/);
    // Non-secret debug headers expose the exact consume outcome.
    assert.equal(consume.headers['x-consume-code-valid'], 'yes');
    assert.equal(consume.headers['x-consume-state-valid'], 'yes');
    assert.equal(consume.headers['x-consume-user-resolved'], 'yes');
    assert.equal(consume.headers['x-consume-session-created'], 'yes');
    assert.equal(consume.headers['x-consume-set-cookie'], 'yes');
    assert.equal(consume.headers['x-consume-redirect-target'], '/tracker');

    // /api/aio/auth/me succeeds from the same (cookie) agent, with debug headers.
    const me = await agent.get('/api/aio/auth/me');
    assert.equal(me.status, 200);
    assert.equal(me.body.authenticated, true);
    assert.equal(me.body.user.discordUserId, 'discord-user-77');
    assert.equal(me.headers['x-auth-cookie-present'], 'yes');
    assert.equal(me.headers['x-auth-user-found'], 'yes');
  });

  test('/tracker no longer redirects to /login after consume', async () => {
    const agent = request.agent(app);
    const { start } = await runHappyPath(agent);
    const status = await agent.get(
      `/api/aio/mobile-auth/status?transactionId=${encodeURIComponent(start.body.transactionId)}&state=${encodeURIComponent(start.body.state)}`,
    );
    await agent.get(status.body.consumeUrl.replace('https://aio.deng.my.id', '')).expect(200);
    const tracker = await agent.get('/tracker').redirects(0);
    assert.notEqual(tracker.status, 302, '/tracker must not bounce to login once authenticated');
  });

  test('consume by transactionId (polling fallback) also creates the session', async () => {
    const agent = request.agent(app);
    const { start } = await runHappyPath(agent);
    const consume = await agent.get(
      `/mobile-auth/consume?transactionId=${encodeURIComponent(start.body.transactionId)}`
      + `&state=${encodeURIComponent(start.body.state)}&target=${encodeURIComponent('/tracker')}`,
    );
    assert.equal(consume.status, 200);
    const cookies = consume.headers['set-cookie'] || [];
    assert.ok(cookies.some((c) => c.startsWith('deng_sid=')), 'transactionId consume sets deng_sid');
    const me = await agent.get('/api/aio/auth/me');
    assert.equal(me.status, 200);
    assert.equal(me.body.authenticated, true);
  });

  test('consume code is single-use (replay fails to /login)', async () => {
    const agent = request.agent(app);
    const { start } = await runHappyPath(agent);
    const status = await agent.get(
      `/api/aio/mobile-auth/status?transactionId=${encodeURIComponent(start.body.transactionId)}&state=${encodeURIComponent(start.body.state)}`,
    );
    const consumePath = status.body.consumeUrl.replace('https://aio.deng.my.id', '');
    await agent.get(consumePath).expect(200);
    const replay = await request.agent(app).get(consumePath);
    assert.equal(replay.status, 302);
    assert.match(replay.headers.location, /auth_error=/);
  });

  test('consume rejects a wrong/mismatched state nonce', async () => {
    const agent = request.agent(app);
    const { start } = await runHappyPath(agent);
    const status = await agent.get(
      `/api/aio/mobile-auth/status?transactionId=${encodeURIComponent(start.body.transactionId)}&state=${encodeURIComponent(start.body.state)}`,
    );
    const u = new URL(status.body.consumeUrl);
    u.searchParams.set('state', 'totally-wrong-state');
    const bad = await request.agent(app).get(u.pathname + u.search);
    assert.equal(bad.status, 302);
    assert.match(bad.headers.location, /auth_error=state_invalid/);
  });

  test('status requires the matching state nonce', async () => {
    const agent = request.agent(app);
    const { start } = await runHappyPath(agent);
    const bad = await agent.get(
      `/api/aio/mobile-auth/status?transactionId=${encodeURIComponent(start.body.transactionId)}&state=wrong`,
    );
    assert.equal(bad.status, 200);
    assert.equal(bad.body.status, 'state_invalid');
  });

  test('deep-link handoff carries both code and state for the consume URL', async () => {
    const agent = request.agent(app);
    const { callbackLocation } = await runHappyPath(agent);
    const apkOpen = new URL(callbackLocation, 'https://aio.deng.my.id');
    const code = apkOpen.searchParams.get('code');
    const state = apkOpen.searchParams.get('state');
    assert.ok(code && state, 'apk-open handoff includes code and state');
    // The apk-open page must surface a deep link with the same code+state.
    const page = await agent.get(apkOpen.pathname + apkOpen.search);
    assert.equal(page.status, 200);
    assert.match(page.text, /deng-aio:\/\/auth\/callback/);
    assert.match(page.text, new RegExp(`state=${state.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&')}`));

    // And those code+state redeem a session at /mobile-auth/consume.
    const consume = await request.agent(app).get(
      `/mobile-auth/consume?code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}&target=${encodeURIComponent('/tracker')}`,
    );
    assert.equal(consume.status, 200);
    assert.match(consume.text, /Signing you in/);
    const cookies = consume.headers['set-cookie'] || [];
    assert.ok(cookies.some((c) => c.startsWith('deng_sid=')), 'deep-link consume sets deng_sid');
  });

  test('auth/me is unauthenticated without a session cookie', async () => {
    const res = await request.agent(app).get('/api/aio/auth/me');
    assert.equal(res.status, 401);
    assert.equal(res.body.authenticated, false);
  });

  test('desktop browser Discord login still lands on /tracker by default', async () => {
    const agent = request.agent(app);
    const start = await agent.get('/auth/discord');
    assert.equal(start.status, 302);
    const state = new URL(start.headers.location, 'https://discord.com').searchParams.get('state');
    const cb = await agent.get(`/auth/discord/callback?code=ok&state=${state}`);
    assert.equal(cb.status, 302);
    assert.match(cb.headers.location, /\/tracker/);
  });

  test('legacy APK flow (no transaction) still produces an apk-open handoff', async () => {
    const agent = request.agent(app);
    const start = await agent.get('/auth/discord?client=apk&apk=1&public_return=1');
    assert.equal(start.status, 302);
    const state = new URL(start.headers.location, 'https://discord.com').searchParams.get('state');
    const cb = await agent.get(`/api/aio/auth/callback?code=ok&state=${state}`);
    assert.equal(cb.status, 302);
    assert.match(cb.headers.location, /\/auth\/apk-open\?code=/);
  });
});
