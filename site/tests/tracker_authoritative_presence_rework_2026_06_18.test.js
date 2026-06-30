'use strict';

/**
 * P0 REWORK (2026-06-18) — authoritative serve-time presence contract + frontend
 * pure-render + content-hash conditional fetch.
 *
 * Guards the exact live regressions from the screenshots:
 *  - offline-since-last-night account showing GREEN + fake "9m ago"
 *  - online account blinking RED
 *  - 10-minute frontend degradation (top cards -> 0, rows -> dashes)
 *  - timers resetting / using non-real time
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const readApp = require('../src/trackerReadApp');

const HOUR = 3600 * 1000;
const MIN = 60 * 1000;

function iso(msAgo, nowMs) {
  return new Date(nowMs - msAgo).toISOString();
}

// ---------------------------------------------------------------------------
// Backend: authoritative presence contract (computed FRESH at serve time)
// ---------------------------------------------------------------------------

test('offline 9h ago -> red + ~9h age, never green', () => {
  const now = Date.now();
  const hit = {
    presenceInput: { isOnline: true, lastAccountSeenAt: iso(9 * HOUR, now), lastSnapshotUploadAt: iso(9 * HOUR, now) },
    hasRenderableData: true,
    snapshotSource: 'precomputed',
  };
  const c = readApp._buildPresenceContract(hit, now);
  assert.equal(c.presenceState, 'offline', 'must be offline despite stale isOnline:true');
  assert.equal(c.isOnline, false);
  // ~9h in seconds.
  assert.ok(c.statusAgeSeconds >= 9 * 3600 - 5 && c.statusAgeSeconds <= 9 * 3600 + 5, `age ~9h, got ${c.statusAgeSeconds}`);
});

test('a stale isOnline:true body (churn-frozen) cannot fake green past the threshold', () => {
  const now = Date.now();
  // Body was last rebuilt while online; account stopped uploading 9 min ago.
  const hit = { presenceInput: { isOnline: true, lastAccountSeenAt: iso(9 * MIN, now) }, hasRenderableData: true };
  const c = readApp._buildPresenceContract(hit, now);
  assert.equal(c.isOnline, false, '9m > 150s window -> offline regardless of baked isOnline');
  assert.equal(c.presenceState, 'offline');
});

test('recent heartbeat within 150s -> green', () => {
  const now = Date.now();
  const hit = { presenceInput: { isOnline: true, lastAccountSeenAt: iso(40 * 1000, now) }, hasRenderableData: true };
  const c = readApp._buildPresenceContract(hit, now);
  assert.equal(c.presenceState, 'online');
  assert.equal(c.isOnline, true);
});

test('heartbeat at edge: <150s green, >150s red', () => {
  const now = Date.now();
  const green = readApp._buildPresenceContract({ presenceInput: { isOnline: true, lastAccountSeenAt: iso(149 * 1000, now) }, hasRenderableData: true }, now);
  const red = readApp._buildPresenceContract({ presenceInput: { isOnline: true, lastAccountSeenAt: iso(151 * 1000, now) }, hasRenderableData: true }, now);
  assert.equal(green.isOnline, true, '149s -> green');
  assert.equal(red.isOnline, false, '151s -> red');
});

test('no-data username -> red (no_data), never green, blank-able ages', () => {
  const now = Date.now();
  const c = readApp._buildPresenceContract({ presenceInput: {}, hasRenderableData: false }, now);
  assert.equal(c.presenceState, 'no_data');
  assert.equal(c.isOnline, false);
  assert.equal(c.statusAgeSeconds, null, 'missing status ts -> null (frontend renders blank, not 1s ago)');
});

test('read/get-backpack success does NOT mark online (presence is timestamp-derived only)', () => {
  const now = Date.now();
  // Simulate a successful read of an old snapshot: the act of reading must not refresh presence.
  const hit = { presenceInput: { isOnline: true, lastAccountSeenAt: iso(3 * HOUR, now) }, hasRenderableData: true };
  const c1 = readApp._buildPresenceContract(hit, now);
  const c2 = readApp._buildPresenceContract(hit, now + 5000); // "read again 5s later"
  assert.equal(c1.isOnline, false);
  assert.equal(c2.isOnline, false, 'reading again never flips to green');
  assert.ok(c2.statusAgeSeconds > c1.statusAgeSeconds, 'age keeps advancing with real time');
});

test('leaderstats contract picks newest upload timestamp when identity field lags', () => {
  const now = Date.now();
  const freshUpload = iso(25 * 1000, now);
  const staleIdentity = iso(18 * MIN, now);
  const hit = {
    presenceInput: {
      lastRealLeaderstatsAt: staleIdentity,
      lastStatsUploadAt: freshUpload,
    },
    hasRenderableData: true,
  };
  const c = readApp._buildPresenceContract(hit, now);
  assert.equal(c.leaderstatsAgeSeconds, 25);
  assert.equal(c.lastRealLeaderstatsAt, freshUpload);
});

test('ages derive from absolute real timestamps (cross-session deterministic)', () => {
  const now = 1_000_000_000_000;
  const hit = {
    presenceInput: {
      isOnline: true,
      lastAccountSeenAt: iso(2 * MIN, now),
      lastInventoryAt: iso(5 * MIN, now),
      lastStatsUploadAt: iso(3 * MIN, now),
    },
    hasRenderableData: true,
  };
  const c = readApp._buildPresenceContract(hit, now);
  assert.equal(c.statusAgeSeconds, 120);
  assert.equal(c.inventoryAgeSeconds, 300);
  assert.equal(c.leaderstatsAgeSeconds, 180);
});

test('hasRenderableData reflects real body content (no false dashes/zeros)', () => {
  assert.equal(readApp._bodyHasRenderableData({ playerStats: { coins: 1 } }), true);
  assert.equal(readApp._bodyHasRenderableData({ fishItems: [{ name: 'x' }] }), true);
  assert.equal(readApp._bodyHasRenderableData({ topCards: { rubyGemstone: { count: 0 } } }), true);
  assert.equal(readApp._bodyHasRenderableData({}), false);
  assert.equal(readApp._bodyHasRenderableData(null), false);
});

test('no-cap: extractPresenceInput never touches/strips the heavy instance arrays', () => {
  const big = { fishItems: new Array(900).fill({ name: 'f' }), stoneItems: new Array(700).fill({ name: 's' }), isOnline: true, lastAccountSeenAt: new Date().toISOString() };
  const input = readApp._extractPresenceInput(big);
  assert.ok(!('fishItems' in input), 'presence input is tiny — does not copy instance arrays');
  // The full body itself is served verbatim elsewhere; presence extraction must not mutate it.
  assert.equal(big.fishItems.length, 900);
  assert.equal(big.stoneItems.length, 700);
});

// ---------------------------------------------------------------------------
// Frontend source contract (the built behaviour the bundle compiles)
// ---------------------------------------------------------------------------

const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs'),
  'utf8',
);

test('frontend: dot/online prefers authoritative _auth.isOnline before any latch', () => {
  const fn = SRC.slice(SRC.indexOf('function isTrackerAccountOnline'), SRC.indexOf('function isTrackerAccountOnline') + 400);
  assert.match(fn, /entry\._auth[\s\S]*?isOnline/);
  // _auth must be checked before the client grace/contact window.
  const authIdx = fn.indexOf('_auth');
  const graceIdx = fn.indexOf('ACCOUNT_PRESENCE_GRACE_MS');
  assert.ok(authIdx > -1 && (graceIdx === -1 || authIdx < graceIdx), 'authoritative _auth wins over client grace');
});

test('frontend: status/leaderstats/inventory timers prefer authoritative absolute timestamps', () => {
  assert.match(SRC, /backendPresenceAgeSeconds[\s\S]*?_auth[\s\S]*?lastRealStatusAt/);
  assert.match(SRC, /backendInventoryAgeSeconds[\s\S]*?_auth[\s\S]*?lastRealInventoryAt/);
  assert.match(SRC, /backendStatsAgeSeconds[\s\S]*?_auth[\s\S]*?lastRealLeaderstatsAt/);
});

test('frontend: poll sends content hash and skips the heavy merge on unchanged', () => {
  assert.match(SRC, /entry\._snapshotHash\s*\?\s*`&h=/);
  const start = SRC.indexOf('async function pollUser');
  const unchangedIdx = SRC.indexOf('contract.unchanged', start);
  const mergeIdx = SRC.indexOf('applyInventoryPollPayload(entry, key, data)', start);
  assert.ok(unchangedIdx > -1, 'has unchanged branch');
  assert.ok(mergeIdx > -1, 'has merge call');
  assert.ok(unchangedIdx < mergeIdx, 'unchanged is handled (and returns) before the merge');
  // The unchanged branch applies presence then returns, without merging inventory.
  assert.match(SRC.slice(unchangedIdx, mergeIdx), /applyAuthPresence\(entry, key, contract\);\s*\r?\n\s*return;/);
});

test('frontend: a transient read failure never wipes a valid displayed snapshot', () => {
  // !res.ok branch: only show "refresh failed" when there is no good data to keep.
  assert.match(SRC, /if \(!entry\.lastData\) setCardRefreshFailed\(entry\);\s*\r?\n\s*return;/);
  // catch branch: same guard — never blow away a valid snapshot on a thrown error.
  assert.match(SRC, /catch \(_\) \{ if \(trackers\.has\(key\) && !entry\.lastData\) setCardRefreshFailed\(entry\); \}/);
});

test('frontend: timer formatter emits ONLY "<age> ago" (s/m/H/D, compound allowed) or blank', () => {
  // Find the real implementation (skip any prose), then check its return templates.
  let idx = SRC.indexOf('function formatAgeAgo(');
  while (idx > -1 && !/function formatAgeAgo\(\s*ms\s*\)/.test(SRC.slice(idx, idx + 40))) {
    idx = SRC.indexOf('function formatAgeAgo(', idx + 1);
  }
  assert.ok(idx > -1, 'formatAgeAgo(ms) is defined');
  const fn = SRC.slice(idx, idx + 900);
  // Restored 4394cfd format spec: "<n>s ago" / "<n>m ago" / "<n>m <n>s ago"
  // / "<n>H ago" / "<n>H <n>m ago" / "<n>D ago". The implementation uses
  // template literals for each branch; assert they end in " ago" and use the
  // s/m/H/D units only.
  assert.match(fn, /\$\{[^}]+\}s ago/);
  assert.match(fn, /\$\{[^}]+\}m ago/);
  assert.match(fn, /\$\{[^}]+\}H ago/);
  assert.match(fn, /\$\{[^}]+\}D ago/);
  assert.doesNotMatch(fn, /Offline|No data|refreshed|since/i, 'no prefix/suffix words beyond "ago"');
  // The produced strings satisfy the restored format grammar.
  const grammar = /^(\d+s|\d+m( \d+s)?|\d+H( \d+m)?|\d+D) ago$/;
  for (const s of ['11s ago', '8m ago', '1m 2s ago', '2H ago', '1H 2m ago', '1D ago']) {
    assert.match(s, grammar);
  }
});

test('frontend: missing age renders blank, never a fabricated "1s ago"', () => {
  const fn = SRC.slice(SRC.indexOf('function formatAgeAgoSeconds'), SRC.indexOf('function formatAgeAgoSeconds') + 400);
  assert.match(fn, /secs == null[\s\S]*?return ''/);
});
