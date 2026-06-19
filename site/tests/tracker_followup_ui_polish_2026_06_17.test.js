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
// T3 — restored 4394cfd visible-timer model (Layer 1 = frontend receive).
// The visible timers measure when THIS browser rendered fresher data. A fresh
// session shows blank until a real renderable response arrives, then resets to
// "1s ago". The seedTimersFromBackend / seedOfflineTimersFromBackend helpers
// are kept as inert no-ops so any stale call site is harmless. The dot (Layer
// 2) is still authoritative on backend status age — proven separately in
// tracker_authoritative_timer_2026_06_18.
// --------------------------------------------------------------------------
describe('T3 — visible timer is frontend-receive (restored 4394cfd)', () => {
  test('seedTimersFromBackend is an inert no-op (does not seed any visible base from backend age)', () => {
    const src = readSource();
    const block = src.match(/function seedTimersFromBackend\(_entry\) \{[\s\S]*?\}/);
    assert.ok(block, 'seedTimersFromBackend stub missing');
    assert.doesNotMatch(block[0], /_frontendRefreshAt/);
    assert.doesNotMatch(block[0], /_leaderstatsFrontendRefreshAt/);
    assert.doesNotMatch(block[0], /_inventoryFrontendRefreshAt/);
    assert.doesNotMatch(block[0], /backendPresenceAgeSeconds/);
  });

  test('seedOfflineTimersFromBackend is an inert no-op (back-compat alias)', () => {
    const src = readSource();
    const block = src.match(/function seedOfflineTimersFromBackend\(_entry\) \{[\s\S]*?\}/);
    assert.ok(block, 'seedOfflineTimersFromBackend stub missing');
    assert.doesNotMatch(block[0], /_frontendRefreshAt/);
  });

  test('neither poll path calls seedOfflineTimersFromBackend any more', () => {
    const src = readSource();
    const inv = src.indexOf('function applyInventoryPollPayload(entry, key, data) {');
    const invEnd = src.indexOf('function applyPollPayload(', inv);
    const invBody = src.slice(inv, invEnd > inv ? invEnd : inv + 4200);
    assert.ok(!/seedOfflineTimersFromBackend\(entry\);/.test(invBody), 'inventory poll must not seed from backend');
    const st = src.indexOf('function applyAccountStatusPayload(payload) {');
    const stEnd = src.indexOf('function entrySnapshotData(', st);
    const stBody = src.slice(st, stEnd > st ? stEnd : st + 3000);
    assert.ok(!/seedOfflineTimersFromBackend\(entry\);/.test(stBody), 'status poll must not seed from backend');
  });

  test('signature-gated reset calls markEntry*Refreshed only — never seeds from backend', () => {
    const src = readSource();
    const fn = src.match(/function maybeResetSectionTimers\(entry\) \{[\s\S]*?\n  \}/)[0];
    assert.match(fn, /markEntryFrontendRefreshed\(entry\);/);
    assert.match(fn, /markEntryLeaderstatsRefreshed\(entry\);/);
    assert.match(fn, /markEntryInventoryRefreshed\(entry\);/);
    assert.doesNotMatch(fn, /seedSectionBaseFromBackendAge/);
    assert.doesNotMatch(fn, /backendPresenceAgeSeconds/);
  });

  test('timer base time is in-memory only (never persisted to localStorage)', () => {
    const src = readSource();
    for (const field of ['_frontendRefreshAt', '_leaderstatsFrontendRefreshAt', '_inventoryFrontendRefreshAt']) {
      assert.ok(!new RegExp(`localStorage[\\s\\S]{0,160}${field}`).test(src));
      assert.ok(!new RegExp(`${field}[\\s\\S]{0,160}localStorage`).test(src));
    }
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

  test('aggregate inventory upload indicator is removed from bulk search row', () => {
    const src = readSource();
    assert.doesNotMatch(src, /#bulkInventoryPanel[\s\S]{0,600}data-inventory-upload-indicator/);
    assert.doesNotMatch(src, /ensureCardInventoryUploadBar/);
  });

  test('updateInventoryUploadIndicator patches fish/item/detail section badges for the active account only', () => {
    const src = readSource();
    const fn = src.match(/function updateInventoryUploadIndicator\(preferredEntry\) \{[\s\S]*?\n {2}\}/);
    assert.ok(fn, 'updateInventoryUploadIndicator missing');
    assert.match(fn[0], /ensureFishGridUploadIndicator/);
    assert.match(fn[0], /ensureItemGridUploadIndicator/);
    assert.match(fn[0], /ensureDetailUploadIndicator/);
    assert.match(fn[0], /accountViewMode === 'account' && activeAccountKey/);
    assert.doesNotMatch(fn[0], /bulkIndicator/);
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

  test('inventory upload indicator is text-only neutral (no dot class churn)', () => {
    const src = readSource();
    const fn = src.match(/function patchInventoryUploadIndicatorDom\(root, entry\) \{[\s\S]*?\n {2}\}/);
    assert.ok(fn, 'patchInventoryUploadIndicatorDom missing');
    assert.match(fn[0], /formatInventoryUploadLabel/);
    assert.match(fn[0], /dotEl\.remove\(\)/);
    assert.match(fn[0], /is-neutral/);
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
