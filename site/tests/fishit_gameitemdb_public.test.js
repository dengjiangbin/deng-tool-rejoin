'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const gameItemDbPublic = require('../src/fishitGameItemDbPublic');
const { buildPublicFishFields, PUBLIC_API_BUILD } = require('../src/fishitTrackerRoutes');
const { BLOCKER10ZG_BUILD } = require('../src/fishitTrackerBuild');

const FINAL_BUILD = 'BLOCKER10ZH_EPIC_PURPLE_MYTHIC_RED_2026_06_09';

function fishRow(overrides = {}) {
  return {
    kind: 'fish',
    itemId: '70',
    name: 'Clownfish',
    baseName: 'Clownfish',
    quantity: 2,
    tier: 1,
    rarity: 'Common',
    type: 'Fish',
    mutation: 'None',
    icon: 'rbxassetid://1234567890123',
    source: 'playerdata_gameitemdb',
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
    icon: 'rbxassetid://9999999999999',
    source: 'playerdata_gameitemdb',
    identityVerified: true,
  };
}

describe('BLOCKER10ZG PlayerData GameItemDB public identity', () => {
  test('build marker is BLOCKER10ZG', () => {
    assert.equal(BLOCKER10ZG_BUILD, FINAL_BUILD);
    assert.equal(PUBLIC_API_BUILD, FINAL_BUILD);
    assert.equal(gameItemDbPublic.FINAL_BUILD, FINAL_BUILD);
  });

  test('GetIcon number returns rbxassetid://number', () => {
    const parsed = gameItemDbPublic.parseGameItemIcon(1234567890123);
    assert.equal(parsed.icon, 'rbxassetid://1234567890123');
    assert.equal(parsed.assetId, '1234567890123');
    assert.equal(parsed.imageSource, 'gameitemdb_icon');
  });

  test('GetIcon string number returns rbxassetid://number', () => {
    const parsed = gameItemDbPublic.parseGameItemIcon('9876543210987');
    assert.equal(parsed.icon, 'rbxassetid://9876543210987');
  });

  test('GetIcon already-prefixed icon is preserved', () => {
    const parsed = gameItemDbPublic.parseGameItemIcon('rbxassetid://1111111111111');
    assert.equal(parsed.icon, 'rbxassetid://1111111111111');
    assert.equal(parsed.assetId, '1111111111111');
  });

  test('GetIcon nil empty or 0 is missing not public image', () => {
    assert.equal(gameItemDbPublic.parseGameItemIcon(null), null);
    assert.equal(gameItemDbPublic.parseGameItemIcon(''), null);
    assert.equal(gameItemDbPublic.parseGameItemIcon(0), null);
    assert.equal(gameItemDbPublic.parseGameItemIcon('rbxassetid://0'), null);
    assert.equal(gameItemDbPublic.isValidPublicGameIcon(null), false);
  });

  test('tierToRarity maps ItemUtility tier through TierNames', () => {
    assert.equal(gameItemDbPublic.tierToRarity(3), 'Rare');
    assert.equal(gameItemDbPublic.tierToRarity(6), 'Mythic');
    assert.equal(gameItemDbPublic.tierToRarity(8), 'Forgotten');
  });

  test('fish inventory returns name quantity tier rarity mutation icon publicly', async () => {
    const session = {
      inventorySource: 'playerdata_gameitemdb',
      playerDataFishItems: [fishRow({ tier: 3, rarity: 'Rare', mutation: 'Shiny' })],
      playerDataStoneItems: [],
      sourceTruth: gameItemDbPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.fishItems.length, 1);
    assert.equal(pub.fishItems[0].name, 'Clownfish');
    assert.equal(pub.fishItems[0].rarity, 'Rare');
    assert.equal(pub.fishItems[0].tier, 3);
    assert.equal(pub.fishItems[0].mutation, null);
    assert.equal(pub.fishItems[0].debugMutation, 'Shiny');
    assert.equal(pub.fishItems[0].imageSource, 'gameitemdb_icon');
    assert.notEqual(pub.fishItems[0].dataSource, 'global_db');
  });

  test('stone inventory returns Normal Double Evolved Eggy Runic stones', async () => {
    const session = {
      inventorySource: 'playerdata_gameitemdb',
      playerDataFishItems: [],
      playerDataStoneItems: [
        stoneRow('Normal', 10, 3),
        stoneRow('Double', 246, 1),
        stoneRow('Evolved', 558, 2),
        stoneRow('Eggy', 873, 4),
        stoneRow('Runic', 929, 1),
      ],
      sourceTruth: gameItemDbPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.stoneItems.length, 5);
    assert.deepEqual(pub.stoneItems.map((s) => s.stoneType).sort(), ['Double', 'Eggy', 'Evolved', 'Normal', 'Runic']);
  });

  test('itemId 10 is stone not fish', async () => {
    const session = {
      inventorySource: 'playerdata_gameitemdb',
      playerDataFishItems: [fishRow()],
      playerDataStoneItems: [stoneRow('Normal', 10, 2)],
      sourceTruth: gameItemDbPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.fishItems.length, 1);
    assert.equal(pub.stoneItems.length, 1);
    assert.equal(pub.stoneItems[0].category, 'stone');
    assert.equal(pub.stoneItems[0].itemId, '10');
    assert.ok(!pub.fishItems.some((f) => f.itemId === '10'));
  });

  test('stones do not affect fish count or fish type count', async () => {
    const session = {
      inventorySource: 'playerdata_gameitemdb',
      playerDataFishItems: [fishRow({ quantity: 5 }), fishRow({ itemId: '71', name: 'Salmon', baseName: 'Salmon', quantity: 2 })],
      playerDataStoneItems: [stoneRow('Normal', 10, 7)],
      sourceTruth: gameItemDbPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.fishCounts.fishTypes, 2);
    assert.equal(pub.fishCounts.fishInstances, 7);
    assert.equal(pub.fishCounts.stoneInstances, 7);
    assert.equal(pub.publicCounts.visibleFishTypes, 2);
    assert.equal(pub.publicCounts.visibleStoneInstances, 7);
  });

  test('Global DB is not used for public identity', async () => {
    const session = {
      inventorySource: 'playerdata_gameitemdb',
      playerDataFishItems: [fishRow({ itemId: '267', name: 'Radiant Catfish', baseName: 'Radiant Catfish', tier: 5, rarity: 'Legendary' })],
      playerDataStoneItems: [],
      sourceTruth: gameItemDbPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.fishItems[0].name, 'Radiant Catfish');
    assert.equal(pub.fishItems[0].rarity, 'Legendary');
    assert.equal(pub.globalDbUiProof, null);
    assert.equal(pub.sourceTruth.globalDbUsedForPublicIdentity, false);
    assert.notEqual(pub.fishItems[0].imageSource, 'global_db');
    assert.notEqual(pub.fishItems[0].dataSource, 'global_db');
  });

  test('unknown unresolved items are hidden publicly', async () => {
    const session = {
      inventorySource: 'playerdata_gameitemdb',
      playerDataFishItems: [fishRow()],
      playerDataStoneItems: [],
      playerDataUnresolvedItems: [{ itemId: '555', reason: 'itemutility_unresolved' }],
      sourceTruth: gameItemDbPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.fishItems.length, 1);
    assert.doesNotMatch(pub.fishItems[0].name, /Unknown Fish #/i);
    assert.equal(pub.playerDataGameItemDbProof.unresolvedItems.length, 1);
  });

  test('public fish cards prefer uploaded game icon', async () => {
    const session = {
      inventorySource: 'playerdata_gameitemdb',
      playerDataFishItems: [fishRow({ icon: 'rbxassetid://1234567890123' })],
      playerDataStoneItems: [],
      sourceTruth: gameItemDbPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.fishItems[0].imageSource, 'gameitemdb_icon');
    assert.notEqual(pub.fishItems[0].imageSource, 'global_db');
    assert.ok(pub.fishItems[0].imageUrlPresent || pub.fishItems[0].imageUrl);
  });

  test('FINAL client without payload returns waiting activation state', async () => {
    const session = {
      trackerBuild: FINAL_BUILD,
      inventorySource: null,
      items: [{ name: 'Legacy Fish', category: 'fish', amount: 1, imageSource: 'global_db' }],
    };
    const pub = await buildPublicFishFields(session.items, 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.activationState, 'waiting_for_playerdata_gameitemdb_payload');
    assert.equal(pub.fishItems.length, 0);
    assert.equal(pub.globalDbUiProof, null);
  });

  test('detectGameItemDbUpload accepts proof uploadPath', () => {
    assert.equal(gameItemDbPublic.detectGameItemDbUpload({
      playerDataGameItemDbProof: { uploadPath: 'playerdata_gameitemdb' },
      fishItems: [],
      stoneItems: [stoneRow('Normal', 10)],
    }), true);
  });

  test('tracker.lua has direct Replion path and BLOCKER10ZC build marker', () => {
    const lua = fs.readFileSync(path.join(__dirname, '..', '..', 'tracker.lua'), 'utf8');
    assert.match(lua, /BLOCKER10ZC_DIRECT_REPLION_GAMEITEMDB_PUBLIC_PATH_2026_06_09/);
    assert.match(lua, /getDataReplionDirect/);
    assert.match(lua, /REPLION_DIRECT_OK/);
    assert.match(lua, /PLAYERDATA_INVENTORY_READ/);
    assert.match(lua, /buildGameItemDB/);
    assert.match(lua, /LiveSafe\.GetIcon/);
    assert.match(lua, /scanPlayerDataGameItemDbInventory/);
    assert.match(lua, /playerdata_gameitemdb/);
    assert.match(lua, /playerDataGameItemDbProof/);
    assert.match(lua, /PLAYERDATA_GAMEITEMDB_UPLOAD_OK/);
    assert.match(lua, /runDirectStartup/);
    assert.doesNotMatch(lua, /task\.spawn\(runReplionStartupPhase\)/);
    assert.doesNotMatch(lua, /task\.spawn\(runDirectPlayerDataStartupPhase\)/);
  });

  test('tracker template has GameItemDB debug proof and stones section', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.match(tpl, /buildPlayerDataGameItemDbProofHtml/);
    assert.match(tpl, /data-stones-section/);
    assert.match(tpl, /Stones:/);
  });

  test('debug proof shows playerDataGameItemDbProof fields', async () => {
    const session = {
      inventorySource: 'playerdata_gameitemdb',
      playerDataFishItems: [fishRow()],
      playerDataStoneItems: [stoneRow('Normal', 10)],
      playerDataGameItemDbProof: {
        enabled: true,
        build: FINAL_BUILD,
        gameItemDbBuilt: true,
        gameItemDbCount: 500,
        fishIconResolvedCount: 1,
      },
      sourceTruth: gameItemDbPublic.defaultSourceTruth(),
    };
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', { sessionData: session });
    assert.equal(pub.playerDataGameItemDbProof.enabled, true);
    assert.equal(pub.playerDataGameItemDbProof.build, FINAL_BUILD);
    assert.equal(pub.playerDataGameItemDbProof.inventorySource, 'playerdata_gameitemdb');
    assert.equal(pub.playerDataGameItemDbProof.globalDbUsedForPublicIdentity, false);
  });
});
