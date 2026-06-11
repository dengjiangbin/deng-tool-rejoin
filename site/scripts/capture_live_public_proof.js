#!/usr/bin/env node
'use strict';

const path = require('path');
const { chromium } = require('playwright');

const BASE = process.env.TOOL_SITE_PUBLIC_URL || 'https://tool.deng.my.id';
const OUT = path.join(__dirname, '..', 'proofs');

async function shot(page, url, file, viewport, beforeShot) {
  await page.setViewportSize(viewport);
  await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
  if (beforeShot) await beforeShot(page);
  await page.screenshot({ path: path.join(OUT, file), fullPage: true });
}

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage();

  await shot(page, `${BASE}/`, 'live_home_desktop.png', { width: 1440, height: 900 });
  await shot(page, `${BASE}/`, 'live_home_statistic.png', { width: 1440, height: 900 }, async (p) => {
    await p.locator('a[data-nav-section="statistic"]').click();
    await p.waitForTimeout(800);
  });
  await shot(page, `${BASE}/`, 'live_home_about.png', { width: 1440, height: 900 }, async (p) => {
    await p.locator('a[data-nav-section="about"]').click();
    await p.waitForTimeout(800);
  });
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
