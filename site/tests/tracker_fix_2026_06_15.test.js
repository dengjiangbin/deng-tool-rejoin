'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

process.env.NODE_ENV = 'test';

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const manifest = require('../src/inventoryAssetManifest.json');
const INVENTORY_JS = path.join(__dirname, '..', 'public', 'assets', manifest.js);
const INVENTORY_CSS = path.join(__dirname, '..', 'public', 'assets', manifest.css);

function readSource() { return fs.readFileSync(SOURCE_PATH, 'utf8'); }
function readJs() { return fs.readFileSync(INVENTORY_JS, 'utf8'); }
function readCss() { return fs.readFileSync(INVENTORY_CSS, 'utf8'); }

// ---- Extract the REAL helper implementations from the source EJS -----------
// We brace-match from a start token so the test exercises the shipped logic
// rather than a re-implementation that could drift.
function sliceBalanced(src, startToken, open, close) {
  const start = src.indexOf(startToken);
  if (start === -1) throw new Error(`token not found: ${startToken}`);
  const from = src.indexOf(open, start);
  let depth = 0;
  for (let i = from; i < src.length; i += 1) {
    const ch = src[i];
    if (ch === open) depth += 1;
    else if (ch === close) {
      depth -= 1;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced block: ${startToken}`);
}

function loadScanHelpers() {
  const src = readSource();
  const parts = [
    sliceBalanced(src, 'const FT_MUTATION_COLORS = {', '{', '}') + ';',
    sliceBalanced(src, 'function ftMutationHashColor(', '{', '}'),
    sliceBalanced(src, 'function ftMutationColor(', '{', '}'),
    sliceBalanced(src, 'function ftBracketToken(', '{', '}'),
    sliceBalanced(src, 'function ftExtractMutation(', '{', '}'),
    sliceBalanced(src, 'function ftExtractBaseName(', '{', '}'),
    sliceBalanced(src, 'function isRubyGemstoneItem(', '{', '}'),
  ];
  const factory = new Function(`${parts.join('\n')}\n return { ftMutationColor, ftMutationHashColor, ftBracketToken, ftExtractMutation, ftExtractBaseName, isRubyGemstoneItem };`);
  return factory();
}

const scan = loadScanHelpers();

describe('STRICT tracker fix — mutation extraction (C)', () => {
  test('mutation from bracket prefix', () => {
    assert.equal(scan.ftExtractMutation({ name: '[Gold] Whale Shark' }), 'Gold');
  });

  test('mutation from metadata field', () => {
    assert.equal(scan.ftExtractMutation({ name: 'Whale Shark', mutation: 'Ruby' }), 'Ruby');
  });

  test('mutation from mutationTags (nested-style metadata)', () => {
    assert.equal(scan.ftExtractMutation({ name: 'Whale Shark', mutationTags: ['Frozen'] }), 'Frozen');
  });

  test('"Normal" mutation is treated as no mutation', () => {
    assert.equal(scan.ftExtractMutation({ name: 'Whale Shark', mutation: 'Normal' }), '');
  });

  test('no mutation -> empty string', () => {
    assert.equal(scan.ftExtractMutation({ name: 'Whale Shark' }), '');
  });

  test('base name strips bracket + leading mutation token, no duplicate', () => {
    assert.equal(scan.ftExtractBaseName({ name: '[Gold] Gold Fish' }), 'Fish');
    assert.equal(scan.ftExtractBaseName({ name: 'Shiny Shiny Totem', mutation: 'Shiny' }), 'Totem');
    assert.equal(scan.ftExtractBaseName({ name: 'Whale Shark', mutation: 'Ruby' }), 'Whale Shark');
  });

  test('mutation color follows the mutation', () => {
    assert.equal(scan.ftMutationColor('Gold'), '#fbbf24');
    assert.equal(scan.ftMutationColor('Ruby'), '#f87171');
    assert.equal(scan.ftMutationColor('Shiny'), '#fde68a');
    assert.equal(scan.ftMutationColor('Frozen'), '#67e8f9');
    assert.equal(scan.ftMutationColor('Corrupt'), '#c084fc');
    assert.equal(scan.ftMutationColor('Normal'), '#94a3b8');
  });

  test('unknown mutation -> safe non-empty default; empty -> blank', () => {
    assert.match(scan.ftMutationColor('Quantum'), /^hsl\(\d+,\d+%,\d+%\)$/);
    assert.equal(scan.ftMutationColor(''), '');
  });
});

describe('STRICT tracker fix — Ruby Gemstone stat (D)', () => {
  test('counts "Ruby Mutation Gemstone"', () => {
    assert.equal(scan.isRubyGemstoneItem({ name: 'Ruby Mutation Gemstone' }), true);
  });

  test('counts "Ruby Gemstone"', () => {
    assert.equal(scan.isRubyGemstoneItem({ name: 'Ruby Gemstone' }), true);
  });

  test('counts "[Gemstone] Ruby" (bracket category prefix)', () => {
    assert.equal(scan.isRubyGemstoneItem({ name: '[Gemstone] Ruby' }), true);
  });

  test('counts separated baseName=Ruby + category=Gemstone', () => {
    assert.equal(scan.isRubyGemstoneItem({ baseFishName: 'Ruby', category: 'Gemstone' }), true);
  });

  test('counts mutation=Ruby + category=Gemstone', () => {
    assert.equal(scan.isRubyGemstoneItem({ name: 'Gemstone', mutation: 'Ruby', category: 'Gemstone' }), true);
  });

  test('does NOT count an unrelated Ruby fish', () => {
    assert.equal(scan.isRubyGemstoneItem({ name: 'Ruby', category: 'fish' }), false);
    assert.equal(scan.isRubyGemstoneItem({ name: 'Ruby Snapper', category: 'fish' }), false);
    assert.equal(scan.isRubyGemstoneItem({ name: '[Ruby] Whale Shark', category: 'fish' }), false);
  });

  test('does NOT count a non-ruby gemstone', () => {
    assert.equal(scan.isRubyGemstoneItem({ name: 'Sapphire Gemstone' }), false);
    assert.equal(scan.isRubyGemstoneItem({ name: 'Gemstone', mutation: 'Diamond', category: 'Gemstone' }), false);
  });
});

describe('STRICT tracker fix — inline detail panel, no modal (A/B/G)', () => {
  test('source no longer defines a modal overlay/dialog for detail', () => {
    const src = readSource();
    assert.doesNotMatch(src, /\.ft-detail-overlay\s*\{/);
    assert.doesNotMatch(src, /\.ft-detail-dialog\s*\{/);
    assert.doesNotMatch(src, /function openFtDetailModal/);
  });

  test('source defines an inline detail panel + inline back button', () => {
    const src = readSource();
    assert.match(src, /\.ft-detail-panel\s*\{/);
    assert.match(src, /class="ft-detail-back"/);
    assert.match(src, /function openFtDetail\(/);
    assert.match(src, /function closeFtDetail\(/);
  });

  test('detail shows individual instances + per-fish weight (no price/chance)', () => {
    const src = readSource();
    assert.match(src, /function ftFishInstanceCards\(/);
    assert.match(src, /function collectGroupInstances\(/);
    assert.match(src, /tracker-detail-fish-weight/);
    assert.match(src, /function formatFishWeight/);
    // The individual fish card renderer must not emit price/chance.
    const renderer = sliceBalanced(src, 'function renderFishInstanceCard(', '{', '}');
    assert.doesNotMatch(renderer, /price/i);
    assert.doesNotMatch(renderer, /chance/i);
  });

  test('fish instance cards expand a stacked amount into individual cards (20 -> 20)', () => {
    const src = readSource();
    const factory = new Function(
      'resolveItemAmount, ftExtractMutation, ftExtractBaseName, ftItemWeightText, ftRarityKey, formatFishWeight',
      'const FT_MAX_INSTANCE_CARDS = 800;\n'
      + `${sliceBalanced(src, 'function ftNormalizeNonNil(value) {', '{', '}')}\n`
      + `${sliceBalanced(src, 'function parseFishWeight(rawWeight) {', '{', '}')}\n`
      + `${sliceBalanced(src, 'function formatFishWeight(rawWeight) {', '{', '}')}\n`
      + `${sliceBalanced(src, 'function normalizeMutation(value) {', '{', '}')}\n`
      + `${sliceBalanced(src, 'function getMutationSortRank(mutation) {', '{', '}')}\n`
      + `${sliceBalanced(src, 'function sortFishDetailInstances(a, b) {', '{', '}')}\n`
      + `${sliceBalanced(src, 'function ftFishInstanceCards(', '{', '}')}\n`
      + ' return ftFishInstanceCards;',
    );
    const ftFishInstanceCards = factory(
      (row) => (row && row.amount) || 1,
      () => '',
      (row) => (row && row.name) || 'Fish',
      (row) => (row && row.weight) || '',
      () => 'common',
      (w) => (w ? String(w) : ''),
    );
    const instances = [{ owner: 'alice', row: { name: 'Tuna', amount: 20, weight: '5 kg' } }];
    const cards = ftFishInstanceCards(instances);
    assert.equal(cards.length, 20);
    assert.ok(cards.every((c) => c.owner === 'alice'));
  });

  test('compiled JS asset ships inline panel, not modal', () => {
    const js = readJs();
    assert.match(js, /openFtDetail/);
    assert.doesNotMatch(js, /openFtDetailModal/);
    assert.doesNotMatch(js, /ft-detail-overlay/);
  });

  test('compiled CSS asset ships inline panel styles, not overlay', () => {
    const css = readCss();
    assert.match(css, /\.ft-detail-panel/);
    assert.doesNotMatch(css, /\.ft-detail-overlay/);
  });
});

describe('STRICT tracker fix — mobile/APK remove dropdown not clipped (F)', () => {
  test('mobile player-control-bar no longer clips with overflow:hidden', () => {
    const src = readSource();
    assert.match(
      src,
      /@media \(max-width:768px\)[\s\S]*\.player-control-bar \{[\s\S]*?overflow:visible;[\s\S]*?\}/,
    );
  });

  test('APK embed player-control-bar uses overflow:visible', () => {
    const src = readSource();
    assert.match(
      src,
      /\.inventory-apk-embed \.player-control-bar \{[\s\S]*?overflow:visible;[\s\S]*?\}/,
    );
  });

  test('bulk username action buttons use scoped classes without dropdown menu z-index', () => {
    const src = readSource();
    assert.match(src, /\.tracker-username-actions \{/);
    assert.match(src, /\.tracker-username-action-button \{/);
    assert.match(src, /\.tracker-username-action-button--offline \{/);
    assert.doesNotMatch(src, /\.remove-dropdown__menu \{/);
  });
});

describe('STRICT tracker fix — Runic Stone image override (E)', () => {
  const RUNIC_SHA = 'a21dbe70e781910ee52b0953676fea831cbf51d45e0ad565de08e0da4562146d';
  const seedDir = path.join(__dirname, '..', 'data', 'manual_image_seed');

  test('catalog Runic Stone override points to the v2 uploaded asset', () => {
    const catalog = require('../data/fishit_inventory_manual_images.json');
    const entry = catalog.overrides['stones||runic stone'];
    assert.ok(entry, 'runic stone override exists');
    assert.equal(entry.uploadedFile, 'runic_stone_2026_06_15_v2.png');
    assert.equal(entry.sha256, RUNIC_SHA);
  });

  test('runic seed v2 file exists and matches the expected uploaded image hash', () => {
    const seed = path.join(seedDir, 'runic_stone_2026_06_15_v2.png');
    assert.ok(fs.existsSync(seed), 'v2 seed present');
    const sha = crypto.createHash('sha256').update(fs.readFileSync(seed)).digest('hex');
    assert.equal(sha, RUNIC_SHA);
  });

  test('the old (wrong/log-screenshot) runic seed is no longer referenced', () => {
    const seedMap = fs.readFileSync(path.join(__dirname, '..', 'src', 'fishitInventoryManualImages.js'), 'utf8');
    assert.match(seedMap, /'stones\|\|runic stone': 'runic_stone_2026_06_15_v2\.png'/);
    assert.ok(!fs.existsSync(path.join(seedDir, 'runic_stone_2026_06_15.png')), 'old runic seed removed');
  });

  test('stone resolver returns the v2 manual override for Runic Stone', () => {
    const stoneAssets = require('../src/fishitStoneImageAssets');
    require('../src/fishitInventoryManualImages').ensureOverrideFilesFromSeed();
    const rows = stoneAssets.attachStoneImagesToItems([{ name: 'Runic Stone', stoneType: 'Runic' }], 'https://x');
    assert.equal(rows[0].imageResolver, 'stone_manual_override');
    assert.match(rows[0].imageUrl, /runic_stone_2026_06_15_v2\.png/);
  });

  test('Love Totem and Shiny Totem overrides still resolve', () => {
    const totemAssets = require('../src/fishitTotemImageAssets');
    require('../src/fishitInventoryManualImages').ensureOverrideFilesFromSeed();
    const love = totemAssets.attachTotemImagesToItems([{ name: 'Love Totem' }], 'https://x');
    const shiny = totemAssets.attachTotemImagesToItems([{ name: 'Shiny Totem' }], 'https://x');
    assert.match(love[0].imageUrl, /love_totem_2026_06_15\.png/);
    assert.match(shiny[0].imageUrl, /shiny_totem_2026_06_15\.png/);
  });
});

describe('STRICT tracker fix — regression (H)', () => {
  test('bulk username remove actions and remove-all modal still present', () => {
    const src = readSource();
    assert.match(src, /id="removeOfflineBtn"/);
    assert.match(src, /id="removeNoDataBtn"/);
    assert.match(src, /id="removeAllBtn"/);
    assert.match(src, /id="removeAllModal"/);
    assert.match(src, /data-remove-account/);
    assert.doesNotMatch(src, /data-remove-key=/);
  });

  test('Ruby Gemstone stat card + mobile 2-col grid retained', () => {
    const src = readSource();
    assert.match(src, /id="statRubyGemstone"/);
    assert.match(src, /grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  });
});
