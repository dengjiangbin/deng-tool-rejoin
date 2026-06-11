'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-the-site-suite';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = process.env.DISCORD_CLIENT_ID || 'test-discord-client-id';
process.env.DISCORD_CLIENT_SECRET = process.env.DISCORD_CLIENT_SECRET || 'test-discord-client-secret';
process.env.DISCORD_REDIRECT_URI = process.env.DISCORD_REDIRECT_URI || 'http://localhost:8791/auth/discord/callback';

const app = require('../src/app');
const homeCssPath = path.join(__dirname, '..', 'public', 'css', 'home.css');
const homeCss = fs.readFileSync(homeCssPath, 'utf8');

describe('home sticky navbar regression', () => {
  test('home CSS keeps fixed navbar above relative nav-wrap override', () => {
    const fixedBlock = homeCss.match(/\.deng-home-nav-wrap\.deng-home-nav-wrap--fixed\s*\{([\s\S]*?)\}/);
    assert.ok(fixedBlock, 'expected combined fixed navbar selector');
    assert.match(fixedBlock[1], /position:\s*fixed/);
    assert.match(fixedBlock[1], /top:\s*0/);
    assert.match(fixedBlock[1], /z-index:\s*1100/);

    const relativeIdx = homeCss.indexOf('.deng-home-nav-wrap {');
    const fixedIdx = homeCss.indexOf('.deng-home-nav-wrap.deng-home-nav-wrap--fixed {');
    assert.ok(relativeIdx >= 0 && fixedIdx > relativeIdx, 'fixed navbar rule must come after relative nav-wrap rule');
  });

  test('landing markup applies fixed class to full header container', async () => {
    const res = await request(app).get('/');
    assert.equal(res.status, 200);
    assert.match(
      res.text,
      /<header class="deng-home-nav-wrap deng-home-nav-wrap--fixed"[\s\S]*?Sign In[\s\S]*?<\/header>/,
    );
    assert.match(res.text, /href="\/public\/css\/home\.css/);
  });

  test('main content reserves top offset via CSS variable', () => {
    assert.match(homeCss, /\.deng-home-main\s*\{[\s\S]*?padding-top:\s*var\(--deng-home-nav-offset\)/);
    assert.match(homeCss, /\.deng-home\s*\{[\s\S]*?--deng-home-nav-offset:\s*108px/);
  });
});
