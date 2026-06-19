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
// P2: server-lane timestamp timer resets (functional)
// ---------------------------------------------------------------------------
function makeLaneTimerEnv(source) {
  const names = [
    'authAgeSecondsFromTs', 'backendInventoryAgeSeconds', 'formatCompactAgeAgoSeconds',
    'formatInventoryUploadLabel', 'laneTimestampAdvanced',
  ];
  const blocks = names.map((n) => {
    const m = source.match(new RegExp(`function ${n}\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n  \\}`));
    assert.ok(m, `${n} missing`);
    return m[0];
  });
  return vm.runInNewContext(`(function(){
    function liveSecondsSinceInventorySuccess(){return null;}
    ${blocks.join('\n')}
    return { formatInventoryUploadLabel, laneTimestampAdvanced };
  })()`, { Math, Number, Date }, { filename: 'lane-timer.js' });
}

describe('P2 — visible timers reset on real server lane timestamp advance', () => {
  test('advanced lastRealInventoryAt resets inventory age even when content unchanged', () => {
    const env = makeLaneTimerEnv(readSource());
    const now = Date.now();
    const prev = { lastRealInventoryAt: new Date(now - 300_000).toISOString(), inventoryRevision: 5 };
    const next = { lastRealInventoryAt: new Date(now - 5000).toISOString(), inventoryRevision: 6 };
    assert.equal(env.laneTimestampAdvanced(prev, next, 'inventory'), true);
    assert.equal(env.formatInventoryUploadLabel({ _auth: next }), '5s ago');
  });

  test('identical lane timestamp does not count as advance', () => {
    const env = makeLaneTimerEnv(readSource());
    const ts = new Date().toISOString();
    const auth = { lastRealInventoryAt: ts, inventoryRevision: 3 };
    assert.equal(env.laneTimestampAdvanced(auth, { ...auth }, 'inventory'), false);
  });

  test('maybeResetSectionTimers no longer gates on content signature', () => {
    const src = readSource();
    assert.match(src, /function maybeResetSectionTimers\(_entry\) \{ \/\* no-op/);
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
    // Slice generously — the two stale-response guards are ~50 lines apart and
    // CRLF line endings inflate the offset on Windows checkouts.
    const body = source.slice(fn, fn + 4000);
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
