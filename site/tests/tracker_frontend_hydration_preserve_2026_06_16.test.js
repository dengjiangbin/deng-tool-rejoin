'use strict';

// Regression: /tracker must keep last good inventory when account-status refresh
// (status-only lane) merges over get-backpack data, choose newest snapshot, and
// render leaderstats from lastData when liveSnapshot stats are missing.

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
  lastInventoryAt: '2026-06-16T08:00:00.000Z',
};

function readSource() {
  return fs.readFileSync(SOURCE_PATH, 'utf8');
}

function extractInventoryHelpers(source) {
  const open = source.indexOf('  const INVENTORY_PRESERVE_KEYS = [');
  const close = source.indexOf('  function statsSnapshotReady(data) {', open);
  assert.ok(open > 0 && close > open, 'inventory preserve helpers missing from source');
  const block = source.slice(open, close);
  const script = `(function(){\n${block}\nreturn {
    mergePreservedInventorySnapshot,
    resolveEntryPublicSnapshot,
    entryHasInventoryRows,
    getSnapshotTime,
    isNewerSnapshot,
    shouldReplaceCurrentSnapshot,
    hasRenderableTrackerData,
    hasLeaderstats,
  };\n})()`;
  return vm.runInNewContext(script, {}, { filename: 'tracker-hydration-helpers.js' });
}

const DENGHUB2_API_FIXTURE = {
  username: 'denghub2',
  sessionKey: 'denghub2',
  userId: 11033782953,
  statusColor: 'red',
  inventoryDisplayState: 'ready',
  snapshotComplete: true,
  hasLeaderstatsSnapshot: true,
  lastInventoryAt: '2026-06-15T18:11:27.140Z',
  playerStats: {
    coins: 92997999,
    totalCaught: 148707,
    coinsText: '93M',
    totalCaughtText: '148.707',
    rarestFishChance: '1/4M',
  },
  liveAccountStats: {
    coins: 92997999,
    coinsText: '93M',
    totalCaught: 148707,
    totalCaughtText: '148.707',
    statsProven: true,
  },
  fishItems: Array.from({ length: 17 }, (_, i) => ({ name: `Fish ${i + 1}`, quantity: 1 })),
  stoneItems: [{ name: 'Evolved Enchant Stone', quantity: 1, stoneType: 'Evolved' }],
  totemItems: [{ name: 'Totem', quantity: 1 }],
};

