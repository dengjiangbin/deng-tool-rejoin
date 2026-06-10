'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const express = require('express');
const request = require('supertest');

const inventorySort = require('../src/fishitInventorySort');
const { PUBLIC_API_BUILD, buildPublicFishFields } = require('../src/fishitTrackerRoutes');
const gameItemDbPublic = require('../src/fishitGameItemDbPublic');
const trackerRouter = require('../src/fishitTrackerRoutes');
const { BLOCKER10ZG_BUILD } = require('../src/fishitTrackerBuild');

const FINAL_BUILD = 'BLOCKER10ZK_INVENTORY_MOBILE_BULK_APK_2026_06_09';
const LAYOUT_PATH = path.join(__dirname, '..', 'views', 'layout.ejs');
const TRACKER_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

function makeTrackerApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZF Inventory rename + rarity sorting', () => {
  test('build marker is BLOCKER10ZG', () => {
    assert.equal(BLOCKER10ZG_BUILD, FINAL_BUILD);
    assert.equal(PUBLIC_API_BUILD, FINAL_BUILD);
  });

  test('sidebar shows Inventory with backpack icon in correct menu order', () => {
    const layout = fs.readFileSync(LAYOUT_PATH, 'utf8');
    const labels = [...layout.matchAll(/<span>(Dashboard|My License|Inventory|Stats|Download)<\/span>/g)].map((m) => m[1]);
    assert.deepEqual(labels, ['Dashboard', 'My License', 'Inventory', 'Stats', 'Download']);
    assert.match(layout, /href="\/inventory"/);
    assert.doesNotMatch(layout, /Inventory[\s\S]{0,120}href="\/tracker"/);
    assert.match(layout, /data-nav-icon="backpack"/);
    assert.doesNotMatch(layout, /<span>Live Tracker<\/span>/);
    assert.doesNotMatch(layout, /<span>Fish It<\/span>/);
    assert.doesNotMatch(layout, /<span>Rejoin APK<\/span>/);
  });

  test('/tracker page visible title/header says Inventory', async () => {
    const res = await request(makeTrackerApp()).get('/tracker').expect(200);
    assert.match(res.text, /<title>[^<]*Inventory[^<]*<\/title>/i);
    assert.match(res.text, /<h1[^>]*>[^<]*Inventory[^<]*<\/h1>/i);
    assert.doesNotMatch(res.text, /Live Inventory Tracker/i);
    assert.doesNotMatch(res.text, /\+ Add Tracker/);
  });

  test('/inventory is the canonical Inventory page', async () => {
    const res = await request(makeTrackerApp()).get('/inventory').expect(200);
    assert.match(res.text, /<h1[^>]*>[^<]*Inventory[^<]*<\/h1>/i);
    assert.match(res.text, /id="usernameInput"/);
    assert.doesNotMatch(res.text, /id="usernameInput" disabled/);
    assert.match(res.text, /id="copyBtn"/);
    assert.match(res.text, /id="loadstringCode"/);
    assert.doesNotMatch(res.text, /id="copyScriptTextarea"/);
    assert.match(res.text, /BLOCKER10ZR_FIX_INVENTORY_BUTTON_BINDINGS_CLEAN_COPY_UI_2026_06_10/);
  });

  test('/tracker legacy route remains compatible', async () => {
    const res = await request(makeTrackerApp()).get('/tracker').expect(200);
    assert.match(res.text, /<h1[^>]*>[^<]*Inventory[^<]*<\/h1>/i);
  });

  test('/inventory?username=denghub2 bootstraps initial username', async () => {
    const res = await request(makeTrackerApp()).get('/inventory?username=denghub2').expect(200);
    assert.match(res.text, /"denghub2"/);
    assert.match(res.text, /initFromQueryUsername/);
  });

  test('sortInventoryFish orders rarities rarest to common', () => {
    const items = [
      { name: 'Common Fish', rarity: 'Common', itemId: '1' },
      { name: 'Secret Fish', rarity: 'Secret', itemId: '2' },
      { name: 'Epic Fish', rarity: 'Epic', itemId: '3' },
      { name: 'Forgotten Fish', rarity: 'Forgotten', itemId: '4' },
      { name: 'Mythic Fish', rarity: 'Mythic', itemId: '5' },
      { name: 'Legendary Fish', rarity: 'Legendary', itemId: '6' },
      { name: 'Rare Fish', rarity: 'Rare', itemId: '7' },
      { name: 'Uncommon Fish', rarity: 'Uncommon', itemId: '8' },
    ];
    const sorted = inventorySort.sortInventoryFish(items);
    assert.deepEqual(sorted.map((f) => f.rarity), [
      'Forgotten', 'Secret', 'Mythic', 'Legendary', 'Epic', 'Rare', 'Uncommon', 'Common',
    ]);
  });

  test('sortInventoryFish tie-breaks by name within same rarity', () => {
    const items = [
      { name: 'Zebra Fish', rarity: 'Rare', itemId: '2' },
      { name: 'Alpha Fish', rarity: 'Rare', itemId: '1' },
    ];
    const sorted = inventorySort.sortInventoryFish(items);
    assert.deepEqual(sorted.map((f) => f.name), ['Alpha Fish', 'Zebra Fish']);
  });

  test('Unknown rarity sorts after Common', () => {
    const items = [
      { name: 'Unknown Fish', rarity: 'Unknown', itemId: '9' },
      { name: 'Common Fish', rarity: 'Common', itemId: '1' },
    ];
    const sorted = inventorySort.sortInventoryFish(items);
    assert.deepEqual(sorted.map((f) => f.name), ['Common Fish', 'Unknown Fish']);
  });

  test('sortInventoryStones keeps Normal Double Evolved Eggy Runic order', () => {
    const stones = [
      { name: 'Runic Enchant Stone', stoneType: 'Runic', itemId: '929' },
      { name: 'Normal Enchant Stone', stoneType: 'Normal', itemId: '10' },
      { name: 'Eggy Enchant Stone', stoneType: 'Eggy', itemId: '873' },
      { name: 'Transcended Stone', stoneType: 'Double', itemId: '246' },
    ];
    const sorted = inventorySort.sortInventoryStones(stones);
    assert.deepEqual(sorted.map((s) => s.stoneType), ['Normal', 'Double', 'Eggy', 'Runic']);
  });

  test('tracker template includes client-side inventory sort helpers', () => {
    const tpl = fs.readFileSync(TRACKER_PATH, 'utf8');
    assert.match(tpl, /function sortInventoryFish/);
    assert.match(tpl, /RARITY_ORDER/);
    assert.match(tpl, /return sortInventoryFish\(items\)/);
  });

  test('public backpack fish list is sorted by rarity rank descending', async () => {
    const session = {
      inventorySource: 'playerdata_gameitemdb',
      playerDataFishItems: [
        { kind: 'fish', itemId: '70', name: 'Clownfish', baseName: 'Clownfish', quantity: 1, tier: 1, rarity: 'Common', type: 'Fish', icon: 'rbxassetid://1', source: 'playerdata_gameitemdb', identityVerified: true },
        { kind: 'fish', itemId: '248', name: 'Panther Eel', baseName: 'Panther Eel', quantity: 1, tier: 7, rarity: 'Secret', type: 'Fish', icon: 'rbxassetid://2', source: 'playerdata_gameitemdb', identityVerified: true },
        { kind: 'fish', itemId: '285', name: 'Red Goatfish', baseName: 'Red Goatfish', quantity: 1, tier: 2, rarity: 'Uncommon', type: 'Fish', icon: 'rbxassetid://3', source: 'playerdata_gameitemdb', identityVerified: true },
        { kind: 'fish', itemId: '268', name: 'Skeleton Angler Fish', baseName: 'Skeleton Angler Fish', quantity: 1, tier: 4, rarity: 'Epic', type: 'Fish', icon: 'rbxassetid://4', source: 'playerdata_gameitemdb', identityVerified: true },
      ],
      playerDataStoneItems: [],
      sourceTruth: gameItemDbPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.deepEqual(pub.fishItems.map((f) => f.rarity), ['Secret', 'Epic', 'Uncommon', 'Common']);
    assert.ok(pub.inventorySortProof);
    assert.equal(pub.inventorySortProof.fishOrder[0].rarity, 'Secret');
  });
});
