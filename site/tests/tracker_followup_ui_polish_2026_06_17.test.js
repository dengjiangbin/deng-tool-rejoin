'use strict';

// STRICT FOLLOW-UP (2026-06-17) post-scale UI/state polish:
//  T1) Inventory upload indicator must NOT show a global "worst"/offline entry
//      over the selected (online) inventory — it is scoped to the active account.
//  T2) Timers/dots must not blink: class swaps are guarded so the CSS pulse
//      animation is never restarted on a 1s tick when state is unchanged.
//  T3) OFFLINE username timers must continue from the backend last-real-update
//      age and must NOT reset to ~1s when opening a new session/device.
//  T4) Narrow screens use the readable stacked account cards, not the cramped
//      fixed-width desktop table (no clipped "dengh..."/"139....").
//  T5) "DENG Tracker" title uses the neon-blue -> light-pink gradient.

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const manifest = require('../src/inventoryAssetManifest.json');
const INVENTORY_JS = path.join(__dirname, '..', 'public', 'assets', manifest.js);
const INVENTORY_CSS = path.join(__dirname, '..', 'public', 'assets', manifest.css);

const readSource = () => fs.readFileSync(SOURCE_PATH, 'utf8');

// --------------------------------------------------------------------------
// T3 — offline timer continuity (functional, via the real seeding helper)
// --------------------------------------------------------------------------
function makeOfflineSeedEnv(source, { presenceAge, statsAge, inventoryAge }) {
  const block = source.match(/function seedTimersFromBackend\(entry\) \{[\s\S]*?\n {2}\}/);
  assert.ok(block, 'seedTimersFromBackend helper missing from source');
  const clock = { now: 0 };
  const sandbox = {
    Math,
    Number,
    Date: { now: () => clock.now },
    backendPresenceAgeSeconds: () => presenceAge,
    backendStatsAgeSeconds: () => statsAge,
    backendInventoryAgeSeconds: () => inventoryAge,
  };
  const script = `(function(){\n${block[0]}\n  return { seedTimersFromBackend };\n})()`;
  const api = vm.runInNewContext(script, sandbox, { filename: 'timer-seed.js' });
  return { api, setNow: (ms) => { clock.now = ms; } };
}

describe('T3 — username timer (all states) does not reset on a new session/device', () => {
  test('timer base time is seeded from backend last-real-update age (~30m), not 1s', () => {
    const env = makeOfflineSeedEnv(readSource(), { presenceAge: 1800, statsAge: 1800, inventoryAge: 1800 });
    env.setNow(5_000_000); // arbitrary "page open" time on a brand-new session
    const entry = {}; // fresh entry — nothing observed yet this session
    env.api.seedTimersFromBackend(entry);
    // Visible age = now - base. It must read ~30m (1800s), not ~1s.
    assert.equal(5_000_000 - entry._frontendRefreshAt, 1_800_000);
    assert.equal(5_000_000 - entry._leaderstatsFrontendRefreshAt, 1_800_000);
    assert.equal(5_000_000 - entry._inventoryFrontendRefreshAt, 1_800_000);
    assert.equal(entry._timersSeededFromBackend, true);
  });

  test('opening from another session/device shows the SAME ~30m, not 1s', () => {
    const src = readSource();
    const a = makeOfflineSeedEnv(src, { presenceAge: 1800, statsAge: 1800, inventoryAge: 1800 });
    a.setNow(1_000_000);
    const entryA = {};
    a.api.seedTimersFromBackend(entryA);
    const b = makeOfflineSeedEnv(src, { presenceAge: 1800, statsAge: 1800, inventoryAge: 1800 });
    b.setNow(9_999_999);
    const entryB = {};
    b.api.seedTimersFromBackend(entryB);
    assert.equal(1_000_000 - entryA._frontendRefreshAt, 1_800_000);
    assert.equal(9_999_999 - entryB._frontendRefreshAt, 1_800_000);
  });

  test('B3: an ONLINE account whose last upload was 30m ago also shows ~30m on open (not 1s)', () => {
    const env = makeOfflineSeedEnv(readSource(), { presenceAge: 1800, statsAge: 1800, inventoryAge: 1800 });
    env.setNow(5_000_000);
    const entry = {}; // first observation, no section base set yet
    env.api.seedTimersFromBackend(entry);
    assert.equal(5_000_000 - entry._inventoryFrontendRefreshAt, 1_800_000);
  });

  test('only seeds UNSET fields — a base already set by maybeResetSectionTimers is preserved', () => {
    const env = makeOfflineSeedEnv(readSource(), { presenceAge: 1800, statsAge: 1800, inventoryAge: 1800 });
    env.setNow(5_000_000);
    const entry = { _frontendRefreshAt: 4_999_000 }; // already seeded from a real data change
    env.api.seedTimersFromBackend(entry);
    assert.equal(entry._frontendRefreshAt, 4_999_000); // untouched
  });

  test('seeding runs once per entry/session and never re-seeds an already-seeded entry', () => {
    const env = makeOfflineSeedEnv(readSource(), { presenceAge: 1800, statsAge: 1800, inventoryAge: 1800 });
    env.setNow(1000);
    const entry = {};
    env.api.seedTimersFromBackend(entry);
    const firstBase = entry._frontendRefreshAt;
    env.setNow(2000); // a later poll on the same session
    env.api.seedTimersFromBackend(entry);
    assert.equal(entry._frontendRefreshAt, firstBase); // not re-seeded
  });
});

