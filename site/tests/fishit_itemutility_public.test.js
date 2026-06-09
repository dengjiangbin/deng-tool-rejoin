'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const itemUtilityPublic = require('../src/fishitItemUtilityPublic');
const { buildPublicFishFields, PUBLIC_API_BUILD } = require('../src/fishitTrackerRoutes');
const { BLOCKER10ZA_BUILD } = require('../src/fishitTrackerBuild');

const ZA_BUILD = 'BLOCKER10ZA_PLAYERDATA_ITEMUTILITY_STONES_UPLOAD_2026_06_09';

function fishRow(overrides = {}) {
  return {
    kind: 'fish',
    itemId: '70',
    name: 'Clownfish',
    baseName: 'Clownfish',
    quantity: 2,
    tier: 'Common',
    type: 'Fish',
    mutation: 'None',
    source: 'playerdata_itemutility',
    identityVerified: true,
    ...overrides,
  };
}

function stoneRow(type, itemId, qty = 1) {
  return {
    kind: 'stone',
    itemId: String(itemId),
    name: `${type} Enchant Stone`,
    stoneType: type,
    quantity: qty,
    source: 'playerdata_itemutility',
    identityVerified: true,
  };
}

describe('BLOCKER10ZA PlayerData ItemUtility public identity', () => {
  test('build marker is BLOCKER10ZA', () => {
    assert.equal(BLOCKER10ZA_BUILD, ZA_BUILD);
    assert.equal(PUBLIC_API_BUILD, ZA_BUILD);
  });

  test('fish resolved from ItemUtility appears publicly', async () => {
    const session = {
      inventorySource: 'playerdata_itemutility',
      playerDataFishItems: [fishRow()],
      playerDataStoneItems: [],
      sourceTruth: itemUtilityPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.fishItems.length, 1);
    assert.equal(pub.fishItems[0].name, 'Clownfish');
    assert.equal(pub.fishItems[0].baseFishName, 'Clownfish');
    assert.equal(pub.fishItems[0].identitySource, 'playerdata_itemutility');
    assert.equal(pub.fishItems[0].publicIdentityProof.globalDbUsedForPublicIdentity, false);
    assert.equal(pub.fishItems[0].mutation, null);
    assert.equal(pub.fishItems[0].debugMutation, null);
  });

  test('stone ids 10, 246, 558, 873, 929 appear as stones', async () => {
    const session = {
      inventorySource: 'playerdata_itemutility',
      playerDataFishItems: [],
      playerDataStoneItems: [
        stoneRow('Normal', 10, 3),
        stoneRow('Double', 246, 1),
        stoneRow('Evolved', 558, 2),
        stoneRow('Eggy', 873, 4),
        stoneRow('Runic', 929, 1),
      ],
      sourceTruth: itemUtilityPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.stoneItems.length, 5);
    const types = pub.stoneItems.map((s) => s.stoneType).sort();
    assert.deepEqual(types, ['Double', 'Eggy', 'Evolved', 'Normal', 'Runic']);
    assert.equal(pub.fishCounts.stoneInstances, 11);
  });

  test('stone items do not increase fish count', async () => {
    const session = {
      inventorySource: 'playerdata_itemutility',
      playerDataFishItems: [fishRow({ quantity: 5 })],
      playerDataStoneItems: [stoneRow('Normal', 10, 7)],
      sourceTruth: itemUtilityPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.fishCounts.fishTypes, 1);
    assert.equal(pub.fishCounts.fishInstances, 5);
    assert.equal(pub.fishCounts.stoneTypes, 1);
    assert.equal(pub.publicCounts.visibleFishTypes, 1);
    assert.equal(pub.publicCounts.visibleStoneInstances, 7);
  });

  test('non-fish non-stone and unresolved rows are hidden from public output', async () => {
    const session = {
      inventorySource: 'playerdata_itemutility',
      playerDataFishItems: [
        fishRow(),
        { itemId: '999', name: 'Unknown Fish #999', source: 'replion', identityVerified: false },
        { itemId: '888', name: 'Rod Item', kind: 'rod', source: 'playerdata_itemutility', identityVerified: true },
      ],
      playerDataStoneItems: [],
      playerDataHiddenUnresolved: [{ itemId: '555', reason: 'itemutility_unresolved' }],
      sourceTruth: itemUtilityPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.fishItems.length, 1);
    assert.equal(pub.fishItems[0].name, 'Clownfish');
    assert.doesNotMatch(pub.fishItems[0].name, /Unknown Fish #/i);
    assert.equal(pub.playerDataItemUtilityProof.hiddenUnresolvedRows.length, 1);
  });

  test('Global DB mapping is not used for public identity', async () => {
    const session = {
      inventorySource: 'playerdata_itemutility',
      playerDataFishItems: [fishRow({ itemId: '267', name: 'Radiant Catfish', baseName: 'Radiant Catfish' })],
      playerDataStoneItems: [],
      sourceTruth: itemUtilityPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.fishItems[0].name, 'Radiant Catfish');
    assert.equal(pub.globalDbUiProof, null);
    assert.equal(pub.sourceTruth.globalDbUsedForPublicIdentity, false);
  });

  test('mutation is uploaded for proof but hidden on public fish cards', async () => {
    const session = {
      inventorySource: 'playerdata_itemutility',
      playerDataFishItems: [fishRow({ mutation: 'Shiny', name: 'Clownfish', baseName: 'Clownfish' })],
      playerDataStoneItems: [],
      sourceTruth: itemUtilityPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.fishItems[0].name, 'Clownfish');
    assert.equal(pub.fishItems[0].mutation, null);
    assert.equal(pub.playerDataItemUtilityProof.sampleFish[0].mutation, 'Shiny');
  });

  test('tracker template has stones section and itemutility debug proof', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.match(tpl, /data-stones-section/);
    assert.match(tpl, /buildPlayerDataItemUtilityProofHtml/);
    assert.match(tpl, /Stones:/);
    assert.match(tpl, /BLOCKER10ZA|data-render-build/);
  });

  test('tracker.lua has ItemUtility scan and BLOCKER10ZA build marker', () => {
    const lua = fs.readFileSync(path.join(__dirname, '..', '..', 'tracker.lua'), 'utf8');
    assert.match(lua, /BLOCKER10ZA_PLAYERDATA_ITEMUTILITY_STONES_UPLOAD_2026_06_09/);
    assert.match(lua, /scanPlayerDataItemUtilityInventory/);
    assert.match(lua, /ItemUtility\.GetItemDataFromItemType/);
    assert.match(lua, /LiveSafe\.getFishIcon/);
    assert.match(lua, /game_fish_icon_catalog/);
    assert.match(lua, /payload\.fishItems/);
    assert.match(lua, /payload\.stoneItems/);
  });

  test('parseGameFishIcon resolves numeric and rbxassetid icons', () => {
    assert.deepEqual(itemUtilityPublic.parseGameFishIcon('123456789012'), {
      icon: 'rbxassetid://123456789012',
      assetId: '123456789012',
      imageSource: 'game_fish_icon_catalog',
    });
    assert.deepEqual(itemUtilityPublic.parseGameFishIcon('rbxassetid://987654321098'), {
      icon: 'rbxassetid://987654321098',
      assetId: '987654321098',
      imageSource: 'game_fish_icon_catalog',
    });
    assert.equal(itemUtilityPublic.parseGameFishIcon('rbxassetid://0'), null);
    assert.equal(itemUtilityPublic.parseGameFishIcon('0'), null);
    assert.equal(itemUtilityPublic.isValidPublicGameIcon(null), false);
  });

  test('public fish cards prefer uploaded game icon over Global DB fallback', async () => {
    const assetId = '1234567890123';
    const session = {
      inventorySource: 'playerdata_itemutility',
      playerDataFishItems: [fishRow({
        icon: `rbxassetid://${assetId}`,
        imageSource: 'game_fish_icon_catalog',
      })],
      playerDataStoneItems: [],
      sourceTruth: itemUtilityPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.fishItems.length, 1);
    assert.equal(pub.fishItems[0].imageSource, 'game_fish_icon_catalog');
    assert.ok(pub.fishItems[0].imageUrlPresent || pub.fishItems[0].imageUrl);
    assert.notEqual(pub.fishItems[0].imageSource, 'global_db');
  });

  test('missing icon does not create fake unknown fish cards', async () => {
    const session = {
      inventorySource: 'playerdata_itemutility',
      playerDataFishItems: [fishRow({ itemId: '267', name: 'Radiant Catfish', baseName: 'Radiant Catfish', icon: null })],
      playerDataStoneItems: [],
      sourceTruth: itemUtilityPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.fishItems.length, 1);
    assert.equal(pub.fishItems[0].name, 'Radiant Catfish');
    assert.doesNotMatch(pub.fishItems[0].name, /Unknown Fish #/i);
    assert.equal(pub.fishItems[0].imageUrlPresent, false);
  });

  test('debug proof shows imageSource game_fish_icon_catalog', async () => {
    const session = {
      inventorySource: 'playerdata_itemutility',
      playerDataFishItems: [fishRow({ icon: 'rbxassetid://1234567890123' })],
      playerDataStoneItems: [],
      playerDataItemUtilityProof: {
        enabled: true,
        imageSource: 'game_fish_icon_catalog',
        fishIconCatalogLoaded: true,
        fishIconResolvedCount: 1,
        fishIconMissingCount: 0,
      },
      sourceTruth: itemUtilityPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.playerDataItemUtilityProof.imageSource, 'game_fish_icon_catalog');
    assert.equal(pub.playerDataItemUtilityProof.fishIconCatalogLoaded, true);
    assert.ok(Array.isArray(pub.playerDataItemUtilityProof.sampleFishIcons));
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.match(tpl, /sampleFishIcons/);
    assert.match(tpl, /game_fish_icon_catalog/);
  });
});
