'use strict';

// Regression: /tracker must keep last good inventory when account-status refresh
// (status-only lane) merges over get-backpack data, and when offline rendering
// reads lastData without fishItems but liveSnapshot still holds rows.

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const BACKPACK_FIXTURE = {
  username: 'geptzy392',
  fishItems: [{ name: 'Test Fish', quantity: 1 }],
  stoneItems: [{ name: 'Evolved Enchant Stone', quantity: 2, stoneType: 'Evolved' }],
  totemItems: [{ name: 'Totem', quantity: 1 }],
  playerStats: { coins: 100, totalCaught: 50, coinsText: '100', totalCaughtText: '50' },
  snapshotComplete: true,
  inventoryDisplayState: 'ready',
};

function readSource() {
  return fs.readFileSync(SOURCE_PATH, 'utf8');
}

function extractInventoryHelpers(source) {
  const open = source.indexOf('  const INVENTORY_PRESERVE_KEYS = [');
  const close = source.indexOf('  function statsSnapshotReady(data) {', open);
  assert.ok(open > 0 && close > open, 'inventory preserve helpers missing from source');
  const block = source.slice(open, close);
  const script = `(function(){\n${block}\nreturn { mergePreservedInventorySnapshot, resolveEntryPublicSnapshot, entryHasInventoryRows };\n})()`;
  return vm.runInNewContext(script, {}, { filename: 'tracker-hydration-helpers.js' });
}

describe('tracker frontend hydration preserve (2026-06-16)', () => {
  test('source wires preserve helpers into account-status + poll merge', () => {
    const src = readSource();
    assert.match(src, /function mergePreservedInventorySnapshot/);
    assert.match(src, /function resolveEntryPublicSnapshot/);
    assert.match(src, /entry\.lastData = mergePreservedInventorySnapshot\(entry\.lastData, data\)/);
    assert.match(src, /refreshLiveSnapshotInventoryFromEntry\(entry, entry\.lastData\)/);
    assert.match(src, /const data = resolveEntryPublicSnapshot\(entry\)/);
    assert.match(src, /resolveEntryPublicSnapshot\(offlineEntry, lastData\)/);
  });

  test('account-status-shaped merge does not wipe fishItems from get-backpack payload', () => {
    const backpack = BACKPACK_FIXTURE;
    const helpers = extractInventoryHelpers(readSource());
    const statusRow = {
      username: backpack.username,
      statusColor: backpack.statusColor,
      accountPresenceLive: false,
      statsProven: true,
      inventoryDisplayState: 'ready',
      snapshotComplete: true,
      fishItems: [],
      stoneItems: [],
      totemItems: [],
    };
    const merged = helpers.mergePreservedInventorySnapshot(backpack, {
      ...backpack,
      ...statusRow,
    });
    assert.equal(merged.fishItems.length, backpack.fishItems.length);
    assert.ok(helpers.entryHasInventoryRows(merged), 'fish must survive status-only merge');
  });

  test('resolveEntryPublicSnapshot falls back to liveSnapshot fish when lastData lost rows', () => {
    const helpers = extractInventoryHelpers(readSource());
    const fish = [{ name: 'Ancient Lochness Monster', quantity: 1, rarity: 'Secret' }];
    const entry = {
      lastData: { username: 'demo', statsProven: true, fishItems: [] },
      liveSnapshot: { fishList: fish, stoneList: [] },
    };
    const resolved = helpers.resolveEntryPublicSnapshot(entry);
    assert.equal(resolved.fishItems.length, 1);
  });

  test('empty poll payload does not replace prior inventory snapshot', () => {
    const helpers = extractInventoryHelpers(readSource());
    const prior = {
      username: 'demo',
      fishItems: [{ name: 'Ruby Gemstone', quantity: 2, stoneType: 'Ruby' }],
      stoneItems: [{ name: 'Evolved Enchant Stone', quantity: 5, stoneType: 'Evolved' }],
      snapshotComplete: true,
      inventoryDisplayState: 'ready',
      playerStats: { coins: 1, totalCaught: 2, coinsText: '1', totalCaughtText: '2' },
    };
    const heartbeat = {
      username: 'demo',
      statusColor: 'yellow',
      accountPresenceLive: true,
      fishItems: [],
      stoneItems: [],
      totemItems: [],
      statsProven: true,
    };
    const merged = helpers.mergePreservedInventorySnapshot(prior, heartbeat);
    assert.equal(merged.fishItems.length, 1);
    assert.equal(merged.playerStats.coins, 1);
  });
});
