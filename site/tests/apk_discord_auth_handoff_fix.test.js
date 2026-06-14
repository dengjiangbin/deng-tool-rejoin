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

const MARKER = 'APK_DISCORD_AUTH_LOGIN_LOOP_REAL_FIX_2026_06_14';

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
            id: 1,
            discord_user_id: 'discord-user-1',
            discord_username: 'DiscordTester',
            username: 'DiscordTester',
          },
          error: null,
        });
      }
      return Promise.resolve({ data: null, error: { message: 'not found' } });
    },
    insert() {
      pendingInsert = true;
      return q;
    },
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
    assert.equal(bootstrap.body.handoffMarker, MARKER);

    const bridge = await agent.get(bootstrap.body.bridgeUrl.replace('https://aio.deng.my.id', ''));
    assert.equal(bridge.status, 302);
    assert.match(bridge.headers.location, /\/tracker\?apk=1/);

    const webSession = await agent.get('/api/aio/auth/web-session');
    assert.equal(webSession.status, 200);
    assert.equal(webSession.body.authenticated, true);

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
    assert.match(replay.headers.location, /auth_error=handoff_expired/);
  });

  test('APK OAuth callback issues deep link HTML not web session redirect', async () => {
    const agent = request.agent(app);
    const start = await agent.get('/auth/discord?client=apk&apk=1&public_return=1');
    assert.equal(start.status, 302);
    const state = new URL(start.headers.location, 'https://discord.com').searchParams.get('state');
    const cb = await agent.get(`/api/aio/auth/callback?code=ok&state=${state}`);
    assert.equal(cb.status, 200);
    assert.match(cb.text, /deng-aio:\/\/auth\/callback\?code=/);
    assert.match(cb.text, /intent:\/\/auth\/callback/);
    assert.doesNotMatch(cb.text, /\/dashboard/);
  });

  test('normal browser Discord login still redirects to dashboard', async () => {
    const agent = request.agent(app);
    const start = await agent.get('/auth/discord?return=/dashboard');
    assert.equal(start.status, 302);
    const state = new URL(start.headers.location, 'https://discord.com').searchParams.get('state');
    const cb = await agent.get(`/api/aio/auth/callback?code=ok&state=${state}`);
    assert.equal(cb.status, 302);
    assert.match(cb.headers.location, /\/dashboard/);
  });
});

describe('APK auth handoff source contracts', () => {
  const root = path.join(__dirname, '..', '..', 'android', 'app', 'src', 'main', 'kotlin', 'my', 'id', 'deng', 'monitor');

  test('MainActivity bootstraps WebView before marking logged in', () => {
    const main = fs.readFileSync(path.join(root, 'MainActivity.kt'), 'utf8');
    assert.match(main, /bootstrapBridgeUrl/);
    assert.match(main, /ApkOAuthHandoffResult\.Ready/);
    assert.doesNotMatch(main, /consumePendingWebBootstrapUrl/);
  });

  test('ApkAuthBootstrapScreen loads bridge URL and finalizes session on tracker', () => {
    const login = fs.readFileSync(path.join(root, 'ui', 'LoginWebViewScreen.kt'), 'utf8');
    assert.match(login, /ApkAuthBootstrapScreen/);
    assert.match(login, /finalizeApkWebSession/);
    const handoff = fs.readFileSync(path.join(root, 'ui', 'ApkOAuthHandoff.kt'), 'utf8');
    const completeFn = handoff.match(/suspend fun completeApkOAuthFromDeepLink[\s\S]*?\n\}/);
    assert.ok(completeFn, 'completeApkOAuthFromDeepLink must exist');
    assert.doesNotMatch(completeFn[0], /setWebLoggedIn\(true\)/);
    assert.match(handoff, /finalizeApkWebSession[\s\S]*setWebLoggedIn\(true\)/);
    assert.match(handoff, new RegExp(MARKER));
  });

  test('AioWebViewScreen preserves redirect cookies', () => {
    const web = fs.readFileSync(path.join(root, 'ui', 'AioWebViewScreen.kt'), 'utf8');
    assert.match(web, /return false/);
    assert.match(web, /CookieManager\.getInstance\(\)\.flush\(\)/);
  });
});
