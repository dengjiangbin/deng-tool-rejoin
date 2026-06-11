#!/usr/bin/env node
'use strict';

const path = require('path');
const { chromium } = require('playwright');

const BASE = process.env.TOOL_SITE_PUBLIC_URL || 'https://tool.deng.my.id';
const OUT = path.join(__dirname, '..', 'proofs');

async function captureCountUp(page, url, filePrefix, viewport) {
  await page.setViewportSize(viewport);
  await page.route('**/api/public-stats', async (route) => {
    await new Promise((r) => setTimeout(r, 2500));
    await route.continue();
  });
  await page.route('**/api/fishit-tracker/public-network', async (route) => {
    await new Promise((r) => setTimeout(r, 2500));
    await route.continue();
  });
  await page.route('**/api/fishit/global', async (route) => {
    await new Promise((r) => setTimeout(r, 2500));
    await route.continue();
  });
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(120);
  await page.screenshot({ path: path.join(OUT, `${filePrefix}_start.png`), fullPage: false });
  await page.waitForTimeout(900);
  await page.screenshot({ path: path.join(OUT, `${filePrefix}_mid.png`), fullPage: false });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: path.join(OUT, `${filePrefix}_final.png`), fullPage: false });
}

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await captureCountUp(page, `${BASE}/#statistic`, 'live_countup_landing', { width: 1440, height: 900 });
  await browser.close();
  console.log('LIVE_COUNTUP_PROOF_OK', BASE, OUT);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
