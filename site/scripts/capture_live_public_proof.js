#!/usr/bin/env node
'use strict';

const path = require('path');
const { chromium } = require('playwright');

const BASE = process.env.TOOL_SITE_PUBLIC_URL || 'https://tool.deng.my.id';
const OUT = path.join(__dirname, '..', 'proofs');

async function shot(page, url, file, viewport) {
  await page.setViewportSize(viewport);
  await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
  await page.screenshot({ path: path.join(OUT, file), fullPage: true });
}

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await shot(page, `${BASE}/`, 'live_home_desktop.png', { width: 1440, height: 900 });
  await shot(page, `${BASE}/login`, 'live_login_desktop.png', { width: 1440, height: 900 });
  await shot(page, `${BASE}/`, 'live_home_mobile.png', { width: 390, height: 844 });
  await shot(page, `${BASE}/login`, 'live_login_mobile.png', { width: 390, height: 844 });
  await browser.close();
  console.log('LIVE_PUBLIC_PROOF_OK', BASE, OUT);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
