'use strict';

// 2026-06-19 — updated for server-lane timestamp timer model.
//
//   Visible timers (status / leaderstats / inventory):
//     * Render age from read-API _auth.lastReal*At (now - serverTimestamp).
//     * Reset when the real lane timestamp advances, even if content is unchanged.
//     * Never reset from page load, login, poll receive time, or content signature.
//   Online/offline dot:
//     * Driven only by backend status via isTrackerAccountOnline.
//   Preservation:
//     * mergePreservedInventorySnapshot keeps offline username data on status-only payloads.

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const readSource = () => fs.readFileSync(SOURCE_PATH, 'utf8');

describe('Layer 1 — server-lane timestamp visible timers', () => {
  const src = readSource();

  test('maybeResetSectionTimers is a no-op (timers are not signature-gated)', () => {
    assert.match(src, /function maybeResetSectionTimers\(_entry\) \{ \/\* no-op/);
  });

  test('display formatters derive from backend*AgeSeconds, not frontend refresh', () => {
    for (const [name, backendFn] of [
      ['formatPresenceStatusText', 'backendPresenceAgeSeconds'],
      ['formatStatsUploadDurationText', 'backendStatsAgeSeconds'],
      ['formatEntrySyncStatusText', 'backendInventoryAgeSeconds'],
    ]) {
      const fn = src.match(new RegExp(`function ${name}\\(entry\\) \\{[\\s\\S]*?\\n  \\}`))[0];
      assert.match(fn, new RegExp(backendFn));
      assert.doesNotMatch(fn, /formatFrontendRefreshAgeText|formatInventoryRefreshAgeText|formatLeaderstatsRefreshAgeText/);
    }
  });

  test('unchanged inventory content with advanced server ts resets age (functional)', () => {
    const block = src.match(/function authAgeSecondsFromTs\(ts\) \{[\s\S]*?\n  \}/)[0]
      + src.match(/function backendInventoryAgeSeconds\(entry\) \{[\s\S]*?\n  \}/)[0]
      + src.match(/function formatCompactAgeAgoSeconds\(secs\) \{[\s\S]*?\n  \}/)[0]
      + src.match(/function formatInventoryUploadLabel\(entry\) \{[\s\S]*?\n  \}/)[0];
    const api = vm.runInNewContext(`(function(){
      function liveSecondsSinceInventorySuccess(){return null;}
      ${block}
      return { formatInventoryUploadLabel };
    })()`, { Math, Number, Date }, { filename: 'inv-age.js' });
    const now = Date.now();
    const entry = { _auth: { lastRealInventoryAt: new Date(now - 240_000).toISOString() } };
    assert.equal(api.formatInventoryUploadLabel(entry), '4m ago');
    entry._auth.lastRealInventoryAt = new Date(now - 2000).toISOString();
    assert.equal(api.formatInventoryUploadLabel(entry), '2s ago');
  });
});

describe('Layer 2 — green/red dot is decided by backend status, not the visible timer', () => {
  const src = readSource();

  test('isTrackerAccountOnline checks entry._auth.isOnline first (read-API serve-time)', () => {
    const fn = src.match(/function isTrackerAccountOnline\(entry, nowMs\) \{[\s\S]*?\n  \}/)[0];
    assert.match(fn, /entry\._auth && typeof entry\._auth\.isOnline === 'boolean'/);
  });

  test('the online predicate path never reads the frontend-receive timer fields', () => {
    const fn = src.match(/function isTrackerAccountOnline\(entry, nowMs\) \{[\s\S]*?\n  \}/)[0];
    assert.doesNotMatch(fn, /_frontendRefreshAt/);
    assert.doesNotMatch(fn, /_leaderstatsFrontendRefreshAt/);
    assert.doesNotMatch(fn, /_inventoryFrontendRefreshAt/);
  });

  test('ACCOUNT_PRESENCE_GRACE_MS hard offline window is 150s', () => {
    assert.match(src, /ACCOUNT_PRESENCE_GRACE_MS\s*=\s*150\s*\*\s*1000/);
  });
});

describe('Preservation — offline username keeps leaderstats and inventory', () => {
  const src = readSource();

  test('mergePreservedInventorySnapshot copies INVENTORY_PRESERVE_KEYS + STATS_PRESERVE_KEYS when incoming has no rows', () => {
    const fn = src.match(/function mergePreservedInventorySnapshot\(previous, incoming\) \{[\s\S]*?\n  \}/)[0];
    assert.match(fn, /INVENTORY_PRESERVE_KEYS/);
    assert.match(fn, /STATS_PRESERVE_KEYS/);
    assert.match(fn, /entryHasInventoryRows\(previous\) && !entryHasInventoryRows\(incoming\)/);
  });
});
