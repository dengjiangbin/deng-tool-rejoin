'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-homepage-restore';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = 'test-discord-client-id';
process.env.DISCORD_CLIENT_SECRET = 'test-discord-client-secret';
process.env.DISCORD_REDIRECT_URI = 'http://localhost:8791/auth/discord/callback';

const app = require('../src/app');

const FORBIDDEN = [
  /DENG Tool\b/i,
  /Tool Players/i,
  /Rejoin Tools Running/i,
];

describe('homepage restoration', () => {
  test('GET / uses public-home-layout body class (not auth-layout centering)', async () => {
    const res = await request(app).get('/');
    assert.equal(res.status, 200);
    assert.match(res.text, /body class="public-home-layout"/);
    assert.doesNotMatch(res.text, /body class="auth-layout"/);
    assert.match(res.headers['cache-control'], /no-store/);
  });

  test('GET / contains DENG All In One branding and modern homepage assets', async () => {
    const res = await request(app).get('/');
    assert.match(res.text, /DENG All In One/);
    assert.match(res.text, /hero-wordmark\.css/);
    assert.match(res.text, /home\.css/);
    assert.match(res.text, /\/public\/js\/home\.js\?v=/);
    assert.match(res.text, /Tracked Players/);
    assert.match(res.text, /Platform Players/);
    for (const pattern of FORBIDDEN) {
      assert.doesNotMatch(res.text, pattern);
    }
  });

  test('GET /login still uses auth-layout and remains restored', async () => {
    const res = await request(app).get('/login');
    assert.equal(res.status, 200);
    assert.match(res.text, /body class="auth-layout"/);
    assert.match(res.text, /DENG All In One/);
    assert.doesNotMatch(res.text, /DENG Tool\b/);
  });

  test('homepage does not load portal app.js bundle', async () => {
    const res = await request(app).get('/');
    assert.doesNotMatch(res.text, /\/public\/js\/app\.js\?v=/);
    assert.match(res.text, /\/public\/js\/home\.js\?v=/);
  });

  test('public tracker stats endpoint is safe for homepage polling', async () => {
    const res = await request(app).get('/api/public/tracker-stats');
    assert.equal(res.status, 200);
    assert.match(String(res.headers['cache-control']), /no-store/);
    assert.equal(typeof res.body.trackedCount, 'number');
    assert.equal(typeof res.body.onlineCount, 'number');
    assert.equal('accounts' in res.body, false);
  });
});
