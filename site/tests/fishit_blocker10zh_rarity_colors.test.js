'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const manualRarity = require('../src/fishitManualRarityOverrides');
const gameItemDbPublic = require('../src/fishitGameItemDbPublic');
const inventorySort = require('../src/fishitInventorySort');
const trackerRarityStyle = require('../src/fishitTrackerRarityStyle');
const { cardRarityClassForTier } = require('../src/fishitRarityColorMap');
const { BLOCKER10ZH_BUILD } = require('../src/fishitTrackerBuild');
const { PUBLIC_API_BUILD, buildPublicFishFields } = require('../src/fishitTrackerRoutes');

const FINAL_BUILD = 'BLOCKER10ZK_INVENTORY_MOBILE_BULK_APK_2026_06_09';
const OVERRIDES_PATH = path.join(__dirname, '..', 'data', 'fishit_manual_rarity_overrides.json');
const TRACKER_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');
const BASE_URL = 'http://127.0.0.1:8791';

function fishRow(overrides = {}) {
  return {
    kind: 'fish',
    itemId: '268',
    name: 'Skeleton Angler Fish',
    baseName: 'Skeleton Angler Fish',
    baseFishName: 'Skeleton Angler Fish',
    quantity: 1,
    tier: 1,
    rarity: 'Common',
    type: 'Fish',
    icon: 'rbxassetid://1',
    source: 'playerdata_gameitemdb',
    identityVerified: true,
    ...overrides,
  };
}

function sessionWith(fishItems = []) {
  return {
    inventorySource: 'playerdata_gameitemdb',
    playerDataFishItems: fishItems,
    playerDataStoneItems: [],
    sourceTruth: gameItemDbPublic.defaultSourceTruth(),
  };
}

describe('BLOCKER10ZH Skeleton Angler Fish Epic + Epic purple / Mythic red', () => {
  test('build marker is BLOCKER10ZH', () => {
    assert.equal(BLOCKER10ZH_BUILD, FINAL_BUILD);
    assert.equal(PUBLIC_API_BUILD, FINAL_BUILD);
  });

  test('manual overrides file maps Skeleton Angler Fish and item 268 to Epic', () => {
    const raw = JSON.parse(fs.readFileSync(OVERRIDES_PATH, 'utf8'));
    assert.equal(raw.byName['Skeleton Angler Fish'], 'Epic');
    assert.equal(raw.byItemId['268'], 'Epic');
    assert.notEqual(raw.byName['Skeleton Angler Fish'], 'Mythic');
    assert.notEqual(raw.byItemId['268'], 'Mythic');
  });

  test('resolvePublicFishRarity returns Epic for Skeleton Angler Fish', () => {
    const resolved = manualRarity.resolvePublicFishRarity(fishRow(), () => 'Common');
    assert.equal(resolved.rarity, 'Epic');
    assert.equal(resolved.tier, 4);
    assert.equal(resolved.raritySource, 'manual_rarity_override');
  });

  test('public API row for Skeleton Angler Fish is Epic with manual override source', async () => {
    const pub = await buildPublicFishFields([], BASE_URL, {
      sessionData: sessionWith([fishRow()]),
    });
    const row = pub.fishItems.find((f) => (f.name || f.baseFishName) === 'Skeleton Angler Fish');
    assert.ok(row, 'Skeleton Angler Fish row must exist');
    assert.equal(row.rarity, 'Epic');
    assert.equal(row.tier, 4);
    assert.equal(row.raritySource, 'manual_rarity_override');
    assert.notEqual(row.rarity, 'Mythic');
  });

  test('Skeleton Angler Fish card class is rarity-epic not rarity-mythic', () => {
    assert.equal(cardRarityClassForTier('Epic'), 'rarity-epic');
    assert.equal(cardRarityClassForTier('Mythic'), 'rarity-mythic');
    assert.equal(trackerRarityStyle.ftRarityClass('Epic'), 'ft-rarity-EPIC');
    assert.equal(trackerRarityStyle.ftRarityClass('Mythic'), 'ft-rarity-MYTHIC');
  });

  test('canonical tracker rarity CSS uses Epic purple and Mythic red', () => {
    const css = trackerRarityStyle.buildFtCardRarityCss();
    const epicLine = css.split('\n').find((line) => line.includes('.ft-rarity-EPIC'));
    const mythicLine = css.split('\n').find((line) => line.includes('.ft-rarity-MYTHIC'));
    assert.match(epicLine, /background:#9333ea/);
    assert.match(mythicLine, /background:#dc2626/);
    assert.doesNotMatch(epicLine, /#dc2626/);
    const tpl = fs.readFileSync(TRACKER_PATH, 'utf8');
    assert.match(tpl, /trackerRarityCardCss/);
    assert.doesNotMatch(tpl, /\.ft-rarity-EPIC[\s\S]*background:#dc2626/);
  });

  test('Skeleton Angler Fish sorts as Epic between Legendary/Mythic and Rare', () => {
    const items = [
      { name: 'Rare Fish', rarity: 'Rare', itemId: '1' },
      { name: 'Skeleton Angler Fish', rarity: 'Epic', itemId: '268' },
      { name: 'Legendary Fish', rarity: 'Legendary', itemId: '2' },
      { name: 'Mythic Fish', rarity: 'Mythic', itemId: '3' },
    ];
    const sorted = inventorySort.sortInventoryFish(items);
    assert.deepEqual(sorted.map((f) => f.name), [
      'Mythic Fish',
      'Legendary Fish',
      'Skeleton Angler Fish',
      'Rare Fish',
    ]);
  });
});
