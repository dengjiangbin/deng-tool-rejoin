'use strict';

// 2026-06-18 — Authoritative red/green status + "X ago" timer contract.
//
// Replaces the prior "frontend-receive timer resets to 1s on refresh" design.
// The visible timer is now the TRUE age since the last real backend tracker
// event, rendered ONLY as "<n><unit> ago" (one unit), and is identical across
// devices / sessions / refreshes because it is derived purely from absolute
// backend timestamps — never the browser receive/page-open time. Presence is
// GREEN only while a real heartbeat is within the tight 150s online threshold.

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const manifest = require('../src/inventoryAssetManifest.json');
const BUNDLE_JS = path.join(__dirname, '..', 'public', 'assets', manifest.js);

const {
  deriveAccountPresenceStatus,
  ACCOUNT_ONLINE_THRESHOLD_MS,
} = require('../src/trackerAccountPresence');
const { buildTrackerAccountSummary } = require('../src/trackerAccountSummary');

function readSource() {
  return fs.readFileSync(SOURCE_PATH, 'utf8');
}

// Extract the self-contained formatAgeAgo / formatAgeAgoSeconds helpers and run
// them against a controllable clock.
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

function iso(msAgo) {
  return new Date(Date.now() - msAgo).toISOString();
}

describe('authoritative timer text — only "<age> ago", one unit', () => {
  const { api } = makeAgeEnv(readSource());

  test('exact seconds/minutes/hours/days formatting', () => {
    assert.equal(api.formatAgeAgo(11 * 1000), '11s ago');
    assert.equal(api.formatAgeAgo(8 * 60 * 1000), '8m ago');
    assert.equal(api.formatAgeAgo(2 * 3600 * 1000), '2h ago');
    assert.equal(api.formatAgeAgo(26 * 3600 * 1000), '1d ago');
    assert.equal(api.formatAgeAgo(9 * 3600 * 1000), '9h ago');
  });

  test('floor of zero shows "1s ago" (never blank for a real event)', () => {
    assert.equal(api.formatAgeAgo(0), '1s ago');
    assert.equal(api.formatAgeAgo(500), '1s ago');
  });

  test('every output ends with " ago", contains exactly one unit and no extra words', () => {
    const samples = [0, 1000, 59000, 60000, 3599000, 3600000, 86399000, 86400000, 9 * 3600 * 1000];
    for (const ms of samples) {
      const label = api.formatAgeAgo(ms);
      assert.match(label, /^\d+[smhd] ago$/, `bad timer format: "${label}"`);
      // no compound units like "9h 04m", no labels, no parentheses
      assert.doesNotMatch(label, /\d+[smhd]\s+\d+[smhd]/);
      assert.doesNotMatch(label, /[()]|offline|online|updated|since|seen|inventory|browser/i);
    }
  });

  test('formatAgeAgoSeconds rejects null/negative -> empty (no fake fresh timer)', () => {
    assert.equal(api.formatAgeAgoSeconds(null), '');
    assert.equal(api.formatAgeAgoSeconds(-5), '');
    assert.equal(api.formatAgeAgoSeconds(undefined), '');
    assert.equal(api.formatAgeAgoSeconds(125), '2m ago');
  });
});

describe('cross-session determinism (D2)', () => {
  // Same backend age + same wall clock in two independent "browser sessions"
  // must render the identical timer text and identical online/offline.
  test('two sessions, same backend timestamps -> identical timer + status', () => {
    const src = readSource();
    const sessionA = makeAgeEnv(src);
    const sessionB = makeAgeEnv(src);
    const backendAgeSeconds = 9 * 3600; // last real event 9h ago (authoritative)
    // Different page-open times must NOT matter — only the backend age does.
    sessionA.setNow(1_000_000);
    sessionB.setNow(9_999_999);
    const a = sessionA.api.formatAgeAgoSeconds(backendAgeSeconds);
    const b = sessionB.api.formatAgeAgoSeconds(backendAgeSeconds);
    assert.equal(a, '9h ago');
    assert.equal(b, '9h ago');
    assert.equal(a, b);

    // Backend-derived presence is identical too (offline at 9h).
    const offline = { isOnline: true, lastAccountSeenAt: iso(9 * 3600 * 1000) };
    assert.equal(deriveAccountPresenceStatus(offline).accountPresenceLive, false);
  });
});

