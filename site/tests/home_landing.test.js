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
    assert.match(res.text, /Platform Stats/);
    assert.match(res.text, /One platform\. Multiple tools\./);
    assert.match(res.text, /deng-home-nav-wrap--fixed/);
    assert.match(res.text, /href="#home"[^>]*>Home<\/a>/);
    assert.match(res.text, /href="#statistic">Statistic<\/a>/);
    assert.match(res.text, /href="#about">About<\/a>/);
    assert.match(res.text, /data-home-stat-card="trackedUsernames"/);
    assert.match(res.text, /data-home-stat-card="onlineUsernames"/);
    assert.match(res.text, /data-home-stat-card="activeDevices"/);
    assert.match(res.text, /Tracked usernames online/);
    assert.match(res.text, /Tracker devices running/);
    assert.match(res.text, /hero-wordmark/);
    assert.match(res.text, /aria-label="Go to home"/);
    assert.doesNotMatch(res.text, /Welcome back/);
    assert.doesNotMatch(res.text, /Sign in with Discord/);
    assert.doesNotMatch(res.text, /login-page--split/);
    assert.doesNotMatch(res.text, /Purchase via Discord/i);
    assert.doesNotMatch(res.text, /href="\/download"/);
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
    assert.doesNotMatch(res.text, /data-home-live-stats-grid/);
    assert.doesNotMatch(res.text, /Purchase via Discord/i);
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

  test('GET /download logged out redirects to login with return path', async () => {
    const res = await request(app).get('/download');
    assert.equal(res.status, 302);
    assert.match(res.headers.location, /^\/login\?return=%2Fdownload$/);
  });

  test('protected /dashboard redirects unauthenticated users to /login', async () => {
    const res = await request(app).get('/dashboard');
    assert.equal(res.status, 302);
    assert.match(res.headers.location, /^\/login(\?return=%2Fdashboard)?$/);
  });

  test('public CTAs use login return links instead of direct private routes', async () => {
    const res = await request(app).get('/');
    assert.match(res.text, /href="\/login\?return=\/download">Visit agent/);
    assert.match(res.text, /href="\/login\?return=\/inventory">/);
    assert.match(res.text, /href="\/login\?return=\/dashboard">/);
  });

  test('home page uses home.js for live stats fetch', async () => {
    const res = await request(app).get('/');
    assert.match(res.text, /home\.js/);
    assert.doesNotMatch(res.text, /fishit-home\.js/);
  });
});
