'use strict';

// P0 add-on 2026-06-19 — timers follow real /tracker lane upload timestamps;
// inventory indicator shows compact age text only.

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const READ_APP = path.join(__dirname, '..', 'src', 'trackerReadApp.js');
const readSource = () => fs.readFileSync(SOURCE_PATH, 'utf8');

const INVENTORY_AGE_PATTERN = /^\d+(s|m|h|d) ago$/;
const FORBIDDEN_INVENTORY_WORDS = /\b(inventory|stale|updated|fresh|waiting|online|offline)\b/i;

function extractFn(source, name) {
  const m = source.match(new RegExp(`function ${name}\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n  \\}`));
  assert.ok(m, `${name} missing`);
  return m[0];
}

function makeLaneAgeEnv(source) {
  const names = [
    'formatAgeAgo', 'formatAgeAgoSeconds', 'formatCompactAgeAgoSeconds', 'authAgeSecondsFromTs',
    'backendPresenceAgeSeconds', 'backendStatsAgeSeconds', 'backendInventoryAgeSeconds',
    'formatBackendAgeText', 'formatPresenceStatusText', 'formatStatsUploadDurationText',
    'formatEntrySyncStatusText', 'formatInventoryUploadLabel', 'laneTimestampAdvanced',
  ];
  const blocks = names.map((n) => extractFn(source, n));
  return new Function(`
    function liveSecondsSinceStatusSuccess(){return null;}
    function entryStatusSuccessTimestamp(){return null;}
    function syncAgeSeconds(){return null;}
    function liveSecondsSinceStatsSuccess(){return null;}
    function liveSecondsSinceInventorySuccess(){return null;}
    ${blocks.join('\n')}
    return {
      authAgeSecondsFromTs, backendPresenceAgeSeconds, backendStatsAgeSeconds,
      backendInventoryAgeSeconds, formatPresenceStatusText, formatStatsUploadDurationText,
      formatEntrySyncStatusText, formatInventoryUploadLabel, formatCompactAgeAgoSeconds,
      laneTimestampAdvanced,
    };
  `)();
}

