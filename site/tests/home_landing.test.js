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
  test('/ renders the marketing home page with the correct title', async () => {
    const res = await request(app).get('/');
    assert.equal(res.status, 200);
    assert.match(res.text, /<title>DENG Tool - Roblox Automation &amp; Stat Tracker<\/title>/);
    assert.doesNotMatch(res.text, /Sign In - DENG Tool/);
    assert.match(res.text, /class="deng-home"/);
    assert.match(res.text, /href="\/login"/);
    assert.match(res.text, /https:\/\/discord\.gg\/v74u5ZtuXf/);
    assert.match(res.text, /home\.css/);
    assert.match(res.text, /home\.js/);
  });

  test('/login renders the Discord sign-in page with login title', async () => {
    const res = await request(app).get('/login');
    assert.equal(res.status, 200);
    assert.match(res.text, /<title>Sign In - DENG Tool<\/title>/);
    assert.match(res.text, /Sign in with Discord/);
    assert.match(res.text, /href="\/auth\/discord"/);
    assert.match(res.text, /class="login-page login-page--split"/);
    assert.match(res.text, /login-page__shell/);
    assert.match(res.text, /Welcome back/);
    assert.match(res.text, /public-theme\.css/);
    assert.match(res.text, /login-page\.css/);
    assert.doesNotMatch(res.text, /class="deng-home"/);
    assert.doesNotMatch(res.text, /login-page__card/);
    assert.doesNotMatch(res.text, /Sign in to continue/);
    assert.doesNotMatch(res.text, /theme-toggle-floating/);
  });

  test('home navbar includes Home, Statistic, About, and Sign In', async () => {
    const res = await request(app).get('/');
    assert.match(res.text, /deng-home-nav__link is-active" href="\/" data-nav-section="home">Home<\/a>/);
    assert.match(res.text, /href="#statistic">Statistic<\/a>/);
    assert.match(res.text, /href="#about">About<\/a>/);
    assert.match(res.text, /deng-home-nav__signin" href="\/login">Sign In<\/a>/);
  });

  test('home page uses home.js for live stats fetch', async () => {
    const res = await request(app).get('/');
    assert.match(res.text, /home\.js/);
    assert.match(res.text, /data-home-stat-value="trackedPlayers"/);
    assert.doesNotMatch(res.text, /fishit-home\.js/);
    assert.doesNotMatch(res.text, /5714 online now/);
  });

  test('home page uses premium public theme without debug markers', async () => {
    const res = await request(app).get('/');
    assert.match(res.text, /public-theme\.css/);
    assert.match(res.text, /dataset\.publicPage\s*=\s*'1'/);
    assert.match(res.text, /Live Network/);
    assert.match(res.text, /Agent Network/);
    assert.match(res.text, /One platform\. Multiple tools\./);
    assert.doesNotMatch(res.text, /theme-toggle-floating/);
    assert.doesNotMatch(res.text, /BLOCKER|DEBUG|build marker/i);
  });
});
