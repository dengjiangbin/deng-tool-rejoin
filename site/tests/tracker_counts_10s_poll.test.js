'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';
process.env.INVENTORY_ACCOUNTS_MEMORY = '1';

const trackerRoutes = require('../src/fishitTrackerRoutes');
const uploadStatus = require('../src/fishitTrackerUploadStatus');
const sessionStore = require('../src/fishitSessionStore');
const inventoryTrackedAccounts = require('../src/inventoryTrackedAccounts');
const { buildTrackerAccountSummary, BUILD_MARKER } = require('../src/trackerAccountSummary');
const { ACCOUNT_PRESENCE_GRACE_MS } = require('../src/trackerAccountPresence');
const { finishTrackerUploadResponse } = require('../src/trackerUploadResponse');
const manifest = require('../src/inventoryAssetManifest.json');
const { MINIMUM_TRACKER_BUILD } = require('../src/fishitTrackerBuild');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const INVENTORY_JS = path.join(__dirname, '..', 'public', 'assets', manifest.js);
const liveTrackDB = trackerRoutes.liveTrackDB;

function iso(msAgo) {
  return new Date(Date.now() - msAgo).toISOString();
}

function isTrusted(build) {
  return build === MINIMUM_TRACKER_BUILD || String(build).includes('LOADER_REGISTER_LIMIT_FIX');
}

function makeApp() {
  const app = express();
  app.use((req, _res, next) => {
    req.inventoryOwnerDiscordId = '123456789012345678';
    next();
  });
  app.use(trackerRoutes);
  return app;
}

