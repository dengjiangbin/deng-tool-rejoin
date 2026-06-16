'use strict';

// STRICT FRONTEND FIX (2026-06-16):
//  - The frontend-receive timer must apply to ALL visible freshness timers:
//    main tracker/status, leaderstats, and inventory/fish sections, each with
//    its OWN per-section frontend-receive timestamp.
//  - The "Ruby Gemstone" top card must count Ruby using the SAME source the
//    detail/list view uses (per-instance mutation), so it never stays 0 when the
//    detail view shows Ruby.
//  - The Fairy Dust and Midnight mutation detail-card color themes must be
//    swapped, leaving every other mutation color unchanged.

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');

function readSource() {
  return fs.readFileSync(SOURCE_PATH, 'utf8');
}

function extractFn(source, name) {
  const m = source.match(new RegExp(`function ${name}\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n  \\}`));
  assert.ok(m, `function ${name} not found`);
  return m[0];
}

// ---------------------------------------------------------------------------
// TASK 1-3: per-section frontend-receive timers
// ---------------------------------------------------------------------------
function makeTimerEnv(source) {
  const open = source.indexOf('  function formatPresenceDurationLabel(secs) {');
  const close = source.indexOf('  function formatStatsUploadDurationText(entry) {');
  assert.ok(open > 0 && close > open, 'per-section timer helper block missing');
  const block = source.slice(open, close);
  const clock = { now: 0 };
  const sandbox = { Math, Number, String, Date: { now: () => clock.now } };
  const script = `(function(){
    const pad2 = (n) => String(n).padStart(2, '0');
${block}
    return {
      markEntryFrontendRefreshed, formatFrontendRefreshAgeText,
      markEntryLeaderstatsRefreshed, formatLeaderstatsRefreshAgeText,
      markEntryInventoryRefreshed, formatInventoryRefreshAgeText,
      formatPresenceStatusText,
    };
  })()`;
  const api = vm.runInNewContext(script, sandbox, { filename: 'section-timers.js' });
  return { api, setNow: (ms) => { clock.now = ms; } };
}

