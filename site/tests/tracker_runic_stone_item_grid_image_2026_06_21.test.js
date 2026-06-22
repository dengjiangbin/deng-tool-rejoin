'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const overrides = require('../src/fishitTrackerItemImageOverrides');
const stoneAssets = require('../src/fishitStoneImageAssets');
const topGrid = require('../src/fishitTrackerTopGridAssets');
const manualInventoryImages = require('../src/fishitInventoryManualImages');

const RUNIC_URL = '/public/assets/tracker-top-grid/runic-stone.png';

describe('tracker Runic Stone item-grid image override (2026-06-21)', () => {
  test('central resolver normalizes name variants to top-grid URL', () => {
    assert.equal(getTrackerItemImage('Runic Stone'), RUNIC_URL);
    assert.equal(getTrackerItemImage('runic stone'), RUNIC_URL);
    assert.equal(getTrackerItemImage('RUNIC STONE'), RUNIC_URL);
    assert.equal(getTrackerItemImage('  Runic   Stone  '), RUNIC_URL);
    assert.equal(getTrackerItemImage('Runic Enchant Stone'), RUNIC_URL);
    assert.equal(overrides.getTrackerItemImageUrl({ stoneType: 'Runic' }), RUNIC_URL);
    assert.equal(overrides.getTrackerItemImageUrl({ itemId: '929' }), RUNIC_URL);
  });

  test('Transcended Stone is not forced to Runic Stone image', () => {
    assert.equal(getTrackerItemImage('Transcended Stone'), null);
    assert.equal(overrides.getTrackerItemImageUrl({ stoneType: 'Double', name: 'Transcended Stone' }), null);
  });

  test('stone backend resolver prefers top-grid URL over manual and gameDB payload', () => {
    manualInventoryImages.ensureOverrideFilesFromSeed();
    const rows = stoneAssets.attachStoneImagesToItems([
      {
        name: 'Runic Stone',
        stoneType: 'Runic',
        itemId: '929',
        imageUrl: 'https://thumbnails.roblox.com/v1/asset?assetId=123',
        icon: 'rbxassetid://888888888',
      },
    ], 'https://aio.deng.my.id');
    assert.equal(rows[0].imageResolver, 'tracker_top_grid_runic_stone');
    assert.equal(rows[0].imageUrl, RUNIC_URL);
    assert.doesNotMatch(rows[0].imageUrl, /manual\/stones/);
    assert.doesNotMatch(rows[0].imageUrl, /roblox|gameitemdb|image\/\d+/i);
  });

  test('Transcended Stone still follows gameDB proxy when icon present', () => {
    const rows = stoneAssets.attachStoneImagesToItems([
      {
        name: 'Transcended Stone',
        stoneType: 'Double',
        itemId: '246',
        icon: 'rbxassetid://73883190545629',
      },
    ], 'https://aio.deng.my.id');
    assert.equal(rows[0].imageResolver, 'stone_gameitemdb_proxy');
    assert.match(rows[0].imageUrl, /73883190545629/);
    assert.notEqual(rows[0].imageUrl, RUNIC_URL);
  });

  test('Evolved Enchant Stone catalog image is unchanged', () => {
    const rows = stoneAssets.attachStoneImagesToItems([
      { name: 'Evolved Enchant Stone', stoneType: 'Evolved', itemId: '558' },
    ], 'https://aio.deng.my.id');
    assert.notEqual(rows[0].imageUrl, RUNIC_URL);
    assert.match(rows[0].imageUrl, /stones\/stone_558_evolved\.png/);
  });

  test('top grid Runic Stone icon remains owned public URL', () => {
    topGrid.syncTopGridAssets({ persist: false });
    const resolved = topGrid.resolveTopSummaryIcons({ forceSync: false });
    assert.equal(resolved.runic, RUNIC_URL);
  });

  test('frontend source uses central override before payload imageUrl', () => {
    const src = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs'),
      'utf8',
    );
    assert.match(src, /resolveTrackerItemImageOverride\(item\)/);
    assert.match(src, /if \(trackerOverride\) return trackerReadPath\(trackerOverride\)/);
    assert.match(src, /\/public\/assets\/tracker-top-grid\//);
  });

  test('built inventory bundle contains Runic Stone override helpers', () => {
    const manifest = JSON.parse(fs.readFileSync(
      path.join(__dirname, '..', 'src', 'inventoryAssetManifest.json'),
      'utf8',
    ));
    const bundle = fs.readFileSync(
      path.join(__dirname, '..', 'public', 'assets', manifest.js),
      'utf8',
    );
    assert.match(bundle, /TRACKER_RUNIC_STONE_IMAGE/);
    assert.match(bundle, /resolveTrackerItemImageOverride/);
    assert.match(bundle, /tracker-top-grid\/runic-stone\.png/);
  });
});

function getTrackerItemImage(nameOrItem) {
  return overrides.getTrackerItemImageUrl(nameOrItem);
}
