#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const http = require('http');
const express = require('express');
const session = require('express-session');
const { chromium } = require('playwright');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = process.env.TOOL_SITE_COOKIE_SECRET
  || 'test-cookie-secret-that-is-long-enough-for-the-site-suite';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';

const ROOT = path.join(__dirname, '..');
const PROOF_DIR = path.join(ROOT, 'proofs');

function makeTrackerApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(ROOT, 'views'));
  app.locals.assetVersion = 'proof';
  app.use(session({
    secret: process.env.TOOL_SITE_COOKIE_SECRET,
    resave: false,
    saveUninitialized: false,
  }));
  app.use((req, _res, next) => {
    req.session.user = { username: 'ProofUser', discord_user_id: '123456789012345678' };
    req.session.csrfToken = 'proof-csrf';
    next();
  });
  app.use('/public', express.static(path.join(ROOT, 'public')));
  app.use(require('../src/fishitTrackerRoutes'));
  return app;
}

async function startPreviewServer(app) {
  const server = http.createServer(app);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  return { server, baseUrl: `http://127.0.0.1:${port}` };
}

async function main() {
  fs.mkdirSync(PROOF_DIR, { recursive: true });
  const app = makeTrackerApp();
  const { server, baseUrl } = await startPreviewServer(app);
  const browser = await chromium.launch();
  const page = await browser.newPage();

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(`${baseUrl}/tracker`, { waitUntil: 'domcontentloaded', timeout: 20000 });
  await page.waitForSelector('.inventory-sidebar.deng-app-sidebar', { timeout: 10000 });
  const sidebar = page.locator('.inventory-sidebar.deng-app-sidebar');
  await sidebar.screenshot({
    path: path.join(PROOF_DIR, 'tracker_desktop_sidebar_no_duplicate_nav_1280.png'),
  });

  const desktopNavLabels = await sidebar.locator('.sidebar-link__label').allTextContents();
  const mobileNavVisible = await sidebar.locator('[data-mobile-tracker-tabs]').isVisible();
  const mobileNavHiddenAttr = await sidebar.locator('[data-mobile-tracker-tabs]').getAttribute('hidden');

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${baseUrl}/tracker`, { waitUntil: 'domcontentloaded', timeout: 20000 });
  await page.waitForSelector('.inventory-sidebar', { timeout: 10000 });
  await page.locator('.inventory-sidebar').screenshot({
    path: path.join(PROOF_DIR, 'tracker_mobile_sidebar_segmented_nav_390.png'),
  });
  const mobileNavVisibleMobile = await page.locator('[data-mobile-tracker-tabs]').isVisible();

  await browser.close();
  await new Promise((resolve) => server.close(resolve));

  const proof = {
    capturedAt: new Date().toISOString(),
    desktopViewport: { width: 1280, height: 800 },
    mobileViewport: { width: 390, height: 844 },
    desktopSidebarLabels: desktopNavLabels,
    desktopDashboardCount: desktopNavLabels.filter((l) => l.trim() === 'Dashboard').length,
    desktopLiveTrackerCount: desktopNavLabels.filter((l) => l.trim() === 'Live Tracker').length,
    desktopMobileSwitcherVisible: mobileNavVisible,
    desktopMobileSwitcherHiddenAttr: mobileNavHiddenAttr,
    mobileSwitcherVisibleOnMobile: mobileNavVisibleMobile,
    screenshots: {
      desktop: 'tracker_desktop_sidebar_no_duplicate_nav_1280.png',
      mobile: 'tracker_mobile_sidebar_segmented_nav_390.png',
    },
  };
  fs.writeFileSync(
    path.join(PROOF_DIR, 'tracker_desktop_sidebar_nav_proof.json'),
    `${JSON.stringify(proof, null, 2)}\n`,
  );
  console.log(JSON.stringify(proof, null, 2));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
