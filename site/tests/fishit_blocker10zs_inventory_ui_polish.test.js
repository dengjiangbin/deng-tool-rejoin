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

function countMatches(text, re) {
  const m = text.match(re);
  return m ? m.length : 0;
}

describe('BLOCKER10ZS inventory UI polish — tabs, search, controls, stats', () => {
  test('rendered inventory HTML no longer uses Each Account or All Accounts tabs', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    assert.doesNotMatch(res.text, />Each Account</);
    assert.doesNotMatch(res.text, />All Accounts</);
    assert.match(res.text, /id="viewFishGridBtn"/);
    assert.match(res.text, /id="viewStoneGridBtn"/);
  });

  test('session helper texts are removed from inventory page', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    assert.doesNotMatch(res.text, /sessions saved automatically/i);
    assert.doesNotMatch(res.text, /sessions are saved and restored on page reload/i);
  });

  test('shared search only — no per-account search row builder in template', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.doesNotMatch(tpl, /function ensureInventorySearchRow/);
    assert.doesNotMatch(tpl, /data-individual-search-input/);
    assert.match(tpl, /data-bulk-search-input/);
    assert.equal(countMatches(tpl, /placeholder="Search fish or stones\.\.\."/g), 1);
    assert.doesNotMatch(tpl, /data-remove-btn/);
    assert.doesNotMatch(tpl, />x Remove</);
  });

  test('top player control bar has Add, Multiple, and icon-only remove menu', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    assert.match(res.text, /placeholder="Enter nickname\.\.\."/);
    assert.match(res.text, /id="addBtn"[^>]*>\+ Add</);
    assert.match(res.text, /id="multipleBtn"[^>]*>\+ Multiple</);
    assert.match(res.text, /id="removeMenuBtn"/);
    assert.match(res.text, /class="btn-remove-menu"/);
    assert.doesNotMatch(res.text, />\s*Remove\s*</);
    assert.match(res.text, /aria-label="Remove account"/);
  });

  test('top stats cards include Online / Accounts and Forgotten Fish', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    assert.match(res.text, /id="inventoryStats"/);
    assert.match(res.text, /Online \/ Accounts/);
    assert.match(res.text, /Evolved Enchant Stone/);
    assert.match(res.text, /Secret Fish/);
    assert.match(res.text, /Forgotten Fish/);
    assert.match(res.text, /id="statOnlineAccounts"/);
    assert.match(res.text, /id="statForgottenFish"/);
    assert.match(res.text, /class="stat-card stat-card--online"/);
    assert.match(res.text, /class="stat-card stat-card--forgotten"/);
  });

  test('mobile and APK inventory summary stats stay compact 2x2 grid', () => {
    const source = fs.readFileSync(path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs'), 'utf8');
    assert.match(source, /@media \(max-width:768px\)[\s\S]*\.inventory-stats[\s\S]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
    assert.match(source, /\.inventory-apk-embed \.inventory-stats[\s\S]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
    assert.doesNotMatch(source, /@media \(max-width:560px\)[\s\S]*\.inventory-stats[\s\S]*grid-template-columns:1fr/);
  });

  test('bulk fish and stone grids aggregate ownership across accounts', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    const script = tpl.slice(tpl.indexOf('<script>') + 8, tpl.indexOf('</script>'));
    assert.match(script, /function aggregateBulkInventory/);
    assert.match(script, /function renderBulkInventory\(showCategory\)/);
    assert.match(script, /function buildOwnerBreakdownHtml/);
    assert.doesNotMatch(script, /function searchEachAccountInventory/);
    assert.doesNotMatch(script, /function renderIndividualSearchResults/);
  });
});