describe('per-section frontend-receive timers (2026-06-16)', () => {
  test('all three section timers read ~1s right after a refresh, even with a 6m-old backend snapshot', () => {
    const { api, setNow } = makeTimerEnv(readSource());
    const entry = {};
    setNow(6 * 60 * 1000); // backend snapshot is 6 minutes old; browser receives "now"
    api.markEntryFrontendRefreshed(entry);
    api.markEntryLeaderstatsRefreshed(entry);
    api.markEntryInventoryRefreshed(entry);
    assert.equal(api.formatFrontendRefreshAgeText(entry), '1s');
    assert.equal(api.formatLeaderstatsRefreshAgeText(entry), '1s');
    assert.equal(api.formatInventoryRefreshAgeText(entry), '1s');
    // None of them leaked the 6m backend age.
    assert.notEqual(api.formatFrontendRefreshAgeText(entry), '6m');
    assert.notEqual(api.formatLeaderstatsRefreshAgeText(entry), '6m');
    assert.notEqual(api.formatInventoryRefreshAgeText(entry), '6m');
  });

  test('each section timer counts up independently from its own receive time', () => {
    const { api, setNow } = makeTimerEnv(readSource());
    const entry = {};
    setNow(0);
    api.markEntryFrontendRefreshed(entry);
    setNow(2000); api.markEntryLeaderstatsRefreshed(entry);
    setNow(4000); api.markEntryInventoryRefreshed(entry);
    setNow(5000); // tracker +5s, leaderstats +3s, inventory +1s
    assert.equal(api.formatFrontendRefreshAgeText(entry), '5s');
    assert.equal(api.formatLeaderstatsRefreshAgeText(entry), '3s');
    assert.equal(api.formatInventoryRefreshAgeText(entry), '1s');
  });

  test('a failed poll (no mark) does not reset any section timer', () => {
    const { api, setNow } = makeTimerEnv(readSource());
    const entry = {};
    setNow(0);
    api.markEntryFrontendRefreshed(entry);
    api.markEntryLeaderstatsRefreshed(entry);
    api.markEntryInventoryRefreshed(entry);
    setNow(9000); // failed poll -> nothing re-marked
    assert.equal(api.formatFrontendRefreshAgeText(entry), '9s');
    assert.equal(api.formatLeaderstatsRefreshAgeText(entry), '9s');
    assert.equal(api.formatInventoryRefreshAgeText(entry), '9s');
  });

  test('a status-only poll resets leaderstats but leaves the inventory timer counting', () => {
    const { api, setNow } = makeTimerEnv(readSource());
    const entry = {};
    setNow(0);
    api.markEntryInventoryRefreshed(entry);
    setNow(10000);
    // Status-only poll: leaderstats arrives, inventory does NOT.
    api.markEntryLeaderstatsRefreshed(entry);
    assert.equal(api.formatLeaderstatsRefreshAgeText(entry), '1s');
    assert.equal(api.formatInventoryRefreshAgeText(entry), '10s'); // not reset
  });

  test('section timers are in-memory only (no localStorage) and the visible Status text uses the tracker timer', () => {
    const source = readSource();
    assert.match(source, /function formatPresenceStatusText\(entry\) \{[\s\S]*?return formatFrontendRefreshAgeText\(entry\)/);
    assert.match(source, /function formatStatsUploadDurationText\(entry\) \{[\s\S]*?return formatLeaderstatsRefreshAgeText\(entry\)/);
    assert.match(source, /function formatEntrySyncStatusText\(entry\) \{[\s\S]*?return formatInventoryRefreshAgeText\(entry\)/);
    ['_frontendRefreshAt', '_leaderstatsFrontendRefreshAt', '_inventoryFrontendRefreshAt'].forEach((field) => {
      assert.ok(!new RegExp(`localStorage[\\s\\S]{0,160}${field}`).test(source), `${field} must not be persisted`);
      assert.ok(!new RegExp(`${field}[\\s\\S]{0,160}localStorage`).test(source), `${field} must not be read from localStorage`);
    });
  });

  test('backend lane ages stay available for debug/proof (not driving the visible timers)', () => {
    const source = readSource();
    assert.match(source, /function backendPresenceAgeSeconds\(entry\)/);
    assert.match(source, /function backendStatsAgeSeconds\(entry\)[\s\S]*?liveSecondsSinceStatsSuccess/);
    assert.match(source, /function backendInventoryAgeSeconds\(entry\)[\s\S]*?liveSecondsSinceInventorySuccess/);
  });
});

describe('per-section timer reset wiring', () => {
  test('renderable get-backpack poll resets all three sections, gated on the RAW response before merge', () => {
    const source = readSource();
    const fn = source.indexOf('function applyInventoryPollPayload(entry, key, data) {');
    assert.ok(fn > 0, 'applyInventoryPollPayload missing');
    const body = source.slice(fn, fn + 1600);
    const trackerIdx = body.indexOf('if (hasRenderableTrackerData(data)) markEntryFrontendRefreshed(entry);');
    const leaderIdx = body.indexOf('if (payloadHasLeaderstats(data)) markEntryLeaderstatsRefreshed(entry);');
    const invIdx = body.indexOf('if (entryHasInventoryRows(data)) markEntryInventoryRefreshed(entry);');
    const mergeIdx = body.indexOf('entry.lastData = mergePreservedInventorySnapshot(entry.lastData, data);');
    assert.ok(trackerIdx >= 0 && leaderIdx >= 0 && invIdx >= 0, 'all three section resets must be wired');
    assert.ok(mergeIdx > trackerIdx && mergeIdx > leaderIdx && mergeIdx > invIdx, 'resets must be gated before the local-data merge');
  });

  test('account-status poll resets leaderstats (when present) but never the inventory timer', () => {
    const source = readSource();
    const fn = source.indexOf('function applyAccountStatusPayload(payload) {');
    const end = source.indexOf('function entrySnapshotData(', fn);
    const body = source.slice(fn, end);
    assert.match(body, /if \(payloadHasLeaderstats\(st\)\) markEntryLeaderstatsRefreshed\(entry\);/);
    assert.ok(!/markEntryInventoryRefreshed/.test(body), 'status-only poll must not reset the inventory timer');
  });
});

// ---------------------------------------------------------------------------
// TASK 4-5: Ruby Gemstone top card count
// ---------------------------------------------------------------------------
function makeRubyEnv(source) {
  const open = source.indexOf('  function ftBracketToken(rawName) {');
  const close = source.indexOf('  function computeInventoryStats() {');
  assert.ok(open > 0 && close > open, 'ruby helper block missing');
  const block = source.slice(open, close);
  const sandbox = { Math, Number, String, Array, Set };
  const script = `(function(){
    function resolveItemAmount(item){ if(!item||typeof item!=='object')return 1; return item.amount ?? item.quantity ?? item.Quantity ?? 1; }
${block}
    // Mirror computeInventoryStats' aggregation across an item list.
    function countRubyAcross(items){ let n = 0; for (const it of items) n += rubyGemstoneCountForItem(it); return n; }
    return { isRubyGemstoneItem, isRubyGemstoneMutationName, rubyGemstoneCountForItem, countRubyAcross, RUBY_GEMSTONE_ALIASES };
  })()`;
  return vm.runInNewContext(script, sandbox, { filename: 'ruby-helpers.js' });
}

describe('Ruby Gemstone top card count (2026-06-16)', () => {
  test('detail/list per-instance Ruby mutation (name "Ruby") makes the top card count it', () => {
    const ruby = makeRubyEnv(readSource());
    // Shape seen in production: a fish card whose ownedInstances carry the Ruby
    // gemstone mutation. The detail view renders 1 fish; the top card must match.
    const fishCard = {
      name: 'Hawks Turtle',
      ownedInstances: [{ mutationName: 'Ruby', weightKg: 5.7, quantity: 1 }],
    };
    assert.equal(ruby.rubyGemstoneCountForItem(fishCard), 1);
    assert.equal(ruby.countRubyAcross([fishCard]), 1);
  });

  test('item named "Ruby Gemstone" counts at the top card', () => {
    const ruby = makeRubyEnv(readSource());
    const item = { name: 'Ruby Gemstone', amount: 2 };
    assert.equal(ruby.rubyGemstoneCountForItem(item), 2);
  });

  test('Ruby amount/quantity as a string still counts correctly', () => {
    const ruby = makeRubyEnv(readSource());
    const stringQtyInstance = { name: 'Fish', ownedInstances: [{ mutation: 'Ruby', quantity: '3' }] };
    assert.equal(ruby.rubyGemstoneCountForItem(stringQtyInstance), 3);
    const stringAmtItem = { name: 'Ruby Gemstone', amount: '4' };
    assert.equal(ruby.rubyGemstoneCountForItem(stringAmtItem), 4);
  });

  test('top card does not stay 0 when detail/list has Ruby data (multiple owners + instances)', () => {
    const ruby = makeRubyEnv(readSource());
    const items = [
      { name: 'Whale', ownedInstances: [{ mutationName: 'Ruby', quantity: 1 }, { mutationName: 'Gold', quantity: 1 }] },
      { name: 'Eel', ownedInstances: [{ mutationName: 'ruby gemstone', quantity: 1 }] },
      { name: 'Cod', ownedInstances: [{ mutationName: 'Shiny', quantity: 1 }] },
    ];
    const total = ruby.countRubyAcross(items);
    assert.ok(total > 0, 'top card must not stay 0 when Ruby exists in detail data');
    assert.equal(total, 2); // one Ruby + one ruby gemstone, the Gold/Shiny ignored
  });

  test('non-Ruby items are not miscounted, and the count is not hardcoded', () => {
    const ruby = makeRubyEnv(readSource());
    assert.equal(ruby.rubyGemstoneCountForItem({ name: 'Goldfish', ownedInstances: [{ mutationName: 'Gold', quantity: 9 }] }), 0);
    assert.equal(ruby.rubyGemstoneCountForItem({ name: 'Bass' }), 0);
    assert.equal(ruby.rubyGemstoneCountForItem({}), 0);
    // Aliases are normalized (trim + case-insensitive), proving no hardcoded number.
    assert.ok(ruby.isRubyGemstoneMutationName('  RUBY  '));
    assert.ok(ruby.isRubyGemstoneMutationName('Ruby Gemstone'));
    assert.ok(!ruby.isRubyGemstoneMutationName('Sapphire'));
  });

  test('top card binding uses the shared rubyGemstoneCountForItem helper (no hardcoded literal)', () => {
    const source = readSource();
    assert.match(source, /const countRuby = \(item\) => \{\s*rubyGemstone \+= rubyGemstoneCountForItem\(item\);/);
    // Ensure the displayed value flows from the computed stat, not a constant.
    assert.match(source, /countUp\.set\(statRubyGemstoneEl, \{ to: stats\.rubyGemstone \|\| 0/);
  });
});

// ---------------------------------------------------------------------------
// TASK 6: Fairy Dust <-> Midnight color swap
// ---------------------------------------------------------------------------
function makeColorEnv(source) {
  const names = [
    'ftMutationHashColor', 'parseHexColor', 'rgbToHex', 'colorDistance',
    'getReadableTextColor', 'ftRarityAccentHex', 'nudgeHexAwayFromRarity',
    'hslToHex', 'colorToHex', 'ensureMutationColorDiffersFromRarity',
    'ftDetailMutationSemanticPalette',
  ];
  const rarityConst = source.match(/const FT_RARITY_ACCENT_HEX = \{[\s\S]*?\};/);
  assert.ok(rarityConst, 'FT_RARITY_ACCENT_HEX const missing');
  const body = names.map((n) => extractFn(source, n)).join('\n');
  const sandbox = { Math, Number, String, Object, parseInt };
  const script = `(function(){
${rarityConst[0]}
${body}
    return { ftDetailMutationSemanticPalette, ftMutationHashColor, colorToHex, colorDistance, ftRarityAccentHex };
  })()`;
  return vm.runInNewContext(script, sandbox, { filename: 'mutation-colors.js' });
}

describe('Fairy Dust <-> Midnight mutation color swap (2026-06-16)', () => {
  test('Fairy Dust and Midnight themes are swapped (each adopts the other\'s resolved accent)', () => {
    const env = makeColorEnv(readSource());
    const fairy = env.ftDetailMutationSemanticPalette('Fairy Dust');
    const midnight = env.ftDetailMutationSemanticPalette('Midnight');
    assert.ok(fairy && midnight, 'both palettes must resolve');
    // Pre-swap each fell through to the hash fallback keyed on its own name.
    const hashFairy = env.colorToHex(env.ftMutationHashColor('fairy dust'));
    const hashMidnight = env.colorToHex(env.ftMutationHashColor('midnight'));
    assert.notEqual(hashFairy, hashMidnight, 'the two source themes must actually differ');
    assert.equal(fairy.accent, hashMidnight, 'Fairy Dust must now use Midnight\'s theme');
    assert.equal(midnight.accent, hashFairy, 'Midnight must now use Fairy Dust\'s theme');
  });

  test('both swapped themes still pass contrast/readability and differ from rarity color', () => {
    const env = makeColorEnv(readSource());
    for (const mut of ['Fairy Dust', 'Midnight']) {
      const base = env.ftDetailMutationSemanticPalette(mut);
      assert.ok(['#111827', '#ffffff'].includes(base.text), `${mut} text must be a readable contrast color`);
      assert.notEqual(base.text, base.accent, `${mut} text must contrast its accent`);
      // Mutation accent must be a real hex and never identical to a rarity color.
      for (const rarity of ['epic', 'legendary', 'mythic']) {
        const rarityHex = env.ftRarityAccentHex(rarity);
        const palette = env.ftDetailMutationSemanticPalette(mut);
        // The theme-vars layer enforces >=72 distance; here we at least assert
        // the raw accent is a real hex and not identical to the rarity color.
        assert.match(palette.accent, /^#[0-9a-f]{6}$/i);
        assert.notEqual(palette.accent.toLowerCase(), rarityHex.toLowerCase());
      }
    }
  });

  test('only Fairy Dust and Midnight are swapped; other mutation themes are unchanged', () => {
    const env = makeColorEnv(readSource());
    assert.equal(env.ftDetailMutationSemanticPalette('Gold').slug, 'gold');
    assert.equal(env.ftDetailMutationSemanticPalette('Sandy').slug, 'sandy');
    assert.equal(env.ftDetailMutationSemanticPalette('Ghost').slug, 'ghost');
    assert.equal(env.ftDetailMutationSemanticPalette('Albino').slug, 'albino');
    assert.equal(env.ftDetailMutationSemanticPalette('Shiny').slug, 'shiny');
    assert.equal(env.ftDetailMutationSemanticPalette('Ruby').slug, 'gemstone');
    assert.equal(env.ftDetailMutationSemanticPalette('Stone').slug, 'stone');
  });

  test('the swap is implemented as a key-swap before rule lookup, touching only these two keys', () => {
    const source = readSource();
    assert.match(
      source,
      /function ftDetailMutationSemanticPalette\(mutation\) \{[\s\S]*?if \(key === 'fairy dust'\) key = 'midnight';\s*else if \(key === 'midnight'\) key = 'fairy dust';/,
    );
  });
});
