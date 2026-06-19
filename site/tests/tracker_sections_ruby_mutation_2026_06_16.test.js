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
  // Restored 4394cfd model: the three frontend-receive formatters call
  // formatAgeAgoSeconds. Shim it into the sandbox so the extracted block
  // resolves correctly (the real formatAgeAgo helper lives in a different
  // section of the source).
  const script = `(function(){
    const pad2 = (n) => String(n).padStart(2, '0');
    function formatAgeAgo(ms) {
      const totalSecs = Math.max(0, Math.floor(Number(ms) / 1000));
      if (totalSecs < 60) return Math.max(1, totalSecs) + 's ago';
      if (totalSecs < 3600) {
        const m = Math.floor(totalSecs / 60);
        const s = totalSecs % 60;
        return s > 0 ? (m + 'm ' + s + 's ago') : (m + 'm ago');
      }
      if (totalSecs < 86400) {
        const h = Math.floor(totalSecs / 3600);
        const m = Math.floor((totalSecs % 3600) / 60);
        return m > 0 ? (h + 'H ' + m + 'm ago') : (h + 'H ago');
      }
      return Math.floor(totalSecs / 86400) + 'D ago';
    }
    function formatAgeAgoSeconds(secs) {
      if (secs == null || secs === '') return '';
      const n = Number(secs);
      if (!Number.isFinite(n) || n < 0) return '';
      return formatAgeAgo(n * 1000);
    }
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
  test('all three section timers read ~1s ago right after a refresh, even with a 6m-old backend snapshot', () => {
    const { api, setNow } = makeTimerEnv(readSource());
    const entry = {};
    setNow(6 * 60 * 1000); // backend snapshot is 6 minutes old; browser receives "now"
    api.markEntryFrontendRefreshed(entry);
    api.markEntryLeaderstatsRefreshed(entry);
    api.markEntryInventoryRefreshed(entry);
    // Restored 4394cfd format: "<n>s ago" / "<n>m ago" / etc.
    assert.equal(api.formatFrontendRefreshAgeText(entry), '1s ago');
    assert.equal(api.formatLeaderstatsRefreshAgeText(entry), '1s ago');
    assert.equal(api.formatInventoryRefreshAgeText(entry), '1s ago');
    // None of them leaked the 6m backend age.
    assert.notEqual(api.formatFrontendRefreshAgeText(entry), '6m ago');
    assert.notEqual(api.formatLeaderstatsRefreshAgeText(entry), '6m ago');
    assert.notEqual(api.formatInventoryRefreshAgeText(entry), '6m ago');
  });

  test('each section timer counts up independently from its own receive time', () => {
    const { api, setNow } = makeTimerEnv(readSource());
    const entry = {};
    setNow(0);
    api.markEntryFrontendRefreshed(entry);
    setNow(2000); api.markEntryLeaderstatsRefreshed(entry);
    setNow(4000); api.markEntryInventoryRefreshed(entry);
    setNow(5000); // tracker +5s, leaderstats +3s, inventory +1s
    assert.equal(api.formatFrontendRefreshAgeText(entry), '5s ago');
    assert.equal(api.formatLeaderstatsRefreshAgeText(entry), '3s ago');
    assert.equal(api.formatInventoryRefreshAgeText(entry), '1s ago');
  });

  test('a failed poll (no mark) does not reset any section timer', () => {
    const { api, setNow } = makeTimerEnv(readSource());
    const entry = {};
    setNow(0);
    api.markEntryFrontendRefreshed(entry);
    api.markEntryLeaderstatsRefreshed(entry);
    api.markEntryInventoryRefreshed(entry);
    setNow(9000); // failed poll -> nothing re-marked
    assert.equal(api.formatFrontendRefreshAgeText(entry), '9s ago');
    assert.equal(api.formatLeaderstatsRefreshAgeText(entry), '9s ago');
    assert.equal(api.formatInventoryRefreshAgeText(entry), '9s ago');
  });

  test('a status-only poll resets leaderstats but leaves the inventory timer counting', () => {
    const { api, setNow } = makeTimerEnv(readSource());
    const entry = {};
    setNow(0);
    api.markEntryInventoryRefreshed(entry);
    setNow(10000);
    // Status-only poll: leaderstats arrives, inventory does NOT.
    api.markEntryLeaderstatsRefreshed(entry);
    assert.equal(api.formatLeaderstatsRefreshAgeText(entry), '1s ago');
    assert.equal(api.formatInventoryRefreshAgeText(entry), '10s ago'); // not reset
  });

  test('the visible text uses the real SERVER lane upload age (server timestamps, no localStorage freshness)', () => {
    const source = readSource();
    assert.match(source, /function formatPresenceStatusText\(entry\) \{[\s\S]*?return formatBackendAgeText\(backendPresenceAgeSeconds\(entry\)\);/);
    assert.match(source, /function formatStatsUploadDurationText\(entry\) \{[\s\S]*?return formatBackendAgeText\(backendStatsAgeSeconds\(entry\)\);/);
    assert.match(source, /function formatEntrySyncStatusText\(entry\) \{[\s\S]*?return formatBackendAgeText\(backendInventoryAgeSeconds\(entry\)\);/);
    ['lastRealStatusAt', 'lastRealLeaderstatsAt', 'lastRealInventoryAt'].forEach((field) => {
      assert.ok(!new RegExp(`localStorage[\\s\\S]{0,160}${field}`).test(source), `${field} must not be a localStorage freshness source`);
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
  test('applyAuthPresence refreshes table + inventory indicators when headers update', () => {
    const source = readSource();
    const fn = source.match(/function applyAuthPresence\(entry, key, contract\) \{[\s\S]*?\n  \}/)[0];
    assert.match(fn, /refreshEntryTableSyncDisplay\(entry, key\)/);
    assert.match(fn, /refreshEntrySyncDisplay\(entry\)/);
    assert.match(fn, /laneTimestampAdvanced/);
  });

  test('unchanged get-backpack poll still applies auth headers (lane ts can advance)', () => {
    const source = readSource();
    const fn = source.indexOf('async function pollUser(key, opts) {');
    const body = source.slice(fn, fn + 4000);
    assert.match(body, /contract\.unchanged/);
    assert.match(body, /applyAuthPresence\(entry, key, contract\)/);
  });

  test('maybeResetSectionTimers is a no-op (timers follow server lane timestamps)', () => {
    const source = readSource();
    assert.match(source, /function maybeResetSectionTimers\(_entry\) \{ \/\* no-op/);
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
  const sandbox = { Math, Number, String, Array, Set, JSON, window: {} };
  const script = `(function(){
    const DEBUG_INVENTORY = false;
    function resolveItemAmount(item){ if(!item||typeof item!=='object')return 1; return item.amount ?? item.quantity ?? item.Quantity ?? 1; }
    function getPublicFishItems(d){ return (d && (d.fishItems || d.publicFishItems)) || []; }
    function getPublicStoneItems(d){ return (d && d.stoneItems) || []; }
    function getPublicTotemItems(d){ return (d && d.totemItems) || []; }
${block}
    function countRubyAcross(items){ let n = 0; for (const it of items) n += rubyGemstoneCountForItem(it); return n; }
    return { isRubyGemstoneItem, isRubyGemstoneMutationName, isRubyGemstoneFishInstance, rubyGemstoneCountForItem, getRubyGemstoneTopCardCount, countRubyAcross };
  })()`;
  return vm.runInNewContext(script, sandbox, { filename: 'ruby-helpers.js' });
}

describe('Ruby Gemstone top card count — fish name Ruby + mutation Gemstone (2026-06-16 P0)', () => {
  // Production shape (confirmed from a live backpack): a fish CARD named "Ruby"
  // whose ownedInstances carry mutation "Gemstone" (the card mutation is null).
  test('a single Ruby fish with Gemstone mutation makes the top card count 1', () => {
    const ruby = makeRubyEnv(readSource());
    const fishCard = {
      name: 'Ruby', baseFishName: 'Ruby', mutation: null, category: 'fish', amount: 1,
      ownedInstances: [{ name: 'Ruby', cleanName: 'Ruby', baseFishName: 'Ruby', mutationName: 'Gemstone', mutation: 'Gemstone', weightKg: 5.7, quantity: 1 }],
    };
    assert.ok(ruby.isRubyGemstoneFishInstance({ cleanName: 'Ruby', mutation: 'Gemstone' }));
    assert.equal(ruby.rubyGemstoneCountForItem(fishCard), 1);
    assert.equal(ruby.getRubyGemstoneTopCardCount({ fishItems: [fishCard] }), 1);
  });

  test('multiple Ruby+Gemstone fish instances count correctly', () => {
    const ruby = makeRubyEnv(readSource());
    const fishCard = {
      name: 'Ruby', baseFishName: 'Ruby',
      ownedInstances: [
        { cleanName: 'Ruby', mutationName: 'Gemstone', quantity: 1 },
        { cleanName: 'Ruby', mutationName: 'Gemstone', quantity: 1 },
        { cleanName: 'Ruby', mutationName: 'Gem Stone', quantity: 1 },
      ],
    };
    assert.equal(ruby.rubyGemstoneCountForItem(fishCard), 3);
    assert.equal(ruby.getRubyGemstoneTopCardCount({ fishItems: [fishCard] }), 3);
  });

  test('an aggregated Ruby Gemstone row with amount 3 counts as 3', () => {
    const ruby = makeRubyEnv(readSource());
    // No instances; the row itself is Ruby + Gemstone with an amount.
    const row = { cleanName: 'Ruby', mutation: 'Gemstone', amount: 3 };
    assert.equal(ruby.rubyGemstoneCountForItem(row), 3);
    // String amount still works.
    assert.equal(ruby.rubyGemstoneCountForItem({ name: 'Ruby', mutationName: 'Gemstone', amount: '4' }), 4);
  });

  test('a Ruby fish WITHOUT Gemstone mutation does not count', () => {
    const ruby = makeRubyEnv(readSource());
    assert.equal(ruby.rubyGemstoneCountForItem({ name: 'Ruby', ownedInstances: [{ cleanName: 'Ruby', mutationName: 'Gold', quantity: 2 }] }), 0);
    assert.equal(ruby.rubyGemstoneCountForItem({ cleanName: 'Ruby', mutation: null }), 0);
  });

  test('a Gemstone mutation on a NON-Ruby fish does not count', () => {
    const ruby = makeRubyEnv(readSource());
    assert.equal(ruby.rubyGemstoneCountForItem({ name: 'Whale Shark', ownedInstances: [{ cleanName: 'Whale Shark', mutationName: 'Gemstone', quantity: 5 }] }), 0);
    assert.ok(!ruby.isRubyGemstoneFishInstance({ cleanName: 'Eel', mutation: 'Gemstone' }));
  });

  test('top card does not stay 0 when a matching Ruby Gemstone fish exists among other fish', () => {
    const ruby = makeRubyEnv(readSource());
    const snapshot = { fishItems: [
      { name: 'Skeleton Narwhal', ownedInstances: [{ cleanName: 'Skeleton Narwhal', mutationName: null, quantity: 1 }] },
      { name: 'Ruby', baseFishName: 'Ruby', ownedInstances: [{ cleanName: 'Ruby', mutationName: 'Gemstone', quantity: 1 }] },
    ] };
    const total = ruby.getRubyGemstoneTopCardCount(snapshot);
    assert.ok(total > 0, 'top card must not stay 0 when a Ruby Gemstone fish exists');
    assert.equal(total, 1);
  });

  test('a legacy standalone "Ruby Gemstone" item still counts (no regression)', () => {
    const ruby = makeRubyEnv(readSource());
    assert.equal(ruby.rubyGemstoneCountForItem({ name: 'Ruby Gemstone', amount: 2 }), 2);
  });

  test('detail view and top card use consistent source data (getPublicFishItems)', () => {
    const source = readSource();
    // getRubyGemstoneTopCardCount reads the same public item getters the detail
    // list view uses, so they cannot diverge.
    assert.match(source, /function getRubyGemstoneTopCardCount\([\s\S]*?getPublicFishItems\(snapshotOrState\)/);
    assert.match(source, /const countRuby = \(item\) => \{\s*rubyGemstone \+= rubyGemstoneCountForItem\(item\);/);
  });

  test('the count is not hardcoded and flows into the displayed stat', () => {
    const source = readSource();
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