describe('authoritative wiring in source + compiled bundle', () => {
  const src = readSource();
  const bundle = fs.readFileSync(BUNDLE_JS, 'utf8');

  test('the three section timers render the backend age via formatAgeAgo', () => {
    assert.match(src, /function formatPresenceStatusText\(entry\) \{[\s\S]*?return formatAgeAgoSeconds\(backendPresenceAgeSeconds\(entry\)\);/);
    assert.match(src, /function formatStatsUploadDurationText\(entry\) \{[\s\S]*?return formatAgeAgoSeconds\(backendStatsAgeSeconds\(entry\)\);/);
    assert.match(src, /function formatEntrySyncStatusText\(entry\) \{[\s\S]*?return formatAgeAgoSeconds\(backendInventoryAgeSeconds\(entry\)\);/);
  });

  test('no fake "1s" fallback anywhere in the status text path', () => {
    assert.doesNotMatch(src, /\|\|\s*'1s'/);
    assert.doesNotMatch(src, /syncEl\.textContent\s*=\s*'1s'/);
  });

  test('frontend online threshold mirrors backend tight 150s window', () => {
    assert.match(src, /ACCOUNT_PRESENCE_GRACE_MS\s*=\s*150\s*\*\s*1000/);
  });

  test('compiled bundle ships formatAgeAgo and the authoritative wiring', () => {
    assert.match(bundle, /formatAgeAgo/);
    assert.match(bundle, /backendPresenceAgeSeconds/);
    assert.doesNotMatch(bundle, /\|\|\s*"1s"/);
  });
});

describe('tight authoritative presence (red/green only)', () => {
  test('online threshold constant is 150s', () => {
    assert.equal(ACCOUNT_ONLINE_THRESHOLD_MS, 150000);
  });

  test('real heartbeat within 150s -> green', () => {
    const r = deriveAccountPresenceStatus({ isOnline: true, lastAccountSeenAt: iso(149000) });
    assert.equal(r.accountPresenceLive, true);
    assert.equal(r.accountPresenceStatus, 'online');
  });

  test('no contact past 150s -> red (offline), not green', () => {
    const r = deriveAccountPresenceStatus({ isOnline: true, lastAccountSeenAt: iso(151000) });
    assert.equal(r.accountPresenceLive, false);
    assert.equal(r.accountPresenceReason, 'account_offline_timeout');
  });

  test('offline since 9h ago -> red (not green, not recent)', () => {
    const r = deriveAccountPresenceStatus({ isOnline: true, lastAccountSeenAt: iso(9 * 3600 * 1000) });
    assert.equal(r.accountPresenceLive, false);
  });

  test('explicit confirmed offline within window -> red', () => {
    const r = deriveAccountPresenceStatus({ isOnline: false, lastAccountSeenAt: iso(20000), lastOfflineAt: iso(20000) });
    assert.equal(r.accountPresenceLive, false);
    assert.equal(r.accountPresenceReason, 'client_offline');
  });

  test('a non-status upload lane within 150s does NOT keep a confirmed-offline green', () => {
    // inventory lane refreshed seen 40s ago but loader confirmed offline 30s ago
    const r = deriveAccountPresenceStatus({
      isOnline: false,
      lastInventoryAt: iso(40000),
      lastAccountSeenAt: iso(30000),
      lastOfflineAt: iso(30000),
    });
    assert.equal(r.accountPresenceLive, false);
  });

  test('account-status summary uses the tight 150s threshold', () => {
    const tracked = [{ robloxUsername: 'StaleGuy', robloxUserId: 555 }];
    const store = {
      staleguy: { username: 'StaleGuy', userId: 555, isOnline: true, lastAccountSeenAt: iso(200000) },
    };
    const summary = buildTrackerAccountSummary(tracked, store);
    assert.equal(summary.onlineCount, 0);
    assert.equal(summary.accounts[0].accountPresenceLive, false);
    assert.equal(summary.freshnessWindowMs, 150000);
  });
});
