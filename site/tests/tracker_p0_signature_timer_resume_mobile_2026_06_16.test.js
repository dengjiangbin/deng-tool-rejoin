'use strict';

// STRICT P0 FRONTEND FIX (2026-06-16):
//  P1) Ruby Gemstone top card must count fish whose name is "Ruby" AND mutation
//      is "Gemstone" (covered in tracker_sections_ruby_mutation_2026_06_16).
//  P2) Visible freshness timers must reset ONLY when the displayed dataset
//      actually changes (effective signature), never on a bare successful poll,
//      a preserved/fallback snapshot, a status-only payload, or identical data.
//  P2C) First-load / return-to-tab must fetch immediately and ignore stale
//      in-flight responses via per-entry request ordering.
//  P3) Mobile coin/username must stay readable (no premature ellipsis cutoff).

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const readSource = () => fs.readFileSync(SOURCE_PATH, 'utf8');

// ---------------------------------------------------------------------------
// P2: signature-gated timer resets (functional)
// ---------------------------------------------------------------------------
function makeSignatureEnv(source) {
  const open = source.indexOf('  function stableStringify(value) {');
  const close = source.indexOf('  function backendPresenceAgeSeconds(entry) {');
  assert.ok(open > 0 && close > open, 'signature helper block missing');
  const block = source.slice(open, close);
  const marks = { tracker: 0, leaderstats: 0, inventory: 0 };
  const sandbox = { Math, Number, String, Array, Object, JSON };
  const script = `(function(){
    const marks = { tracker: 0, leaderstats: 0, inventory: 0 };
    // Stubs mirroring the real helpers the signature reads.
    function normalizeToken(v){ return String(v == null ? '' : v).trim().toLowerCase().replace(/\\s+/g, ' '); }
    function resolveItemAmount(it){ return (it && (it.amount ?? it.quantity)) ?? 1; }
    function getEntryPlayerStats(entry){ return entry && entry.lastData ? entry.lastData.__stats : null; }
    function displayCoinsStat(stats){ return stats ? String(stats.coins ?? '') : ''; }
    function displayTotalCaughtStat(stats){ return stats ? String(stats.totalCaught ?? '') : ''; }
    function displayRarestFishStat(stats){ return stats ? String(stats.rarest ?? '') : ''; }
    function getPublicFishItems(d){ return (d && d.fishItems) || []; }
    function getPublicStoneItems(d){ return (d && d.stoneItems) || []; }
    function getPublicTotemItems(d){ return (d && d.totemItems) || []; }
    function getRubyGemstoneTopCardCount(d){ return (d && d.__ruby) || 0; }
    function hasRenderableTrackerData(d){ return !!(d && (d.__stats || (d.fishItems && d.fishItems.length))); }
    function payloadHasLeaderstats(d){ return !!(d && d.__stats); }
    function entryHasInventoryRows(d){ return !!(d && d.fishItems && d.fishItems.length); }
    function markEntryFrontendRefreshed(){ marks.tracker++; }
    function markEntryLeaderstatsRefreshed(){ marks.leaderstats++; }
    function markEntryInventoryRefreshed(){ marks.inventory++; }
${block}
    return { maybeResetSectionTimers, buildDisplayedDatasetSignature, buildInventorySignature, buildLeaderstatsSignature, marks };
  })()`;
  const api = vm.runInNewContext(script, sandbox, { filename: 'signatures.js' });
  return api;
}