// --------------------------------------------------------------------------
// T3 wiring — seeding is called from both poll paths, after the reset
// --------------------------------------------------------------------------
describe('T3 — wiring', () => {
  test('seedOfflineTimersFromBackend runs after maybeResetSectionTimers in both poll paths', () => {
    const src = readSource();
    // Inventory poll path.
    const inv = src.indexOf('function applyInventoryPollPayload(entry, key, data) {');
    const invBody = src.slice(inv, inv + 4200);
    const r1 = invBody.indexOf('maybeResetSectionTimers(entry);');
    const s1 = invBody.indexOf('seedOfflineTimersFromBackend(entry);');
    assert.ok(r1 >= 0 && s1 > r1, 'inventory poll must seed offline timers after the reset');
    // Status-only poll path.
    const st = src.indexOf('function applyAccountStatusPayload(payload) {');
    const stBody = src.slice(st, st + 3000);
    const r2 = stBody.indexOf('maybeResetSectionTimers(entry);');
    const s2 = stBody.indexOf('seedOfflineTimersFromBackend(entry);');
    assert.ok(r2 >= 0 && s2 > r2, 'status poll must seed offline timers after the reset');
  });

  test('seeding does not introduce a new markEntryFrontendRefreshed call-site', () => {
    const src = readSource();
    const calls = src.match(/markEntryFrontendRefreshed\(entry\);/g) || [];
    assert.equal(calls.length, 1, 'still exactly one frontend-refresh reset call-site');
  });

  test('timer base time is in-memory only (never persisted to localStorage)', () => {
    const src = readSource();
    assert.ok(!/localStorage[\s\S]{0,160}_timersSeededFromBackend/.test(src));
    assert.ok(!/_timersSeededFromBackend[\s\S]{0,160}localStorage/.test(src));
  });
});

// --------------------------------------------------------------------------
// T1 — inventory indicator scoped to the active account (no global/offline one)
// --------------------------------------------------------------------------
describe('T1 — no global inventory indicator over the selected username', () => {
  test('resolveInventoryIndicatorEntry never falls back to a global "worst"/stalest entry', () => {
    const src = readSource();
    const fn = src.match(/function resolveInventoryIndicatorEntry\(preferredEntry\) \{[\s\S]*?\n {2}\}/);
    assert.ok(fn, 'resolveInventoryIndicatorEntry missing');
    // The old global "worst entry" fallback (worstAge loop over all accounts)
    // must be gone — it is what surfaced an offline username over the selected one.
    assert.doesNotMatch(fn[0], /worstAge/, 'must not score a global worst entry');
    assert.doesNotMatch(fn[0], /getFilteredAccountEntries/, 'must not scan all accounts');
    assert.match(fn[0], /accountViewMode === 'account' && activeAccountKey/);
    assert.match(fn[0], /return null;/);
  });

  test('updateInventoryUploadIndicator hides the bulk indicator unless scoped to one account', () => {
    const src = readSource();
    const fn = src.match(/function updateInventoryUploadIndicator\(preferredEntry\) \{[\s\S]*?\n {2}\}/);
    assert.ok(fn, 'updateInventoryUploadIndicator missing');
    assert.match(fn[0], /setInventoryIndicatorHidden\(bulkIndicator, !scoped\)/);
    assert.match(fn[0], /accountViewMode === 'account'/);
  });
});

