'use strict';

// Fish detail view: weight-first sort among mutated fish, semantic mutation colors, weight formatting.

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const src = fs.readFileSync(SOURCE_PATH, 'utf8');

function sliceBalanced(source, startNeedle, openChar, closeChar) {
  const start = source.indexOf(startNeedle);
  assert.ok(start >= 0, `missing ${startNeedle}`);
  let i = source.indexOf(openChar, start);
  let depth = 0;
  for (; i < source.length; i += 1) {
    const ch = source[i];
    if (ch === openChar) depth += 1;
    else if (ch === closeChar) {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced ${startNeedle}`);
}

function loadDetailFishHelpers() {
  const mutStart = src.indexOf('const FT_MUTATION_COLORS = {');
  const blockStart = src.indexOf('function normalizeMutation(value) {');
  const blockEnd = src.indexOf('function ftRarityKey(item) {', blockStart);
  const snippet = [
    src.slice(mutStart, blockStart),
    src.slice(blockStart, blockEnd),
    sliceBalanced(src, 'function renderFishInstanceCard(card) {', '{', '}'),
    'function escHtml(v){return String(v==null?"":v).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/"/g,"&quot;");}',
  ].join('\n');
  const script = `(function(){\n${snippet}\nreturn { normalizeMutation, getMutationSortRank, parseFishWeight, formatFishWeight, sortFishDetailInstances, renderFishInstanceCard, ftDetailMutationSlug, ftDetailMutationSemanticPalette, ftDetailMutationThemeVars, ensureMutationColorDiffersFromRarity, ftNormalizeNonNil, getReadableTextColor, nudgeHexAwayFromRarity, ftRarityAccentHex, colorToHex, colorDistance };})()`;
  return vm.runInNewContext(script, {}, { filename: 'tracker-detail-fish-helpers.js' });
}

const H = loadDetailFishHelpers();

describe('fish detail weight formatting', () => {
  test('uses thousand separators and keeps unit', () => {
    assert.equal(H.formatFishWeight(999), '999 kg');
    assert.equal(H.formatFishWeight(1000), '1,000 kg');
    assert.equal(H.formatFishWeight(12345.67), '12,345.67 kg');
    assert.equal(H.formatFishWeight(1234567.89), '1,234,567.89 kg');
    assert.equal(H.formatFishWeight('1.31K'), '1,310 kg');
  });
  test('never returns NaN kg', () => {
    assert.equal(H.formatFishWeight(null), '');
    assert.equal(H.formatFishWeight(''), '');
    assert.doesNotMatch(H.formatFishWeight('bad'), /NaN/i);
  });
});

describe('fish detail sort: mutated first, then weight desc', () => {
  test('mutated fish before non-mutated', () => {
    const sample = [
      { name: 'Plain A', mutation: '', weightRaw: 500, weight: '500 kg', owner: 'u1', rarity: 'legendary' },
      { name: 'Gold Heavy', mutation: 'Gold', weightRaw: 12345.67, weight: '12,345.67 kg', owner: 'u1', rarity: 'legendary' },
    ];
    const sorted = [...sample].sort(H.sortFishDetailInstances);
    assert.ok(H.normalizeMutation(sorted[0].mutation));
    assert.equal(sorted[sorted.length - 1].mutation, '');
  });

  test('heaviest mutated fish is first when mutations exist', () => {
    const sample = [
      { name: 'Gold Light', mutation: 'Gold', weightRaw: 100, weight: '100 kg', owner: 'u1', rarity: 'legendary' },
      { name: 'Gold Heavy', mutation: 'Gold', weightRaw: 12345.67, weight: '12,345.67 kg', owner: 'u1', rarity: 'legendary' },
      { name: 'Albino Mid', mutation: 'Albino', weightRaw: 2000, weight: '2,000 kg', owner: 'u1', rarity: 'secret' },
      { name: 'Gem Small', mutation: 'Gemstone', weightRaw: 50, weight: '50 kg', owner: 'u1', rarity: 'secret' },
      { name: 'Plain A', mutation: '', weightRaw: 500, weight: '500 kg', owner: 'u1', rarity: 'legendary' },
    ];
    const sorted = [...sample].sort(H.sortFishDetailInstances);
    assert.equal(sorted[0].name, 'Gold Heavy');
    assert.equal(sorted[0].weightRaw, 12345.67);
  });

  test('heavier Gemstone beats lighter Gold (weight before mutation priority)', () => {
    const sample = [
      { name: 'Gold Light', mutation: 'Gold', weightRaw: 100, weight: '100 kg', owner: 'u1', rarity: 'legendary' },
      { name: 'Gem Heavy', mutation: 'Gemstone', weightRaw: 5000, weight: '5,000 kg', owner: 'u1', rarity: 'secret' },
    ];
    const sorted = [...sample].sort(H.sortFishDetailInstances);
    assert.equal(sorted[0].name, 'Gem Heavy');
    assert.equal(sorted[1].name, 'Gold Light');
  });

  test('non-mutated fish sort by weight descending after mutated fish', () => {
    const sample = [
      { name: 'Plain Light', mutation: '', weightRaw: 100, weight: '100 kg', owner: 'u1', rarity: 'common' },
      { name: 'Plain Heavy', mutation: '', weightRaw: 900, weight: '900 kg', owner: 'u1', rarity: 'common' },
      { name: 'Gold Any', mutation: 'Gold', weightRaw: 50, weight: '50 kg', owner: 'u1', rarity: 'legendary' },
    ];
    const sorted = [...sample].sort(H.sortFishDetailInstances);
    assert.equal(sorted[0].mutation, 'Gold');
    assert.equal(sorted[1].name, 'Plain Heavy');
    assert.equal(sorted[2].name, 'Plain Light');
  });

  test('weight ties use mutation rank then name', () => {
    const sample = [
      { name: 'B', mutation: 'Albino', weightRaw: 500, weight: '500 kg', owner: 'u1', rarity: 'secret', cleanName: 'B' },
      { name: 'A', mutation: 'Gold', weightRaw: 500, weight: '500 kg', owner: 'u1', rarity: 'legendary', cleanName: 'A' },
    ];
    const sorted = [...sample].sort(H.sortFishDetailInstances);
    assert.equal(sorted[0].mutation, 'Gold');
    assert.equal(sorted[1].mutation, 'Albino');
  });
});

describe('fish detail mutation card render', () => {
  test('mutation card gets has-mutation + slug class, not rarity', () => {
    const html = H.renderFishInstanceCard({
      name: 'King Crab', mutation: 'Gold', weight: '12,345.67 kg', owner: 'demo',
      imgSrc: '', rarity: 'legendary',
    });
    assert.match(html, /tracker-detail-fish-card has-mutation mutation-gold/);
    assert.match(html, /--tdf-bg:/);
    assert.doesNotMatch(html, /data-rarity/);
    assert.match(html, /tracker-detail-fish-weight">12,345\.67 kg/);
    assert.match(html, /tracker-detail-fish-mutation">Gold/);
    assert.doesNotMatch(html, /nil|null|undefined/i);
  });

  test('non-mutated fish uses neutral card without mutation classes', () => {
    const html = H.renderFishInstanceCard({
      name: 'Tuna', mutation: '', weight: '1,000 kg', owner: 'demo', imgSrc: '', rarity: 'common',
    });
    assert.match(html, /tracker-detail-fish-card/);
    assert.doesNotMatch(html, /has-mutation/);
    assert.doesNotMatch(html, /tracker-detail-fish-mutation/);
  });

  test('name appears before weight in card body (weight is not the title)', () => {
    const html = H.renderFishInstanceCard({
      name: 'Ruby Fish', mutation: 'Gemstone', weight: '1,234 kg', owner: 'x', imgSrc: '', rarity: 'epic',
    });
    const body = html.slice(html.indexOf('tracker-detail-fish-card__body'));
    const n = body.indexOf('tracker-detail-fish-name');
    const w = body.indexOf('tracker-detail-fish-weight');
    assert.ok(n >= 0 && w > n, 'name should precede weight');
  });

  test('item breakdown rows still use ft-detail-owner (unchanged scope)', () => {
    const breakdownFn = sliceBalanced(src, 'function renderBreakdownRow(row) {', '{', '}');
    assert.match(breakdownFn, /ft-detail-owner__mut/);
    assert.doesNotMatch(breakdownFn, /tracker-detail-fish-mutation/);
    assert.doesNotMatch(breakdownFn, /tracker-detail-fish-card/);
  });
});

describe('semantic mutation palettes', () => {
  test('Gold uses gold slug and amber accent', () => {
    const p = H.ftDetailMutationSemanticPalette('Gold');
    assert.equal(p.slug, 'gold');
    assert.match(p.bg, /4a3410/i);
    assert.match(p.pillBg, /ffd166/i);
  });

  test('Stone / Runic Stone uses stone slug', () => {
    assert.equal(H.ftDetailMutationSemanticPalette('Stone').slug, 'stone');
    assert.equal(H.ftDetailMutationSemanticPalette('Runic Stone').slug, 'stone');
  });

  test('Gemstone and Ruby use jewel slug', () => {
    assert.equal(H.ftDetailMutationSemanticPalette('Gemstone').slug, 'gemstone');
    assert.equal(H.ftDetailMutationSemanticPalette('Ruby').slug, 'gemstone');
  });

  test('Sand/Sandy uses sandy slug', () => {
    assert.equal(H.ftDetailMutationSemanticPalette('Sandy').slug, 'sandy');
    assert.equal(H.ftDetailMutationSemanticPalette('Sand').slug, 'sandy');
  });

  test('Albino uses light palette with dark readable text', () => {
    const p = H.ftDetailMutationSemanticPalette('Albino');
    assert.equal(p.slug, 'albino');
    assert.equal(p.text, '#111827');
    assert.equal(H.getReadableTextColor('#f5f3e8'), '#111827');
  });

  test('unknown mutation gets deterministic custom slug', () => {
    const p = H.ftDetailMutationSemanticPalette('Quantum Flux');
    assert.equal(p.slug, 'custom');
    assert.ok(p.accent);
  });

  test('mutation palette differs from matching rarity color', () => {
    const legendary = H.ftRarityAccentHex('legendary');
    const gold = H.ensureMutationColorDiffersFromRarity(H.ftDetailMutationSemanticPalette('Gold'), 'legendary', 72);
    assert.ok(H.colorDistance(H.colorToHex(gold.accent), legendary) >= 72
      || H.colorDistance(H.colorToHex(gold.pillBg), legendary) >= 72);
  });

  test('mutation palettes use contrasting pill and body text', () => {
    for (const mut of ['Gold', 'Albino', 'Shadow', 'Frozen', 'Quantum Flux']) {
      const p = H.ftDetailMutationSemanticPalette(mut);
      assert.ok(p.pillText);
      assert.ok(p.text);
      assert.notEqual(String(p.pillText).toLowerCase(), String(p.accent).toLowerCase());
    }
    assert.equal(H.ftDetailMutationSemanticPalette('Albino').text, '#111827');
  });
});

describe('source wiring', () => {
  test('scoped tracker-detail-fish-card CSS uses CSS vars for mutation themes', () => {
    assert.match(src, /\.tracker-detail-fish-card\.has-mutation[\s\S]*--tdf-bg/);
    assert.match(src, /function ftDetailMutationSemanticPalette/);
    assert.match(src, /function ensureMutationColorDiffersFromRarity/);
  });
  test('weight is normal-sized, not oversized title typography', () => {
    assert.doesNotMatch(src, /\.tracker-detail-fish-weight\s*\{[^}]*font-size:18px/);
    assert.match(src, /\.tracker-detail-fish-weight\s*\{[^}]*font-size:12px/);
  });
  test('sortFishDetailInstances used for fish detail cards', () => {
    assert.match(src, /function sortFishDetailInstances/);
    assert.match(src, /cards\.sort\(sortFishDetailInstances\)/);
  });
});