describe('P2 — visible timers reset only on real displayed-data change', () => {
  test('initial renderable data marks all three sections once', () => {
    const env = makeSignatureEnv(readSource());
    const entry = { displayName: 'u', lastData: { username: 'u', __stats: { coins: 100, totalCaught: 5 }, fishItems: [{ name: 'A', amount: 1 }], __ruby: 0 } };
    env.maybeResetSectionTimers(entry);
    assert.equal(env.marks.tracker, 1);
    assert.equal(env.marks.leaderstats, 1);
    assert.equal(env.marks.inventory, 1);
  });

  test('an identical poll after 10s does NOT reset any timer', () => {
    const env = makeSignatureEnv(readSource());
    const data = { username: 'u', __stats: { coins: 100, totalCaught: 5 }, fishItems: [{ name: 'A', amount: 1 }], __ruby: 0 };
    const entry = { displayName: 'u', lastData: data };
    env.maybeResetSectionTimers(entry);
    // Same effective data again (cache-bust returned identical snapshot).
    entry.lastData = { ...data, fishItems: [{ name: 'A', amount: 1 }], __stats: { coins: 100, totalCaught: 5 } };
    env.maybeResetSectionTimers(entry);
    assert.equal(env.marks.tracker, 1);
    assert.equal(env.marks.leaderstats, 1);
    assert.equal(env.marks.inventory, 1);
  });

  test('changed coins resets leaderstats + tracker but not inventory', () => {
    const env = makeSignatureEnv(readSource());
    const entry = { displayName: 'u', lastData: { username: 'u', __stats: { coins: 100, totalCaught: 5 }, fishItems: [{ name: 'A', amount: 1 }], __ruby: 0 } };
    env.maybeResetSectionTimers(entry);
    entry.lastData = { username: 'u', __stats: { coins: 250, totalCaught: 5 }, fishItems: [{ name: 'A', amount: 1 }], __ruby: 0 };
    env.maybeResetSectionTimers(entry);
    assert.equal(env.marks.leaderstats, 2);
    assert.equal(env.marks.tracker, 2);
    assert.equal(env.marks.inventory, 1); // unchanged inventory
  });

  test('changed totalCaught resets leaderstats + tracker', () => {
    const env = makeSignatureEnv(readSource());
    const entry = { displayName: 'u', lastData: { username: 'u', __stats: { coins: 100, totalCaught: 5 }, fishItems: [{ name: 'A', amount: 1 }], __ruby: 0 } };
    env.maybeResetSectionTimers(entry);
    entry.lastData = { username: 'u', __stats: { coins: 100, totalCaught: 6 }, fishItems: [{ name: 'A', amount: 1 }], __ruby: 0 };
    env.maybeResetSectionTimers(entry);
    assert.equal(env.marks.leaderstats, 2);
    assert.equal(env.marks.tracker, 2);
    assert.equal(env.marks.inventory, 1);
  });

  test('changed fish/item count resets inventory + tracker but not leaderstats', () => {
    const env = makeSignatureEnv(readSource());
    const entry = { displayName: 'u', lastData: { username: 'u', __stats: { coins: 100, totalCaught: 5 }, fishItems: [{ name: 'A', amount: 1 }], __ruby: 0 } };
    env.maybeResetSectionTimers(entry);
    entry.lastData = { username: 'u', __stats: { coins: 100, totalCaught: 5 }, fishItems: [{ name: 'A', amount: 2 }], __ruby: 0 };
    env.maybeResetSectionTimers(entry);
    assert.equal(env.marks.inventory, 2);
    assert.equal(env.marks.tracker, 2);
    assert.equal(env.marks.leaderstats, 1);
  });

  test('changed Ruby Gemstone count resets inventory + tracker', () => {
    const env = makeSignatureEnv(readSource());
    const entry = { displayName: 'u', lastData: { username: 'u', __stats: { coins: 100, totalCaught: 5 }, fishItems: [{ name: 'A', amount: 1 }], __ruby: 0 } };
    env.maybeResetSectionTimers(entry);
    entry.lastData = { username: 'u', __stats: { coins: 100, totalCaught: 5 }, fishItems: [{ name: 'A', amount: 1 }], __ruby: 1 };
    env.maybeResetSectionTimers(entry);
    assert.equal(env.marks.inventory, 2);
    assert.equal(env.marks.tracker, 2);
    assert.equal(env.marks.leaderstats, 1);
  });

  test('status-only payload (no inventory rows) reuses preserved inventory and does NOT reset inventory timer', () => {
    const env = makeSignatureEnv(readSource());
    const entry = { displayName: 'u', lastData: { username: 'u', __stats: { coins: 100, totalCaught: 5 }, fishItems: [{ name: 'A', amount: 1 }], __ruby: 0 } };
    env.maybeResetSectionTimers(entry);
    // Status poll merges new stats but the SAME preserved inventory rows.
    entry.lastData = { username: 'u', __stats: { coins: 100, totalCaught: 9 }, fishItems: [{ name: 'A', amount: 1 }], __ruby: 0 };
    env.maybeResetSectionTimers(entry);
    assert.equal(env.marks.leaderstats, 2); // stats changed
    assert.equal(env.marks.inventory, 1);  // inventory unchanged
  });

  test('a fallback/preserved snapshot with no renderable change does not reset', () => {
    const env = makeSignatureEnv(readSource());
    const data = { username: 'u', __stats: { coins: 100, totalCaught: 5 }, fishItems: [{ name: 'A', amount: 1 }], __ruby: 0 };
    const entry = { displayName: 'u', lastData: data };
    env.maybeResetSectionTimers(entry);
    // Three more identical polls (preserved data reused each time).
    for (let i = 0; i < 3; i++) { entry.lastData = { ...data }; env.maybeResetSectionTimers(entry); }
    assert.equal(env.marks.tracker, 1);
    assert.equal(env.marks.leaderstats, 1);
    assert.equal(env.marks.inventory, 1);
  });
});

