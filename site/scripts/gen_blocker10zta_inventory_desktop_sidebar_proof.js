#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const http = require('http');

const OUT_HTML = path.join(__dirname, '..', 'proofs', 'blocker10zta_inventory_desktop_sidebar_proof.html');
const OUT_JSON = path.join(__dirname, '..', 'proofs', 'blocker10zta_inventory_desktop_sidebar_proof.json');

function fetch(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, body, headers: res.headers }));
    }).on('error', reject);
  });
}

async function main() {
  const desktop = await fetch('http://127.0.0.1:8791/inventory');
  const apk = await fetch('http://127.0.0.1:8791/inventory?apk=1');
  const html = desktop.body;
  const checks = {
    status: desktop.status,
    deploy: desktop.headers['x-tracker-ui-deploy'] || null,
    marker: html.includes('BLOCKER10ZTA_INVENTORY_DESKTOP_SIDEBAR_2026_06_11'),
    hasBrandSection: /inventory-sidebar__title">DENG Inventory</.test(html) && /inventory-sidebar__subtitle">Fish It</.test(html),
    hasSidebarBottomControls: /id="hideUsernamesBtn"/.test(html) && /id="sidebarScriptBtn"/.test(html) && /inventory-profile-card/.test(html),
    noBackLink: !/>Back to DENG Tool</.test(html),
    noMiddleNav: !/class="nav-list"/.test(html) && !/>Dashboard</.test(html) && !/>Solver</.test(html),
    hideUsernameEyeIcons: /data-icon="eye"/.test(html) && /data-icon="eye-off"/.test(html),
    desktopHiddenLoadstring: /loadstring-box--desktop-hidden/.test(html),
    apkCompactLoadstring: /loadstring-box is-compact/.test(apk.body),
    apkNoBackLink: !/>Back to DENG Tool</.test(apk.body),
    pollPipelineIntact: /const POLL_MS\s*=\s*10000/.test(html) && html.includes('function applyInventoryPollPayload'),
  };
  fs.mkdirSync(path.dirname(OUT_HTML), { recursive: true });
  fs.writeFileSync(OUT_HTML, html, 'utf8');
  fs.writeFileSync(OUT_JSON, JSON.stringify(checks, null, 2), 'utf8');
  console.log('BLOCKER10ZTA_INVENTORY_DESKTOP_SIDEBAR_PROOF', JSON.stringify(checks));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
