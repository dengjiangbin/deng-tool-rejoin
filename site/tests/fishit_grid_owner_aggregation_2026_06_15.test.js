'use strict';

// Regression tests for the Discord-owner grid aggregation fix (2026-06-15).
// Bug: Fish / Enchant Stone / Totem grid cards were rendered once per Roblox
// username instead of being merged across all usernames linked to the same
// Discord owner. Two concrete defects were fixed:
//   A) the totem group key folded in a per-instance `uuid`, so the same totem
//      owned by N usernames produced N duplicate cards (person icon = 1 each).
//   B) the stone/totem subgrid render path never applied the per-item
//      `buildOpts`, so the aggregated `accountCount` never reached the card and
//      the person icon was stuck at 1.
// The grid must show ONE card per canonical item, person icon = distinct
// usernames, quantity = combined total. The per-account backpack/table view
// must stay individual (no owner chip, no cross-username merge).

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const bulk = require('../src/fishitInventoryBulk');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const MANIFEST_PATH = path.join(__dirname, '..', 'src', 'inventoryAssetManifest.json');

function readSource() {
  return fs.readFileSync(SOURCE_PATH, 'utf8');
}

function readBuiltJs() {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
  return fs.readFileSync(path.join(__dirname, '..', 'public', 'assets', manifest.js), 'utf8');
}

