'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.join(__dirname, '..');
const CSS_PATH = path.join(ROOT, 'public', 'css', 'login-page.css');
const PROOF_DIR = path.join(ROOT, 'proofs');

function buildLoginProofHtml(loginBody, { apkEmbed } = {}) {
  const pageClass = apkEmbed
    ? 'login-page login-page--split login-page--apk-embed'
    : 'login-page login-page--split';
  const bodyClass = apkEmbed ? 'auth-layout apk-webview' : 'auth-layout';
  return [
    '<!DOCTYPE html>',
    '<html lang="en" data-theme="dark" data-public-page="1">',
    '<head>',
    '  <meta charset="utf-8">',
    '  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
    '  <title>Login mobile full-width proof</title>',
    '  <link rel="stylesheet" href="../public/css/style.css">',
    '  <link rel="stylesheet" href="../public/css/public-theme.css">',
    '  <link rel="stylesheet" href="../public/css/hero-wordmark.css">',
    '  <link rel="stylesheet" href="../public/css/login-page.css">',
    '</head>',
    `<body class="${bodyClass}">`,
    loginBody.replace(/class="login-page login-page--split"/, `class="${pageClass}"`),
    '</body>',
    '</html>',
  ].join('\n');
}

describe('login mobile browser full-width layout', () => {
  test('login CSS does not cap the mobile page shell to a fixed phone width', () => {
    const css = fs.readFileSync(CSS_PATH, 'utf8');
    const mobileShellBlock = css.match(
      /@media \(max-width: 640px\)[\s\S]*?\.login-page--split:not\(\.login-page--apk-embed\) \.login-page__shell \{([\s\S]*?)\n  \}/
    );
    assert.ok(mobileShellBlock, 'expected mobile browser shell block');
    const rules = mobileShellBlock[1];
    assert.doesNotMatch(rules, /max-width:\s*420px/);
    assert.doesNotMatch(rules, /max-width:\s*390px/);
    assert.doesNotMatch(rules, /max-width:\s*375px/);
    assert.doesNotMatch(rules, /max-width:\s*360px/);
    assert.doesNotMatch(rules, /max-width:\s*430px/);
    assert.match(rules, /width:\s*100%/);
    assert.match(rules, /max-width:\s*none/);
  });

  test('mobile browser login fills viewport width without side gutters', async () => {
    const loginTpl = fs.readFileSync(path.join(ROOT, 'views', 'login.ejs'), 'utf8');
    fs.mkdirSync(PROOF_DIR, { recursive: true });
    const proofHtml = path.join(PROOF_DIR, 'login_mobile_fullwidth_proof.html');
    const loginBody = loginTpl
      .replace(/<\?[\s\S]*?\?>/g, '')
      .replace(/<%[\s\S]*?%>/g, '')
      .replace(/<link[^>]*>\n?/g, '')
      .replace(/<script[^>]*>[\s\S]*?<\/script>\n?/g, '');
    fs.writeFileSync(proofHtml, buildLoginProofHtml(loginBody), 'utf8');

    const browser = await chromium.launch();
    const page = await browser.newPage();
    const widths = [360, 375, 390, 412, 430];

    for (const width of widths) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto(`file:///${proofHtml.replace(/\\/g, '/')}`, { waitUntil: 'networkidle' });
      const metrics = await page.evaluate(() => {
        const pageRoot = document.querySelector('.login-page--split');
        const shell = document.querySelector('.login-page__shell');
        const card = document.querySelector('.login-page__auth-inner');
        if (!pageRoot || !shell || !card) return null;
        const pageRect = pageRoot.getBoundingClientRect();
        const shellRect = shell.getBoundingClientRect();
        const cardRect = card.getBoundingClientRect();
        const docWidth = document.documentElement.clientWidth;
        return {
          docWidth,
          pageWidth: Math.round(pageRect.width),
          shellWidth: Math.round(shellRect.width),
          shellLeft: Math.round(shellRect.left),
          shellRightGap: Math.round(docWidth - shellRect.right),
          pageLeftGap: Math.round(pageRect.left),
          pageRightGap: Math.round(docWidth - pageRect.right),
          docScrollWidth: document.documentElement.scrollWidth,
          cardWidth: Math.round(cardRect.width),
          cardInsideShell: cardRect.left >= shellRect.left - 1 && cardRect.right <= shellRect.right + 1,
        };
      });

      assert.ok(metrics, `metrics missing at ${width}px`);
      assert.equal(metrics.pageWidth, width, `page root must fill ${width}px viewport`);
      assert.equal(metrics.shellWidth, width, `shell must fill ${width}px viewport`);
      assert.ok(metrics.shellLeft <= 1, `shell must start at viewport edge at ${width}px`);
      assert.ok(metrics.shellRightGap <= 1, `shell must reach viewport edge at ${width}px`);
      assert.ok(metrics.pageLeftGap <= 1, `page root must not leave left gutter at ${width}px`);
      assert.ok(metrics.pageRightGap <= 1, `page root must not leave right gutter at ${width}px`);
      assert.ok(metrics.docScrollWidth <= width + 1, `no horizontal overflow at ${width}px`);
      assert.ok(metrics.cardInsideShell, `auth card must stay inside shell at ${width}px`);
      assert.ok(metrics.cardWidth <= width, `auth card must not exceed viewport at ${width}px`);
    }

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`file:///${proofHtml.replace(/\\/g, '/')}`, { waitUntil: 'networkidle' });
    await page.screenshot({
      path: path.join(PROOF_DIR, 'login_mobile_fullwidth_390.png'),
      fullPage: true,
    });
    await browser.close();
  });
});
