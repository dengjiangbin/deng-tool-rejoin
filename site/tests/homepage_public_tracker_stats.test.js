'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-homepage-stats';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.INVENTORY_ACCOUNTS_MEMORY = '1';

const inventoryTrackedAccounts = require('../src/inventoryTrackedAccounts');
const trackerRoutes = require('../src/fishitTrackerRoutes');
const { EXPECTED_CLIENT_TRACKER_BUILD } = require('../src/fishitTrackerBuild');
const { ACCOUNT_PRESENCE_GRACE_MS } = require('../src/trackerAccountPresence');

const app = require('../src/app');
const liveTrackDB = trackerRoutes.liveTrackDB;

function iso(msAgo) {
  return new Date(Date.now() - msAgo).toISOString();
}

describe('homepage public tracker stats', () => {
  beforeEach(() => {
    inventoryTrackedAccounts.resetMemoryStoreForTests();
    for (const key of Object.keys(liveTrackDB)) delete liveTrackDB[key];
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
});
