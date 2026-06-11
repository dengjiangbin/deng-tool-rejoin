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
  BLOCKER10ZT7_LIVE_STATS_STATUS_TOOLBAR_LAYOUT_MARKER,
  BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
} = require('../src/fishitTrackerBuild');

const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZT7 live stats, status format, toolbar, layout', () => {
  test('UI deploy marker points to BLOCKER10ZT7', () => {
    assert.equal(BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, BLOCKER10ZT7_LIVE_STATS_STATUS_TOOLBAR_LAYOUT_MARKER);
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /BLOCKER10ZT7_LIVE_STATS_STATUS_TOOLBAR_LAYOUT_2026_06_11/);
  });

  test('status format is circle duration username without Last sync label', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /function formatEntrySyncStatusText/);
    assert.match(tpl, /return `\$\{duration\} \$\{name\}`/);
    assert.doesNotMatch(tpl, /Last sync:/i);
    assert.match(tpl, /data-table-sync-text/);
    assert.match(tpl, /entry\.lastSyncAt = pollAt/);
    assert.doesNotMatch(tpl, /if \(ts\) entry\.lastSyncAt = ts/);
  });

  test('toolbar order is table, fish grid, stone grid, copy usernames, refresh', async () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /id="viewTableBtn"/);
    assert.match(tpl, /id="viewFishGridBtn"[^>]*title="Fish grid"/);
    assert.match(tpl, /id="viewStoneGridBtn"[^>]*title="Stone grid"/);
    assert.match(tpl, /id="copyUsernamesBtn"[^>]*title="Copy all usernames"/);
    assert.match(tpl, /id="refreshAccountsBtn"/);
    assert.match(tpl, /function copyAllUsernames/);
    assert.doesNotMatch(tpl, /copyAccountsTableSummary/);
    assert.doesNotMatch(tpl, /id="viewInventoryBtn"/);

    const res = await request(makeApp()).get('/inventory').expect(200);
    const tableIdx = res.text.indexOf('id="viewTableBtn"');
    const fishIdx = res.text.indexOf('id="viewFishGridBtn"');
    const stoneIdx = res.text.indexOf('id="viewStoneGridBtn"');
    const copyIdx = res.text.indexOf('id="copyUsernamesBtn"');
    const refreshIdx = res.text.indexOf('id="refreshAccountsBtn"');
    assert.ok(tableIdx < fishIdx && fishIdx < stoneIdx && stoneIdx < copyIdx && copyIdx < refreshIdx);
  });

  test('desktop table forced at min-width 769 and mobile stats horizontal', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /@media \(min-width:769px\)[\s\S]*\.accounts-table-wrap \{ display:block !important/);
    assert.match(tpl, /@media \(min-width:769px\)[\s\S]*\.accounts-mobile-list \{ display:none !important/);
    assert.match(tpl, /@media \(max-width:768px\)[\s\S]*\.accounts-mobile-card__grid--stats[\s\S]*flex-direction:row/);
  });

  test('debug API exposes proof fields only in debug route', async () => {
    const app = makeApp();
    const username = 'DebugProofUserZT7';
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username,
        userId: 99104,
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
    assert.equal(debug.body.statusFormatProof.publicFormat, '[circle] <duration> <username>');
    assert.equal(debug.body.statusFormatProof.literalLastSyncLabelPresent, false);
    assert.equal(debug.body.statsPollingProof.publicPollIntervalMs, 10000);
    assert.equal(debug.body.uploadIntervalProof.trackerUploadIntervalSeconds, 10);
    assert.deepEqual(debug.body.toolbarActionProof.order, ['table', 'fishGrid', 'stoneGrid', 'copyUsernames', 'refresh']);
    assert.equal(debug.body.toolbarActionProof.copyUsernamesOnly, true);
    assert.equal(debug.body.responsiveLayoutProof.desktopLayoutReverted, true);
    assert.equal(debug.body.connectionIndicatorProof.indicatorColor, 'green');

    const backpack = await request(app).get(`/api/fishit-tracker/get-backpack/${username}`).expect(200);
    assert.equal(backpack.body.statusFormatProof, undefined);
    assert.equal(backpack.body.toolbarActionProof, undefined);
  });
});
