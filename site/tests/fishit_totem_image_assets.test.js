'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');

const manualInventoryImages = require('../src/fishitInventoryManualImages');
const gameItemDbPublic = require('../src/fishitGameItemDbPublic');
const totemImageAssets = require('../src/fishitTotemImageAssets');

describe('totem image resolver', () => {
  test('Mutation Totem uses manual override when catalog placeholder is stale', () => {
    const rows = totemImageAssets.attachTotemImagesToItems([
      {
        kind: 'totem',
        itemId: '2',
        name: 'Mutation Totem',
        quantity: 60,
        icon: 'rbxassetid://75593774049916',
        source: 'playerdata_gameitemdb',
      },
    ], 'https://tool.deng.my.id');

    assert.equal(rows.length, 1);
    const row = rows[0];
    assert.match(row.imageUrl, /\/api\/tracker\/assets\/manual\/totems\//);
    assert.equal(row.imageSource, 'manual_override');
    assert.equal(row.imageResolver, 'totem_manual_override');
    assert.equal(row.category, 'totem');
    assert.doesNotMatch(String(row.imageUrl), /\/assets\/fish\//);
  });

  test('Shiny Totem uses the 2026-06-15 manual override (wins over gameDB proxy)', () => {
    const rows = totemImageAssets.attachTotemImagesToItems([
      {
        kind: 'totem',
        itemId: '502',
        name: 'Shiny Totem',
        quantity: 1,
        icon: 'rbxassetid://1234567890123',
        source: 'playerdata_gameitemdb',
      },
    ], 'http://127.0.0.1:8791');

    assert.match(rows[0].imageUrl, /\/api\/(fishit-)?tracker\/assets\/manual\/totems\/shiny_totem_2026_06_15\.png/);
    assert.equal(rows[0].imageSource, 'manual_override');
    assert.equal(rows[0].imageResolver, 'totem_manual_override');
    assert.equal(rows[0].name, 'Shiny Totem');
  });

  test('Love Totem and Runic Stone resolve to their 2026-06-15 manual overrides', () => {
    const totemRows = totemImageAssets.attachTotemImagesToItems([
      { kind: 'totem', itemId: '777', name: 'Love Totem', quantity: 2, icon: 'rbxassetid://999', source: 'playerdata_gameitemdb' },
    ], 'http://127.0.0.1:8791');
    assert.match(totemRows[0].imageUrl, /manual\/totems\/love_totem_2026_06_15\.png/);
    assert.equal(totemRows[0].imageSource, 'manual_override');

    const stoneImageAssets = require('../src/fishitStoneImageAssets');
    const stoneRows = stoneImageAssets.attachStoneImagesToItems([
      { kind: 'stone', itemId: '900', name: 'Runic Stone', stoneType: 'Runic', quantity: 1, icon: 'rbxassetid://888', source: 'playerdata_gameitemdb' },
    ], 'http://127.0.0.1:8791');
    assert.match(stoneRows[0].imageUrl, /manual\/stones\/runic_stone_2026_06_15\.png/);
    assert.equal(stoneRows[0].imageSource, 'manual_override');
    assert.equal(stoneRows[0].imageResolver, 'stone_manual_override');
  });

  test('buildPublicFromPlayerDataGameItemDb maps totem cards with totem image source', async () => {
    const out = await gameItemDbPublic.buildPublicFromPlayerDataGameItemDb({
      playerDataFishItems: [],
      playerDataStoneItems: [],
      playerDataTotemItems: [{
        kind: 'totem',
        itemId: '2',
        name: 'Mutation Totem',
        quantity: 3,
        icon: 'rbxassetid://75593774049916',
        source: 'playerdata_gameitemdb',
        identityVerified: true,
      }],
      sourceTruth: gameItemDbPublic.defaultSourceTruth(),
    }, 'https://tool.deng.my.id', {});

    assert.equal(out.totemItems.length, 1);
    const card = out.totemItems[0];
    assert.equal(card.name, 'Mutation Totem');
    assert.match(card.imageUrl, /\/api\/tracker\/assets\/(manual\/totems\/|totems\/)/);
    assert.equal(card.imageSource, manualInventoryImages.MANUAL_OVERRIDE_SOURCE);
    assert.equal(card.imageResolver, 'totem_manual_override');
    assert.ok(out.totemAssetProof);
    assert.equal(out.totemAssetProof.rows[0].usesFishAssetPath, false);
  });

  test('fish rows still use fish resolver when totems are present', async () => {
    const fishImageCache = {
      attachItemUtilityGameIcons: async (items) => items.map((item) => ({
        ...item,
        imageUrl: '/api/fishit-tracker/assets/fish/test_fish.png',
        imageSource: 'gameitemdb_icon',
      })),
    };
    const out = await gameItemDbPublic.buildPublicFromPlayerDataGameItemDb({
      playerDataFishItems: [{
        kind: 'fish', itemId: '70', name: 'Clownfish', baseName: 'Clownfish',
        quantity: 1, tier: 1, rarity: 'Common', type: 'Fish',
        icon: 'rbxassetid://123', source: 'playerdata_gameitemdb', identityVerified: true,
      }],
      playerDataStoneItems: [],
      playerDataTotemItems: [{
        kind: 'totem', itemId: '2', name: 'Mutation Totem', quantity: 1,
        icon: 'rbxassetid://75593774049916', source: 'playerdata_gameitemdb', identityVerified: true,
      }],
      sourceTruth: gameItemDbPublic.defaultSourceTruth(),
    }, 'https://tool.deng.my.id', { fishImageCache });

    assert.match(out.fishItems[0].imageUrl, /\/assets\/fish\//);
    assert.match(out.totemItems[0].imageUrl, /\/api\/tracker\/assets\/(manual\/totems\/|totems\/)/);
  });
});
