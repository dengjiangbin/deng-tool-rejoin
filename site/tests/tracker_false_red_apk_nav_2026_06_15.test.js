'use strict';

/**
 * Regression coverage for the 2026-06-15 production blockers:
 *   1. False-red account-state indicator (10-minute grace + lane/race protection)
 *   2. APK login: /auth/web-bridge 200 cookie interstitial (no 302 cookie loss)
 *   3. Live Tracker first/default nav across desktop sidebar + mobile tabs + web
 */

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('node:path');

const { deriveAccountPresenceStatus } = require('../src/trackerAccountPresence');

const SRC_DIR = path.join(__dirname, '..', 'src');
const SOURCE_EJS = path.join(SRC_DIR, 'inventory', 'fishit_tracker.source.ejs');
const SIDEBAR_PARTIAL = path.join(__dirname, '..', 'views', 'partials', 'deng-sidebar-nav.ejs');
const manifest = require('../src/inventoryAssetManifest.json');
const BUNDLE_JS = path.join(SRC_DIR, '..', 'public', 'assets', manifest.js);

function iso(msAgo) {
  return new Date(Date.now() - msAgo).toISOString();
}

describe('Blocker 1 — account-state false-red grace (deriveAccountPresenceStatus)', () => {
  test('T=0 successful online upload → green', () => {
    const r = deriveAccountPresenceStatus({ isOnline: true, lastAccountSeenAt: iso(0) });
    assert.equal(r.accountPresenceLive, true);
  });

  test('T=30s no new upload (isOnline:false from a non-status lane, no lastOfflineAt) → still green', () => {
    // This is the production bug: a leaderstats/inventory lane wrote isOnline=false
    // ~30s after the last contact. Within grace and without a confirmed offline
    // snapshot, the account must NOT turn red.
    const r = deriveAccountPresenceStatus({ isOnline: false, lastAccountSeenAt: iso(30000) });
    assert.equal(r.accountPresenceLive, true);
  });

  test('T=2m within grace, no confirmed offline → still green', () => {
    const r = deriveAccountPresenceStatus({ isOnline: false, lastAccountSeenAt: iso(120000) });
    assert.equal(r.accountPresenceLive, true);
  });

  test('T=149s within the tight 150s online window → still green', () => {
    const r = deriveAccountPresenceStatus({ isOnline: false, lastAccountSeenAt: iso(149000) });
    assert.equal(r.accountPresenceLive, true);
  });

  test('T=151s past the 150s online window (online flag, stale) → red (account_offline_timeout)', () => {
    const r = deriveAccountPresenceStatus({ isOnline: true, lastAccountSeenAt: iso(151000) });
    assert.equal(r.accountPresenceLive, false);
    assert.equal(r.accountPresenceReason, 'account_offline_timeout');
  });

  test('confirmed offline (fresh lastOfflineAt is the most recent contact) → red even within grace', () => {
    const r = deriveAccountPresenceStatus({
      isOnline: false,
      lastAccountSeenAt: iso(2000),
      lastOfflineAt: iso(2000),
    });
    assert.equal(r.accountPresenceLive, false);
    assert.equal(r.accountPresenceReason, 'client_offline');
  });

  test('account came back online after a prior offline (newer contact than lastOfflineAt) → green', () => {
    const r = deriveAccountPresenceStatus({
      isOnline: false,
      lastAccountSeenAt: iso(3000),
      lastOfflineAt: iso(300000),
    });
    assert.equal(r.accountPresenceLive, true);
  });

  test('transient 502 upload failure within grace keeps account green', () => {
    const r = deriveAccountPresenceStatus({
      isOnline: true,
      lastAccountSeenAt: iso(20000),
      lastFailureReason: 'server_502',
      lastUploadStatusCodeReturned: 502,
    });
    assert.equal(r.accountPresenceLive, true);
  });
});

