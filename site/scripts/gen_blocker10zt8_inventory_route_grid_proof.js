#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const http = require('http');

const OUT_HTML = path.join(__dirname, '..', 'proofs', 'blocker10zt8_inventory_route_grid_proof.html');
const OUT_JSON = path.join(__dirname, '..', 'proofs', 'blocker10zt8_inventory_route_grid_proof.json');

function fetch(url, opts = {}) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, opts, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => resolve({ status: res.statusCode, body, headers: res.headers }));
    });
    req.on('error', reject);
  });
}

async function main() {
  const inv = await fetch('http://127.0.0.1:8791/inventory');
  const legacy = await fetch('http://127.0.0.1:8791/tracker');
  const tpl = inv.body;
  const checks = {
    inventoryStatus: inv.status,
    legacyTrackerStatus: legacy.status,
    legacyTrackerLocation: legacy.headers.location || null,
    deploy: inv.headers['x-tracker-ui-deploy'] || null,
    titleInventory: /<title>Inventory &mdash; Fish It<\/title>/.test(tpl),
    noPublicTrackerHref: !/href="\/tracker"/.test(tpl),
    marker: tpl.includes('BLOCKER10ZT8_INVENTORY_ROUTE_GRID_CLEANUP_2026_06_11'),
    noZeroSecondsGuard: /Math\.max\(1, secs\)/.test(tpl),
    noEachAccountTabs: !/Each Account/.test(tpl),
    fishGridIcon: /id="viewFishGridBtn"[\s\S]*M6\.5 12c\.94-3\.01/.test(tpl),
    openAccountInventory: tpl.includes('function openAccountInventory'),
    renderBulkInventory: tpl.includes('function renderBulkInventory(showCategory)'),
    pollMs10k: /const POLL_MS\s*=\s*10000/.test(tpl),
    toolbarOrder: [
      'viewTableBtn',
      'viewFishGridBtn',
      'viewStoneGridBtn',
      'copyUsernamesBtn',
      'refreshAccountsBtn',
    ].every((id) => tpl.includes(`id="${id}"`)),
  };
  fs.mkdirSync(path.dirname(OUT_HTML), { recursive: true });
  fs.writeFileSync(OUT_HTML, tpl, 'utf8');
  fs.writeFileSync(OUT_JSON, JSON.stringify(checks, null, 2), 'utf8');
  console.log('BLOCKER10ZT8_INVENTORY_ROUTE_GRID_PROOF', JSON.stringify(checks));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
