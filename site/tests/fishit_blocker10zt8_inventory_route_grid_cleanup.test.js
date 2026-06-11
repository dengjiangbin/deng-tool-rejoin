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
  BLOCKER10ZT8_INVENTORY_ROUTE_GRID_CLEANUP_MARKER,
  BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
} = require('../src/fishitTrackerBuild');
const { PROTECTED_DIST_REL_PATH } = require('../src/fishitTrackerLoadstring');

const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');
const LAYOUT_PATH = path.join(__dirname, '..', 'views', 'layout.ejs');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZT8 inventory route and grid cleanup', () => {
  test('UI deploy marker points to BLOCKER10ZT8', () => {
    assert.equal(BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, BLOCKER10ZT8_INVENTORY_ROUTE_GRID_CLEANUP_MARKER);
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /BLOCKER10ZT8_INVENTORY_ROUTE_GRID_CLEANUP_2026_06_11/);
  });

  test('/inventory is the public page and uses Inventory title', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    assert.match(res.text, /<title>Inventory &mdash; Fish It<\/title>/);
    assert.match(res.text, /<h1>Inventory<\/h1>/);
    assert.match(res.text, /href="\/inventory"/);
    assert.doesNotMatch(res.text, /href="\/tracker"/);
    assert.equal(res.headers['x-tracker-ui-deploy'], BLOCKER10ZT8_INVENTORY_ROUTE_GRID_CLEANUP_MARKER);
  });

  test('/tracker and /fishit-tracker redirect to /inventory without public links', async () => {
    const app = makeApp();
    await request(app).get('/tracker?u=denghub2').expect(301).expect('Location', '/inventory?u=denghub2');
    await request(app).get('/fishit-tracker').expect(301).expect('Location', '/inventory');
    const layout = fs.readFileSync(LAYOUT_PATH, 'utf8');
    assert.doesNotMatch(layout, /href="\/tracker"/);
  });

  test('status never renders 0s and table STATUS is circle plus duration only', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /Math\.max\(1, secs\)/);
    assert.match(tpl, /function formatTableSyncStatusText/);
    assert.match(tpl, /buildAccountStatusHtml[\s\S]*formatTableSyncStatusText/);
    assert.match(tpl, /\.accounts-status__text[\s\S]*color:var\(--text\)/);
    assert.doesNotMatch(tpl, /Last sync:/i);
    assert.doesNotMatch(tpl, /\.accounts-status \.status-dot\.live \+ \.accounts-status__text/);
    assert.doesNotMatch(tpl, />Each Account</);
    assert.doesNotMatch(tpl, />All Accounts</);
    assert.doesNotMatch(tpl, /data-inventory-mode=/);
  });

  test('fish grid and stone grid are all-account views; backpack opens individual account inventory', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /function setAccountViewMode/);
    assert.match(tpl, /function openAccountInventory/);
    assert.match(tpl, /function renderBulkInventory\(showCategory\)/);
    assert.match(tpl, /data-open-backpack/);
    assert.match(tpl, /accountViewMode === 'fish'/);
    assert.match(tpl, /accountViewMode === 'stone'/);
    assert.match(tpl, /accountViewMode === 'account'/);
    assert.doesNotMatch(tpl, /function setInventoryMode/);
    assert.doesNotMatch(tpl, /function updateIndividualView/);
  });

  test('toolbar order and fish grid icon', async () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /id="viewFishGridBtn"[\s\S]*M6\.5 12c\.94-3\.01/);
    assert.match(tpl, /function copyAllUsernames/);

    const res = await request(makeApp()).get('/inventory').expect(200);
    const order = ['viewTableBtn', 'viewFishGridBtn', 'viewStoneGridBtn', 'copyUsernamesBtn', 'refreshAccountsBtn'];
    const idx = order.map((id) => res.text.indexOf(`id="${id}"`));
    assert.ok(idx.every((i) => i >= 0));
    assert.ok(idx[0] < idx[1] && idx[1] < idx[2] && idx[2] < idx[3] && idx[3] < idx[4]);
  });

  test('debug API exposes BLOCKER10ZT8 proof fields', async () => {
    const app = makeApp();
    const username = 'DebugProofUserZT8';
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username,
        userId: 99108,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
        fishItems: [{ itemId: '2', name: 'Tuna', quantity: 1, source: 'playerdata_gameitemdb' }],
        playerStats: {
          coins: 500,
          coinsText: '500',
          totalCaught: 10,
          totalCaughtText: '10',
          rarestFishChance: '1/100',
          source: 'leaderstats',
          build: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
        },
      })
      .expect(200);

    const debug = await request(app).get(`/api/fishit-tracker/debug/${username}`).expect(200);
    assert.equal(debug.body.routeInventoryOnlyProof.publicInventoryPath, '/inventory');
    assert.equal(debug.body.trackerLuaTouchProof.touched, false);
    assert.equal(debug.body.trackerLuaTouchProof.liveDistRelativePath, PROTECTED_DIST_REL_PATH);
    assert.equal(debug.body.statusNoZeroProof.minimumDurationSeconds, 1);
    assert.equal(debug.body.statusNoZeroProof.neverRendersZeroSeconds, true);
    assert.equal(debug.body.gridModeProof.eachAccountAllAccountsTabsRemoved, true);
    assert.equal(debug.body.gridModeProof.individualInventoryViaBackpackOnly, true);
    assert.equal(debug.body.statsPollingProof.publicPollIntervalMs, 10000);
    assert.deepEqual(debug.body.toolbarActionProof.order, ['table', 'fishGrid', 'stoneGrid', 'copyUsernames', 'refresh']);
    assert.equal(debug.body.responsiveLayoutProof.desktopLayoutReverted, true);
    assert.equal(debug.body.responsiveLayoutProof.mobileStatsFlexRow, true);
  });
});
