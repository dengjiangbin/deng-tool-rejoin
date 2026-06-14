'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const request = require('supertest');
const { chromium } = require('playwright');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = process.env.TOOL_SITE_COOKIE_SECRET || 'test-cookie-secret-that-is-long-enough-for-the-site-suite';
process.env.TOOL_SITE_STATE_SECRET = process.env.TOOL_SITE_STATE_SECRET || 'test-state-secret-that-is-long-enough-for-challenge-suite';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = process.env.DISCORD_CLIENT_ID || 'test-discord-client-id';
process.env.DISCORD_CLIENT_SECRET = process.env.DISCORD_CLIENT_SECRET || 'test-discord-client-secret';
process.env.DISCORD_REDIRECT_URI = process.env.DISCORD_REDIRECT_URI || 'http://localhost:8791/auth/discord/callback';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';

const CSS_PATH = path.join(__dirname, '..', 'public', 'css', 'login-page.css');
const ROOT = path.join(__dirname, '..');
const app = require('../src/app');

function buildLoginProofHtml(loginBody, { apkEmbed } = {}) {
  const pageClass = apkEmbed
    ? 'login-page login-page--split login-page--apk-embed'
    : 'login-page login-page--split';
  const bodyClass = apkEmbed ? 'auth-layout apk-webview' : 'auth-layout';
  const normalizedBody = loginBody
    .replace(/class="login-page login-page--split[^"]*"/, `class="${pageClass}"`);
  return [
    '<!DOCTYPE html>',
    '<html lang="en" data-theme="dark" data-public-page="1">',
    '<head>',
    '  <meta charset="utf-8">',
    '  <meta name="viewport" content="width=device-width, initial-scale=1">',
    '  <title>Login mobile hero proof</title>',
    '  <link rel="stylesheet" href="../public/css/style.css">',
    '  <link rel="stylesheet" href="../public/css/public-theme.css">',
    '  <link rel="stylesheet" href="../public/css/login-page.css">',
    '</head>',
    `<body class="${bodyClass}">`,
    normalizedBody,
    '  <script src="../public/js/hero-wordmark.js"></script>',
    '</body>',
    '</html>',
  ].join('\n');
}