function loadInventoryModalStatusFns() {
  const tpl = fs.readFileSync(TPL_PATH, 'utf8');
  const script = tpl.slice(tpl.indexOf('<script>') + 8, tpl.indexOf('</script>'));
  const names = ['pad2', 'syncAgeSeconds', 'formatExactSyncAge', 'syncFreshnessFromTimestamp', 'parseMultipleUsernames'];
  const fns = names.map((name) => script.match(new RegExp(`function ${name}\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n  \\}`)));
  for (let i = 0; i < names.length; i += 1) {
    assert.ok(fns[i], `tracker template helper must exist: ${names[i]}`);
  }
  const constants = script.match(/const SYNC_LIVE_MAX_SEC = 30;/);
  assert.ok(constants, 'sync freshness threshold must exist');
  return new Function(`
    ${constants[0]}
    ${fns.map((h) => h[0]).join('\n')}
    return { formatExactSyncAge, syncFreshnessFromTimestamp, parseMultipleUsernames };
  `)();
}

describe('BLOCKER10ZS inventory modal + sync status polish', () => {
  test('multiple add uses custom modal, not native prompt', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(res.text, /Add Multiple Accounts/);
    assert.match(res.text, /Enter usernames separated by comma or new line\./);
    assert.match(res.text, /Example: denghub2, player123, testaccount/);
    assert.match(res.text, /id="multipleAddTextarea"/);
    assert.match(res.text, /id="multipleAddSubmit"[^>]*>Add Accounts</);
    assert.match(res.text, /id="multipleAddCancel"[^>]*>Cancel</);
    assert.match(res.text, /class="modal-textarea"/);
    assert.doesNotMatch(res.text, /window\.prompt\(/);
    assert.doesNotMatch(tpl, /window\.prompt\(/);
    assert.doesNotMatch(tpl, /[^.]prompt\(/);
  });

  test('watching helper and bottom last sync text are removed', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.doesNotMatch(res.text, /Watching /);
    assert.doesNotMatch(res.text, /Watching 1 player/i);
    assert.doesNotMatch(res.text, /Last sync:/i);
    assert.doesNotMatch(tpl, /Last sync:/);
    assert.doesNotMatch(tpl, /Watching /);
    assert.doesNotMatch(tpl, /data-sync-time/);
    assert.doesNotMatch(tpl, /card-foot/);
  });

  test('account card header shows sync duration beside status dot', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(res.text, /data-card-sync-text/);
    assert.match(res.text, /data-card-sync-line/);
    assert.match(res.text, /data-status-dot/);
    assert.match(res.text, /class="status-dot/);
    assert.match(res.text, /function formatExactSyncAge/);
    assert.match(res.text, /function tickAllCardSyncStatus/);
    assert.match(res.text, /safeBind\('sync age tick'/);
    assert.match(res.text, /setInterval\(tickAllCardSyncStatus, SYNC_TICK_MS\)/);
    assert.match(tpl, /function formatTableSyncStatusText/);
    assert.match(tpl, /function formatTableSyncAge/);
    assert.doesNotMatch(tpl, /data-sync-ago/);
    assert.doesNotMatch(tpl, /class="account-name"/);
  });

  test('formatExactSyncAge returns exact duration labels without ago or just now', () => {
    const fns = loadInventoryModalStatusFns();
    const now = Date.now();
    assert.equal(fns.formatExactSyncAge(null), 'no sync');
    assert.equal(fns.formatExactSyncAge(new Date(now - 1000).toISOString()), '1s');
    assert.equal(fns.formatExactSyncAge(new Date(now - 65000).toISOString()), '1m 05s');
    assert.equal(fns.formatExactSyncAge(new Date(now - 2678400000).toISOString()), '31D');
    assert.equal(fns.syncFreshnessFromTimestamp(new Date(now - 5000).toISOString()), 'live');
    assert.equal(fns.syncFreshnessFromTimestamp(new Date(now - 90000).toISOString()), 'dead');
    assert.equal(fns.syncFreshnessFromTimestamp(new Date(now - 2678400000).toISOString()), 'dead');
  });

  test('parseMultipleUsernames trims, dedupes, and accepts comma/newline input', () => {
    const fns = loadInventoryModalStatusFns();
    assert.deepEqual(
      fns.parseMultipleUsernames('denghub2, player123\ntestaccount, denghub2 ,  , player123'),
      ['denghub2', 'player123', 'testaccount'],
    );
    assert.deepEqual(fns.parseMultipleUsernames('   \n  '), []);
  });
});