describe('tracker frontend hydration preserve (2026-06-16)', () => {
  test('source wires preserve helpers into account-status + poll merge', () => {
    const src = readSource();
    assert.match(src, /function mergePreservedInventorySnapshot/);
    assert.match(src, /function shouldReplaceCurrentSnapshot/);
    assert.match(src, /function resolveEntryPublicSnapshot/);
    assert.match(src, /entry\.lastData = mergePreservedInventorySnapshot\(entry\.lastData, data\)/);
    assert.match(src, /refreshLiveSnapshotInventoryFromEntry\(entry, entry\.lastData\)/);
    assert.match(src, /const data = resolveEntryPublicSnapshot\(entry\)/);
    assert.match(src, /resolveEntryPublicSnapshot\(offlineEntry, lastData\)/);
    assert.match(src, /getEntryPlayerStats[\s\S]*extractPlayerStatsFromPayload\(entry\.lastData\)/);
    assert.match(src, /pollUser[\s\S]*credentials: 'same-origin'/);
    assert.match(src, /cache: 'no-store'/);
    assert.match(src, /params\.set\('_', String\(Date\.now\(\)\)\)/);
    assert.match(src, /function getFishRows/);
    assert.match(src, /function getItemRows/);
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

  test('fresh API snapshot replaces older cached snapshot', () => {
    const helpers = extractInventoryHelpers(readSource());
    const oldSnap = {
      fishItems: [{ name: 'Old Fish', quantity: 1 }],
      stoneItems: [],
      playerStats: { coinsText: '1', totalCaughtText: '1' },
      lastInventoryAt: '2026-06-15T08:00:00.000Z',
    };
    const freshSnap = {
      fishItems: [{ name: 'New Fish', quantity: 1 }, { name: 'New Fish 2', quantity: 1 }],
      stoneItems: [{ name: 'Stone', quantity: 1, stoneType: 'Evolved' }],
      playerStats: { coinsText: '999', totalCaughtText: '999' },
      lastInventoryAt: '2026-06-16T08:00:00.000Z',
    };
    assert.ok(helpers.shouldReplaceCurrentSnapshot(freshSnap, oldSnap));
    const merged = helpers.mergePreservedInventorySnapshot(oldSnap, freshSnap);
    assert.equal(merged.fishItems.length, 2);
    assert.equal(merged.lastInventoryAt, freshSnap.lastInventoryAt);
  });

  test('older empty heartbeat cannot override newer inventory snapshot', () => {
    const helpers = extractInventoryHelpers(readSource());
    const prior = {
      ...BACKPACK_FIXTURE,
      lastInventoryAt: '2026-06-16T08:00:00.000Z',
    };
    const heartbeat = {
      username: prior.username,
      statusColor: 'yellow',
      accountPresenceLive: true,
      fishItems: [],
      stoneItems: [],
      totemItems: [],
      statsProven: true,
      lastInventoryAt: '2026-06-16T08:00:05.000Z',
    };
    assert.ok(!helpers.shouldReplaceCurrentSnapshot(heartbeat, prior));
    const merged = helpers.mergePreservedInventorySnapshot(prior, heartbeat);
    assert.equal(merged.fishItems.length, 1);
    assert.equal(merged.playerStats.coins, 100);
    assert.equal(merged.lastInventoryAt, prior.lastInventoryAt);
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

  test('entryHasInventoryRows counts rows even when provenEmptyInventory flag is stale', () => {
    const helpers = extractInventoryHelpers(readSource());
    assert.ok(helpers.entryHasInventoryRows({
      provenEmptyInventory: true,
      fishItems: [{ name: 'Still Here', quantity: 1 }],
    }));
  });

  test('failed status refresh does not clear last good inventory', () => {
    const helpers = extractInventoryHelpers(readSource());
    const prior = { ...BACKPACK_FIXTURE };
    const failedStatus = {
      username: prior.username,
      accountPresenceLive: false,
      fishItems: [],
      stoneItems: [],
      totemItems: [],
      statsProven: false,
      liveAccountStats: { emptyReason: 'awaiting_inventory_snapshot' },
    };
    const merged = helpers.mergePreservedInventorySnapshot(prior, failedStatus);
    assert.ok(helpers.hasRenderableTrackerData(merged));
    assert.equal(merged.fishItems.length, 1);
    assert.equal(merged.playerStats.coins, 100);
  });

  test('denghub2-shaped API snapshot stays renderable after newer stats-only status merge', () => {
    const helpers = extractInventoryHelpers(readSource());
    const prior = { ...DENGHUB2_API_FIXTURE };
    const statusOnly = {
      username: 'denghub2',
      statusColor: 'red',
      accountPresenceLive: false,
      fishItems: [],
      stoneItems: [],
      totemItems: [],
      liveAccountStats: {
        coins: 93000000,
        coinsText: '93M',
        totalCaught: 148800,
        totalCaughtText: '148.800',
        statsProven: true,
      },
      lastStatsUploadAt: '2026-06-16T05:30:00.000Z',
    };
    const merged = helpers.mergePreservedInventorySnapshot(prior, statusOnly);
    assert.equal(merged.fishItems.length, 17);
    assert.equal(merged.stoneItems.length, 1);
    assert.equal(merged.totemItems.length, 1);
    assert.equal(merged.lastInventoryAt, prior.lastInventoryAt);
    assert.ok(helpers.hasRenderableTrackerData(merged));
  });

  test('fresh denghub2-style API response replaces 10-hour-old local snapshot', () => {
    const helpers = extractInventoryHelpers(readSource());
    const oldLocal = {
      ...DENGHUB2_API_FIXTURE,
      fishItems: [{ name: 'Stale Fish', quantity: 1 }],
      lastInventoryAt: '2026-06-15T08:00:00.000Z',
    };
    const freshApi = {
      ...DENGHUB2_API_FIXTURE,
      lastInventoryAt: '2026-06-16T05:00:00.000Z',
    };
    assert.ok(helpers.shouldReplaceCurrentSnapshot(freshApi, oldLocal));
    const merged = helpers.mergePreservedInventorySnapshot(oldLocal, freshApi);
    assert.equal(merged.fishItems.length, 17);
    assert.equal(merged.lastInventoryAt, freshApi.lastInventoryAt);
  });
});