describe('TRACKER_COUNTS_AND_10S_POLL_FIX_2026_06_14', () => {
  test('server summary: 3 tracked, 2 online, 1 offline', () => {
    const now = Date.now();
    const tracked = [
      { robloxUsername: 'UserA', robloxUsernameKey: 'usera', robloxUserId: 1 },
      { robloxUsername: 'UserB', robloxUsernameKey: 'userb', robloxUserId: 2 },
      { robloxUsername: 'UserC', robloxUsernameKey: 'userc', robloxUserId: 3 },
    ];
    liveTrackDB.usera = {
      username: 'UserA',
      userId: 1,
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      lastAccountSeenAt: iso(5000),
      lastSuccessfulUploadAt: iso(5000),
    };
    liveTrackDB.userb = {
      username: 'UserB',
      userId: 2,
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      lastAccountSeenAt: iso(8000),
      lastSuccessfulUploadAt: iso(8000),
    };
    liveTrackDB.userc = {
      username: 'UserC',
      userId: 3,
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      lastAccountSeenAt: iso(ACCOUNT_PRESENCE_GRACE_MS + 5000),
      lastSuccessfulUploadAt: iso(ACCOUNT_PRESENCE_GRACE_MS + 5000),
    };
    const summary = buildTrackerAccountSummary(tracked, liveTrackDB, {
      serverNowMs: now,
      expectedTrackerBuild: MINIMUM_TRACKER_BUILD,
      isTrustedBuild: isTrusted,
    });
    assert.equal(summary.trackedCount, 3);
    assert.equal(summary.onlineCount, 2);
    assert.equal(summary.offlineCount, 1);
    assert.equal(summary.buildMarker, BUILD_MARKER);
    assert.equal(summary.sources.notFrom, 'stabilitySnapshot');
  });

  test('202 accepted path still updates heartbeat and online count', () => {
    const key = 'heartuser';
    const now = iso(0);
    liveTrackDB[key] = {
      username: 'HeartUser',
      userId: 99,
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      lastAccountSeenAt: now,
      lastSuccessfulUploadAt: now,
      lastUploadReceivedAt: now,
    };
    const req = { headers: {}, trackerDeferEnrichment: true };
    const res = {
      statusCode: 200,
      body: null,
      status(code) { this.statusCode = code; return this; },
      json(payload) { this.body = payload; return this; },
    };
    finishTrackerUploadResponse(req, res, {
      ok: true,
      status: 'success',
      acceptedCount: 1,
      serverTime: now,
    }, key);
    assert.equal(res.statusCode, 202);
    const tracked = [{ robloxUsername: 'HeartUser', robloxUsernameKey: key, robloxUserId: 99 }];
    const summary = buildTrackerAccountSummary(tracked, liveTrackDB, {
      expectedTrackerBuild: MINIMUM_TRACKER_BUILD,
      isTrustedBuild: isTrusted,
    });
    assert.equal(summary.onlineCount, 1);
    assert.ok(liveTrackDB[key].lastSuccessfulUploadAt);
  });

  test('stale account offline but trackedCount unchanged', () => {
    const tracked = [
      { robloxUsername: 'StaleOne', robloxUsernameKey: 'staleone', robloxUserId: 11 },
      { robloxUsername: 'FreshOne', robloxUsernameKey: 'freshone', robloxUserId: 12 },
    ];
    liveTrackDB.staleone = {
      username: 'StaleOne',
      userId: 11,
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      lastAccountSeenAt: iso(ACCOUNT_PRESENCE_GRACE_MS + 60_000),
      lastSuccessfulUploadAt: iso(ACCOUNT_PRESENCE_GRACE_MS + 60_000),
    };
    liveTrackDB.freshone = {
      username: 'FreshOne',
      userId: 12,
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      lastAccountSeenAt: iso(2000),
      lastSuccessfulUploadAt: iso(2000),
    };
    const summary = buildTrackerAccountSummary(tracked, liveTrackDB, {
      expectedTrackerBuild: MINIMUM_TRACKER_BUILD,
      isTrustedBuild: isTrusted,
    });
    assert.equal(summary.trackedCount, 2);
    assert.equal(summary.onlineCount, 1);
    assert.equal(summary.offlineCount, 1);
  });

  test('polling interval static scan rejects 10-minute live polling', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    const built = fs.readFileSync(INVENTORY_JS, 'utf8');
    assert.match(source, /TRACKER_POLL_INTERVAL_MS\s*=\s*10_000/);
    assert.match(source, /const POLL_MS\s*=\s*TRACKER_POLL_INTERVAL_MS/);
    assert.doesNotMatch(source, /600000|600_000|10\s*\*\s*60\s*\*\s*1000/);
    assert.doesNotMatch(built, /600000|600_000|10\s*\*\s*60\s*\*\s*1000/);
    assert.match(built, /TRACKER_POLL_INTERVAL_MS\s*=\s*10_000|const POLL_MS\s*=\s*10_000/);
  });

  test('session store persists heartbeat fields for web reload', () => {
    sessionStore._reset();
    const key = 'persistproof';
    const data = {
      username: 'PersistProof',
      userId: 42,
      isOnline: true,
      lastAccountSeenAt: iso(1000),
      lastSuccessfulUploadAt: iso(1000),
      lastHeartbeatAt: iso(1000),
      lastSuccessfulHeartbeatAt: iso(1000),
      lastStatus: 'green',
      lastStatusAt: iso(1000),
      lastUploadReceivedAt: iso(1000),
      items: [],
    };
    sessionStore.saveSession(key, data, { [key]: data });
    const raw = JSON.parse(fs.readFileSync(sessionStore.STORE_PATH, 'utf8'));
    const row = raw.sessions[key];
    assert.equal(row.lastAccountSeenAt, data.lastAccountSeenAt);
    assert.equal(row.lastSuccessfulUploadAt, data.lastSuccessfulUploadAt);
    assert.equal(row.lastHeartbeatAt, data.lastHeartbeatAt);
    sessionStore._reset();
  });

  test('live summary and account-status endpoints return no-store headers', async () => {
    const app = makeApp();
    const ownerId = '123456789012345678';
    await inventoryTrackedAccounts.addTrackedAccounts(ownerId, ['HdrUser']);
    liveTrackDB.hdruser = {
      username: 'HdrUser',
      userId: 77,
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      lastAccountSeenAt: iso(1000),
      lastSuccessfulUploadAt: iso(1000),
    };
    const summaryRes = await request(app).get('/api/tracker/summary');
    assert.equal(summaryRes.status, 200);
    assert.match(summaryRes.headers['cache-control'] || '', /no-store/i);
    assert.equal(summaryRes.body.trackedCount, 1);
    const statusRes = await request(app).get('/api/tracker/account-status');
    assert.equal(statusRes.status, 200);
    assert.match(statusRes.headers['cache-control'] || '', /no-store/i);
    assert.equal(statusRes.body.trackedCount, 1);
  });

  test('mobile/APK source uses same 10s poll constant and summary endpoint path', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /pollAccountStatuses/);
    assert.match(source, /\/api\/tracker\/account-status/);
    assert.match(source, /APK_EMBED/);
    assert.match(source, /lastValidTrackerSummary/);
    assert.match(source, /TRACKER_POLL_INTERVAL_MS\s*=\s*10_000/);
  });

  test('applyAcceptedUploadMeta sets heartbeat timestamps', () => {
    const now = iso(0);
    const next = uploadStatus.applyAcceptedUploadMeta({}, { intervalSeconds: 10 }, now, { heartbeatOnly: true });
    assert.equal(next.lastAccountSeenAt, now);
    assert.equal(next.lastUploadReceivedAt, now);
    assert.equal(next.lastSuccessfulHeartbeatAt, now);
    assert.ok(next.lastSuccessfulUploadAt);
  });
});
