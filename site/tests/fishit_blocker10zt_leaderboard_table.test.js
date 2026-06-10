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

const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZT leaderboard-style account table', () => {
  test('inventory page renders required table columns and controls', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    for (const label of [
      'Status', 'Username', 'Coins', 'Total Caught', 'Rarest Fish',
      'Backpack', 'Actions',
    ]) {
      assert.match(res.text, new RegExp(`>${label}<`));
    }
    assert.match(res.text, /placeholder="Search players\.\.\."/);
    assert.match(res.text, /data-account-filter="all"[^>]*>All</);
    assert.match(res.text, /data-account-filter="online"[^>]*>Online</);
    assert.match(res.text, /data-account-filter="offline"[^>]*>Offline</);
    assert.match(res.text, /id="accountsTableBody"/);
    assert.match(res.text, /data-open-backpack/);
    assert.match(res.text, /data-remove-account/);
    assert.match(res.text, /function renderAccountsTable/);
    assert.match(res.text, /function formatTableSyncAge/);
    assert.match(res.text, /id="hideUsernamesBtn"/);
    assert.match(res.text, /id="viewInventoryBtn"[^>]*title="Inventory View"/);
    assert.match(res.text, /id="viewInventoryBtn"[^>]*aria-label="Inventory View"/);
    assert.match(res.text, /id="inventoryViewSection"/);
    assert.match(res.text, /inventoryViewSectionEl\.hidden = accountViewMode !== 'inventory'/);
    assert.doesNotMatch(res.text, />Ruin</);
    assert.doesNotMatch(res.text, />Artifact</);
    assert.doesNotMatch(res.text, />Quest</);
    assert.doesNotMatch(res.text, /just now/i);
    assert.doesNotMatch(res.text, /\d+s ago/i);
  });

  test('offline table status uses red dead styling, not orange stale', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /function tableSyncFreshness/);
    assert.match(tpl, /accounts-status \.status-dot\.dead/);
    assert.doesNotMatch(tpl, /accounts-status[\s\S]*status-dot\.stale/);
    assert.doesNotMatch(tpl, /\.status-dot\.stale/);
    assert.match(tpl, /return 'dead'/);
  });

  test('leaderboard status format is compact duration without ago or just now', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /function formatTableSyncAge/);
    assert.doesNotMatch(tpl, /function formatSyncStatusAgo/);
    assert.doesNotMatch(tpl, /just now/);
    assert.match(tpl, /\$\{secs\}s/);
    assert.match(tpl, /\$\{Math\.floor\(secs \/ 60\)\}m/);
    assert.doesNotMatch(tpl, /tickAccountsTableStatus/);
  });

  test('quest and crossed icon/check columns are not present', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.doesNotMatch(tpl, />Quest</);
    assert.doesNotMatch(tpl, /quest progress/i);
    assert.doesNotMatch(tpl, /data-quest/i);
    assert.doesNotMatch(tpl, /Quest \(\d/i);
    assert.doesNotMatch(tpl, /elementFlags/i);
    assert.doesNotMatch(tpl, />Remove</);
    assert.doesNotMatch(tpl, /Last sync:/i);
    assert.doesNotMatch(tpl, /displayProgressStat/);
    assert.doesNotMatch(tpl, /viewCardsBtn/);
    assert.doesNotMatch(tpl, /is-cards-only/);
  });

  test('backpack buttons reuse main website nav backpack icon', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    const layout = fs.readFileSync(path.join(__dirname, '..', 'views', 'layout.ejs'), 'utf8');
    const backpackPath = 'M4 10a4 4 0 0 1 4-4h8a4 4 0 0 1 4 4v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V10Z';
    assert.match(layout, new RegExp(backpackPath));
    assert.match(tpl, /data-nav-icon="backpack"/);
    assert.match(tpl, new RegExp(backpackPath));
    assert.match(tpl, /BACKPACK_NAV_ICON/);
    assert.match(tpl, /data-open-backpack[\s\S]*\$\{BACKPACK_NAV_ICON\}/);
  });

  test('inventory view uses two-column mobile card grid', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /@media \(max-width:768px\)[\s\S]*\.inventory-grid[\s\S]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
    assert.match(tpl, /@media \(max-width:768px\)[\s\S]*\.fish-grid[\s\S]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
    assert.match(tpl, /@media \(max-width:280px\)[\s\S]*grid-template-columns:1fr/);
  });

  test('mobile table view uses compact table rows without vertical cards', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /th-short">User</);
    assert.match(tpl, /th-short">Coin</);
    assert.match(tpl, /th-short">Caught</);
    assert.match(tpl, /th-short">Rare</);
    assert.match(tpl, /@media \(max-width:768px\)[\s\S]*\.accounts-table[\s\S]*min-width:0/);
    assert.match(tpl, /@media \(max-width:768px\)[\s\S]*table-layout:fixed/);
    assert.doesNotMatch(tpl, /buildAccountMobileCardHtml/);
    assert.doesNotMatch(tpl, /accounts-mobile-list/);
  });

  test('mobile controls and compact loadstring rules exist', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /@media \(max-width:768px\)[\s\S]*\.accounts-filters[\s\S]*grid-template-columns:repeat\(3/);
    assert.match(tpl, /@media \(max-width:768px\)[\s\S]*\.accounts-actions[\s\S]*grid-template-columns:repeat\(5/);
    assert.match(tpl, /\.accounts-icon-btn[\s\S]*height:42px/);
    assert.match(tpl, /\.loadstring-box\.is-compact/);
    assert.match(tpl, /inventory-apk-embed/);
  });

  test('table view hides inventory section; inventory view shows it', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /id="inventoryViewSection"[^>]*hidden/);
    assert.match(tpl, /setAccountViewMode\('inventory'\)/);
    assert.match(tpl, /setAccountViewMode\('table'\)/);
    assert.match(tpl, /is-inventory-only/);
    assert.match(tpl, /\.inventory-view-section\[hidden\]/);
  });

  test('update-backpack stores and returns playerStats without quest fields', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'LeaderTableUser',
        userId: 991,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10',
        clientOrigin: 'roblox_tracker',
        items: [],
        playerStats: {
          coins: 201200,
          coinsText: '201.2K',
          totalCaught: 450,
          rarestFishChance: '1/4.50K',
          source: 'leaderstats',
          build: 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10',
          ruin: { current: 0, max: 4 },
          artifact: { current: 4, max: 4 },
          quest: { current: 1, max: 4 },
        },
      })
      .expect(200);

    const res = await request(app).get('/api/fishit-tracker/get-backpack/LeaderTableUser').expect(200);
    assert.equal(res.body.playerStats.coinsText, '201.2K');
    assert.equal(res.body.playerStats.totalCaught, 450);
    assert.equal(res.body.playerStats.rarestFishChance, '1/4.50K');
    assert.deepEqual(res.body.playerStats.ruin, { current: 0, max: 4 });
    assert.deepEqual(res.body.playerStats.artifact, { current: 4, max: 4 });
    assert.equal(res.body.playerStats.quest, undefined);
  });
});
