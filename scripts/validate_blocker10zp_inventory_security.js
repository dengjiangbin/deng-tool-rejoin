#!/usr/bin/env node
/**
 * BLOCKER10ZP: live inventory/security smoke checks for /inventory page and public dist URLs.
 */
const fs = require('fs');
const path = require('path');
const https = require('https');
const ejs = require(path.join(__dirname, '..', 'site', 'node_modules', 'ejs'));

const {
  BLOCKER10ZQ_CLEAN_DIST_REPO_LIVE_CACHE_REQUEST_PM2_HEALTH_MARKER,
  BLOCKER10ZP_CLEAN_PUBLIC_REPO_HISTORY_PURGE_INVENTORY_COPY_FIX_MARKER,
} = require('../site/src/fishitTrackerBuild');
const {
  CLEAN_TRACKER_LOADSTRING,
  PROTECTED_DIST_RAW_URL,
  PUBLIC_TRACKER_GITHUB_REPO,
  LEGACY_TRACKER_GITHUB_REPO,
} = require('../site/src/fishitTrackerLoadstring');
const { buildTrackerPageLocals } = require('../site/src/fishitTrackerRoutes');
const layoutPath = path.join(__dirname, '..', 'site', 'views', 'layout.ejs');
const trackerPath = path.join(__dirname, '..', 'site', 'views', 'fishit_tracker.ejs');

function fetchHead(url) {
  return new Promise((resolve, reject) => {
    https.get(`${url}?v=${Date.now()}`, (res) => {
      res.resume();
      resolve(res.statusCode || 0);
    }).on('error', reject);
  });
}

async function renderInventoryHtml(query = {}) {
  const locals = buildTrackerPageLocals({ query });
  return ejs.renderFile(trackerPath, locals, { async: true });
}

async function main() {
  const errors = [];

  if (BLOCKER10ZP_CLEAN_PUBLIC_REPO_HISTORY_PURGE_INVENTORY_COPY_FIX_MARKER
    !== 'BLOCKER10ZP_CLEAN_PUBLIC_REPO_HISTORY_PURGE_INVENTORY_COPY_FIX_2026_06_10') {
    errors.push('BLOCKER10ZP marker mismatch in fishitTrackerBuild.js');
  }

  const layout = fs.readFileSync(layoutPath, 'utf8');
  if (!layout.includes('href="/inventory"')) errors.push('layout Inventory nav must href="/inventory"');
  if (layout.match(/Inventory[\s\S]{0,120}href="\/tracker"/)) {
    errors.push('layout Inventory nav must not href="/tracker"');
  }

  const html = await renderInventoryHtml({});
  const queryHtml = await renderInventoryHtml({ username: 'denghub2' });

  for (const [label, page] of [['inventory', html], ['inventory?username=denghub2', queryHtml]]) {
    if (!page.includes(BLOCKER10ZQ_CLEAN_DIST_REPO_LIVE_CACHE_REQUEST_PM2_HEALTH_MARKER)) {
      errors.push(`${label}: missing BLOCKER10ZQ marker`);
    }
    if (!page.includes('id="usernameInput"')) errors.push(`${label}: missing username input`);
    if (page.includes('id="usernameInput" disabled')) errors.push(`${label}: username input disabled`);
    if (!page.includes('id="copyBtn"')) errors.push(`${label}: missing copy button`);
    if (!page.includes('id="copyScriptTextarea"')) errors.push(`${label}: missing copy textarea fallback`);
    if (!page.includes(CLEAN_TRACKER_LOADSTRING)) errors.push(`${label}: missing canonical loadstring`);
    if (page.includes('/main/tracker.lua')) errors.push(`${label}: exposes root raw tracker URL`);
    if (!page.includes('dist/tracker.lua')) errors.push(`${label}: missing dist/tracker.lua reference`);
    if (!page.includes('copyTrackerScript')) errors.push(`${label}: missing copy fallback JS`);
    if (!page.includes('initFromQueryUsername')) errors.push(`${label}: missing query username bootstrap`);
  }

  if (!queryHtml.includes('"denghub2"')) errors.push('inventory?username=denghub2 must embed initial username');

  const rootLegacy = await fetchHead(`https://raw.githubusercontent.com/${LEGACY_TRACKER_GITHUB_REPO}/main/tracker.lua`);
  const distLegacy = await fetchHead(`https://raw.githubusercontent.com/${LEGACY_TRACKER_GITHUB_REPO}/main/dist/tracker.lua`);
  if (rootLegacy === 200) errors.push('legacy repo root tracker.lua still public');
  if (distLegacy !== 200) errors.push('legacy repo dist/tracker.lua not reachable');

  let rootPublic = 0;
  let distPublic = 0;
  try {
    rootPublic = await fetchHead(`https://raw.githubusercontent.com/${PUBLIC_TRACKER_GITHUB_REPO}/main/tracker.lua`);
    distPublic = await fetchHead(`https://raw.githubusercontent.com/${PUBLIC_TRACKER_GITHUB_REPO}/main/dist/tracker.lua`);
    if (rootPublic === 200) errors.push('clean public repo root tracker.lua is public');
    if (distPublic !== 200) console.log('SKIP clean public repo dist check: not live yet (HTTP ' + distPublic + ')');
  } catch (e) {
    console.log('SKIP clean public repo live check:', e.message);
  }

  if (errors.length) {
    console.error('BLOCKER10ZP_INVENTORY_SECURITY_VALIDATION FAILED');
    for (const err of errors) console.error('  -', err);
    process.exit(1);
  }

  console.log('BLOCKER10ZP_INVENTORY_SECURITY_VALIDATION OK');
  console.log('  marker:', BLOCKER10ZQ_CLEAN_DIST_REPO_LIVE_CACHE_REQUEST_PM2_HEALTH_MARKER);
  console.log('  loadstring URL:', PROTECTED_DIST_RAW_URL);
  console.log('  legacy root:', rootLegacy, 'legacy dist:', distLegacy);
  console.log('  clean root:', rootPublic || 'skipped', 'clean dist:', distPublic || 'skipped');
}

main().catch((e) => {
  console.error('BLOCKER10ZP_INVENTORY_SECURITY_VALIDATION FAILED:', e.message);
  process.exit(1);
});
