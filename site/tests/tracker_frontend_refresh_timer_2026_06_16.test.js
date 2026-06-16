'use strict';

// /tracker UX: the visible Status sync timer must follow the FRONTEND
// receive/refresh time, not the backend snapshot/heartbeat age. After a page
// refresh that receives a backend snapshot already 6 minutes old, the visible
// timer must read ~1s, count up from frontend receive time, reset on a
// successful renderable poll, and NOT reset on a failed/empty poll. The
// online/offline dot must still be driven by real upload age, and the backend
// age must remain available for debug.

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');

function readSource() {
  return fs.readFileSync(SOURCE_PATH, 'utf8');
}

// Extract the self-contained timer/label helpers and run them against a
// controllable clock so we can assert the visible timer math directly.
function makeTimerEnv(source) {
  const open = source.indexOf('  function formatPresenceDurationLabel(secs) {');
  const close = source.indexOf('  function formatStatsUploadDurationText(entry) {');
  assert.ok(open > 0 && close > open, 'presence timer helper block missing from source');
  const block = source.slice(open, close);
  const clock = { now: 0 };
  const sandbox = {
    Math,
    Number,
    String,
    Date: { now: () => clock.now },
  };
  const script = `(function(){
    const pad2 = (n) => String(n).padStart(2, '0');
${block}
    return {
      markEntryFrontendRefreshed,
      getEntryFrontendRefreshAgeMs,
      formatFrontendRefreshAgeText,
      formatPresenceStatusText,
    };
  })()`;
  const api = vm.runInNewContext(script, sandbox, { filename: 'tracker-timer-helpers.js' });
  return { api, setNow: (ms) => { clock.now = ms; } };
}

describe('tracker frontend refresh timer (2026-06-16)', () => {
  test('visible timer reads ~1s right after a refresh, even with a 6m-old backend snapshot', () => {
    const { api, setNow } = makeTimerEnv(readSource());
    const entry = {};
    // Backend snapshot is 6 minutes old, but the browser receives it "now".
    setNow(12 * 60 * 1000);
    api.markEntryFrontendRefreshed(entry);
    assert.equal(api.formatPresenceStatusText(entry), '1s');
  });

  test('visible timer counts up from frontend receive time', () => {
    const { api, setNow } = makeTimerEnv(readSource());
    const entry = {};
    setNow(100000);
    api.markEntryFrontendRefreshed(entry);
    setNow(105000); // +5s, no new poll
    assert.equal(api.formatPresenceStatusText(entry), '5s');
    setNow(100000 + 90000); // +90s -> "1m 30s"
    assert.equal(api.formatPresenceStatusText(entry), '1m 30s');
  });

  test('a successful poll resets the visible timer back to ~1s', () => {
    const { api, setNow } = makeTimerEnv(readSource());
    const entry = {};
    setNow(0);
    api.markEntryFrontendRefreshed(entry);
    setNow(8000); // 8s later
    assert.equal(api.formatPresenceStatusText(entry), '8s');
    api.markEntryFrontendRefreshed(entry); // successful poll
    assert.equal(api.formatPresenceStatusText(entry), '1s');
  });

  test('a failed/empty poll (no mark) does not reset the timer', () => {
    const { api, setNow } = makeTimerEnv(readSource());
    const entry = {};
    setNow(0);
    api.markEntryFrontendRefreshed(entry);
    setNow(7000);
    // Failed poll -> markEntryFrontendRefreshed is NOT called.
    assert.equal(api.formatPresenceStatusText(entry), '7s');
  });

  test('no in-browser refresh recorded yet -> empty (caller falls back to default)', () => {
    const { api, setNow } = makeTimerEnv(readSource());
    setNow(50000);
    assert.equal(api.formatPresenceStatusText({}), '');
    assert.equal(api.getEntryFrontendRefreshAgeMs({}), null);
  });
});

describe('tracker frontend refresh timer source wiring', () => {
  test('Status sync text is driven by the frontend refresh helper', () => {
    const src = readSource();
    assert.match(src, /function formatPresenceStatusText\(entry\) \{[\s\S]*return formatFrontendRefreshAgeText\(entry\);/);
  });

  test('timer resets via signature-gated maybeResetSectionTimers AFTER the merge (sees displayed data)', () => {
    const src = readSource();
    const fn = src.indexOf('function applyInventoryPollPayload(entry, key, data) {');
    assert.ok(fn > 0, 'applyInventoryPollPayload missing');
    const body = src.slice(fn, fn + 4200);
    const mergeIdx = body.indexOf('entry.lastData = mergePreservedInventorySnapshot(entry.lastData, data);');
    const resetIdx = body.indexOf('maybeResetSectionTimers(entry);');
    assert.ok(mergeIdx >= 0, 'snapshot merge missing');
    assert.ok(resetIdx > mergeIdx, 'timer reset must run after the merge so it reflects the displayed dataset');
    // The gated mark lives inside maybeResetSectionTimers, behind a signature change.
    assert.match(src, /function maybeResetSectionTimers\(entry\) \{[\s\S]*?if \(sig !== entry\._trackerDisplaySig\)[\s\S]*?markEntryFrontendRefreshed\(entry\)/);
  });

  test('exactly one call-site resets the timer (status-only poll path does not)', () => {
    const src = readSource();
    const calls = src.match(/markEntryFrontendRefreshed\(entry\);/g) || [];
    assert.equal(calls.length, 1, 'frontend refresh must reset from a single renderable poll call-site');
    const statusFn = src.indexOf('function applyAccountStatusPayload(payload) {');
    const statusEnd = src.indexOf('function ', statusFn + 10);
    const statusBody = src.slice(statusFn, statusEnd);
    assert.ok(!/markEntryFrontendRefreshed/.test(statusBody), 'status-only poll must not reset the visible timer');
  });

  test('online/offline dot still driven by real upload age, not the frontend timer', () => {
    const src = readSource();
    assert.match(src, /function entryConnectionFreshness\(entry\) \{[\s\S]*isTrackerAccountOnline\(entry, Date\.now\(\)\)/);
  });

  test('backend presence age remains available for debug/proof', () => {
    const src = readSource();
    assert.match(src, /function backendPresenceAgeSeconds\(entry\)/);
    assert.match(src, /data-backend-presence-age/);
  });

  test('frontend refresh timestamp is in-memory only (never persisted to localStorage)', () => {
    const src = readSource();
    assert.ok(!/localStorage[\s\S]{0,120}_frontendRefreshAt/.test(src), '_frontendRefreshAt must not be persisted');
    assert.ok(!/_frontendRefreshAt[\s\S]{0,120}localStorage/.test(src), '_frontendRefreshAt must not be read from localStorage');
  });
});
