'use strict';

const { describe, test, before, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const request = require('supertest');

process.env.TOOL_SITE_COOKIE_SECRET = 'apk-handoff-test-cookie-secret-long!!';
process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = 'test-client-id';
process.env.DISCORD_CLIENT_SECRET = 'test-client-secret';
process.env.DISCORD_REDIRECT_URI = 'https://aio.deng.my.id/api/aio/auth/callback';
process.env.DISCORD_AIO_REDIRECT_URI = 'https://aio.deng.my.id/api/aio/auth/callback';
process.env.TOOL_SITE_PUBLIC_URL = 'https://aio.deng.my.id';
process.env.TOOL_SITE_INTERNAL_URL = 'https://aio.deng.my.id';
process.env.TOOL_SITE_STATE_SECRET = 'apk-handoff-test-state-secret-long!!!';

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

let app;
let aioSessionStore;

before(() => {
  app = require('../src/app');
  aioSessionStore = require('../src/aioSessionStore');
});

beforeEach(() => {
  aioSessionStore._reset();
});

describe('APK Discord auth handoff login loop fix', () => {
  test('exchange + web-bootstrap + web-bridge creates authenticated web session', async () => {
    const agent = request.agent(app);

    const { code: loginCode } = aioSessionStore.createLoginCode({
      discordUserId: '990011223344556677',
      siteUserId: 42,
      username: 'ApkHandoffUser',
      avatar: null,
    });

    const exchange = await agent.post('/api/aio/auth/exchange').send({ code: loginCode, device_name: 'test-apk' });
    assert.equal(exchange.status, 200);
    assert.equal(exchange.body.ok, true);
    assert.ok(exchange.body.appSessionToken);

    const bootstrap = await agent
      .post('/api/aio/auth/web-bootstrap')
      .set('Authorization', `Bearer ${exchange.body.appSessionToken}`)
      .send({});
    assert.equal(bootstrap.status, 200);
    assert.equal(bootstrap.body.ok, true);
    assert.match(bootstrap.body.bridgeUrl, /^https:\/\/aio\.deng\.my\.id\/auth\/web-bridge\?code=/);
    assert.equal(bootstrap.body.handoffMarker, 'APK_DISCORD_AUTH_HANDOFF_FIX_2026_06_14');

    const bridge = await agent.get(bootstrap.body.bridgeUrl.replace('https://aio.deng.my.id', ''));
    assert.equal(bridge.status, 302);
    assert.match(bridge.headers.location, /\/tracker\?apk=1/);

    const me = await agent.get('/api/internal/auth-debug');
    assert.equal(me.status, 200);
    assert.equal(me.body.session.authenticated, true);
  });

  test('web-bootstrap bridge code is single-use', async () => {
    const agent = request.agent(app);
    const { code: loginCode } = aioSessionStore.createLoginCode({
      discordUserId: '880011223344556677',
      username: 'OnceUser',
    });
    const ex = await agent.post('/api/aio/auth/exchange').send({ code: loginCode });
    const bootstrap = await agent
      .post('/api/aio/auth/web-bootstrap')
      .set('Authorization', `Bearer ${ex.body.appSessionToken}`)
      .send({});
    const bridgePath = bootstrap.body.bridgeUrl.replace('https://aio.deng.my.id', '');
    await agent.get(bridgePath).expect(302);
    const replay = await agent.get(bridgePath);
    assert.equal(replay.status, 302);
    assert.match(replay.headers.location, /\/login/);
  });

  test('APK OAuth callback issues deep link HTML not web session redirect', async () => {
    const agent = request.agent(app);
    const start = await agent.get('/auth/discord?apk=1&public_return=1');
    assert.equal(start.status, 302);
    const state = new URL(start.headers.location, 'https://discord.com').searchParams.get('state');
    const cb = await agent.get(`/api/aio/auth/callback?code=ok&state=${state}`);
    assert.equal(cb.status, 200);
    assert.match(cb.text, /deng-aio:\/\/auth\/callback\?code=/);
    assert.doesNotMatch(cb.text, /\/dashboard/);
  });
});

describe('APK auth handoff source contracts', () => {
  const root = path.join(__dirname, '..', '..', 'android', 'app', 'src', 'main', 'kotlin', 'my', 'id', 'deng', 'monitor');

  test('MainActivity does not consume pending web bootstrap URL', () => {
    const main = fs.readFileSync(path.join(root, 'MainActivity.kt'), 'utf8');
    assert.doesNotMatch(main, /consumePendingWebBootstrapUrl/);
  });

  test('LiveTrackerWebViewScreen waits for bootstrap before loading WebView', () => {
    const live = fs.readFileSync(path.join(root, 'ui', 'LiveTrackerWebViewScreen.kt'), 'utf8');
    assert.match(live, /consumePendingWebBootstrapUrl/);
    assert.match(live, /if \(url != null\)/);
  });

  test('LoginWebViewScreen does not mark logged in from URL alone', () => {
    const login = fs.readFileSync(path.join(root, 'ui', 'LoginWebViewScreen.kt'), 'utf8');
    const composable = login.split('fun completeApkOAuthFromDeepLink')[0];
    assert.doesNotMatch(composable, /setWebLoggedIn\(true\)/);
    assert.match(login, /completeApkOAuthFromDeepLink/);
    assert.match(login, /APK_DISCORD_AUTH_HANDOFF_FIX_2026_06_14/);
  });
});
