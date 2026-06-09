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

const manualStatsFishImages = require('../src/fishitManualStatsFishImages');
const fishit = require('../src/fishitDb');
const inventorySort = require('../src/fishitInventorySort');
const fishitRoutes = require('../src/fishitRoutes');
const { BLOCKER10ZJ_BUILD } = require('../src/fishitTrackerBuild');
const { PUBLIC_API_BUILD } = require('../src/fishitTrackerRoutes');

const FINAL_BUILD = 'BLOCKER10ZK_INVENTORY_MOBILE_BULK_APK_2026_06_09';
const LAYOUT_PATH = path.join(__dirname, '..', 'views', 'layout.ejs');
const TRACKER_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');
const FISHIT_PATH = path.join(__dirname, '..', 'views', 'fishit.ejs');
const APP_ROOT_PATH = path.join(__dirname, '..', '..', 'android', 'app', 'src', 'main', 'kotlin', 'my', 'id', 'deng', 'monitor', 'ui', 'AppRoot.kt');
const INVENTORY_SCREEN_PATH = path.join(__dirname, '..', '..', 'android', 'app', 'src', 'main', 'kotlin', 'my', 'id', 'deng', 'monitor', 'ui', 'InventoryScreen.kt');

const SPOTLIGHT_FISH = [
  'Skeleton Narwhal',
  'Elshark Gran Maja',
  'Cryoshade Glider',
  'King Jelly',
];

function loadInventorySearchFns() {
  const tpl = fs.readFileSync(TRACKER_PATH, 'utf8');
  const script = tpl.slice(tpl.indexOf('<script>'), tpl.indexOf('</script>') + 9);
  const blocks = [
    script.match(/function formatQuantity\(value\)\s*\{[\s\S]*?\n  \}/),
    script.match(/function formatAmountLabel\(value\)\s*\{[\s\S]*?\n  \}/),
    script.match(/function resolveItemAmount\(item\)\s*\{[\s\S]*?\n  \}/),
    script.match(/function inventorySearchHaystack\(item\)\s*\{[\s\S]*?\n  \}/),
    script.match(/function filterByInventorySearch\(items, query\)\s*\{[\s\S]*?\n  \}/),
    script.match(/function normalizeRarityLabel\(item\)\s*\{[\s\S]*?\n  \}/),
    script.match(/function rarityRank\(item\)\s*\{[\s\S]*?\n  \}/),
  ];
  for (const block of blocks) assert.ok(block, 'inventory search helper must exist');
  return new Function(`
    const RARITY_ORDER = { Forgotten:800, Secret:700, Mythic:600, Legendary:500, Epic:400, Rare:300, Uncommon:200, Common:100, Unknown:0 };
    const TIER_TO_RARITY = { 1:'Common',2:'Uncommon',3:'Rare',4:'Epic',5:'Legendary',6:'Mythic',7:'Secret',8:'Forgotten' };
    ${blocks.map((b) => b[0]).join('\n')}
    return { inventorySearchHaystack, filterByInventorySearch, formatAmountLabel, resolveItemAmount, rarityRank };
  `)();
}

function makeFishitApp() {
  const app = express();
  app.use(fishitRoutes);
  return app;
}

