'use strict';

const { describe, test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-homepage-stats';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.INVENTORY_ACCOUNTS_MEMORY = '1';

const inventoryTrackedAccounts = require('../src/inventoryTrackedAccounts');
const trackerRoutes = require('../src/fishitTrackerRoutes');
const sessionStore = require('../src/fishitSessionStore');
const shardedStore = require('../src/fishitSessionStoreSharded');
const { EXPECTED_CLIENT_TRACKER_BUILD } = require('../src/fishitTrackerBuild');
const { ACCOUNT_PRESENCE_GRACE_MS } = require('../src/trackerAccountPresence');

const app = require('../src/app');
const liveTrackDB = trackerRoutes.liveTrackDB;

function iso(msAgo) {
  return new Date(Date.now() - msAgo).toISOString();
}

function writeJson(filePath, data) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(data), 'utf8');
}

describe('homepage public tracker stats', () => {
  let tmpRoot;

  beforeEach(() => {
    tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'homepage-public-stats-'));
    process.env.FISHIT_LIVE_SESSIONS_DIR = tmpRoot;
    process.env.FISHIT_SESSION_SHARDED = '1';
    shardedStore.resetShardedForTests();
    sessionStore._invalidateReloadCursorForTests();
    trackerRoutes.resetPublicNetworkStatsCacheForTests();
    inventoryTrackedAccounts.resetMemoryStoreForTests();
    for (const key of Object.keys(liveTrackDB)) delete liveTrackDB[key];
    writeJson(path.join(tmpRoot, 'index.json'), {
      updatedAt: new Date().toISOString(),
      accounts: {},
      uidAliases: {},
    });
    fs.mkdirSync(path.join(tmpRoot, 'accounts'), { recursive: true });
  });

  afterEach(() => {
    shardedStore.resetShardedForTests();
    delete process.env.FISHIT_LIVE_SESSIONS_DIR;
    delete process.env.FISHIT_SESSION_SHARDED;
    fs.rmSync(tmpRoot, { recursive: true, force: true });
  });

  test('GET /api/public/tracker-stats is public and returns no-store aggregate counts', async () => {
    await inventoryTrackedAccounts.addTrackedAccounts('505185072211689472', ['UserA', 'UserB']);
    liveTrackDB.usera = {
      username: 'UserA',
      userId: 1,
      trackerBuild: EXPECTED_CLIENT_TRACKER_BUILD,
      isOnline: true,
      lastPayloadType: 'tracker_status',
      lastAccountSeenAt: iso(5000),
      lastSuccessfulUploadAt: iso(5000),
      lastHeartbeatAt: iso(5000),
    };
    liveTrackDB.userb = {
      username: 'UserB',
      userId: 2,
      trackerBuild: EXPECTED_CLIENT_TRACKER_BUILD,
      isOnline: true,
      lastAccountSeenAt: iso(ACCOUNT_PRESENCE_GRACE_MS + 5000),
      lastSuccessfulUploadAt: iso(ACCOUNT_PRESENCE_GRACE_MS + 5000),
    };

    const res = await request(app).get('/api/public/tracker-stats');
    assert.equal(res.status, 200);
    assert.match(String(res.headers['cache-control']), /no-store/);
    assert.equal(res.body.ok, true);
    assert.equal(res.body.source, 'canonical_tracker_summary');
    assert.equal(res.body.cache, 'no-store');
    assert.equal(res.body.trackedCount, 2);
    assert.equal(res.body.onlineCount, 1);
    assert.equal('accounts' in res.body, false);
    assert.equal('discordUserId' in res.body, false);
  });

  test('registered tracked count persists when live sessions are stale', async () => {
    await inventoryTrackedAccounts.addTrackedAccounts('505185072211689472', ['SavedUser']);
    const res = await request(app).get('/api/home/network-stats');
    assert.equal(res.status, 200);
    assert.equal(res.body.trackedCount, 1);
    assert.equal(res.body.onlineCount, 0);
  });

  test('returns last good cache when snapshot is empty but registered accounts exist', async () => {
    await inventoryTrackedAccounts.addTrackedAccounts('505185072211689472', ['CachedUser']);
    const originalSnapshot = sessionStore.buildPublicStatsSessionSnapshot;
    trackerRoutes.setPublicNetworkStatsCacheForTests({
      available: true,
      rawUploadRows: 10,
      rawSessionRows: 10,
      onlineUniqueUsers: 7,
      onlineUsernames: 7,
      trackedUsernames: 10,
      registeredTrackedCount: 10,
      currentBuildUniqueUsers: 10,
      updatedAt: new Date().toISOString(),
    }, Date.now() - 31000);
    sessionStore.buildPublicStatsSessionSnapshot = () => ({});
    try {
      const stats = trackerRoutes.collectPublicTrackerNetworkStats();
      assert.equal(stats.onlineUsernames, 7);
      assert.equal(stats.cacheStale, true);
    } finally {
      sessionStore.buildPublicStatsSessionSnapshot = originalSnapshot;
      trackerRoutes.resetPublicNetworkStatsCacheForTests();
    }
  });
});
