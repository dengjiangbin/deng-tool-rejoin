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
    singleHideUsernameIcon: html.includes('id="hideUsernameIcon"') && !html.includes('data-icon="eye-off"'),
    noGuestUi: !html.includes('>Guest<') && !html.includes('Sign in to sync profile') && !html.includes('inventory-action-btn--login" title="Sign in"'),
    hasLogout: html.includes('>Logout<'),
    hasScript: html.includes('>Script<'),
    normalizePollPlayerStats: html.includes('function normalizePollPlayerStats'),
    statRefreshCycleProof: html.includes('entry._statRefreshCycleProof'),
    pollPipelineIntact: html.includes('applyInventoryPollPayload') && /POLL_MS\s*=\s*10000/.test(html),
    headerTitle: html.includes('DENG Inventory Tracker'),
    headerSubtitle: html.includes('Track Your Fish It Accounts'),
  };
  const outDir = path.join(__dirname, '..', 'proofs');
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'blocker10ztc_side_controls_stat_refresh_proof.html'), html, 'utf8');
  fs.writeFileSync(path.join(outDir, 'blocker10ztc_side_controls_stat_refresh_proof.json'), JSON.stringify(checks, null, 2), 'utf8');
  console.log('BLOCKER10ZTC_SIDE_CONTROLS_STAT_REFRESH_PROOF', JSON.stringify(checks));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
