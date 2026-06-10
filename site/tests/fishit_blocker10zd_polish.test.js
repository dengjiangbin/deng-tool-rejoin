'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const gameItemDbPublic = require('../src/fishitGameItemDbPublic');
const manualRarity = require('../src/fishitManualRarityOverrides');
const stoneImageAssets = require('../src/fishitStoneImageAssets');
const { buildPublicFishFields, PUBLIC_API_BUILD } = require('../src/fishitTrackerRoutes');
const { BLOCKER10ZG_BUILD } = require('../src/fishitTrackerBuild');

const FINAL_BUILD = 'BLOCKER10ZK_INVENTORY_MOBILE_BULK_APK_2026_06_09';
const BASE_URL = 'http://127.0.0.1:8791';

function fishRow(overrides = {}) {
  return {
    kind: 'fish',
    itemId: '70',
    name: 'Clownfish',
    baseName: 'Clownfish',
    quantity: 1,
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

function sessionWith(fishItems = [], stoneItems = []) {
  return {
    inventorySource: 'playerdata_gameitemdb',
    playerDataFishItems: fishItems,
    playerDataStoneItems: stoneItems,
    sourceTruth: gameItemDbPublic.defaultSourceTruth(),
  };
}

describe('BLOCKER10ZG stone assets + manual rarity polish', () => {
  test('build marker is BLOCKER10ZG', () => {
    assert.equal(BLOCKER10ZG_BUILD, FINAL_BUILD);
    assert.equal(PUBLIC_API_BUILD, FINAL_BUILD);
    assert.equal(gameItemDbPublic.FINAL_BUILD, FINAL_BUILD);
  });

  test('stone catalog and cache exist for all ENCHANT_STONES', () => {
    const catalog = stoneImageAssets.loadCatalog();
    for (const [id, meta] of Object.entries(stoneImageAssets.ENCHANT_STONES)) {
      const entry = catalog.stones[String(id)];
      assert.ok(entry, `catalog missing itemId ${id}`);
      assert.equal(entry.stoneType, meta.stoneType);
      assert.ok(stoneImageAssets.stoneAssetFileExists(entry.filename), `cache missing ${entry.filename}`);
    }
  });

  test('Normal Enchant Stone itemId 10 gets stone_manual_asset image', async () => {
    const pub = await buildPublicFishFields([], BASE_URL, {
      sessionData: sessionWith([], [stoneRow('Normal', 10, 2)]),
    });
    assert.equal(pub.stoneItems.length, 1);
    const stone = pub.stoneItems[0];
    assert.equal(stone.itemId, '10');
    assert.equal(stone.category, 'stone');
    assert.equal(stone.imageSource, 'stone_manual_asset');
    assert.ok(stone.imageUrlPresent);
    assert.match(stone.imageUrl, /\/api\/fishit-tracker\/assets\/stones\/stone_10_normal\.png\?v=\d+/);
    assert.equal(stone.dataSource, 'playerdata_gameitemdb');
    assert.equal(stone.identitySource, 'playerdata_gameitemdb');
  });

  test('manual rarity overrides resolve expected fish rarities', async () => {
    const fish = [
      fishRow({ itemId: '248', name: 'Panther Eel', baseName: 'Panther Eel', tier: 1, rarity: null }),
      fishRow({ itemId: '268', name: 'Skeleton Angler Fish', baseName: 'Skeleton Angler Fish', tier: 1, rarity: 'Common' }),
      fishRow({ itemId: '285', name: 'Red Goatfish', baseName: 'Red Goatfish', tier: null, rarity: null }),
      fishRow({ itemId: '287', name: 'Zebra Snakehead', baseName: 'Zebra Snakehead', tier: null, rarity: '-' }),
      fishRow({ itemId: '999', name: 'Mystic Squid', baseName: 'Mystic Squid', tier: 2, rarity: 'Uncommon' }),
    ];
    const pub = await buildPublicFishFields([], BASE_URL, { sessionData: sessionWith(fish) });
    const byName = Object.fromEntries(pub.fishItems.map((f) => [f.name, f]));

    assert.equal(byName['Panther Eel'].rarity, 'Secret');
    assert.equal(byName['Panther Eel'].raritySource, 'manual_rarity_override');
    assert.equal(byName['Skeleton Angler Fish'].rarity, 'Epic');
    assert.equal(byName['Skeleton Angler Fish'].raritySource, 'manual_rarity_override');
    assert.equal(byName['Red Goatfish'].rarity, 'Uncommon');
    assert.equal(byName['Red Goatfish'].raritySource, 'manual_rarity_override');
    assert.equal(byName['Zebra Snakehead'].rarity, 'Uncommon');
    assert.equal(byName['Zebra Snakehead'].raritySource, 'manual_rarity_override');
    assert.equal(byName['Mystic Squid'].rarity, 'Mythic');
    assert.equal(byName['Mystic Squid'].raritySource, 'manual_rarity_override');
  });

  test('missingPublicRarityCount is zero for public fish rows', async () => {
    const fish = [
      fishRow({ itemId: '285', name: 'Red Goatfish', baseName: 'Red Goatfish', tier: null, rarity: null }),
      fishRow({ itemId: '287', name: 'Zebra Snakehead', baseName: 'Zebra Snakehead', tier: null, rarity: null }),
      fishRow({ itemId: '70', name: 'No Tier Fish', baseName: 'No Tier Fish', tier: null, rarity: null }),
    ];
    const pub = await buildPublicFishFields([], BASE_URL, { sessionData: sessionWith(fish) });
    assert.equal(pub.missingPublicRarityCount, 0);
    assert.equal(manualRarity.countMissingPublicRarity(pub.fishItems), 0);
    for (const f of pub.fishItems) {
      assert.ok(f.rarity && f.rarity !== 'Unknown' && f.rarity !== '-');
      assert.ok(f.tier != null && f.tier !== '-' && f.tier !== '');
      assert.ok(f.raritySource);
    }
  });

  test('no active public fish or stone row uses global_db identity rarity or image', async () => {
    const pub = await buildPublicFishFields([], BASE_URL, {
      sessionData: sessionWith(
        [
          fishRow({ itemId: '248', name: 'Panther Eel', baseName: 'Panther Eel' }),
          fishRow({ itemId: '285', name: 'Red Goatfish', baseName: 'Red Goatfish', tier: null, rarity: null }),
        ],
        [stoneRow('Normal', 10, 1)],
      ),
    });
    const rows = [...pub.fishItems, ...pub.stoneItems];
    assert.ok(rows.length >= 3);
    for (const row of rows) {
      assert.notEqual(row.dataSource, 'global_db');
      assert.notEqual(row.identitySource, 'global_db');
      assert.notEqual(row.imageSource, 'global_db');
      if (row.category === 'fish') {
        assert.notEqual(row.raritySource, 'global_db');
      }
    }
    assert.equal(pub.inventorySource, 'playerdata_gameitemdb');
  });

  test('manual override JSON contains required initial entries', () => {
    const overrides = manualRarity.loadOverrides();
    assert.equal(overrides.byName['Red Goatfish'], 'Uncommon');
    assert.equal(overrides.byName['Zebra Snakehead'], 'Uncommon');
    assert.equal(overrides.byName['Panther Eel'], 'Secret');
    assert.equal(overrides.byItemId['285'], 'Uncommon');
    assert.equal(overrides.byItemId['287'], 'Uncommon');
  });

  test('tracker template supports stone asset images and always-on rarity classes', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.match(tpl, /assets\/stones\//);
    assert.match(tpl, /publicRarity/);
    assert.match(tpl, /rarity-uncommon/);
    assert.match(tpl, /#00ff7f|#00FF7F/i);
  });
});
