#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const http = require('http');

const OUT_HTML = path.join(__dirname, '..', 'proofs', 'blocker10zt7_live_stats_proof.html');
const OUT_JSON = path.join(__dirname, '..', 'proofs', 'blocker10zt7_live_stats_proof.json');

function fetch(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => resolve({ status: res.statusCode, body, headers: res.headers }));
    }).on('error', reject);
  });
}

async function main() {
  const inv = await fetch('http://127.0.0.1:8791/inventory');
  const tpl = inv.body;
  const checks = {
    status: inv.status,
    deploy: inv.headers['x-tracker-ui-deploy'] || null,
    pollMs10k: /const POLL_MS\s*=\s*10000/.test(tpl),
    syncTick1s: /const SYNC_TICK_MS\s*=\s*1000/.test(tpl),
    applyPollPayload: tpl.includes('function applyPollPayload'),
    formatEntrySyncStatusText: tpl.includes('function formatEntrySyncStatusText'),
    noLastSyncLabel: !/Last sync:/i.test(tpl),
    statusDurationUsername: /\$\{duration\} \$\{name\}/.test(tpl),
    copyAllUsernames: tpl.includes('function copyAllUsernames'),
    toolbarOrder: [
      'viewTableBtn',
      'viewFishGridBtn',
      'viewStoneGridBtn',
      'copyUsernamesBtn',
      'refreshAccountsBtn',
    ].every((id) => tpl.includes(`id="${id}"`)),
    desktopTable769: /@media \(min-width:769px\)[\s\S]*\.accounts-table-wrap \{ display:block !important/.test(tpl),
    mobileStatsRow: /@media \(max-width:768px\)[\s\S]*\.accounts-mobile-card__grid--stats[\s\S]*flex-direction:row/.test(tpl),
    marker: tpl.includes('BLOCKER10ZT7_LIVE_STATS_STATUS_TOOLBAR_LAYOUT_2026_06_11'),
  };
  fs.mkdirSync(path.dirname(OUT_HTML), { recursive: true });
  fs.writeFileSync(OUT_HTML, tpl, 'utf8');
  fs.writeFileSync(OUT_JSON, JSON.stringify(checks, null, 2), 'utf8');
  console.log('BLOCKER10ZT7_LIVE_STATS_PROOF', JSON.stringify(checks));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