describe('grid owner-aggregation (2026-06-15)', () => {
  test('case 1: 3 usernames with the same Luck Totem => one card, person=3, qty summed', () => {
    const result = bulk.aggregateBulkInventory([
      { username: 'acc1', totemList: [{ name: 'Luck Totem', itemId: 'totem_luck', uuid: 'u-aaa', amount: 2 }] },
      { username: 'acc2', totemList: [{ name: 'Luck Totem', itemId: 'totem_luck', uuid: 'u-bbb', amount: 3 }] },
      { username: 'acc3', totemList: [{ name: 'Luck Totem', itemId: 'totem_luck', uuid: 'u-ccc', amount: 5 }] },
    ]);
    assert.equal(result.totems.length, 1, 'expected a single merged Luck Totem card');
    assert.equal(result.totems[0].accountCount, 3, 'person icon must equal distinct username count');
    assert.equal(result.totems[0].amount, 10, 'quantity must be the combined total across usernames');
    assert.equal(result.accountCount, 3);
  });

  test('case 2: duplicate rows for the same username do not inflate person count or quantity', () => {
    const result = bulk.aggregateBulkInventory([
      {
        username: 'solo',
        totemList: [
          { name: 'Love Totem', itemId: 'totem_love', uuid: 'x1', amount: 7 },
          { name: 'Love Totem', itemId: 'totem_love', uuid: 'x2', amount: 7 },
          { name: 'Love Totem', itemId: 'totem_love', uuid: 'x3', amount: 7 },
        ],
      },
    ]);
    assert.equal(result.totems.length, 1);
    assert.equal(result.totems[0].accountCount, 1, 'one username = one contributor regardless of duplicate rows');
    assert.equal(result.totems[0].amount, 7, 'duplicate same-source rows must not double-count quantity');
  });

  test('case 3: different real items with similar names are not merged', () => {
    const result = bulk.aggregateBulkInventory([
      {
        username: 'acc1',
        totemList: [
          { name: 'Luck Totem', itemId: 'totem_luck', amount: 1 },
          { name: 'Lucky Totem', itemId: 'totem_lucky', amount: 1 },
        ],
      },
    ]);
    assert.equal(result.totems.length, 2, 'distinct totem identities must stay distinct');
  });

  test('case 4: fish of the same species merge across usernames', () => {
    const result = bulk.aggregateBulkInventory([
      { username: 'acc1', fishList: [{ name: 'King Crab', baseFishName: 'King Crab', rarity: 'Secret', amount: 10 }] },
      { username: 'acc2', fishList: [{ name: 'King Crab', baseFishName: 'King Crab', rarity: 'Secret', amount: 22 }] },
      { username: 'acc3', fishList: [{ name: 'King Crab', baseFishName: 'King Crab', rarity: 'Secret', amount: 8 }] },
    ]);
    assert.equal(result.fish.length, 1);
    assert.equal(result.fish[0].accountCount, 3);
    assert.equal(result.fish[0].amount, 40);
  });

  test('case 5: enchant stone of the same type merges across usernames', () => {
    const result = bulk.aggregateBulkInventory([
      { username: 'acc1', stoneList: [{ name: 'Normal Enchant Stone', stoneType: 'Normal', amount: 5 }] },
      { username: 'acc2', stoneList: [{ name: 'Normal Enchant Stone', stoneType: 'Normal', amount: 8 }] },
    ]);
    assert.equal(result.stones.length, 1);
    assert.equal(result.stones[0].accountCount, 2);
    assert.equal(result.stones[0].amount, 13);
  });

  test('case 7: search/filter operates on aggregated cards', () => {
    const result = bulk.aggregateBulkInventory([
      { username: 'alpha', totemList: [{ name: 'Mutation Totem', itemId: 'totem_mut', amount: 2 }] },
      { username: 'beta', totemList: [{ name: 'Mutation Totem', itemId: 'totem_mut', amount: 4 }] },
      { username: 'beta', totemList: [{ name: 'Luck Totem', itemId: 'totem_luck', amount: 1 }] },
    ]);
    const merged = bulk.aggregateBulkInventory([
      { username: 'alpha', totemList: [{ name: 'Mutation Totem', itemId: 'totem_mut', amount: 2 }] },
      { username: 'beta', totemList: [{ name: 'Mutation Totem', itemId: 'totem_mut', amount: 4 }] },
    ]);
    assert.equal(merged.totems.length, 1);
    assert.equal(merged.totems[0].accountCount, 2);
    assert.equal(bulk.filterBulkItems(result.totems, 'mutation').length, 1);
    assert.equal(bulk.filterBulkItems(result.totems, 'alpha').length, 1, 'owner usernames remain searchable on aggregated cards');
    assert.equal(bulk.filterBulkItems(result.totems, 'nope').length, 0);
  });

  test('group keys: totems key on canonical name+itemId, never per-instance uuid', () => {
    const a = bulk.bulkGroupKey('totem', { name: 'Luck Totem', itemId: 'totem_luck', uuid: 'aaa' });
    const b = bulk.bulkGroupKey('totem', { name: 'Luck Totem', itemId: 'totem_luck', uuid: 'bbb' });
    assert.equal(a, b, 'same totem with different uuid must share a group key');
    assert.doesNotMatch(a, /aaa|bbb/, 'totem group key must not contain the uuid');
  });

  // ---- Production source guards (these protect the live /tracker grid) ----

  test('source: totem bulk group key does not use uuid', () => {
    const src = readSource();
    const totemBranch = src.match(/if \(cat === 'totem'\) \{[\s\S]*?\n {4}\}/);
    assert.ok(totemBranch, 'totem branch present in bulkGroupKey');
    const code = totemBranch[0].replace(/\/\/[^\n]*/g, ''); // drop explanatory comments
    assert.doesNotMatch(code, /uuid/, 'totem group key code must not fold in per-instance uuid');
    assert.match(code, /item\?\.itemId/);
  });

  test('source: stone & totem subgrids apply per-item buildOpts so accountCount reaches cards', () => {
    const src = readSource();
    const stoneFn = src.match(/function patchStonesSubgrid[\s\S]*?\n  \}/);
    const totemFn = src.match(/function patchTotemsSubgrid[\s\S]*?\n  \}/);
    assert.ok(stoneFn && totemFn);
    assert.match(stoneFn[0], /typeof opts\.buildOpts === 'function'/);
    assert.match(totemFn[0], /typeof opts\.buildOpts === 'function'/);
  });

  test('source: backpack/table view stays individual (no owner chip, no aggregation)', () => {
    const src = readSource();
    assert.match(src, /INDIVIDUAL_BACKPACK_CARD_OPTS = Object\.freeze\(\{ includeOwnerChip: false/);
    // backpack render path uses the individual opts, not the bulk buildOpts factory
    assert.match(src, /patchItemGrid\(stoneHost, stoneList \|\| \[\], totemList \|\| \[\], INDIVIDUAL_BACKPACK_CARD_OPTS\)/);
  });

  test('built asset mirrors the source fixes', () => {
    const js = readBuiltJs();
    assert.match(js, /ownerAmounts/, 'per-owner dedupe shipped to built asset');
    const totemBranch = js.match(/if \(cat === 'totem'\) \{[\s\S]*?\}/);
    assert.ok(totemBranch);
    assert.doesNotMatch(totemBranch[0], /uuid/);
  });
});
