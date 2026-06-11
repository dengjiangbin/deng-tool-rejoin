'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-the-site-suite';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = process.env.DISCORD_CLIENT_ID || 'test-discord-client-id';
process.env.DISCORD_CLIENT_SECRET = process.env.DISCORD_CLIENT_SECRET || 'test-discord-client-secret';
process.env.DISCORD_REDIRECT_URI = process.env.DISCORD_REDIRECT_URI || 'http://localhost:8791/auth/discord/callback';

const countUp = require('../public/js/count-up-stats.js');
const app = require('../src/app');

describe('count-up stats formatter', () => {
  test('formats integers with commas', () => {
    assert.equal(countUp.formatInteger(5888), '5,888');
    assert.equal(countUp.formatInteger(35061646), '35,061,646');
    assert.equal(countUp.formatInteger('16038'), '16,038');
  });

  test('formats decimals', () => {
    assert.equal(countUp.formatDecimal(1234.5, 1), '1,234.5');
    assert.equal(countUp.formatDecimal(0, 2), '0.00');
  });

  test('formats percentages', () => {
    assert.equal(countUp.formatPercent(85, 1), '85.0%');
    assert.equal(countUp.placeholder('percent', 1), '0.0%');
  });

  test('formats ratios', () => {
    assert.equal(countUp.formatRatio(2962, 4217), '2,962 / 4,217');
    assert.equal(countUp.placeholder('ratio'), '0 / 0');
  });

  test('formats compact values', () => {
    assert.equal(countUp.formatCompact(1200), '1.2K');
    assert.equal(countUp.formatCompact(3400000), '3.4M');
    assert.equal(countUp.parseRawNumber('3.4M'), 3400000);
  });

  test('reduced-motion helper is safe without window.matchMedia', () => {
    assert.equal(typeof countUp.prefersReducedMotion(), 'boolean');
  });
});

describe('count-up stat markup on routes', () => {
  test('landing stat cards use js-count-up markers', async () => {
    const res = await request(app).get('/');
    assert.equal(res.status, 200);
    assert.match(res.text, /class="[^"]*js-count-up[^"]*"[^>]*data-home-stat-value="trackedPlayers"/);
    assert.match(res.text, /data-home-stat-value="onlineNow"[^>]*data-count-format="integer"/);
    assert.match(res.text, /data-home-stat-value="activeAgents"[^>]*data-count-format="ratio"/);
    assert.match(res.text, /data-home-stat-meta="onlineNow"/);
    assert.match(res.text, /count-up-stats\.js/);
  });

  test('dashboard template stat cards use js-count-up with server targets', () => {
    const fs = require('fs');
    const path = require('path');
    const dashboard = fs.readFileSync(path.join(__dirname, '..', 'views', 'dashboard.ejs'), 'utf8');
    assert.match(dashboard, /Total Licenses[\s\S]*?class="stat-value js-count-up"[\s\S]*?data-count-to="<%= stats\.total %>"/);
    assert.match(dashboard, /class="stat-value js-count-up"[\s\S]*?data-count-to="<%= stats\.primary_key_card\.value %>"/);
  });

  test('download template does not mark version strings with js-count-up', () => {
    const fs = require('fs');
    const path = require('path');
    const download = fs.readFileSync(path.join(__dirname, '..', 'views', 'download.ejs'), 'utf8');
    assert.doesNotMatch(download, /stat-label">App Version[\s\S]*?class="stat-value js-count-up"/);
    assert.match(download, /Monitoring companion app/);
    assert.match(download, /window\.DengCountUpStats\.set\(textEl/);
  });

  test('inventory tracker source stat cards use js-count-up', () => {
    const fs = require('fs');
    const path = require('path');
    const source = fs.readFileSync(path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs'), 'utf8');
    assert.match(source, /statOnlineAccounts/);
    assert.match(source, /js-count-up/);
    assert.match(source, /data-count-format="ratio"/);
    assert.match(source, /countUp\.set\(statOnlineAccountsEl/);
  });
});
