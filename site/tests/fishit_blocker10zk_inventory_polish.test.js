'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const bulk = require('../src/fishitInventoryBulk');
const { BLOCKER10ZK_BUILD } = require('../src/fishitTrackerBuild');
const { PUBLIC_API_BUILD } = require('../src/fishitTrackerRoutes');

const FINAL_BUILD = 'BLOCKER10ZK_INVENTORY_MOBILE_BULK_APK_2026_06_09';
const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');
const INVENTORY_KT = path.join(__dirname, '..', '..', 'android', 'app', 'src', 'main', 'kotlin', 'my', 'id', 'deng', 'monitor', 'ui', 'InventoryScreen.kt');

describe('BLOCKER10ZK inventory mobile, bulk, public cleanup, APK UX', () => {
  test('build marker is BLOCKER10ZK', () => {
    assert.equal(BLOCKER10ZK_BUILD, FINAL_BUILD);
    assert.equal(PUBLIC_API_BUILD, FINAL_BUILD);
  });

  test('public tracker template hides debug noise by default', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /const DEBUG_INVENTORY = <%= \(typeof debugInventory/);
    assert.match(tpl, /No inventory data yet for this username/);
    assert.doesNotMatch(tpl, /Awaiting first data/);
    assert.match(tpl, /data-ui-marker="<%= \(typeof debugInventory/);
    assert.match(tpl, /BLOCKER10ZT3_SYNC_STATUS_COIN_MOBILE_TABLE_2026_06_10/);
  });

  test('debug-only proof blocks stay gated behind DEBUG_INVENTORY', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /if \(DEBUG_INVENTORY\) \{/);
    assert.match(tpl, /buildPlayerDataGameItemDbProofHtml/);
    assert.match(tpl, /phaseMessage\(data\.phase\)/);
  });

  test('Each Account and All Accounts tabs render', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /data-inventory-mode="individual"/);
    assert.match(tpl, /data-inventory-mode="bulk"/);
    assert.match(tpl, />Each Account</);
    assert.match(tpl, />All Accounts</);
    assert.doesNotMatch(tpl, /Bulk \/ All/);
    assert.match(tpl, /function aggregateBulkInventory/);
    assert.match(tpl, /function renderBulkInventory/);
  });

  test('mobile CSS keeps ft-card horizontal layout in two-column inventory grid', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /@media \(max-width:768px\)[\s\S]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
    assert.match(tpl, /@media \(max-width:768px\)[\s\S]*\.ft-card--fish[\s\S]*width:100%/);
    assert.match(tpl, /\.ft-card-icon img[\s\S]*object-fit:contain/);
    assert.match(tpl, /@media \(max-width:280px\)[\s\S]*grid-template-columns:1fr/);
  });

  test('bulk aggregation sums quantities and tracks account count', () => {
    const result = bulk.aggregateBulkInventory([
      {
        username: 'user1',
        fishList: [{ name: 'King Crab', baseFishName: 'King Crab', rarity: 'Secret', amount: 10, imageUrl: 'http://127.0.0.1/a.webp' }],
        stoneList: [{ name: 'Normal Enchant Stone', stoneType: 'Normal', amount: 5 }],
      },
      {
        username: 'user2',
        fishList: [{ name: 'King Crab', baseFishName: 'King Crab', rarity: 'Secret', amount: 22 }],
        stoneList: [{ name: 'Normal Enchant Stone', stoneType: 'Normal', amount: 8 }],
      },
    ]);
    assert.equal(result.accountCount, 2);
    assert.equal(result.fish.length, 1);
    assert.equal(result.fish[0].amount, 32);
    assert.equal(result.fish[0].accountCount, 2);
    assert.equal(result.stones[0].amount, 13);
    assert.equal(result.fish[0].dataSource, 'bulk_playerdata_gameitemdb');
  });

  test('bulk search filters by fish name and owner username', () => {
    const aggregated = bulk.aggregateBulkInventory([
      { username: 'alpha', fishList: [{ name: 'Panther Eel', rarity: 'Secret', amount: 1 }], stoneList: [] },
      { username: 'beta', fishList: [{ name: 'Red Goatfish', rarity: 'Uncommon', amount: 2 }], stoneList: [] },
    ]);
    assert.equal(bulk.filterBulkItems(aggregated.fish, 'panther').length, 1);
    assert.equal(bulk.filterBulkItems(aggregated.fish, 'alpha').length, 1);
    assert.equal(bulk.filterBulkItems(aggregated.fish, 'nope').length, 0);
  });

  test('APK inventory screen uses apk=1 route, skeleton, and no Snapshot', () => {
    const kt = fs.readFileSync(INVENTORY_KT, 'utf8');
    const appRoot = fs.readFileSync(path.join(__dirname, '..', '..', 'android', 'app', 'src', 'main', 'kotlin', 'my', 'id', 'deng', 'monitor', 'ui', 'AppRoot.kt'), 'utf8');
    assert.match(kt, /\/inventory\?apk=1/);
    assert.match(kt, /InventoryLoadingSkeleton/);
    assert.doesNotMatch(kt, /Open in website/);
    assert.doesNotMatch(kt, /Continue in Browser/i);
    assert.doesNotMatch(kt, /Snapshot/i);
    assert.match(appRoot, /NavItem\("inventory"/);
    assert.doesNotMatch(appRoot, /NavItem\("snapshot"/);
  });
});
