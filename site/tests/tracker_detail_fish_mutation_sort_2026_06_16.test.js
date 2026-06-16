'use strict';

// Fish detail view: mutation-colored cards, mutation-first sort, weight formatting.

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
  const script = `(function(){\n${snippet}\nreturn { normalizeMutation, getMutationSortRank, parseFishWeight, formatFishWeight, sortFishDetailInstances, renderFishInstanceCard, ftDetailMutationSlug, ftNormalizeNonNil, getReadableTextColor, nudgeHexAwayFromRarity, ftRarityAccentHex, ftMutationColor };})()`;
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

describe('fish detail sort: mutation first, then weight', () => {
  const sample = [
    { name: 'Plain A', mutation: '', weightRaw: 500, weight: '500 kg', owner: 'u1', rarity: 'legendary' },
    { name: 'Gold Light', mutation: 'Gold', weightRaw: 100, weight: '100 kg', owner: 'u1', rarity: 'legendary' },
    { name: 'Gold Heavy', mutation: 'Gold', weightRaw: 12345.67, weight: '12,345.67 kg', owner: 'u1', rarity: 'legendary' },
    { name: 'Albino Mid', mutation: 'Albino', weightRaw: 2000, weight: '2,000 kg', owner: 'u1', rarity: 'secret' },
    { name: 'Gem Small', mutation: 'Gemstone', weightRaw: 50, weight: '50 kg', owner: 'u1', rarity: 'secret' },
  ];

  test('mutated fish before non-mutated', () => {
    const sorted = [...sample].sort(H.sortFishDetailInstances);
    assert.ok(H.normalizeMutation(sorted[0].mutation), 'top card must be mutated');
    assert.equal(sorted[sorted.length - 1].mutation, '');
  });

  test('Gold before Gemstone before Albino (MUTATION_SORT_ORDER)', () => {
    const sorted = [...sample].sort(H.sortFishDetailInstances);
    const muts = sorted.filter((c) => H.normalizeMutation(c.mutation)).map((c) => c.mutation);
    assert.deepEqual(muts, ['Gold', 'Gold', 'Gemstone', 'Albino']);
  });

  test('heaviest Gold is first overall', () => {
    const sorted = [...sample].sort(H.sortFishDetailInstances);
    assert.equal(sorted[0].name, 'Gold Heavy');
    assert.equal(sorted[0].weightRaw, 12345.67);
  });

  test('within Gold group heavier first', () => {
    const goldOnly = [
      { name: 'G1', mutation: 'Gold', weightRaw: 100, weight: '100 kg', owner: 'u', rarity: 'rare' },
      { name: 'G2', mutation: 'Gold', weightRaw: 999, weight: '999 kg', owner: 'u', rarity: 'rare' },
    ].sort(H.sortFishDetailInstances);
    assert.equal(goldOnly[0].weightRaw, 999);
  });
});

describe('fish detail mutation card render', () => {
  test('mutation card gets has-mutation + slug class, not rarity', () => {
    const html = H.renderFishInstanceCard({
      name: 'King Crab', mutation: 'Gold', weight: '12,345.67 kg', owner: 'demo',
      imgSrc: '', rarity: 'legendary',
    });
    assert.match(html, /tracker-detail-fish-card has-mutation mutation-gold/);
    assert.doesNotMatch(html, /data-rarity/);
    assert.doesNotMatch(html, /ft-rarity/);
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

  test('weight line is first in card body (before mutation/name)', () => {
    const html = H.renderFishInstanceCard({
      name: 'Ruby Fish', mutation: 'Gemstone', weight: '1,234 kg', owner: 'x', imgSrc: '', rarity: 'epic',
    });
    const body = html.slice(html.indexOf('tracker-detail-fish-card__body'));
    const w = body.indexOf('tracker-detail-fish-weight');
    const m = body.indexOf('tracker-detail-fish-mutation');
    const n = body.indexOf('tracker-detail-fish-name');
    assert.ok(w >= 0 && w < m && m < n);
  });

  test('item breakdown rows still use ft-detail-owner (unchanged scope)', () => {
    const breakdownFn = sliceBalanced(src, 'function renderBreakdownRow(row) {', '{', '}');
    assert.match(breakdownFn, /ft-detail-owner__mut/);
    assert.doesNotMatch(breakdownFn, /tracker-detail-fish-mutation/);
    assert.doesNotMatch(breakdownFn, /tracker-detail-fish-card/);
  });
});

describe('mutation vs rarity color guard', () => {
  test('Gold mutation slug differs from legendary rarity accent', () => {
    const legendary = H.ftRarityAccentHex('legendary');
    assert.notEqual(legendary.toLowerCase(), '#ffd166');
    assert.match(src, /\.tracker-detail-fish-card\.mutation-gold[\s\S]*#ffd166/);
  });

  test('getReadableTextColor picks dark text on light backgrounds', () => {
    assert.equal(H.getReadableTextColor('#f5f3e8'), '#111827');
    assert.equal(H.getReadableTextColor('#17171d'), '#ffffff');
  });
});

describe('source wiring', () => {
  test('scoped tracker-detail-fish-card CSS present', () => {
    assert.match(src, /\.tracker-detail-fish-card\.mutation-gold/);
    assert.match(src, /\.tracker-detail-fish-card\.mutation-albino/);
    assert.match(src, /\.tracker-detail-fish-card\.mutation-gemstone/);
    assert.match(src, /\.tracker-detail-fish-weight/);
  });
  test('sortFishDetailInstances used for fish detail cards', () => {
    assert.match(src, /function sortFishDetailInstances/);
    assert.match(src, /cards\.sort\(sortFishDetailInstances\)/);
  });
});
