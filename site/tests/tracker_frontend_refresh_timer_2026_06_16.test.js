'use strict';

// 2026-06-16 (rewritten 2026-06-18): the visible Status sync timer must NOT
// follow the frontend receive/refresh time and must NOT reset to "1s" on a
// refresh / poll / new session. It is the TRUE age since the last real backend
// tracker event, taken from the authoritative backend timestamp and rendered as
// "<age> ago". A 6-minute-old snapshot reads ~"6m ago" on every device/session,
// and an offline account keeps counting up (e.g. "9h ago") rather than resetting.
// This file is a regression guard so the old "reset to 1s on refresh" bug cannot
// return. (Exact "<age> ago" math + cross-session proof also live in
// tracker_authoritative_timer_2026_06_18.test.js.)

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');

function readSource() {
  return fs.readFileSync(SOURCE_PATH, 'utf8');
}

// Extract the self-contained formatAgeAgo/formatAgeAgoSeconds helpers under a
// controllable clock to prove the no-fake-1s + cross-session behaviour directly.
function makeAgeEnv(source) {
  const open = source.indexOf('  function formatAgeAgo(ms) {');
  const close = source.indexOf('  function syncAgeSeconds(timestamp) {');
  assert.ok(open > 0 && close > open, 'formatAgeAgo helper block missing from source');
  const block = source.slice(open, close);
  const clock = { now: 0 };
  const sandbox = { Math, Number, Date: { now: () => clock.now } };
  const script = `(function(){\n${block}\n  return { formatAgeAgo, formatAgeAgoSeconds };\n})()`;
  const api = vm.runInNewContext(script, sandbox, { filename: 'age-helpers.js' });
  return { api, setNow: (ms) => { clock.now = ms; } };
}

describe('tracker Status timer is authoritative, never resets to 1s (2026-06-16 regression)', () => {
  const src = readSource();

  test('formatPresenceStatusText is wired to the backend age, NOT the frontend refresh helper', () => {
    assert.match(src, /function formatPresenceStatusText\(entry\) \{[\s\S]*?return formatAgeAgoSeconds\(backendPresenceAgeSeconds\(entry\)\);/);
    const fn = src.match(/function formatPresenceStatusText\(entry\) \{[\s\S]*?\n  \}/)[0];
    assert.doesNotMatch(fn, /formatFrontendRefreshAgeText/);
  });

  test('all three section timers render the authoritative backend age', () => {
    assert.match(src, /function formatStatsUploadDurationText\(entry\) \{[\s\S]*?return formatAgeAgoSeconds\(backendStatsAgeSeconds\(entry\)\);/);
    assert.match(src, /function formatEntrySyncStatusText\(entry\) \{[\s\S]*?return formatAgeAgoSeconds\(backendInventoryAgeSeconds\(entry\)\);/);
  });

  test('a missing/old backend timestamp never fakes a fresh "1s" timer', () => {
    const { api, setNow } = makeAgeEnv(src);
    setNow(99_999_999); // browser "now" is irrelevant
    assert.equal(api.formatAgeAgoSeconds(null), '');
    assert.equal(api.formatAgeAgoSeconds(undefined), '');
    assert.equal(api.formatAgeAgoSeconds(-1), '');
    // a real 6-minute-old backend age reads "6m ago", not "1s"
    assert.equal(api.formatAgeAgoSeconds(6 * 60), '6m ago');
    assert.doesNotMatch(src, /\|\|\s*'1s'/);
  });

  test('same backend age renders identically across two independent sessions', () => {
    const a = makeAgeEnv(src);
    const b = makeAgeEnv(src);
    a.setNow(1234);
    b.setNow(98_765_432);
    assert.equal(a.api.formatAgeAgoSeconds(9 * 3600), '9h ago');
    assert.equal(b.api.formatAgeAgoSeconds(9 * 3600), '9h ago');
  });

  test('online/offline dot is still driven by real upload age, not the timer text', () => {
    assert.match(src, /function entryConnectionFreshness\(entry\) \{[\s\S]*isTrackerAccountOnline\(entry, Date\.now\(\)\)/);
  });

  test('backend presence age remains available for debug/proof', () => {
    assert.match(src, /function backendPresenceAgeSeconds\(entry\)/);
    assert.match(src, /data-backend-presence-age/);
  });

  test('frontend refresh timestamp is in-memory only (never persisted to localStorage)', () => {
    assert.ok(!/localStorage[\s\S]{0,120}_frontendRefreshAt/.test(src), '_frontendRefreshAt must not be persisted');
    assert.ok(!/_frontendRefreshAt[\s\S]{0,120}localStorage/.test(src), '_frontendRefreshAt must not be read from localStorage');
  });
});