describe('Blocker 1 — frontend monotonic + grace guard is present', () => {
  const src = fs.readFileSync(SOURCE_EJS, 'utf8');
  const bundle = fs.readFileSync(BUNDLE_JS, 'utf8');

  test('source defines the tight 150s online window constant', () => {
    assert.match(src, /ACCOUNT_PRESENCE_GRACE_MS\s*=\s*150\s*\*\s*1000/);
  });

  test('source has reconcileEntryPresence with monotonic + grace + confirmed-offline logic', () => {
    assert.match(src, /function reconcileEntryPresence/);
    assert.match(src, /_presenceContactMs/);
    assert.match(src, /serverHardRed/);
    assert.match(src, /client_offline/);
  });

  test('isAccountPresent prefers the reconciled monotonic presence value', () => {
    assert.match(src, /typeof entry\._presenceLive === 'boolean'\) return entry\._presenceLive/);
  });

  test('both poll paths route presence through the guard', () => {
    const calls = (src.match(/reconcileEntryPresence\(entry,/g) || []).length;
    assert.ok(calls >= 2, 'account-status poll and inventory poll must both reconcile presence');
  });

  test('compiled bundle includes the presence guard', () => {
    assert.match(bundle, /reconcileEntryPresence/);
    assert.match(bundle, /_presenceContactMs/);
  });
});

describe('Blocker 2 — APK /auth/web-bridge sets the cookie on a 200 interstitial', () => {
  const oauth = fs.readFileSync(path.join(SRC_DIR, 'oauthRoutes.js'), 'utf8');

  test('web-bridge renders a 200 HTML cookie-priming page (not a 302 redirect)', () => {
    assert.match(oauth, /function renderWebBridgeRedirectHtml/);
    assert.match(oauth, /res\.status\(200\)\.type\('html'\)\.send\(renderWebBridgeRedirectHtml/);
  });

  test('interstitial navigates to the return target via JS replace', () => {
    assert.match(oauth, /window\.location\.replace\(t\)/);
  });
});

describe('Blocker 3 — Live Tracker is first/default everywhere', () => {
  const src = fs.readFileSync(SOURCE_EJS, 'utf8');
  const partial = fs.readFileSync(SIDEBAR_PARTIAL, 'utf8');

  test('mobile segmented tabs list Live Tracker before Dashboard', () => {
    const nav = src.match(/<nav class="inventory-main-nav inventory-main-nav--mobile"[\s\S]*?<\/nav>/)[0];
    const liveIdx = nav.indexOf('data-inventory-section="accounts"');
    const dashIdx = nav.indexOf('data-inventory-section="dashboard"');
    assert.ok(liveIdx >= 0 && dashIdx >= 0);
    assert.ok(liveIdx < dashIdx, 'Live Tracker tab must come before Dashboard tab');
  });

  test('desktop sidebar lists Live Tracker before Dashboard', () => {
    const liveIdx = partial.indexOf('sidebar-link__label">Live Tracker');
    const dashIdx = partial.indexOf('sidebar-link__label">Dashboard');
    assert.ok(liveIdx >= 0 && dashIdx >= 0);
    assert.ok(liveIdx < dashIdx, 'Live Tracker link must come before Dashboard link');
  });

  test('web post-login defaults land on /tracker (not /dashboard)', () => {
    const oauth = fs.readFileSync(path.join(SRC_DIR, 'oauthRoutes.js'), 'utf8');
    const cb = fs.readFileSync(path.join(SRC_DIR, 'discordOAuthCallback.js'), 'utf8');
    const pub = fs.readFileSync(path.join(SRC_DIR, 'publicRoutes.js'), 'utf8');
    assert.match(cb, /safeReturnPath\(stored\.authReturnTo\)\s*\|\|\s*'\/tracker'/);
    assert.match(pub, /req\.session\.user\) return res\.redirect\('\/tracker'\)/);
    assert.match(oauth, /safeReturnPath\(req\.query\.return\)\s*\|\|\s*'\/tracker'/);
  });
});
