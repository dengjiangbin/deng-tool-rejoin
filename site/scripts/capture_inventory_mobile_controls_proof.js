#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const express = require('express');
const request = require('supertest');
const { chromium } = require('playwright');

process.env.NODE_ENV = 'test';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';

const ROOT = path.join(__dirname, '..');
const OUT = path.join(ROOT, 'proofs');
const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, 'src', 'inventoryAssetManifest.json'), 'utf8'));
const cssHref = '../public/assets/' + manifest.css;

const trackerRouter = require('../src/fishitTrackerRoutes');

const SAMPLE_CARD = `<article class="accounts-mobile-card" data-account-mobile-key="demo">
  <div class="accounts-mobile-card__top">
    <div class="accounts-mobile-card__account">
      <span class="accounts-status"><span class="status-dot live" aria-hidden="true"></span></span>
      <span class="accounts-mobile-card__username">denghub2</span>
    </div>
    <div class="accounts-mobile-card__actions">
      <button type="button" class="accounts-table__icon-btn" aria-label="Open inventory"><svg viewBox="0 0 24 24" width="16" height="16"><path d="M6 2h12v4H6z"></path></svg></button>
      <button type="button" class="accounts-table__icon-btn accounts-table__icon-btn--danger" aria-label="Remove"><svg viewBox="0 0 24 24" width="16" height="16"><path d="M3 6h18"></path></svg></button>
    </div>
  </div>
  <div class="accounts-mobile-card__grid accounts-mobile-card__grid--stats">
    <div class="accounts-mobile-card__row col-coin" data-col="coin"><span class="accounts-mobile-card__row-label">Coin</span><span class="accounts-mobile-card__row-value coin-value">43.4M</span></div>
    <div class="accounts-mobile-card__row col-total-caught" data-col="total-caught"><span class="accounts-mobile-card__row-label">Caught</span><span class="accounts-mobile-card__row-value total-caught-value">66.354</span></div>
    <div class="accounts-mobile-card__row col-rarest-fish" data-col="rarest-fish"><span class="accounts-mobile-card__row-label">Rare</span><span class="accounts-mobile-card__row-value rarest-fish-value">1/4M</span></div>
  </div>
</article>`;

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(ROOT, 'views'));
  app.use('/public', express.static(path.join(ROOT, 'public')));
  app.use(trackerRouter);
  return app;
}

function extractInventoryShell(html) {
  const start = html.indexOf('<div class="inventory-shell">');
  const end = html.indexOf('<script', start);
  if (start < 0 || end < 0) throw new Error('inventory shell not found');
  return html.slice(start, end);
}

function buildProofHtml(shell, apkEmbed) {
  const withSample = shell.replace(
    '<div class="accounts-mobile-list" id="accountsMobileList" aria-label="Account cards"></div>',
    '<div class="accounts-mobile-list" id="accountsMobileList" aria-label="Account cards">' + SAMPLE_CARD + '</div>',
  );
  return [
    '<!DOCTYPE html>',
    '<html lang="en" data-theme="dark">',
    '<head>',
    '  <meta charset="utf-8">',
    '  <meta name="viewport" content="width=device-width, initial-scale=1">',
    '  <title>Inventory mobile controls proof</title>',
    '  <link rel="stylesheet" href="' + cssHref + '">',
    '</head>',
    '<body class="' + (apkEmbed ? 'inventory-apk-embed' : '') + '">',
    withSample,
    '</body>',
    '</html>',
  ].join('\n');
}

async function capture(page, htmlPath, label) {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('file:///' + htmlPath.replace(/\\/g, '/'), { waitUntil: 'networkidle' });
  const checks = await page.evaluate(function() {
    var loadstring = document.querySelector('.loadstring-box:not(.loadstring-box--debug)');
    var card = document.querySelector('.accounts-mobile-card');
    var stats = document.getElementById('inventoryStats');
    return {
      loadstringHidden: !loadstring || getComputedStyle(loadstring).display === 'none',
      cardCols: card ? getComputedStyle(card).gridTemplateColumns : null,
      cardHeight: card ? Math.round(card.getBoundingClientRect().height) : null,
      summaryCols: stats ? getComputedStyle(stats).gridTemplateColumns : null,
    };
  });
  console.log(label, JSON.stringify(checks));
  await page.locator('#accountsOverview').screenshot({
    path: path.join(OUT, 'inventory_mobile_controls_' + label + '_after.png'),
  });
}

async function main() {
  const app = makeApp();
  const res = await request(app).get('/inventory').expect(200);
  const shell = extractInventoryShell(res.text);
  fs.mkdirSync(OUT, { recursive: true });

  const mobileHtml = path.join(OUT, 'inventory_mobile_controls_proof.html');
  const apkHtml = path.join(OUT, 'inventory_mobile_controls_apk_proof.html');
  fs.writeFileSync(mobileHtml, buildProofHtml(shell, false), 'utf8');
  fs.writeFileSync(apkHtml, buildProofHtml(shell, true), 'utf8');

  const browser = await chromium.launch();
  const page = await browser.newPage();
  await capture(page, mobileHtml, 'mobile_390');
  await capture(page, apkHtml, 'apk_390');
  await browser.close();
  console.log('INVENTORY_MOBILE_CONTROLS_PROOF_OK', OUT);
}

main().catch(function(err) {
  console.error(err);
  process.exit(1);
});
