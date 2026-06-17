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

// After the performance rebuild the page CSS/JS are extracted into hashed asset
// bundles and only a shell EJS is rendered, so behavioural (JS) and style (CSS)
// assertions are made against the inlined source-of-truth template, while the
// rendered HTML is checked over the canonical /tracker route (legacy /inventory
// is now a 301 alias).
const SRC = fs.readFileSync(path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs'), 'utf8');
const LAYOUT = fs.readFileSync(path.join(__dirname, '..', 'views', 'layout.ejs'), 'utf8');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZT leaderboard-style account table', () => {
  test('inventory page renders required table columns and controls', async () => {
    const res = await request(makeApp()).get('/tracker').expect(200);
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
    assert.match(res.text, /id="hideUsernamesBtn"/);
    assert.match(res.text, /id="viewFishGridBtn"[^>]*title="Fish grid"/);
    assert.match(res.text, /id="viewFishGridBtn"[^>]*aria-label="Fish grid"/);
    assert.match(res.text, /id="inventoryViewSection"/);
    // Row/behaviour wiring lives in the extracted bundle (source of truth).
    assert.match(SRC, /data-open-backpack/);
    assert.match(SRC, /data-remove-account/);
    assert.match(SRC, /function renderAccountsTable/);
    assert.match(SRC, /function formatTableSyncAge/);
    assert.match(SRC, /inventoryViewSectionEl\.hidden = !isGrid/);
    assert.doesNotMatch(res.text, />Ruin</);
    assert.doesNotMatch(res.text, />Artifact</);
    assert.doesNotMatch(res.text, />Quest</);
    assert.doesNotMatch(res.text, /just now/i);
    assert.doesNotMatch(res.text, /\d+s ago/i);
  });

  test('legacy /inventory alias redirects to canonical /tracker', async () => {
    const res = await request(makeApp()).get('/inventory');
    assert.equal(res.status, 301);
    assert.match(String(res.headers.location || ''), /\/tracker/);
  });

  test('offline table status uses red dead styling, not orange stale', () => {
    assert.match(SRC, /function tableSyncFreshness/);
    assert.match(SRC, /\.accounts-status \.status-dot\.dead/);
    assert.doesNotMatch(SRC, /\.accounts-status \.status-dot\.stale/);
    assert.match(SRC, /return 'dead'/);
  });

  test('leaderboard status format is compact duration without ago or just now', () => {
    assert.match(SRC, /function formatTableSyncAge/);
    assert.doesNotMatch(SRC, /function formatSyncStatusAgo/);
    assert.doesNotMatch(SRC, /just now/);
    assert.match(SRC, /\$\{secs\}s/);
    assert.match(SRC, /\$\{mins\}m/);
    assert.doesNotMatch(SRC, /tickAccountsTableStatus/);
  });

  test('quest and crossed icon/check columns are not present', () => {
    assert.doesNotMatch(SRC, />Quest</);
    assert.doesNotMatch(SRC, /quest progress/i);
    assert.doesNotMatch(SRC, /data-quest/i);
    assert.doesNotMatch(SRC, /Quest \(\d/i);
    assert.doesNotMatch(SRC, /elementFlags/i);
    assert.doesNotMatch(SRC, />Remove</);
    assert.doesNotMatch(SRC, /Last sync:/i);
    assert.doesNotMatch(SRC, /displayProgressStat/);
    assert.doesNotMatch(SRC, /viewCardsBtn/);
    assert.doesNotMatch(SRC, /is-cards-only/);
  });

  test('backpack buttons reuse main website nav backpack icon', () => {
    const backpackPath = 'M4 10a4 4 0 0 1 4-4h8a4 4 0 0 1 4 4v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V10Z';
    assert.match(LAYOUT, new RegExp(backpackPath));
    assert.match(SRC, /data-nav-icon="backpack"/);
    assert.match(SRC, new RegExp(backpackPath));
    assert.match(SRC, /BACKPACK_NAV_ICON/);
    assert.match(SRC, /data-open-backpack[\s\S]*\$\{BACKPACK_NAV_ICON\}/);
  });

  test('inventory view uses two-column mobile card grid', () => {
    assert.match(SRC, /@media \(max-width:768px\)[\s\S]*\.inventory-grid[\s\S]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
    assert.match(SRC, /@media \(max-width:768px\)[\s\S]*\.fish-grid[\s\S]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
    assert.match(SRC, /@media \(max-width:280px\)[\s\S]*grid-template-columns:1fr/);
  });

  test('mobile keeps the desktop account table in a smooth horizontal scroll (cards retired)', () => {
    // Part C revert: instead of swapping to stacked cards on mobile, the desktop
    // table stays visible inside a horizontally scrollable wrap so text is never
    // squeezed/cut. The card builder remains defined but is no longer shown.
    assert.match(SRC, /buildAccountMobileCardHtml/);
    assert.match(SRC, /accounts-mobile-list/);
    assert.match(SRC, /@media \(max-width:768px\)[\s\S]*\.accounts-table-wrap \{[\s\S]*display:block !important/);
    assert.match(SRC, /@media \(max-width:768px\)[\s\S]*\.accounts-mobile-list \{ display:none !important/);
    assert.match(SRC, /@media \(max-width:768px\)[\s\S]*\.accounts-table-wrap \{[\s\S]*overflow-x:auto/);
    assert.match(SRC, /@media \(max-width:768px\)[\s\S]*table-layout:auto !important/);
  });

  test('mobile controls use compact flex layout and apk embed rules exist', () => {
    assert.match(SRC, /@media \(max-width:768px\)[\s\S]*\.accounts-filters \{[\s\S]*display:flex/);
    assert.match(SRC, /@media \(max-width:768px\)[\s\S]*\.accounts-actions \{[\s\S]*display:flex/);
    assert.match(SRC, /@media \(max-width:768px\)[\s\S]*\.accounts-icon-btn \{[\s\S]*height:38px/);
    assert.match(SRC, /inventory-apk-embed/);
  });

  test('table view hides inventory section; inventory view shows it', () => {
    assert.match(SRC, /id="inventoryViewSection"[^>]*hidden/);
    assert.match(SRC, /setAccountViewMode\('fish'\)/);
    assert.match(SRC, /setAccountViewMode\('table'\)/);
    assert.match(SRC, /is-inventory-only/);
    assert.match(SRC, /\.inventory-view-section\[hidden\]/);
  });

  test('update-backpack stores and returns playerStats without quest fields', async () => {
    const app = makeApp();
    const postRes = await request(app)
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
      });
    // Uploads are accepted asynchronously now (202) but a synchronous 200 is also fine.
    assert.ok([200, 202].includes(postRes.status), `unexpected upload status ${postRes.status}`);

    const res = await request(app).get('/api/fishit-tracker/get-backpack/LeaderTableUser').expect(200);
    assert.equal(res.body.playerStats.coinsText, '201.2K');
    assert.equal(res.body.playerStats.totalCaught, 450);
    assert.equal(res.body.playerStats.rarestFishChance, '1/4.50K');
    assert.deepEqual(res.body.playerStats.ruin, { current: 0, max: 4 });
    assert.deepEqual(res.body.playerStats.artifact, { current: 4, max: 4 });
    assert.equal(res.body.playerStats.quest, undefined);
  });
});
