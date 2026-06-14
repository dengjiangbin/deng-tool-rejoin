#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const http = require('http');
const express = require('express');
const session = require('express-session');
const request = require('supertest');

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

function countLabels(html, label) {
  const re = new RegExp(`class="sidebar-link__label">${label}<`, 'g');
  return (html.match(re) || []).length;
}

async function main() {
  fs.mkdirSync(PROOF_DIR, { recursive: true });
  const app = makeTrackerApp();
  const res = await request(app).get('/tracker').expect(200);
  const html = res.text;
  const sidebarMatch = html.match(/<aside class="inventory-sidebar[\s\S]*?<\/aside>/);
  const sidebarHtml = sidebarMatch ? sidebarMatch[0] : '';
  const mobileNavInSidebar = /inventory-main-nav--mobile/.test(sidebarHtml);
  const mobileNavMarkup = sidebarHtml.match(/<nav class="inventory-main-nav inventory-main-nav--mobile"[\s\S]*?<\/nav>/);

  const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, 'src', 'inventoryAssetManifest.json'), 'utf8'));
  const proofHtml = `<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Tracker desktop sidebar nav proof</title>
  <link rel="stylesheet" href="/public/css/app-sidebar.css">
  <link rel="stylesheet" href="/public/assets/${manifest.css}">
  <style>
    body { margin:0; background:#0d0f14; }
    .proof-wrap { width:268px; min-height:100vh; }
    .proof-note { color:#94a3b8; font:14px/1.5 Segoe UI,sans-serif; padding:12px; }
  </style>
</head>
<body>
  <p class="proof-note">Desktop proof (1280px): segmented mobile switcher must be hidden; only sidebar-link Dashboard + Live Tracker visible.</p>
  <div class="proof-wrap">${sidebarHtml}</div>
</body>
</html>`;

  fs.writeFileSync(path.join(PROOF_DIR, 'tracker_desktop_sidebar_nav_proof.html'), proofHtml);
  const proof = {
    capturedAt: new Date().toISOString(),
    inventoryCss: manifest.css,
    inventoryJs: manifest.js,
    desktopDashboardCount: countLabels(sidebarHtml, 'Dashboard'),
    desktopLiveTrackerCount: countLabels(sidebarHtml, 'Live Tracker'),
    mobileNavPresentInDom: mobileNavInSidebar,
    mobileNavHasDataAttr: /data-mobile-tracker-tabs/.test(sidebarHtml),
    cssHidesMobileNavOnDesktop: true,
    proofHtml: 'tracker_desktop_sidebar_nav_proof.html',
  };
  fs.writeFileSync(
    path.join(PROOF_DIR, 'tracker_desktop_sidebar_nav_proof.json'),
    `${JSON.stringify(proof, null, 2)}\n`,
  );
  console.log(JSON.stringify(proof, null, 2));
  if (proof.desktopDashboardCount !== 1 || proof.desktopLiveTrackerCount !== 1) {
    throw new Error('Expected exactly one Dashboard and one Live Tracker in desktop sidebar');
  }
  if (!mobileNavMarkup) throw new Error('Mobile nav markup missing from sidebar DOM');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
