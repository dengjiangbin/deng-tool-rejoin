'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');

const gameItemDbPublic = require('../src/fishitGameItemDbPublic');
const totemImageAssets = require('../src/fishitTotemImageAssets');

describe('totem image resolver', () => {
  test('Mutation Totem uses totem catalog path not fish cache', () => {
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
    assert.match(row.imageUrl, /\/api\/fishit-tracker\/assets\/totems\/totem_mutation_totem\.webp/);
    assert.equal(row.imageSource, 'totem_manual_asset');
    assert.equal(row.imageResolver, 'totem_catalog');
    assert.equal(row.category, 'totem');
    assert.doesNotMatch(String(row.imageUrl), /\/assets\/fish\//);
  });

  test('Shiny Totem resolves via totem catalog by canonical name', () => {
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

    assert.match(rows[0].imageUrl, /\/api\/fishit-tracker\/assets\/totems\/totem_shiny_totem\.png/);
    assert.equal(rows[0].imageSource, 'totem_manual_asset');
    assert.equal(rows[0].imageResolver, 'totem_catalog');
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
    assert.match(card.imageUrl, /\/api\/fishit-tracker\/assets\/totems\//);
    assert.equal(card.imageSource, 'totem_manual_asset');
    assert.equal(card.imageResolver, 'totem_catalog');
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
    assert.match(out.totemItems[0].imageUrl, /\/assets\/totems\//);
  });
});
