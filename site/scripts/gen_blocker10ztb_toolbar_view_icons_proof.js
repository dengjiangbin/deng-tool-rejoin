#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';

const trackerRouter = require('../src/fishitTrackerRoutes');
const app = express();
app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, '..', 'views'));
app.use(trackerRouter);

async function main() {
  const res = await request(app).get('/inventory');
  const html = res.text;
  const checks = {
    marker: html.includes('accounts-view-icon'),
    sharedViewIconClass: html.includes('class="accounts-view-icon"'),
    fishToolbarIcon: html.includes('data-toolbar-icon="fish"'),
    oldFishIconRemoved: !html.includes('M6.5 12c.94-3.01'),
    fishTailPaths: html.includes('M6 12c-1-1-1-1.8 0-2.8'),
    tableIconPresent: html.includes('M3 3h18v18H3z'),
    stoneIconPresent: html.includes('M12 3 20 8v8l-8 5-8-5V8l8-5Z'),
    toolbarOrderOk: (() => {
      const order = ['viewTableBtn', 'viewFishGridBtn', 'viewStoneGridBtn', 'copyUsernamesBtn', 'refreshAccountsBtn'];
      const idx = order.map((id) => html.indexOf(`id="${id}"`));
      return idx.every((i) => i >= 0) && idx[0] < idx[1] && idx[1] < idx[2] && idx[2] < idx[3] && idx[3] < idx[4];
    })(),
    pollPipelineIntact: html.includes('applyInventoryPollPayload') && /POLL_MS\s*=\s*10000/.test(html),
  };
  const outDir = path.join(__dirname, '..', 'proofs');
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'blocker10ztb_toolbar_view_icons_proof.html'), html, 'utf8');
  fs.writeFileSync(path.join(outDir, 'blocker10ztb_toolbar_view_icons_proof.json'), JSON.stringify(checks, null, 2), 'utf8');
  console.log('BLOCKER10ZTB_TOOLBAR_VIEW_ICONS_PROOF', JSON.stringify(checks));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
