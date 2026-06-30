'use strict';

// 2026-06-18, restored 2026-06-19 — Two-layer contract (restore_4394cfd plan):
//
//   Layer 1 (visible timer UX) is the 4394cfd FRONTEND-RECEIVE model. The
//   visible "X ago" Status / leaderstats / inventory timers measure how long
//   since THIS browser actually rendered fresher displayed data for that
//   section (signature-gated reset). A refresh starts blank and resets to
//   "1s ago" only when the next renderable response actually arrives.
//
//   Layer 2 (online/offline dot) is the AUTHORITATIVE backend status model:
//   green iff a real heartbeat is within the tight 150s online threshold,
//   red otherwise. The visible timer NEVER decides the dot, and the dot is
//   NEVER driven by the frontend-receive time. Backend lane ages remain
//   available for debug (backendPresenceAgeSeconds / backendStatsAgeSeconds /
//   backendInventoryAgeSeconds).

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

describe('Layer 1 visible timer text — "<age> ago" with the restored 4394cfd format', () => {
  const { api } = makeAgeEnv(readSource());

  test('exact seconds/minutes/hours/days formatting (uppercase H/D)', () => {
    assert.equal(api.formatAgeAgo(11 * 1000), '11s ago');
    assert.equal(api.formatAgeAgo(8 * 60 * 1000), '8m ago');
    assert.equal(api.formatAgeAgo(2 * 3600 * 1000), '2H ago');
    assert.equal(api.formatAgeAgo(26 * 3600 * 1000), '1D ago');
    assert.equal(api.formatAgeAgo(9 * 3600 * 1000), '9H ago');
  });

  test('floor of zero shows "1s ago" (never blank for a real event)', () => {
    assert.equal(api.formatAgeAgo(0), '1s ago');
    assert.equal(api.formatAgeAgo(500), '1s ago');
  });

  test('every output ends with " ago" and uses only s/m/H/D units (compound m+s and H+m allowed)', () => {
    const samples = [0, 1000, 59000, 60000, 65000, 3599000, 3600000, 3720000, 86399000, 86400000, 9 * 3600 * 1000];
    for (const ms of samples) {
      const label = api.formatAgeAgo(ms);
      assert.match(label, /^(\d+s|\d+m( \d+s)?|\d+H( \d+m)?|\d+D) ago$/, `bad timer format: "${label}"`);
      assert.doesNotMatch(label, /[()]|offline|online|updated|since|seen|inventory|browser/i);
    }
  });

  test('formatAgeAgoSeconds rejects null/negative -> empty (no fake fresh timer)', () => {
    assert.equal(api.formatAgeAgoSeconds(null), '');
    assert.equal(api.formatAgeAgoSeconds(-5), '');
    assert.equal(api.formatAgeAgoSeconds(undefined), '');
    // 125s = 2m 5s ago (compound, exact remainder).
    assert.equal(api.formatAgeAgoSeconds(125), '2m 5s ago');
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
    assert.equal(a, '9H ago');
    assert.equal(b, '9H ago');
    assert.equal(a, b);

    // Backend-derived presence is identical too (offline at 9h).
    const offline = { isOnline: true, lastAccountSeenAt: iso(9 * 3600 * 1000) };
    assert.equal(deriveAccountPresenceStatus(offline).accountPresenceLive, false);
  });
});

describe('two-layer wiring in source + compiled bundle', () => {
  const src = readSource();
  const bundle = fs.readFileSync(BUNDLE_JS, 'utf8');

  test('Layer 1: the three section visible timers render the real SERVER lane upload age', () => {
    assert.match(src, /function formatPresenceStatusText\(entry\) \{[\s\S]*?return formatBackendAgeText\(backendPresenceAgeSeconds\(entry\)\);/);
    assert.match(src, /function formatStatsUploadDurationText\(entry\) \{[\s\S]*?return formatBackendAgeText\(backendStatsAgeSeconds\(entry\)\);/);
    assert.match(src, /function formatEntrySyncStatusText\(entry\) \{[\s\S]*?return formatBackendAgeText\(backendInventoryAgeSeconds\(entry\)\);/);
  });

  test('Layer 1: formatBackendAgeText renders "X ago" via formatAgeAgoSeconds', () => {
    assert.match(src, /function formatBackendAgeText\(ageSeconds\) \{[\s\S]*?return formatAgeAgoSeconds\(/);
  });

  test('Layer 2: the dot threshold is the tight 150s online window', () => {
    assert.match(src, /ACCOUNT_PRESENCE_GRACE_MS\s*=\s*150\s*\*\s*1000/);
  });

  test('Layer 2: backend lane ages remain available as debug-only helpers', () => {
    assert.match(src, /function backendPresenceAgeSeconds\(entry\)/);
    assert.match(src, /function backendStatsAgeSeconds\(entry\)/);
    assert.match(src, /function backendInventoryAgeSeconds\(entry\)/);
  });

  test('no fake "1s" fallback anywhere in the status text path', () => {
    assert.doesNotMatch(src, /\|\|\s*'1s'/);
    assert.doesNotMatch(src, /syncEl\.textContent\s*=\s*'1s'/);
  });

  test('compiled bundle ships formatAgeAgo and the server-timestamp wiring', () => {
    assert.match(bundle, /formatAgeAgo/);
    assert.match(bundle, /backendPresenceAgeSeconds/);
    assert.match(bundle, /formatBackendAgeText/);
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
