'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';

const trackerRouter = require('../src/fishitTrackerRoutes');
const {
  BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_MARKER,
  BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
} = require('../src/fishitTrackerBuild');

const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');
const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const manifest = require('../src/inventoryAssetManifest.json');
const INVENTORY_JS = path.join(__dirname, '..', 'public', 'assets', manifest.js);
const INVENTORY_CSS = path.join(__dirname, '..', 'public', 'assets', manifest.css);
const RAW_LUA = path.join(__dirname, '..', '..', '..', 'DENG PRIVATE SOURCE', 'fishtracker', 'tracker.lua');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZT3 sync status + coin probe + mobile account cards', () => {
  test('UI deploy marker and loader register fix build are wired', () => {
    const {
      BLOCKER10ZTF_STAT_INTERVAL_SOURCE_HARDENING_MARKER,
      LOADER_FIX_REGISTER_LIMIT_BUILD,
    } = require('../src/fishitTrackerBuild');
    assert.equal(BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, BLOCKER10ZTF_STAT_INTERVAL_SOURCE_HARDENING_MARKER);
    assert.equal(LOADER_FIX_REGISTER_LIMIT_BUILD, 'LOADER_FIX_REGISTER_LIMIT_2026_06_11');
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /BLOCKER10ZTF_STAT_INTERVAL_SOURCE_HARDENING_2026_06_11/);
  });

  test('frontend uses stats upload timestamps for connection freshness', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    const js = fs.readFileSync(INVENTORY_JS, 'utf8');
    assert.match(source, /function statsSyncTimestamp/);
    assert.match(js, /function statsSyncTimestamp/);
    assert.match(js, /connectionStatus/);
    assert.match(js, /cache: 'no-store'/);
  });

  test('pollUser uses shared applyInventoryPollPayload and connection freshness', () => {
    const js = fs.readFileSync(INVENTORY_JS, 'utf8');
    assert.match(js, /function entryConnectionFreshness/);
    assert.match(js, /function applyInventoryPollPayload/);
    assert.match(js, /applyInventoryPollPayload\(entry, key, data\)/);
  });

  test('mobile uses stacked account cards instead of compact desktop table', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    const js = fs.readFileSync(INVENTORY_JS, 'utf8');
    assert.match(tpl, /accounts-mobile-list/);
    assert.match(js, /function buildAccountMobileCardHtml/);
    assert.match(source, /@media \(max-width:768px\)[\s\S]*\.accounts-table-wrap \{ display:none/);
    assert.match(source, /@media \(max-width:768px\)[\s\S]*\.accounts-mobile-list \{ display:flex/);
    assert.match(source, /\.accounts-mobile-card__username[\s\S]*overflow-wrap:anywhere/);
    assert.doesNotMatch(source, /@media \(max-width:768px\)[\s\S]*table-layout:fixed/);
  });

  test('inventory view keeps 2-column mobile grid', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    const css = fs.readFileSync(INVENTORY_CSS, 'utf8');
    assert.match(source, /@media \(max-width:768px\)[\s\S]*\.inventory-grid[\s\S]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
    assert.match(css, /grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  });

  test('tracker_status updates heartbeat but does not mark stats sync green', async () => {
    const app = makeApp();
    const username = 'SyncProofUser';
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username,
        userId: 4421,
        isOnline: true,
        trackerBuild: 'LOADER_FIX_REGISTER_LIMIT_2026_06_11',
        phase: 'live',
      })
      .expect(200);

    const debug = await request(app).get(`/api/fishit-tracker/debug/${username}`).expect(200);
    assert.equal(debug.body.ok, true);
    assert.ok(debug.body.lastSeenAt);
    assert.ok(debug.body.lastHeartbeatAt || debug.body.lastSeenAt);
    assert.ok(debug.body.lastUploadReceivedAt);
    assert.ok(debug.body.lastUploadAcceptedAt);
    assert.equal(debug.body.lastUploadRejectedAt, null);
    assert.equal(debug.body.lastUploadRejectReason, null);
    assert.equal(debug.body.lastUploadPayloadType, 'tracker_status');
    assert.equal(debug.body.syncProof.isOnline, false);
    assert.equal(debug.body.syncProof.statusColor, 'yellow');
    assert.equal(debug.body.syncProof.connectionStatus, 'stale');
  });

  test('heartbeat alone stays stale when inventory upload is old', () => {
    const { deriveConnectionStatus } = require('../src/fishitTrackerRoutes');
    const now = Date.now();
    const stale = new Date(now - 3600_000).toISOString();
    const freshHb = new Date(now - 1000).toISOString();
    const st = deriveConnectionStatus({
      trackerBuild: 'LOADER_FIX_REGISTER_LIMIT_2026_06_11',
      lastHeartbeatAt: freshHb,
      lastSeenAt: freshHb,
      lastInventoryAt: stale,
      lastStatsUploadAt: stale,
      lastSnapshotUploadAt: stale,
    });
    assert.equal(st.connectionStatus, 'stale');
    assert.equal(st.connectionStatusColor, 'yellow');
    assert.equal(st.connectionStatusMessage, 'Heartbeat only, stats stale');
  });

  test('missing coins does not force offline when sync is fresh', async () => {
    const app = makeApp();
    const username = 'NoCoinsStillLive';
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username,
        userId: 4423,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10',
        clientOrigin: 'roblox_tracker',
        items: [],
        playerStats: {
          totalCaught: 500,
          totalCaughtText: '500',
          rarestFishChance: '1/4M',
          source: 'leaderstats',
          build: 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10',
        },
        playerStatsDebug: {
          enabled: true,
          build: 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10',
          coinProbe: {
            source: 'missing',
            candidateKeys: ['Caught', 'Rarest Fish'],
            leaderstatsChildren: [{ name: 'Caught', value: '500' }, { name: 'Rarest Fish', value: '1/4M' }],
          },
        },
      })
      .expect(200);

    const res = await request(app).get(`/api/fishit-tracker/get-backpack/${username}`).expect(200);
    assert.equal(res.body.isOnline, true);
    assert.equal(res.body.playerStats.coinsText, undefined);
    assert.equal(res.body.playerStats.totalCaughtText, '500');
    assert.equal(res.body.playerStats.rarestFishChance, '1/4M');
  });

  test('coinProbe sanitiser keeps leaderstatsChildren', () => {
    const { sanitisePlayerStatsDebug } = require('../src/fishitPlayerStats');
    const out = sanitisePlayerStatsDebug({
      enabled: true,
      build: 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10',
      coinProbe: {
        source: 'missing',
        candidateKeys: ['Coins'],
        leaderstatsChildren: [{ name: 'Caught', value: '68,885' }],
      },
    });
    assert.deepEqual(out.coinProbe.leaderstatsChildren, [{ name: 'Caught', value: '68,885' }]);
  });

  test('private Lua includes leaderstatsChildren and sync debug prints', () => {
    if (!fs.existsSync(RAW_LUA)) return;
    const raw = fs.readFileSync(RAW_LUA, 'utf8');
    assert.match(raw, /leaderstatsChildren = collectLeaderstatsChildren\(\)/);
    assert.match(raw, /LOADER_FIX_REGISTER_LIMIT_2026_06_11/);
    assert.match(raw, /print\("TRACKER_BUILD=" \.\. TRACKER_BUILD\)/);
    assert.match(raw, /print\("UPLOAD_URL=" \.\. tostring\(opts\.url or TRACKER_URL\)\)/);
    assert.match(raw, /DASHBOARD_SEND tracker_status/);
  });
});
