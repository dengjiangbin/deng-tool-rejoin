'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const manifest = require('../src/inventoryAssetManifest.json');
const INVENTORY_JS = path.join(__dirname, '..', 'public', 'assets', manifest.js);
const INVENTORY_CSS = path.join(__dirname, '..', 'public', 'assets', manifest.css);

describe('inventory card stability during polling', () => {
  test('source patches cards in place instead of full grid innerHTML redraw', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /function patchFishCardDom/);
    assert.match(source, /function patchStoneCardDom/);
    assert.match(source, /function patchHtmlIfChanged/);
    assert.match(source, /function placeGridCardAtIndex/);
    assert.match(source, /function patchBulkStoneGrid/);
    assert.doesNotMatch(source, /function patchItemCardElement[\s\S]*?card\.innerHTML = buildFishCardInnerHtml/);
    assert.doesNotMatch(source, /function patchStonesGrid[\s\S]*?card\.innerHTML = buildStoneCardInnerHtml/);
    assert.match(source, /patchItemsGrid\(host, fish/);
    assert.doesNotMatch(source, /bulkBodyEl\.innerHTML = `<div class="items-grid inventory-grid fish-grid">\$\{fish\.map/);
  });

  test('compiled inventory bundle keeps incremental patch helpers', () => {
    const js = fs.readFileSync(INVENTORY_JS, 'utf8');
    const css = fs.readFileSync(INVENTORY_CSS, 'utf8');
    assert.match(js, /function patchFishCardDom/);
    assert.match(js, /function patchBulkStoneGrid/);
    assert.match(js, /function placeGridCardAtIndex/);
    assert.match(js, /function markCardEnterAnimation/);
    assert.match(css, /\.ft-card\.ft-card--enter/);
  });

  test('entry animation only applies to new cards', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /function markCardEnterAnimation/);
    assert.match(source, /markCardEnterAnimation\(card\)/);
    assert.doesNotMatch(source, /\.ft-card\s*\{[^}]*animation:fadeIn/);
  });
});
