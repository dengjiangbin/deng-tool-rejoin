'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';
process.env.INVENTORY_ACCOUNTS_MEMORY = '1';

const inventoryTrackedAccounts = require('../src/inventoryTrackedAccounts');
const trackerRouter = require('../src/fishitTrackerRoutes');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const manifest = require('../src/inventoryAssetManifest.json');
const INVENTORY_JS = path.join(__dirname, '..', 'public', 'assets', manifest.js);
const INVENTORY_CSS = path.join(__dirname, '..', 'public', 'assets', manifest.css);

function readSource() { return fs.readFileSync(SOURCE_PATH, 'utf8'); }
function readJs() { return fs.readFileSync(INVENTORY_JS, 'utf8'); }
function readCss() { return fs.readFileSync(INVENTORY_CSS, 'utf8'); }

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('STRICT /tracker polish — mobile/APK grid (A)', () => {
  test('mobile (<=768px) fish/item/totem grids use 2 columns, not 3', () => {
    const source = readSource();
    assert.match(
      source,
      /@media \(max-width:768px\)[\s\S]*\.inventory-grid,\s*\.items-grid,\s*\.fish-grid,\s*\.totems-grid \{\s*grid-template-columns:repeat\(2,minmax\(0,1fr\)\) !important/,
    );
  });

  test('mobile (<=768px) stone grids use 2 columns', () => {
    const source = readSource();
    assert.match(
      source,
      /@media \(max-width:768px\)[\s\S]*\.stones-grid,\s*\.stone-grid \{\s*grid-template-columns:repeat\(2,minmax\(0,1fr\)\) !important/,
    );
  });

  test('APK embed grids use 2 columns (no forced 3-column)', () => {
    const source = readSource();
    assert.match(
      source,
      /\.inventory-apk-embed \.inventory-grid,[\s\S]*?\.inventory-apk-embed \.totems-grid \{\s*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/,
    );
  });

  test('no remaining forced 3-column item/stone grid rule', () => {
    const source = readSource();
    assert.doesNotMatch(source, /\.fish-grid \{\s*grid-template-columns:repeat\(3,minmax\(0,1fr\)\) !important/);
    assert.doesNotMatch(source, /\.stone-grid \{\s*grid-template-columns:repeat\(3,minmax\(0,1fr\)\) !important/);
  });
});

describe('STRICT /tracker polish — desktop grid consistency (B)', () => {
  test('fish, stone, and totem grids share one consistent column template', () => {
    const source = readSource();
    assert.match(
      source,
      /\.inventory-grid,\s*\.items-grid,\s*\.fish-grid,\s*\.stones-grid,\s*\.stone-grid,\s*\.totems-grid \{\s*display:grid;\s*grid-template-columns:repeat\(auto-fill,minmax\(210px,1fr\)\)/,
    );
  });
});

describe('STRICT /tracker polish — Ruby Gemstone stat card (C/G)', () => {
  test('source has Ruby Gemstone stat card markup', () => {
    const source = readSource();
    assert.match(source, /class="stat-card stat-card--ruby"/);
    assert.match(source, /id="statRubyGemstone"/);
    assert.match(source, /Ruby Gemstone/);
  });

  test('desktop shows 5 stat cards in one row, mobile keeps 4 per row', () => {
    const source = readSource();
    // desktop: 5 columns for the main stat row
    assert.match(source, /@media \(min-width:901px\) \{\s*#inventoryStats \{ grid-template-columns:repeat\(5,minmax\(0,1fr\)\)/);
    // mobile (<=768): still 4 columns so the 5th card (Ruby) wraps to row 2
    assert.match(source, /@media \(max-width:768px\)[\s\S]*\.inventory-stats \{\s*grid-template-columns:repeat\(4,minmax\(0,1fr\)\) !important/);
  });

  test('compiled JS computes ruby gemstone count with safe normalized matching', () => {
    const js = readJs();
    assert.match(js, /function isRubyGemstoneItem/);
    assert.match(js, /rubyGemstone/);
    assert.match(js, /statRubyGemstone/);
    // bare "ruby" must require gemstone context, explicit gemstone names always match
    assert.match(js, /ruby mutation gemstone|ruby gemstone/);
  });

  test('existing 4 stat cards still present', () => {
    const source = readSource();
    assert.match(source, /id="statOnlineAccounts"/);
    assert.match(source, /id="statEvolvedStones"/);
    assert.match(source, /id="statSecretFish"/);
    assert.match(source, /id="statForgottenFish"/);
  });
});

describe('STRICT /tracker polish — remove all usernames (D)', () => {
  test('remove menu includes a Remove all usernames item', () => {
    const source = readSource();
    assert.match(source, /data-remove-all="1"/);
    assert.match(source, /Remove all usernames/);
  });

  test('confirm modal exists with explicit copy and danger action', () => {
    const source = readSource();
    assert.match(source, /id="removeAllModal"/);
    assert.match(source, /Remove all usernames\?/);
    assert.match(source, /This will remove every username from your tracker list\. This will not delete your Discord account\./);
    assert.match(source, /id="removeAllConfirm"/);
    assert.match(source, /btn-modal-submit--danger/);
  });

  test('compiled JS wires remove-all to a DELETE on the accounts collection', () => {
    const js = readJs();
    assert.match(js, /function persistTrackerRemoveAll/);
    assert.match(js, /function confirmRemoveAll/);
    assert.match(js, /function openRemoveAllModal/);
  });
});

describe('STRICT /tracker polish — clickable detail view (E/G)', () => {
  test('compiled CSS has detail modal styling', () => {
    const css = readCss();
    assert.match(css, /\.ft-detail-overlay/);
    assert.match(css, /\.ft-detail-tag/);
    assert.match(css, /\.ft-detail-name/);
    assert.match(css, /\.ft-detail-owner/);
    assert.match(css, /\.ft-card--interactive/);
  });

  test('compiled JS exposes detail open/build with mutation-above-name + clean name', () => {
    const js = readJs();
    assert.match(js, /function openFtDetailModal/);
    assert.match(js, /function ftDetailMeta/);
    assert.match(js, /function ftCleanDetailName/);
    assert.match(js, /function attachFtCardItem/);
    assert.match(js, /function bindFtDetailModal/);
  });

  test('detail owner rows sort by username ascending, case-insensitive', () => {
    const js = readJs();
    assert.match(js, /function ftDetailOwnerRows/);
    assert.match(js, /toLowerCase\(\)\.localeCompare\(/);
  });

  test('cards are made interactive on build for fish, stone, and totem', () => {
    const js = readJs();
    assert.match(js, /attachFtCardItem\(card, item, 'fish'\)/);
    assert.match(js, /attachFtCardItem\(card, item, 'stone'\)/);
    assert.match(js, /attachFtCardItem\(card, item, 'totem'\)/);
  });
});

describe('STRICT /tracker polish — backend remove-all is safe and scoped', () => {
  beforeEach(() => {
    inventoryTrackedAccounts.resetMemoryStoreForTests();
  });

  test('removeAllTrackedAccounts clears only the owner bucket', async () => {
    await inventoryTrackedAccounts.addTrackedAccounts('111111111111111111', ['Alpha', 'Bravo', 'Charlie']);
    await inventoryTrackedAccounts.addTrackedAccounts('222222222222222222', ['Other1', 'Other2']);

    const before = await inventoryTrackedAccounts.listTrackedAccounts('111111111111111111');
    assert.equal(before.length, 3);

    const result = await inventoryTrackedAccounts.removeAllTrackedAccounts('111111111111111111');
    assert.equal(result.ok, true);
    assert.equal(result.removed, 3);
    assert.deepEqual(result.accounts, []);

    const after = await inventoryTrackedAccounts.listTrackedAccounts('111111111111111111');
    assert.equal(after.length, 0);

    // Other owner is untouched
    const others = await inventoryTrackedAccounts.listTrackedAccounts('222222222222222222');
    assert.equal(others.length, 2);
  });

  test('single remove still works after adding remove-all', async () => {
    await inventoryTrackedAccounts.addTrackedAccounts('333333333333333333', ['Solo1', 'Solo2']);
    const removed = await inventoryTrackedAccounts.removeTrackedAccount('333333333333333333', 'solo1');
    assert.equal(removed.ok, true);
    const list = await inventoryTrackedAccounts.listTrackedAccounts('333333333333333333');
    assert.equal(list.length, 1);
    assert.equal(list[0].robloxUsernameKey, 'solo2');
  });
});

describe('STRICT /tracker polish — render regression', () => {
  test('GET /tracker renders 200 with all 5 stat cards and modals', async () => {
    const res = await request(makeApp()).get('/tracker').expect(200);
    assert.match(res.text, /id="statOnlineAccounts"/);
    assert.match(res.text, /Evolved Enchant Stone/);
    assert.match(res.text, /Secret Fish/);
    assert.match(res.text, /Forgotten Fish/);
    assert.match(res.text, /Ruby Gemstone/);
    assert.match(res.text, /id="statRubyGemstone"/);
    assert.match(res.text, /id="removeAllModal"/);
  });
});
