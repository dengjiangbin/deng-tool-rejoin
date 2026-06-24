'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const ROOT = path.join(__dirname, '..', '..');

function read(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8');
}

test('legacy OAuth entry always returns the browser to the canonical AIO host', () => {
  const { oauthReturnPublicBase } = require('../src/publicDomain');
  assert.equal(
    oauthReturnPublicBase({ headers: { host: 'tool.deng.my.id' } }),
    'https://aio.deng.my.id',
  );
  assert.equal(
    oauthReturnPublicBase({ headers: { host: 'aio.deng.my.id' } }),
    'https://aio.deng.my.id',
  );
});

test('generated provider callbacks use AIO, including a stale legacy env override', () => {
  const routes = read('site/src/routes.js');
  assert.match(routes, /completeUrl: 'https:\/\/aio\.deng\.my\.id\/unlock\/linkvertise\/complete'/);
  assert.match(routes, /completeUrl: 'https:\/\/aio\.deng\.my\.id\/unlock\/lootlabs\/complete'/);
  assert.match(routes, /if \(url\.hostname === 'tool\.deng\.my\.id'\)/);
  assert.match(routes, /publicUrl: publicUrl\(\)/);

  const previous = process.env.TOOL_SITE_PUBLIC_URL;
  delete process.env.TOOL_SITE_PUBLIC_URL;
  try {
    delete require.cache[require.resolve('../src/providers/lootlabs')];
    const lootlabs = require('../src/providers/lootlabs');
    assert.equal(
      lootlabs.buildLootLabsCallbackUrl({ signedState: 'test-state' }),
      'https://aio.deng.my.id/unlock/lootlabs/complete?s=test-state',
    );
  } finally {
    if (previous === undefined) delete process.env.TOOL_SITE_PUBLIC_URL;
    else process.env.TOOL_SITE_PUBLIC_URL = previous;
  }
});

test('tracker and Discord panel present the migrated AIO journey', () => {
  const sidebar = read('site/views/partials/deng-sidebar-nav.ejs');
  assert.ok(sidebar.indexOf('Daily Report') < sidebar.indexOf('Live Tracker'));
  assert.ok(sidebar.indexOf('Live Tracker') < sidebar.indexOf('My License'));

  const mobileTracker = read('site/views/fishit_tracker.ejs');
  assert.match(mobileTracker, /data-inventory-section="dashboard">Daily Report/);
  assert.match(mobileTracker, /data-inventory-section="accounts">Live Tracker/);
  assert.match(mobileTracker, /href="\/license">My License/);

  const sourceTracker = read('site/src/inventory/fishit_tracker.source.ejs');
  assert.match(sourceTracker, /data-inventory-section="dashboard">Daily Report/);
  assert.match(sourceTracker, /href="\/license">My License/);

  const panel = read('bot/cog_license_panel.py');
  const embed = read('agent/license_panel.py');
  assert.match(panel, /url="https:\/\/aio\.deng\.my\.id\/license"/);
  assert.match(embed, /https:\/\/aio\.deng\.my\.id\/public\/img\/deng-logo\.png/);
  assert.match(embed, /https:\/\/aio\.deng\.my\.id\/license/);
  assert.doesNotMatch(embed, /tool\.deng\.my\.id/);
});

test('login uses the homepage light blue and pink visual system', () => {
  const login = read('site/views/login.ejs');
  const css = read('site/public/css/login-page.css');
  assert.match(login, /dataset\.theme = 'light'/);
  assert.match(css, /linear-gradient\(135deg, #dbeafe 0%, #fce7f3 100%\)/);
  assert.match(css, /background: rgba\(255, 255, 255, 0\.78\)/);
});

test('license generation uses a browser route and renders one server-owned block notice', () => {
  const license = read('site/views/license.ejs');
  const layout = read('site/views/layout.ejs');
  const appJs = read('site/public/js/app.js');
  const routes = read('site/src/routes.js');
  const sidebarCss = read('site/public/css/app-sidebar.css');

  assert.match(license, /action="\/license\/generate"/);
  assert.doesNotMatch(license, /action="\/api\/key\/start"/);
  assert.match(license, /data-server-license-notice/);
  assert.match(layout, /typeof suppressLayoutFlash !== 'undefined'/);
  assert.match(appJs, /serverNotice\.dataset\.blockReason === \(body\.blockReason \|\| ''\)/);
  assert.match(routes, /router\.get\('\/api\/key\/start', requireLogin, \(_req, res\) => res\.redirect\(303, '\/license'\)\)/);
  assert.match(sidebarCss, /\.inventory-main-nav__tab:visited/);
  assert.match(sidebarCss, /text-decoration: none/);
});

test('production rate limiting trusts a bounded proxy hop', () => {
  const nodeEnv = process.env.NODE_ENV;
  const trustProxy = process.env.TOOL_SITE_TRUST_PROXY;
  try {
    process.env.NODE_ENV = 'production';
    delete process.env.TOOL_SITE_TRUST_PROXY;
    delete require.cache[require.resolve('../src/rateLimitUtils')];
    const { resolveTrustProxySetting } = require('../src/rateLimitUtils');
    assert.equal(resolveTrustProxySetting(), 1);
    process.env.TOOL_SITE_TRUST_PROXY = 'true';
    assert.equal(resolveTrustProxySetting(), 1);
  } finally {
    if (nodeEnv === undefined) delete process.env.NODE_ENV;
    else process.env.NODE_ENV = nodeEnv;
    if (trustProxy === undefined) delete process.env.TOOL_SITE_TRUST_PROXY;
    else process.env.TOOL_SITE_TRUST_PROXY = trustProxy;
  }
});