describe('login mobile/APK centered hero redesign', () => {
  test('login template uses DENG All In One branding and online pill', async () => {
    const res = await request(app).get('/login').expect(200);
    assert.match(res.text, /login-page__brand-name">DENG All In One/);
    assert.match(res.text, /data-login-online-pill/);
    assert.match(res.text, /hero-wordmark/);
    assert.match(res.text, /All In One/);
    assert.match(res.text, /login-page__auth-inner/);
    assert.doesNotMatch(res.text, /DENG Tool\b/);
  });

  test('login CSS keeps split layout and online pill styling', () => {
    const css = fs.readFileSync(CSS_PATH, 'utf8');
    assert.match(css, /\.login-page__brand-row/);
    assert.match(css, /\.login-page__online-pill/);
    assert.match(css, /\.login-page__online-pill:not\(\.login-page__online-pill--offline\)/);
    assert.match(css, /@media \(max-width: 640px\)[\s\S]*\.login-page--split \.login-page__shell/);
  });

  test('APK embed login shell renders on mobile width', async () => {
    const loginTpl = fs.readFileSync(path.join(ROOT, 'views', 'login.ejs'), 'utf8');
    const proofDir = path.join(ROOT, 'proofs');
    fs.mkdirSync(proofDir, { recursive: true });
    const proofHtml = path.join(proofDir, 'login_mobile_auth_panel_proof.html');
    const loginBody = loginTpl
      .replace(/<\?[\s\S]*?\?>/g, '')
      .replace(/<%[\s\S]*?%>/g, '')
      .replace(/<link[^>]*>\n?/g, '')
      .replace(/<script[^>]*>[\s\S]*?<\/script>\n?/g, '');
    fs.writeFileSync(proofHtml, buildLoginProofHtml(loginBody, { apkEmbed: true }), 'utf8');

    const browser = await chromium.launch();
    const page = await browser.newPage();

    for (const width of [414, 390, 375]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto('file:///' + proofHtml.replace(/\\/g, '/'), { waitUntil: 'networkidle' });
      const metrics = await page.evaluate(function() {
        var shell = document.querySelector('.login-page__shell');
        var brand = document.querySelector('.login-page__brand-name');
        var card = document.querySelector('.login-page__auth-inner');
        if (!shell || !brand || !card) return null;
        return {
          brandText: brand.textContent.trim(),
          shellWidth: Math.round(shell.getBoundingClientRect().width),
          viewportWidth: window.innerWidth,
          hasAllInOne: brand.textContent.indexOf('All In One') >= 0,
        };
      });
      assert.ok(metrics, `metrics missing at ${width}px`);
      assert.equal(metrics.hasAllInOne, true, `brand must say All In One at ${width}px`);
      assert.ok(metrics.shellWidth >= width - 2, `shell must fill viewport at ${width}px`);
    }

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('file:///' + proofHtml.replace(/\\/g, '/'), { waitUntil: 'networkidle' });
    await page.locator('.login-page__shell').screenshot({
      path: path.join(proofDir, 'login_mobile_auth_panel_390.png'),
    });
    await browser.close();
  });
});

describe('login mobile browser fog logo layout', () => {
  test('login CSS scopes mobile browser full-width shell', () => {
    const css = fs.readFileSync(CSS_PATH, 'utf8');
    assert.match(css, /@media \(max-width: 640px\)[\s\S]*\.login-page--split \.login-page__shell[\s\S]*width:\s*100%/);
  });

  test('mobile browser login shell fills viewport width', async () => {
    const loginTpl = fs.readFileSync(path.join(ROOT, 'views', 'login.ejs'), 'utf8');
    const proofDir = path.join(ROOT, 'proofs');
    fs.mkdirSync(proofDir, { recursive: true });
    const proofHtml = path.join(proofDir, 'login_mobile_browser_proof.html');
    const loginBody = loginTpl
      .replace(/<\?[\s\S]*?\?>/g, '')
      .replace(/<%[\s\S]*?%>/g, '')
      .replace(/<link[^>]*>\n?/g, '')
      .replace(/<script[^>]*>[\s\S]*?<\/script>\n?/g, '');
    fs.writeFileSync(proofHtml, buildLoginProofHtml(loginBody, { apkEmbed: false }), 'utf8');

    const browser = await chromium.launch();
    const page = await browser.newPage();

    for (const width of [414, 390, 375]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto('file:///' + proofHtml.replace(/\\/g, '/'), { waitUntil: 'networkidle' });
      const metrics = await page.evaluate(function() {
        var shell = document.querySelector('.login-page__shell');
        var brand = document.querySelector('.login-page__brand-name');
        var card = document.querySelector('.login-page__auth-inner');
        if (!shell || !brand || !card) return null;
        var shellRect = shell.getBoundingClientRect();
        return {
          brandText: brand.textContent.trim(),
          shellWidth: Math.round(shellRect.width),
          viewportWidth: window.innerWidth,
          hasAllInOne: brand.textContent.indexOf('All In One') >= 0,
          noToolBrand: brand.textContent.indexOf('DENG Tool') < 0,
        };
      });
      assert.ok(metrics, `metrics missing at ${width}px`);
      assert.equal(metrics.hasAllInOne, true, `brand must say All In One at ${width}px`);
      assert.equal(metrics.noToolBrand, true, `brand must not say DENG Tool at ${width}px`);
      assert.equal(metrics.shellWidth, width, `shell must fill ${width}px viewport`);
    }

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('file:///' + proofHtml.replace(/\\/g, '/'), { waitUntil: 'networkidle' });
    await page.locator('.login-page--split').screenshot({
      path: path.join(proofDir, 'login_mobile_browser_390.png'),
    });
    await browser.close();
  });
});