describe('BLOCKER10ZJ inventory search, menu, stats images, APK inventory', () => {
  test('build marker is BLOCKER10ZJ', () => {
    assert.equal(BLOCKER10ZJ_BUILD, FINAL_BUILD);
    assert.equal(PUBLIC_API_BUILD, FINAL_BUILD);
  });

  test('sidebar order and labels are Dashboard, My License, Inventory, Stats, Download', () => {
    const layout = fs.readFileSync(LAYOUT_PATH, 'utf8');
    const labels = [...layout.matchAll(/<span>(Dashboard|My License|Inventory|Stats|Download)<\/span>/g)].map((m) => m[1]);
    assert.deepEqual(labels, ['Dashboard', 'My License', 'Inventory', 'Stats', 'Download']);
    assert.doesNotMatch(layout, /<span>Fish It<\/span>/);
    assert.doesNotMatch(layout, /<span>Rejoin APK<\/span>/);
    assert.doesNotMatch(layout, /<span>Live Tracker<\/span>/);
    assert.match(layout, /data-nav-icon="backpack"/);
    assert.match(layout, /data-nav-icon="download"/);
    assert.match(layout, /data-nav-icon="stats"/);
  });

  test('inventory search UI and helpers exist in tracker template', () => {
    const tpl = fs.readFileSync(TRACKER_PATH, 'utf8');
    assert.match(tpl, /placeholder="Search fish or stones\.\.\."/);
    assert.match(tpl, /inventory-search-row/);
    assert.match(tpl, /No inventory items found/);
    assert.match(tpl, /function filterByInventorySearch/);
    assert.match(tpl, /function ensureInventorySearchRow/);
  });

  test('inventory search filters fish, stones, rarity, and clears', () => {
    const fns = loadInventorySearchFns();
    const fish = [
      { name: 'Panther Eel', baseFishName: 'Panther Eel', rarity: 'Secret', itemId: '248', amount: 1 },
      { name: 'Red Goatfish', baseFishName: 'Red Goatfish', rarity: 'Uncommon', itemId: '285', amount: 2 },
      { name: 'Zebra Snakehead', baseFishName: 'Zebra Snakehead', rarity: 'Uncommon', itemId: '287', amount: 3 },
    ];
    const stones = [
      { name: 'Normal Enchant Stone', displayName: 'Normal Enchant Stone', stoneType: 'Normal', itemId: '10', quantity: 5 },
    ];
    assert.equal(fns.filterByInventorySearch(fish, 'panther').length, 1);
    assert.equal(fns.filterByInventorySearch(stones, 'stone').length, 1);
    assert.equal(fns.filterByInventorySearch(stones, 'normal').length, 1);
    assert.equal(fns.filterByInventorySearch(fish, 'secret').length, 1);
    assert.equal(fns.filterByInventorySearch(fish, '285').length, 1);
    assert.equal(fns.filterByInventorySearch(fish, 'nope').length, 0);
    assert.equal(fns.filterByInventorySearch(fish, '').length, 3);
    const filtered = fns.filterByInventorySearch(fish, 'eel');
    const sorted = inventorySort.sortInventoryFish(filtered);
    assert.equal(sorted.length, 1);
    assert.equal(sorted[0].rarity, 'Secret');
  });

  test('stats page title is Stats', () => {
    const fishitTpl = fs.readFileSync(FISHIT_PATH, 'utf8');
    assert.match(fishitTpl, /page-title[^>]*>Stats</);
    assert.doesNotMatch(fishitTpl, /Fish It Stats/);
  });

  test('manual stats fish images resolve non-placeholder URLs for spotlight fish', () => {
    fishit._resetCache();
    for (const name of SPOTLIGHT_FISH) {
      const hit = manualStatsFishImages.lookupByName(name);
      assert.ok(hit, `${name} must have manual stats image mapping`);
      assert.equal(hit.imageSource, 'manual_verified_image');
      assert.ok(manualStatsFishImages.assetFileExists(hit.filename));
      const resolved = fishit.resolveSpeciesImageSource(name, null);
      assert.ok(resolved.url, `${name} must resolve image URL`);
      assert.match(resolved.url, /\/api\/fishit\/assets\/stats-fish\//);
      assert.ok(!manualStatsFishImages.isPlaceholderUrl(resolved.url));
      assert.equal(resolved.source, 'manual_verified_image');
    }
  });

  test('stats fish image route returns HTTP 200 for spotlight fish files', async () => {
    const app = makeFishitApp();
    for (const name of SPOTLIGHT_FISH) {
      const hit = manualStatsFishImages.lookupByName(name);
      const res = await request(app).get(`/api/fishit/assets/stats-fish/${hit.filename}`).expect(200);
      assert.ok(res.headers['content-type']);
      assert.ok(res.body && res.body.length > 100);
    }
  });

  test('APK nav uses Inventory instead of Snapshot', () => {
    const appRoot = fs.readFileSync(APP_ROOT_PATH, 'utf8');
    const inventoryScreen = fs.readFileSync(INVENTORY_SCREEN_PATH, 'utf8');
    assert.match(appRoot, /NavItem\("inventory",\s*"Inventory"\)/);
    assert.match(appRoot, /composable\("inventory"\)/);
    assert.doesNotMatch(appRoot, /NavItem\("snapshot"/);
    assert.doesNotMatch(appRoot, /"Snapshot"/);
    assert.match(inventoryScreen, /"Inventory"/);
    assert.match(inventoryScreen, /Open in website/);
    assert.match(inventoryScreen, /\/tracker\?apk=1"/);
    assert.doesNotMatch(inventoryScreen, /Waiting for snapshot/i);
  });
});
