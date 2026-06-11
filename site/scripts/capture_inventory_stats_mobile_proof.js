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
  if (start < 0 || end < 0) throw new Error('inventory shell not found in rendered HTML');
  return html.slice(start, end);
}

function buildProofHtml(shell, apkEmbed) {
  return [
    '<!DOCTYPE html>',
    '<html lang="en" data-theme="dark">',
    '<head>',
    '  <meta charset="utf-8">',
    '  <meta name="viewport" content="width=device-width, initial-scale=1">',
    '  <title>Inventory stats mobile proof</title>',
    '  <link rel="stylesheet" href="' + cssHref + '">',
    '</head>',
    '<body class="' + (apkEmbed ? 'inventory-apk-embed' : '') + '">',
    shell,
    '</body>',
    '</html>',
  ].join('\n');
}

async function capture(page, htmlPath, label) {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('file:///' + htmlPath.replace(/\\/g, '/'), { waitUntil: 'networkidle' });
  const stats = await page.evaluate(function() {
    var el = document.getElementById('inventoryStats');
    if (!el) return null;
    return {
      cols: getComputedStyle(el).gridTemplateColumns,
      count: el.children.length,
    };
  });
  console.log(label, JSON.stringify(stats));
  await page.locator('#inventoryStats').screenshot({
    path: path.join(OUT, 'inventory_stats_' + label + '_after.png'),
  });
}

async function main() {
  const app = makeApp();
  const res = await request(app).get('/inventory').expect(200);
  const shell = extractInventoryShell(res.text);
  fs.mkdirSync(OUT, { recursive: true });

  const mobileHtml = path.join(OUT, 'inventory_stats_mobile_proof.html');
  const apkHtml = path.join(OUT, 'inventory_stats_apk_proof.html');
  fs.writeFileSync(mobileHtml, buildProofHtml(shell, false), 'utf8');
  fs.writeFileSync(apkHtml, buildProofHtml(shell, true), 'utf8');

  const browser = await chromium.launch();
  const page = await browser.newPage();
  await capture(page, mobileHtml, 'mobile_390');
  await capture(page, apkHtml, 'apk_390');
  await browser.close();
  console.log('INVENTORY_STATS_MOBILE_PROOF_OK', OUT);
}

main().catch(function(err) {
  console.error(err);
  process.exit(1);
});