describe('server lane timer reset — real upload timestamp is source of truth', () => {
  const src = readSource();
  const fns = makeLaneAgeEnv(src);

  test('status timer resets when lastRealStatusAt advances (unchanged content)', () => {
    const now = Date.now();
    const oldTs = new Date(now - 120_000).toISOString();
    const newTs = new Date(now - 3000).toISOString();
    const entry = { _auth: { lastRealStatusAt: oldTs } };
    assert.equal(fns.formatPresenceStatusText(entry), '2m ago');
    entry._auth.lastRealStatusAt = newTs;
    assert.equal(fns.formatPresenceStatusText(entry), '3s ago');
  });

  test('leaderstats timer resets when lastRealLeaderstatsAt advances', () => {
    const now = Date.now();
    const entry = { _auth: { lastRealLeaderstatsAt: new Date(now - 90_000).toISOString() } };
    assert.equal(fns.formatStatsUploadDurationText(entry), '1m 30s ago');
    entry._auth.lastRealLeaderstatsAt = new Date(now - 2000).toISOString();
    assert.equal(fns.formatStatsUploadDurationText(entry), '2s ago');
  });

  test('fish/item/detail inventory timer resets when lastRealInventoryAt advances', () => {
    const now = Date.now();
    const entry = { _auth: { lastRealInventoryAt: new Date(now - 180_000).toISOString() } };
    assert.equal(fns.formatEntrySyncStatusText(entry), '3m ago');
    entry._auth.lastRealInventoryAt = new Date(now - 8000).toISOString();
    assert.equal(fns.formatEntrySyncStatusText(entry), '8s ago');
  });

  test('inventory unchanged but timestamp advanced still resets timer', () => {
    const now = Date.now();
    const prev = { lastRealInventoryAt: new Date(now - 300_000).toISOString(), inventoryRevision: 10 };
    const next = { lastRealInventoryAt: new Date(now - 4000).toISOString(), inventoryRevision: 11 };
    assert.equal(fns.laneTimestampAdvanced(prev, next, 'inventory'), true);
    const entry = { _auth: next };
    assert.equal(fns.formatInventoryUploadLabel(entry), '4s ago');
  });

  test('content hash / signature gate does not block timer reset', () => {
    assert.match(src, /function maybeResetSectionTimers\(_entry\) \{ \/\* no-op/);
    assert.doesNotMatch(extractFn(src, 'maybeResetSectionTimers'), /buildInventorySignature/);
  });

  test('page load / frontend receive time does not reset timer', () => {
    const now = Date.now();
    const ts = new Date(now - 60_000).toISOString();
    const entry = { _frontendRefreshAt: now, _auth: { lastRealInventoryAt: ts } };
    assert.equal(fns.formatInventoryUploadLabel(entry), '1m ago');
  });

  test('applyAuthPresence refreshes indicators on unchanged poll path', () => {
    const poll = extractFn(src, 'pollUser');
    assert.match(poll, /if \(contract && contract\.unchanged\)/);
    assert.match(poll, /applyAuthPresence\(entry, key, contract\)/);
    const apply = extractFn(src, 'applyAuthPresence');
    assert.match(apply, /refreshEntryTableSyncDisplay\(entry, key\)/);
    assert.match(apply, /refreshEntrySyncDisplay\(entry\)/);
    assert.match(apply, /laneTimestampAdvanced/);
  });

  test('8793 read API exposes lastReal* lane timestamps in headers', () => {
    const readSrc = fs.readFileSync(READ_APP, 'utf8');
    assert.match(readSrc, /X-DENG-Last-Real-Status-At/);
    assert.match(readSrc, /X-DENG-Last-Real-Inventory-At/);
    assert.match(readSrc, /X-DENG-Last-Real-Leaderstats-At/);
    assert.match(readSrc, /unchanged: true/);
  });
});

describe('inventory indicator — timer text only', () => {
  const src = readSource();
  const fns = makeLaneAgeEnv(src);

  test('formatInventoryUploadLabel renders compact age pattern only', () => {
    const now = Date.now();
    const cases = [
      [8000, '8s ago'],
      [59_000, '59s ago'],
      [60_000, '1m ago'],
      [180_000, '3m ago'],
    ];
    for (const [offsetMs, expected] of cases) {
      const entry = { _auth: { lastRealInventoryAt: new Date(now - offsetMs).toISOString() } };
      const text = fns.formatInventoryUploadLabel(entry);
      assert.equal(text, expected, `offset ${offsetMs}`);
      assert.match(text, INVENTORY_AGE_PATTERN);
    }
  });

  test('inventory label has no forbidden words', () => {
    const now = Date.now();
    const entry = { _auth: { lastRealInventoryAt: new Date(now - 5000).toISOString() } };
    const text = fns.formatInventoryUploadLabel(entry);
    assert.doesNotMatch(text, FORBIDDEN_INVENTORY_WORDS);
  });

  test('inventory badge HTML has no dot/circle element', () => {
    const html = extractFn(src, 'buildSectionUploadIndicatorHtml');
    assert.doesNotMatch(html, /data-inventory-upload-dot|status-dot/);
    assert.match(html, /data-inventory-upload-text/);
    assert.match(html, /is-neutral/);
  });

  test('patchInventoryUploadIndicatorDom removes dot and green/red classes', () => {
    const patch = extractFn(src, 'patchInventoryUploadIndicatorDom');
    assert.match(patch, /dotEl\.remove\(\)/);
    assert.match(patch, /classList\.remove\('is-live', 'is-stale'/);
    assert.doesNotMatch(patch, /classList\.add\('is-live'/);
    assert.doesNotMatch(patch, /classList\.add\('is-stale'/);
    assert.match(patch, /formatInventoryUploadLabel/);
  });

  test('source does not prefix inventory age with Inventory updated/stale/waiting', () => {
    const labelFn = extractFn(src, 'formatInventoryUploadLabel');
    assert.doesNotMatch(labelFn, /Inventory updated|Inventory stale|waiting for upload/i);
    assert.match(labelFn, /formatCompactAgeAgoSeconds/);
  });

  test('no duplicate inventory indicator helpers on bulk panel', () => {
    assert.doesNotMatch(src, /#bulkInventoryPanel[\s\S]{0,600}data-inventory-upload-indicator/);
  });
});

describe('denghub2 lane proof wiring (live curl optional)', () => {
  test('frontend stores lane timer proof on applyAuthPresence', () => {
    const apply = extractFn(readSource(), 'applyAuthPresence');
    assert.match(apply, /_laneTimerProof/);
    assert.match(apply, /inventoryAdvanced/);
  });
});
