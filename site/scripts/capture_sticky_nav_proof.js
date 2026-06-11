#!/usr/bin/env node
'use strict';

const path = require('path');
const { chromium } = require('playwright');

const BASE = process.env.TOOL_SITE_PUBLIC_URL || 'http://127.0.0.1:8791';
const OUT = path.join(__dirname, '..', 'proofs');

async function captureViewport(page, file, viewport) {
  await page.setViewportSize(viewport);
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(1200);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(OUT, file.replace('.png', '_top.png')), fullPage: false });

  await page.evaluate(() => window.scrollTo(0, Math.max(document.body.scrollHeight, 2400)));
  await page.waitForTimeout(500);

  const header = page.locator('header.deng-home-nav-wrap--fixed');
  const box = await header.boundingBox();
  const styles = await header.evaluate((el) => {
    const cs = window.getComputedStyle(el);
    return {
      position: cs.position,
      top: cs.top,
      zIndex: cs.zIndex,
    };
  });

  if (!box || box.y > 24) {
    throw new Error(`navbar not frozen after scroll: y=${box && box.y}`);
  }
  if (styles.position !== 'fixed' || styles.top !== '0px') {
    throw new Error(`navbar styles invalid: ${JSON.stringify(styles)}`);
  }
  if (Number(styles.zIndex) < 1000) {
    throw new Error(`navbar z-index too low: ${styles.zIndex}`);
  }

  await page.screenshot({ path: path.join(OUT, file), fullPage: false });
  return { box, styles, viewport };
}

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const desktop = await captureViewport(page, 'sticky_nav_desktop_scrolled.png', { width: 1440, height: 900 });
  const mobile = await captureViewport(page, 'sticky_nav_mobile_scrolled.png', { width: 390, height: 844 });
  await browser.close();
  console.log('STICKY_NAV_PROOF_OK', JSON.stringify({ BASE, desktop, mobile }));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
