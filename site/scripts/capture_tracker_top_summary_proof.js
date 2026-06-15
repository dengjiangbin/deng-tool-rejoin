#!/usr/bin/env node
'use strict';

// Proof: /tracker top summary cards render with REAL DB image assets only,
// correct 2-col desktop / 1-col mobile layout, and a green online count.
// Renders the real built template, serves it (so asset URLs resolve through the
// tracker router), then screenshots desktop + mobile and asserts every top
// image has naturalWidth > 0 (no broken images, no fallbacks).

const fs = require('fs');
const path = require('path');
const http = require('http');
const ejs = require('ejs');
const express = require('express');
const { chromium } = require('playwright');

const ROOT = path.join(__dirname, '..');
const OUT = path.join(ROOT, 'proofs');
const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, 'src', 'inventoryAssetManifest.json'), 'utf8'));

const topIcons = require('../src/fishitTrackerTopSummaryIcons');

const FISH_DIR = path.join(ROOT, 'data', 'fish_image_cache');
const STONE_DIR = path.join(ROOT, 'data', 'stone_image_cache');

function renderPage() {
  const icons = topIcons.resolveTopSummaryIcons();
  const tplPath = path.join(ROOT, 'views', 'fishit_tracker.ejs');
  const tpl = fs.readFileSync(tplPath, 'utf8');
  const html = ejs.render(tpl, {
    trackerTopSummaryIcons: icons,
    viewer: { loggedIn: false, avatarUrl: '', displayName: '' },
    inventoryRuntimeConfig: {},
    inventoryAssetCssUrl: '/public/assets/' + manifest.css,
    inventoryAssetJsUrl: '/public/assets/' + manifest.js,
    renderBuild: 'proof', publicApiBuild: 'proof', trackerUiDeployMarker: 'proof',
    title: 'proof', layout: false,
  }, { filename: tplPath });
  return { html, icons };
}

async function shoot(page, label, width, height) {
  await page.setViewportSize({ width, height });
  await page.goto('http://127.0.0.1:' + PORT + '/', { waitUntil: 'domcontentloaded', timeout: 20000 });
  // Top images are eagerly loaded; wait for the icon images to settle rather
  // than networkidle (the live tracker keeps polling).
  await page.waitForSelector('.tracker-top-summary-icon .tracker-top-summary-img', { timeout: 10000 });
  await page.waitForFunction(() => {
    const imgs = Array.from(document.querySelectorAll('.tracker-top-summary-icon .tracker-top-summary-img'));
    return imgs.length > 0 && imgs.every((i) => i.complete);
  }, { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(600);
  const info = await page.evaluate(() => {
    const grid = document.getElementById('inventoryStats');
    const imgs = Array.from(document.querySelectorAll('.tracker-top-summary-icon .tracker-top-summary-img'));
    const onlineCount = document.querySelector('.tracker-online-value .online-count');
    return {
      cols: grid ? getComputedStyle(grid).gridTemplateColumns : null,
      cardCount: grid ? grid.children.length : 0,
      images: imgs.map((i) => ({ alt: i.alt, src: i.getAttribute('src'), nw: i.naturalWidth, complete: i.complete })),
      onlineColor: onlineCount ? getComputedStyle(onlineCount).color : null,
    };
  });
  const file = path.join(OUT, 'tracker_top_summary_' + label + '.png');
  await page.locator('#inventoryStats').screenshot({ path: file });
  return { info, file };
}

let PORT = 0;

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const { html, icons } = renderPage();

  const app = express();
  app.get('/', (_req, res) => res.type('html').send(html));
  app.use('/public', express.static(path.join(ROOT, 'public')));
  const serveFrom = (dir) => (req, res) => {
    const file = path.basename(String(req.params.filename || ''));
    const full = path.join(dir, file);
    if (file && fs.existsSync(full)) return res.sendFile(full);
    return res.status(404).end();
  };
  app.get('/api/fishit-tracker/assets/fish/:filename', serveFrom(FISH_DIR));
  app.get('/api/fishit-tracker/assets/stones/:filename', serveFrom(STONE_DIR));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  PORT = server.address().port;

  console.error('[proof] server listening on', PORT);
  const browser = await chromium.launch();
  console.error('[proof] chromium launched');
  const page = await browser.newPage();
  const desktop = await shoot(page, 'desktop_1280', 1280, 900);
  console.error('[proof] desktop captured');
  const mobile = await shoot(page, 'mobile_390', 390, 900);
  console.error('[proof] mobile captured');
  await browser.close();
  server.close();

  const report = { resolvedIcons: icons.proof, desktop: desktop.info, mobile: mobile.info,
    files: { desktop: desktop.file, mobile: mobile.file } };
  fs.writeFileSync(path.join(OUT, 'tracker_top_summary_proof.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));

  const allImgs = [...desktop.info.images, ...mobile.info.images];
  const broken = allImgs.filter((i) => !i.src || i.nw === 0);
  if (broken.length) {
    console.error('BROKEN_IMAGES', JSON.stringify(broken));
    process.exit(2);
  }
  if (!/^repeat|,/.test(desktop.info.cols) && desktop.info.cols.split(' ').length !== 2) {
    console.error('DESKTOP_NOT_TWO_COLUMNS', desktop.info.cols);
    process.exit(3);
  }
  console.log('TRACKER_TOP_SUMMARY_PROOF_OK');
}

main().catch((err) => { console.error(err); process.exit(1); });
