'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';

const trackerRouter = require('../src/fishitTrackerRoutes');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const MANIFEST_PATH = path.join(__dirname, '..', 'src', 'inventoryAssetManifest.json');

function readSource() {
  return fs.readFileSync(SOURCE_PATH, 'utf8');
}

function readJs() {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
  const jsPath = path.join(__dirname, '..', 'public', 'assets', manifest.js);
  return fs.readFileSync(jsPath, 'utf8');
}

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

function extractBulkRemoveHelpers(source) {
  const open = source.indexOf('  function isTrackerOfflineForRemoval(entry) {');
  const close = source.indexOf('  function fmtTime(iso) {', open);
  assert.ok(open > 0 && close > open, 'bulk remove helpers missing from source');
  const block = source.slice(open, close);
  const script = `(function(){
  const trackers = new Map();
  let activeAccountKey = null;
  let accountViewMode = 'table';
  function entryConnectionFreshness(entry) {
    return entry && entry.__fresh === 'live' ? 'live' : 'dead';
  }
  function resolveEntryPublicSnapshot(entry) {
    return entry && entry.snap ? entry.snap : null;
  }
  function hasRenderableTrackerData(snapshot) {
    if (!snapshot) return false;
    if (snapshot.playerStats && snapshot.playerStats.coins != null) return true;
    if (Array.isArray(snapshot.fishItems) && snapshot.fishItems.length) return true;
    if (Array.isArray(snapshot.stoneItems) && snapshot.stoneItems.length) return true;
    if (Array.isArray(snapshot.totemItems) && snapshot.totemItems.length) return true;
    return false;
  }
  function clearInlineDetailState() {}
  function setAccountViewMode(mode, opts) {
    accountViewMode = mode;
    activeAccountKey = opts && opts.key ? opts.key : null;
  }
${block}
  return {
    trackers,
    activeAccountKey: () => activeAccountKey,
    accountViewMode: () => accountViewMode,
    setActiveKey: (k) => { activeAccountKey = k; },
    isTrackerOfflineForRemoval,
    isTrackerNoDataForRemoval,
    collectTrackerRemovalKeys,
    reconcileActiveAccountAfterRemoval,
  };
})()`;
  return vm.runInNewContext(script, {}, { filename: 'tracker-bulk-remove-helpers.js' });
}

function playerControlBarBlock(source) {
  const start = source.indexOf('<div class="player-control-bar">');
  const end = source.indexOf('<div class="summary-bar"', start);
  assert.ok(start > 0 && end > start);
  return source.slice(start, end);
}

