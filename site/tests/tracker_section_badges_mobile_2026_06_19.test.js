'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const manifest = require('../src/inventoryAssetManifest.json');
const INVENTORY_CSS = path.join(__dirname, '..', 'public', 'assets', manifest.css);

describe('section inventory badges + mobile table clipping (2026-06-19)', () => {
  const src = () => fs.readFileSync(SOURCE_PATH, 'utf8');
  const css = () => fs.readFileSync(INVENTORY_CSS, 'utf8');

  test('aggregate inventory badge removed; fish/item/detail section badges exist', () => {
    const source = src();
    assert.match(source, /data-fish-grid-upload-indicator/);
    assert.match(source, /data-item-grid-upload-indicator/);
    assert.match(source, /data-detail-upload-indicator/);
    assert.doesNotMatch(source, /#bulkInventoryPanel[\s\S]{0,800}data-inventory-upload-indicator/);
    assert.doesNotMatch(source, /inventory-grid-upload-bar/);
    assert.doesNotMatch(source, /ensureCardInventoryUploadBar/);
  });

  test('detail badge is stable in panel template and opening detail does not reset freshness', () => {
    const source = src();
    assert.match(source, /ensureFtDetailPanel[\s\S]*data-detail-upload-indicator/);
    assert.match(source, /function openFtDetail[\s\S]*updateInventoryUploadIndicator\(\)/);
    const patchFn = source.match(/function patchInventoryUploadIndicatorDom\(root, entry\) \{[\s\S]*?\n {2}\}/)[0];
    assert.match(patchFn, /formatInventoryUploadLabel/);
    assert.match(patchFn, /dotEl\.remove\(\)/);
    assert.match(patchFn, /is-neutral/);
  });

  test('mobile table uses clamp/overflow-wrap at narrow widths (360–430px class of fixes)', () => {
    const source = src();
    assert.match(source, /@media \(max-width:768px\)[\s\S]*clamp\(\.58rem, 2\.6vw, \.68rem\)/);
    assert.match(source, /@media \(max-width:768px\)[\s\S]*overflow-wrap:anywhere/);
    assert.match(source, /\.accounts-table__stat-sub[\s\S]*overflow-wrap:anywhere/);
  });

  test('compiled CSS bundle includes gradient title and mobile table clamp rules', () => {
    const sheet = css();
    assert.match(sheet, /\.header h1\{[^}]*linear-gradient\(90deg,#60a5fa,#f9a8d4\)/);
    assert.match(sheet, /clamp\(\.58rem,2\.6vw,\.68rem\)/);
  });
});
