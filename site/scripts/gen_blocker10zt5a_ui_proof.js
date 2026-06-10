#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const http = require('http');

const OUT = path.join(__dirname, '..', 'proofs', 'blocker10zt5a_ui_proof.html');

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
    hasCoinCol: tpl.includes('col-coins'),
    hasCaughtCol: tpl.includes('col-caught'),
    hasRareCol: tpl.includes('col-rare'),
    hasCoinMobile: tpl.includes('accounts-mobile-card__row-label">Coin'),
    hasCaughtMobile: tpl.includes('accounts-mobile-card__row-label">Caught'),
    hasRareMobile: tpl.includes('accounts-mobile-card__row-label">Rare'),
    noLastSyncMobile: !tpl.includes('accounts-mobile-card__row-label">Last sync'),
    noFishMobile: !tpl.includes('accounts-mobile-card__row-label">Fish'),
    noTypesMobile: !tpl.includes('accounts-mobile-card__row-label">Types'),
    noStatusMobile: !tpl.includes('accounts-mobile-card__row-label">Status'),
    syncAgeHidden: tpl.includes('.sync-age { display:none;'),
    tableSyncAgeHidden: tpl.includes('accounts-status [data-table-sync-age] { display:none;'),
    bestSyncTimestamp: tpl.includes('function bestSyncTimestamp'),
    isEntryConnectionLive: tpl.includes('function isEntryConnectionLive'),
  };
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, tpl, 'utf8');
  const report = path.join(__dirname, '..', 'proofs', 'blocker10zt5a_ui_proof.json');
  fs.writeFileSync(report, JSON.stringify(checks, null, 2), 'utf8');
  console.log('BLOCKER10ZT5A_UI_PROOF', JSON.stringify(checks));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
