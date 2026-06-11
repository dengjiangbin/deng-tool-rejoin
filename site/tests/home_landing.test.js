'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-the-site-suite';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';

const app = require('../src/app');

describe('public home landing page', () => {
  test('GET / logged out returns landing page, not login', async () => {
    const res = await request(app).get('/');
    assert.equal(res.status, 200);
    assert.match(res.text, /<title>DENG Tool - Roblox Automation &amp; Stat Tracker<\/title>/);
    assert.match(res.text, /class="deng-home"/);
    assert.match(res.text, /Live Network/);
    assert.match(res.text, /One platform\. Multiple tools\./);
    assert.match(res.text, /deng-home-nav__link[^>]*>Home<\/a>/);
    assert.match(res.text, /href="#statistic">Statistic<\/a>/);
    assert.match(res.text, /href="#about">About<\/a>/);
    assert.match(res.text, /deng-home-nav__signin" href="\/login">Sign In<\/a>/);
    assert.match(res.text, /aria-label="Go to home"/);
    assert.match(res.text, /deng-home-hero__title--interactive/);
    assert.doesNotMatch(res.text, /Welcome back/);
    assert.doesNotMatch(res.text, /Sign in with Discord/);
    assert.doesNotMatch(res.text, /login-page--split/);
    assert.doesNotMatch(res.text, /login-page__card/);
    assert.doesNotMatch(res.text, /theme-toggle-floating/);
    assert.doesNotMatch(res.text, /BLOCKER|DEBUG/i);
    assert.match(res.headers['cache-control'], /no-store/i);
  });

  test('GET /login logged out returns dedicated login page', async () => {
    const res = await request(app).get('/login');
    assert.equal(res.status, 200);
    assert.match(res.text, /<title>Sign In - DENG Tool<\/title>/);
    assert.match(res.text, /class="login-page login-page--split"/);
    assert.match(res.text, /Welcome back/);
    assert.match(res.text, /Sign in with Discord/);
    assert.match(res.text, /href="\/auth\/discord"/);
    assert.match(res.text, /login-page__back" href="\/">.*Back to Home<\/a>/);
    assert.doesNotMatch(res.text, /class="deng-home"/);
    assert.doesNotMatch(res.text, /data-home-stats-grid/);
    assert.doesNotMatch(res.text, /login-page__card/);
    assert.doesNotMatch(res.text, /Sign in to continue/);
    assert.doesNotMatch(res.text, /theme-toggle-floating/);
    assert.match(res.headers['cache-control'], /no-store/i);
  });

  test('/login does not redirect to / when logged out', async () => {
    const res = await request(app).get('/login');
    assert.equal(res.status, 200);
    assert.notEqual(res.headers.location, '/');
  });

  test('/ does not redirect to /login when logged out', async () => {
    const res = await request(app).get('/');
    assert.equal(res.status, 200);
    assert.notEqual(res.headers.location, '/login');
  });

  test('protected /dashboard redirects unauthenticated users to /login', async () => {
    const res = await request(app).get('/dashboard');
    assert.equal(res.status, 302);
    assert.equal(res.headers.location, '/login');
  });

  test('home navbar brand links to / and login back link links to /', async () => {
    const home = await request(app).get('/');
    assert.match(home.text, /class="deng-home-brand" href="\/"/);

    const login = await request(app).get('/login');
    assert.match(login.text, /class="login-page__brand-row" href="\/"/);
    assert.match(login.text, /login-page__back" href="\/"/);
  });

  test('home page uses home.js for live stats fetch', async () => {
    const res = await request(app).get('/');
    assert.match(res.text, /home\.js/);
    assert.match(res.text, /data-home-stat-value="trackedPlayers"/);
    assert.doesNotMatch(res.text, /fishit-home\.js/);
  });

  test('public pages use premium dark theme assets', async () => {
    for (const path of ['/', '/login']) {
      const res = await request(app).get(path);
      assert.match(res.text, /public-theme\.css/);
      assert.match(res.text, /dataset\.publicPage\s*=\s*'1'/);
    }
  });
});