// ---------------------------------------------------------------------------
// P2C: immediate fetch + request ordering wiring
// ---------------------------------------------------------------------------
describe('P2C — first-load / resume freshness + stale-response guard', () => {
  test('pollUser tags each request and ignores a stale in-flight response', () => {
    const source = readSource();
    const fn = source.indexOf('async function pollUser(key, opts) {');
    assert.ok(fn > 0, 'pollUser missing');
    const body = source.slice(fn, fn + 1600);
    assert.match(body, /entry\._pollReqSeq = \(entry\._pollReqSeq \|\| 0\) \+ 1/);
    assert.match(body, /const isStaleResponse = \(\) =>/);
    // Guard runs after the fetch resolves AND after the body is read.
    assert.ok((body.match(/if \(isStaleResponse\(\)\) return;/g) || []).length >= 2, 'stale guard must run after fetch and after reading the body');
  });

  test('tab visibility / focus / pageshow trigger an immediate debounced refresh', () => {
    const source = readSource();
    const idx = source.indexOf("safeBind('visibility refetch'");
    assert.ok(idx > 0, 'visibility refetch bind missing');
    const body = source.slice(idx, idx + 900);
    assert.match(body, /const refreshTrackerNow = \(reason\) =>/);
    assert.match(body, /now - resumeRefreshAt < 700/); // leading-edge debounce
    assert.match(body, /visibilitychange[\s\S]*?refreshTrackerNow\('tab-visible'\)/);
    assert.match(body, /'focus'[\s\S]*?refreshTrackerNow\('window-focus'\)/);
    // pageshow fires unconditionally now (not only on bfcache restore).
    assert.match(body, /'pageshow'[\s\S]*?refreshTrackerNow\('pageshow'\)/);
    assert.ok(!/pageshow[\s\S]{0,80}ev\.persisted/.test(body), 'pageshow must not be gated on ev.persisted');
  });
});

// ---------------------------------------------------------------------------
// P3: mobile coin/username readability
// ---------------------------------------------------------------------------
describe('P3 — mobile coin/username not cut off', () => {
  test('mobile username wraps with a tooltip instead of hard ellipsis', () => {
    const source = readSource();
    const idx = source.indexOf('.accounts-mobile-card__username {');
    const body = source.slice(idx, idx + 520);
    assert.match(body, /font-size:clamp\(/);
    assert.match(body, /overflow-wrap:anywhere/);
    assert.match(body, /-webkit-line-clamp:2/);
    assert.ok(!/white-space:nowrap/.test(body), 'username must not be locked to a single nowrap line');
    // Full value is exposed via title for accessibility.
    assert.match(source, /class="accounts-mobile-card__username" title="\$\{hideUsernames \? 'Username hidden' : escHtml\(entry\.displayName\)\}"/);
  });

  test('narrow-screen media queries scale coin/value fonts and let them wrap', () => {
    const source = readSource();
    assert.match(source, /@media \(max-width:420px\)/);
    assert.match(source, /@media \(max-width:340px\)/);
    const idx = source.indexOf('.accounts-mobile-card__grid--stats .coin-value');
    const body = source.slice(idx, idx + 700);
    assert.match(body, /min-width:0/);
    assert.match(body, /overflow-wrap:anywhere/);
    assert.match(body, /font-size:clamp\(/);
    assert.match(body, /white-space:normal/);
  });
});