// --------------------------------------------------------------------------
// T2 — timers/dots do not blink (guarded class swaps)
// --------------------------------------------------------------------------
describe('T2 — no blinking timers/indicators', () => {
  test('presence dot only swaps classes when the state changes', () => {
    const src = readSource();
    const fn = src.match(/function patchAccountStatusDom\(root, entry\) \{[\s\S]*?\n {2}\}/);
    assert.ok(fn, 'patchAccountStatusDom missing');
    assert.match(fn[0], /if \(!statusEl\.classList\.contains\(want\)\)/);
  });

  test('inventory upload dot only swaps classes when the state changes', () => {
    const src = readSource();
    const fn = src.match(/function patchInventoryUploadIndicatorDom\(root, entry\) \{[\s\S]*?\n {2}\}/);
    assert.ok(fn, 'patchInventoryUploadIndicatorDom missing');
    assert.match(fn[0], /if \(!dotEl\.classList\.contains\(want\)\)/);
  });
});

// --------------------------------------------------------------------------
// T4 — mobile uses readable cards, not the cramped fixed table
// --------------------------------------------------------------------------
describe('T4 — REVERTED: desktop-style table with smooth horizontal scroll on mobile', () => {
  test('the max-width:768px query shows the table (scrollable) and hides the cards', () => {
    const src = readSource();
    assert.match(src, /@media \(max-width:768px\)[\s\S]*\.accounts-table-wrap \{\s*display:block !important;/);
    assert.match(src, /@media \(max-width:768px\)[\s\S]*\.accounts-mobile-list \{ display:none !important; \}/);
    // The table keeps natural columns + a readable min-width for horizontal scroll.
    assert.match(src, /@media \(max-width:768px\)[\s\S]*\.accounts-table \{\s*table-layout:auto !important;\s*min-width:760px;/);
  });

  test('APK embed also keeps the scrollable table (not the stacked cards)', () => {
    const src = readSource();
    assert.match(src, /\.inventory-apk-embed [^\n]*\.accounts-table-wrap \{\s*\n\s*display:block !important;/);
    assert.match(src, /\.inventory-apk-embed [^\n]*\.accounts-mobile-list \{ display:none !important; \}/);
  });

  test('the table wrap is a smooth horizontal scroll container', () => {
    const css = fs.readFileSync(INVENTORY_CSS, 'utf8');
    assert.match(css, /-webkit-overflow-scrolling:touch/);
    assert.match(css, /scroll-behavior:smooth/);
    assert.match(css, /min-width:760px/);
  });

  test('compiled CSS bundle reflects the reverted table layout (cards hidden on mobile)', () => {
    const css = fs.readFileSync(INVENTORY_CSS, 'utf8');
    assert.match(css, /@media \(max-width:768px\)[\s\S]*accounts-mobile-list\{display:none ?!important/);
  });
});

// --------------------------------------------------------------------------
// T5 — DENG Tracker title gradient
// --------------------------------------------------------------------------
describe('T5 — DENG Tracker title neon-blue -> light-pink gradient', () => {
  test('source .header h1 uses a blue->pink linear-gradient with background-clip:text', () => {
    const src = readSource();
    const block = src.match(/\.header h1 \{[^}]*\}/);
    assert.ok(block, '.header h1 rule missing');
    assert.match(block[0], /linear-gradient\(90deg,#60a5fa,#f9a8d4\)/);
    assert.match(block[0], /background-clip:text/);
  });

  test('compiled CSS bundle contains the gradient title', () => {
    const css = fs.readFileSync(INVENTORY_CSS, 'utf8');
    assert.match(css, /\.header h1\{[^}]*linear-gradient\(90deg,#60a5fa,#f9a8d4\)/);
  });
});