describe('tracker username bulk remove UI (2026-06-16)', () => {
  test('top bulk buttons render in exact order and no username chips in control bar', () => {
    const bar = playerControlBarBlock(readSource());
    assert.match(bar, /class="tracker-username-actions"/);
    assert.doesNotMatch(bar, /remove-dropdown/);
    assert.doesNotMatch(bar, /data-remove-key/);
    assert.doesNotMatch(bar, /removeMenuBtn/);
    const offlineIdx = bar.indexOf('>Remove Offline<');
    const noDataIdx = bar.indexOf('>Remove No Data<');
    const allIdx = bar.indexOf('>Remove All<');
    assert.ok(offlineIdx > 0, 'Remove Offline button missing');
    assert.ok(noDataIdx > offlineIdx, 'Remove No Data must follow Remove Offline');
    assert.ok(allIdx > noDataIdx, 'Remove All must follow Remove No Data');
  });

  test('individual table rows keep remove action; mobile cards do not duplicate it', () => {
    const src = readSource();
    const rowFn = src.slice(src.indexOf('function buildAccountRowHtml'), src.indexOf('function renderAccountsTable'));
    const mobileFn = src.slice(src.indexOf('function buildAccountMobileCardHtml'), src.indexOf('function buildAccountRowHtml'));
    assert.match(rowFn, /data-remove-account/);
    assert.match(src, />Actions</);
    assert.doesNotMatch(mobileFn, /data-remove-account/);
  });

  test('compiled JS wires bulk remove handlers and keeps remove-all confirmation modal', () => {
    const js = readJs();
    assert.match(js, /function bindUsernameBulkActions/);
    assert.match(js, /function removeOfflineTrackers/);
    assert.match(js, /function removeNoDataTrackers/);
    assert.match(js, /function openRemoveAllModal/);
    assert.match(js, /No offline usernames to remove\./);
    assert.match(js, /No no-data usernames to remove\./);
    assert.doesNotMatch(js, /function updateRemoveMenu/);
    assert.match(js, /data-remove-account/);
  });

  test('Remove Offline removes only dead/offline entries', () => {
    const h = extractBulkRemoveHelpers(readSource());
    h.trackers.set('online1', { __fresh: 'live', snap: { fishItems: [{ name: 'A' }] } });
    h.trackers.set('offline1', { __fresh: 'dead', snap: { fishItems: [{ name: 'B' }] } });
    h.trackers.set('offline2', { __fresh: 'dead', snap: null });
    const keys = h.collectTrackerRemovalKeys((entry) => h.isTrackerOfflineForRemoval(entry));
    assert.equal(keys.length, 2);
    assert.ok(keys.includes('offline1'));
    assert.ok(keys.includes('offline2'));
  });

  test('Remove No Data removes only entries without renderable snapshot', () => {
    const h = extractBulkRemoveHelpers(readSource());
    h.trackers.set('withdata', { __fresh: 'dead', snap: { fishItems: [{ name: 'Fish' }] } });
    h.trackers.set('nodata', { __fresh: 'live', snap: null });
    h.trackers.set('empty', { __fresh: 'live', snap: { fishItems: [], stoneItems: [] } });
    const keys = h.collectTrackerRemovalKeys((entry) => h.isTrackerNoDataForRemoval(entry));
    assert.equal(keys.length, 2);
    assert.ok(keys.includes('nodata'));
    assert.ok(keys.includes('empty'));
  });

  test('online usernames with valid data are kept by offline and no-data filters', () => {
    const h = extractBulkRemoveHelpers(readSource());
    h.trackers.set('good', { __fresh: 'live', snap: { fishItems: [{ name: 'X' }] } });
    const offlineKeys = h.collectTrackerRemovalKeys((entry) => h.isTrackerOfflineForRemoval(entry));
    const noDataKeys = h.collectTrackerRemovalKeys((entry) => h.isTrackerNoDataForRemoval(entry));
    assert.equal(offlineKeys.length, 0);
    assert.equal(noDataKeys.length, 0);
  });

  test('reconcileActiveAccountAfterRemoval selects next account or table mode', () => {
    const h = extractBulkRemoveHelpers(readSource());
    h.trackers.set('a', { __fresh: 'live', snap: { fishItems: [{ name: 'A' }] } });
    h.trackers.set('b', { __fresh: 'live', snap: { fishItems: [{ name: 'B' }] } });
    h.setActiveKey('gone');
    h.reconcileActiveAccountAfterRemoval();
    assert.equal(h.accountViewMode(), 'table');
  });

  test('mobile player-control-bar bulk actions span full width without horizontal overflow clipping', () => {
    const src = readSource();
    assert.match(
      src,
      /@media \(max-width:768px\)[\s\S]*\.player-control-bar \.tracker-username-actions \{[\s\S]*?grid-column:1 \/ -1;[\s\S]*?\}/,
    );
    assert.match(src, /\.tracker-username-actions \{[\s\S]*?flex-wrap:wrap;/);
  });

  test('denghub2 with valid API snapshot is not classified as no-data for removal', () => {
    const h = extractBulkRemoveHelpers(readSource());
    h.trackers.set('denghub2', {
      __fresh: 'dead',
      snap: {
        fishItems: Array.from({ length: 17 }, (_, i) => ({ name: `Fish ${i + 1}` })),
        stoneItems: [{ name: 'Stone' }],
        playerStats: { coinsText: '93M', totalCaughtText: '148.707' },
        lastInventoryAt: '2026-06-15T18:11:27.140Z',
      },
    });
    const keys = h.collectTrackerRemovalKeys((entry) => h.isTrackerNoDataForRemoval(entry));
    assert.equal(keys.length, 0);
  });

  test('GET /tracker renders bulk remove controls and table Actions column', async () => {
    const res = await request(makeApp()).get('/tracker').expect(200);
    assert.match(res.text, /id="removeOfflineBtn"/);
    assert.match(res.text, /id="removeNoDataBtn"/);
    assert.match(res.text, /id="removeAllBtn"/);
    assert.match(res.text, /Remove Offline/);
    assert.match(res.text, /Remove No Data/);
    assert.match(res.text, /Remove All/);
    assert.doesNotMatch(res.text, /id="removeMenuBtn"/);
    assert.match(res.text, />Actions</);
    assert.match(readJs(), /data-remove-account/);
    assert.match(res.text, /id="removeAllModal"/);
  });
});
