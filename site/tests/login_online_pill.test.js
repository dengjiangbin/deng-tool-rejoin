'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.join(__dirname, '..');
const LOGIN_CSS = path.join(ROOT, 'public', 'css', 'login-page.css');
const THEME_CSS = path.join(ROOT, 'public', 'css', 'public-theme.css');
const HOME_CSS = path.join(ROOT, 'public', 'css', 'home.css');
const PROOF_DIR = path.join(ROOT, 'proofs');

function buildLoginProofHtml(loginBody) {
  return [
    '<!DOCTYPE html>',
    '<html lang="en" data-theme="dark" data-public-page="1">',
    '<head>',
    '  <meta charset="utf-8">',
    '  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
    '  <title>Login online pill proof</title>',
    '  <link rel="stylesheet" href="../public/css/style.css">',
    '  <link rel="stylesheet" href="../public/css/public-theme.css">',
    '  <link rel="stylesheet" href="../public/css/hero-wordmark.css">',
    '  <link rel="stylesheet" href="../public/css/login-page.css">',
    '</head>',
    '<body class="auth-layout">',
    loginBody.replace(/login-page__online-pill--offline/g, ''),
    '</body>',
    '</html>',
  ].join('\n');
}

describe('login online status pill styling', () => {
  test('status tokens exist and are separate from brand blue/pink', () => {
    const theme = fs.readFileSync(THEME_CSS, 'utf8');
    const wordmark = fs.readFileSync(path.join(ROOT, 'public', 'css', 'hero-wordmark.css'), 'utf8');
    assert.match(theme, /--status-green:\s*#00e6a8/);
    assert.match(theme, /--status-green-border:/);
    assert.match(wordmark, /--deng-neon-blue:\s*#38bdf8/);
    assert.match(wordmark, /--deng-neon-pink:\s*#f9a8d4/);
    assert.match(theme, /--public-success:\s*(var\(--status-green\)|#00e6a8)/);
  });

  test('mobile login CSS does not apply brand gradient to online pill', () => {
    const css = fs.readFileSync(LOGIN_CSS, 'utf8');
    const mobilePillBlock = css.match(
      /@media \(max-width: 640px\)[\s\S]*?\.login-page--split \.login-page__online-pill \{([\s\S]*?)\n  \}/
    );
    assert.ok(mobilePillBlock, 'expected mobile login online pill block');
    const rules = mobilePillBlock[1];
    assert.doesNotMatch(rules, /linear-gradient/);
    assert.doesNotMatch(rules, /deng-neon-blue/);
    assert.doesNotMatch(rules, /249,\s*168,\s*212/);
    assert.doesNotMatch(rules, /color:\s*white/i);
    assert.doesNotMatch(rules, /background:\s*white/i);
  });

  test('homepage hero online pill uses green status tokens', () => {
    const css = fs.readFileSync(HOME_CSS, 'utf8');
    const pillBlock = css.match(/\.deng-home-hero__pill \{([\s\S]*?)\n\}/);
    assert.ok(pillBlock, 'expected homepage hero pill block');
    assert.match(pillBlock[1], /var\(--status-green/);
    assert.doesNotMatch(pillBlock[1], /linear-gradient/);
    assert.doesNotMatch(pillBlock[1], /deng-neon-blue-bright/);
  });

  test('mobile browser online pill renders green text, dot, border, and glow', async () => {
    const loginTpl = fs.readFileSync(path.join(ROOT, 'views', 'login.ejs'), 'utf8');
    fs.mkdirSync(PROOF_DIR, { recursive: true });
    const proofHtml = path.join(PROOF_DIR, 'login_online_pill_proof.html');
    const loginBody = loginTpl
      .replace(/<\?[\s\S]*?\?>/g, '')
      .replace(/<%[\s\S]*?%>/g, '')
      .replace(/<link[^>]*>\n?/g, '')
      .replace(/<script[^>]*>[\s\S]*?<\/script>\n?/g, '')
      .replace(/hidden>/, '>');
    fs.writeFileSync(proofHtml, buildLoginProofHtml(loginBody), 'utf8');

    const launchOpts = { headless: true };
    try {
      launchOpts.channel = 'msedge';
      var browser = await chromium.launch(launchOpts);
    } catch (_) {
      browser = await chromium.launch({ headless: true });
    }
    const page = await browser.newPage();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`file:///${proofHtml.replace(/\\/g, '/')}`, { waitUntil: 'networkidle' });
    await page.evaluate(() => {
      var pill = document.querySelector('[data-login-online-pill]');
      var text = document.querySelector('[data-login-online-text]');
      if (pill && text) {
        pill.hidden = false;
        pill.classList.remove('login-page__online-pill--offline');
        text.textContent = '8 Online Now';
      }
    });

    const colors = await page.evaluate(() => {
      var pill = document.querySelector('.login-page__online-pill:not(.login-page__online-pill--offline)');
      if (!pill) return null;
      var cs = getComputedStyle(pill);
      var dot = getComputedStyle(pill, '::before');
      return {
        color: cs.color,
        background: cs.backgroundColor,
        borderColor: cs.borderTopColor,
        dotBackground: dot.backgroundColor,
        backgroundImage: cs.backgroundImage,
      };
    });

    assert.ok(colors, 'online pill not found');
    assert.doesNotMatch(colors.backgroundImage, /gradient/i);
    assert.match(colors.color, /rgb\(0,\s*230,\s*168\)/i);
    assert.match(colors.dotBackground, /rgb\(0,\s*230,\s*168\)/i);
    assert.match(colors.borderColor, /rgba\(0,\s*230,\s*168/i);

    await page.screenshot({
      path: path.join(PROOF_DIR, 'login_online_pill_mobile_390.png'),
      fullPage: false,
      clip: { x: 0, y: 0, width: 390, height: 120 },
    });
    await browser.close();
  });
});
