'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const COUNT_UP_PATH = path.join(__dirname, '..', 'public', 'js', 'count-up-stats.js');
const src = fs.readFileSync(SOURCE_PATH, 'utf8');
const countUpSrc = fs.readFileSync(COUNT_UP_PATH, 'utf8');

describe('tracker top summary card layout (screenshot polish)', () => {
  test('inventoryStats uses dedicated tracker-top-summary grid/card classes', () => {
    assert.match(src, /tracker-top-summary-grid" id="inventoryStats"/);
    assert.match(src, /class="tracker-top-summary-card tracker-top-summary-card--online"/);
    assert.match(src, /class="tracker-top-summary-card tracker-top-summary-card--stones"/);
  });

  test('cards are centered with icon, label, value hierarchy', () => {
    assert.match(src, /\.tracker-top-summary-card\s*\{[^}]*align-items:center/);
    assert.match(src, /\.tracker-top-summary-icon\s*\{/);
    assert.match(src, /\.tracker-top-summary-label\s*\{/);
    assert.match(src, /\.tracker-top-summary-value\s*\{/);
  });

  test('card background/border/radius match screenshot spec', () => {
    assert.match(src, /\.tracker-top-summary-card\s*\{[^}]*background:#17171d/);
    assert.match(src, /\.tracker-top-summary-card\s*\{[^}]*border-radius:26px/);
    assert.match(src, /\.tracker-top-summary-card\s*\{[^}]*border:1px solid rgba\(255,255,255,0\.08\)/);
    assert.match(src, /\.tracker-top-summary-card\s*\{[^}]*min-height:170px/);
  });

  test('grid uses auto-fit two-column layout on desktop', () => {
    assert.match(src, /#inventoryStats\.tracker-top-summary-grid\s*\{[^}]*grid-template-columns:repeat\(auto-fit,minmax\(320px,1fr\)\)/);
  });

  test('mobile collapses to one column without breaking padding', () => {
    assert.match(src, /@media \(max-width:520px\)[\s\S]*#inventoryStats\.tracker-top-summary-grid[\s\S]*grid-template-columns:1fr/);
  });
});

describe('tracker top summary typography + colors', () => {
  test('label is muted gray and not uppercase', () => {
    assert.match(src, /\.tracker-top-summary-label\s*\{[^}]*color:rgba\(255,255,255,0\.48\)/);
    assert.match(src, /\.tracker-top-summary-label\s*\{[^}]*font-size:18px/);
    assert.doesNotMatch(src, /\.tracker-top-summary-label\s*\{[^}]*text-transform:uppercase/);
  });

  test('values are large white except online count green only', () => {
    assert.match(src, /\.tracker-top-summary-value\s*\{[^}]*color:#fff/);
    assert.match(src, /\.tracker-top-summary-value \.online-count\s*\{[^}]*color:#62e68a/);
    assert.match(src, /\.tracker-top-summary-value \.total-count\s*\{[^}]*color:#fff/);
  });

  test('online accounts uses styled ratio markup hook', () => {
    assert.match(src, /id="statOnlineAccounts"[^>]*data-count-ratio-styled/);
    assert.match(countUpSrc, /data-count-ratio-styled/);
    assert.match(countUpSrc, /formatRatioHtml/);
    assert.match(countUpSrc, /class="online-count"/);
  });
});

describe('tracker top summary icons + scoped styling', () => {
  test('cards include centered top icons/images', () => {
    assert.match(src, /tracker-top-summary-icon--avatar/);
    assert.match(src, /stone_558_evolved\.png/);
    assert.match(src, /tracker-top-summary-card--stones[\s\S]*Evolved Enchant Stone/);
    assert.match(src, /tracker-top-summary-card--ruby[\s\S]*Ruby Gemstone/);
  });

  test('legacy stat-card rarity/value colors are not applied to top summary cards', () => {
    assert.doesNotMatch(src, /\.stat-card--online \.stat-card__value/);
    assert.doesNotMatch(src, /\.stat-card--stones \.stat-card__value/);
  });

  test('dashboard stats still use legacy stat-card tiles', () => {
    assert.match(src, /class="inventory-stats dashboard-stats"/);
    assert.match(src, /\.dashboard-stats \.stat-card\s*\{/);
  });
});
