'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const manualRarity = require('../src/fishitManualRarityOverrides');
const gameItemDbPublic = require('../src/fishitGameItemDbPublic');
const inventorySort = require('../src/fishitInventorySort');
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

function extractCardGradientCss(tpl, rarityClass) {
  const re = new RegExp(
    `\\.item-card\\.${rarityClass}[^{]*\\{[^}]*background:linear-gradient\\(([^)]+)\\)`,
  );
  const match = tpl.match(re);
  return match ? match[1] : '';
}

function loadCardRarityClassFn() {
  const tpl = fs.readFileSync(TRACKER_PATH, 'utf8');
  const script = tpl.slice(tpl.indexOf('<script>'), tpl.indexOf('</script>') + 9);
  const fn = script.match(/function cardRarityClass\(r\)\s*\{[\s\S]*?\n  \}/);
  assert.ok(fn, 'cardRarityClass must exist in tracker template');
  return new Function(`
    const CARD_RARITY_MAP = { common:'rarity-common', uncommon:'rarity-uncommon', rare:'rarity-rare', epic:'rarity-epic', legendary:'rarity-legendary', legend:'rarity-legendary', mythic:'rarity-mythic', secret:'rarity-secret', forgotten:'rarity-forgotten' };
    ${fn[0]}
    return cardRarityClass;
  `)();
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

    const proofRows = pub.manualRarityProof?.rows || [];
    const proofRow = proofRows.find((r) => r.name === 'Skeleton Angler Fish');
    if (proofRow) {
      assert.equal(proofRow.rarity, 'Epic');
      assert.notEqual(proofRow.rarity, 'Mythic');
    }
  });

  test('Skeleton Angler Fish card class is rarity-epic not rarity-mythic', () => {
    const cardRarityClass = loadCardRarityClassFn();
    assert.equal(cardRarityClass('Epic'), 'rarity-epic');
    assert.equal(cardRarityClass('Mythic'), 'rarity-mythic');
    assert.equal(cardRarityClassForTier('Epic'), 'rarity-epic');
    assert.notEqual(cardRarityClass('Epic'), 'rarity-mythic');
  });

  test('ft-card epic background is red and mythic background is dark red', () => {
    const tpl = fs.readFileSync(TRACKER_PATH, 'utf8');
    assert.match(tpl, /\.ft-rarity-EPIC[\s\S]*background:#dc2626/);
    assert.match(tpl, /\.ft-rarity-MYTHIC[\s\S]*background:#b91c1c/);
    assert.match(tpl, /\.ft-rarity-SECRET[\s\S]*background:#16d487/);
    assert.doesNotMatch(tpl, /\.ft-rarity-EPIC[\s\S]*#9333ea/);
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
    assert.equal(inventorySort.rarityRank({ rarity: 'Epic' }), 400);
    assert.equal(inventorySort.rarityRank({ rarity: 'Mythic' }), 600);
    assert.ok(inventorySort.rarityRank({ rarity: 'Mythic' }) > inventorySort.rarityRank({ rarity: 'Epic' }));
    assert.ok(inventorySort.rarityRank({ rarity: 'Epic' }) > inventorySort.rarityRank({ rarity: 'Rare' }));
  });
});
