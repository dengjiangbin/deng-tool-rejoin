'use strict';

// 2026-06-16 detail-card polish regression. Covers:
//  - fish detail cards use mutation-themed full-card styling (not rarity),
//  - overview grid cards keep their rarity colors,
//  - mutation label never renders nil/null/undefined,
//  - weight at top with thousand separators,
//  - the 2-column detail layout is preserved.

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const src = fs.readFileSync(SOURCE_PATH, 'utf8');

function loadMutationHelpers() {
  const start = src.indexOf('const FT_MUTATION_COLORS = {');
  const end = src.indexOf('function normalizeMutation(value) {');
  assert.ok(start >= 0 && end > start, 'mutation helper block must be present');
  const snippet = src.slice(start, end);
  // eslint-disable-next-line no-new-func
  const factory = new Function(
    `${snippet}; return { FT_MUTATION_COLORS, ftMutationHashColor, ftMutationColor, ftMutationStyle, ftNormalizeNonNil };`,
  );
  return factory();
}

const M = loadMutationHelpers();

describe('mutation style resolver (BLOCKER polish #2)', () => {
  test('Gold is gold/yellow', () => {
    assert.match(M.ftMutationStyle('Gold'), /#fbbf24/i);
  });

  test('Albino is intentional white with readable shadow/outline', () => {
    const s = M.ftMutationStyle('Albino');
    assert.match(s, /#fff(fff)?/i);
    assert.match(s, /text-shadow/);
  });

  test('Gemstone is a green→red gradient with a solid fallback color', () => {
    const s = M.ftMutationStyle('Gemstone');
    assert.match(s, /linear-gradient/);
    assert.match(s, /background-clip:text/);
    assert.match(s, /text-fill-color:transparent/);
    assert.match(s, /#34d399/i, 'fallback solid color present for unsupported browsers');
  });

  test('Stone is light chocolate / brown', () => {
    assert.match(M.ftMutationStyle('Stone'), /#c8a06b/i);
  });

  test('Sandy is sand color', () => {
    assert.match(M.ftMutationStyle('Sandy'), /#e3c879/i);
  });

  test('nil/null/undefined/empty render nothing', () => {
    for (const bad of ['nil', 'null', 'undefined', 'none', '', '   ', null, undefined]) {
      assert.equal(M.ftNormalizeNonNil(bad), '', `${JSON.stringify(bad)} must normalize to empty`);
    }
    assert.equal(M.ftNormalizeNonNil('Gold'), 'Gold');
  });
});

describe('fish detail cards use mutation theme, not rarity background', () => {
  test('no rarity background rules on fish detail cards', () => {
    assert.doesNotMatch(src, /\.tracker-detail-fish-card\[data-rarity=/);
    assert.doesNotMatch(src, /data-rarity="\$\{escHtml\(rarity\)\}"/);
  });
  test('mutated fish detail cards get mutation-* classes and CSS vars', () => {
    assert.match(src, /mutation-\$\{slug\}/);
    assert.match(src, /--tdf-bg:/);
    assert.match(src, /function ftDetailMutationSemanticPalette/);
  });
  test('overview grid rarity colors are still generated elsewhere', () => {
    const rarityStyle = fs.readFileSync(path.join(__dirname, '..', 'src', 'fishitTrackerRarityStyle.js'), 'utf8');
    assert.match(rarityStyle, /buildFtCardRarityCss/);
    assert.match(rarityStyle, /ft-card rarity backgrounds/);
  });
});

describe('fish detail typography + weight formatting', () => {
  test('weight is normal metadata size, not oversized title', () => {
    assert.doesNotMatch(src, /\.tracker-detail-fish-weight\s*\{[^}]*font-size:18px/);
    assert.match(src, /\.tracker-detail-fish-weight\s*\{[^}]*font-size:12px/);
  });
  test('renderFishInstanceCard puts name before weight', () => {
    assert.match(src, /tracker-detail-fish-name">\$\{escHtml\(card\.name\)\}<\/div>\$\{weight\}/);
  });
  test('formatFishWeight uses Intl thousand separators', () => {
    assert.match(src, /function formatFishWeight/);
    assert.match(src, /Intl\.NumberFormat\('en-US'/);
  });
});

describe('two-column detail layout preserved (BLOCKER polish #4)', () => {
  test('desktop detail grid is multi/two-column', () => {
    assert.match(src, /\.ft-detail-instances\s*\{[^}]*grid-template-columns:repeat\(auto-fill,minmax\(/);
  });
  test('narrow mobile collapses to one column', () => {
    assert.match(src, /@media \(max-width:340px\)\s*\{\s*\.ft-detail-instances\s*\{\s*grid-template-columns:1fr/);
  });
});
