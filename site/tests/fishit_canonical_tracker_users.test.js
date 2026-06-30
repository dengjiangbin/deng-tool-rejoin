'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-the-site-suite';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = process.env.DISCORD_CLIENT_ID || 'test-discord-client-id';
process.env.DISCORD_CLIENT_SECRET = process.env.DISCORD_CLIENT_SECRET || 'test-discord-client-secret';
process.env.DISCORD_REDIRECT_URI = process.env.DISCORD_REDIRECT_URI || 'http://localhost:8791/auth/discord/callback';

const { EXPECTED_CLIENT_TRACKER_BUILD } = require('../src/fishitTrackerBuild');
const {
  computeCanonicalTrackerUsers,
  deriveAccountPresenceLive,
} = require('../src/fishitCanonicalTrackerUsers');
const trackerRoutes = require('../src/fishitTrackerRoutes');
const app = require('../src/app');

function isoAt(ms) {
  return new Date(ms).toISOString();
}

function validSession(overrides = {}, nowMs = Date.now()) {
  const ts = isoAt(nowMs);
  return {
    username: 'denghub2',
    userId: 999001,
    isOnline: true,
    phase: 'live',
    trackerBuild: EXPECTED_CLIENT_TRACKER_BUILD,
    lastSuccessfulUploadAt: ts,
    lastAccountSeenAt: ts,
    lastHeartbeatAt: ts,
    trackerClientProof: {
      replionSourceOfTruth: true,
      trackerBuild: EXPECTED_CLIENT_TRACKER_BUILD,
    },
    uploadRequestCount: 1,
    ...overrides,
  };
}

describe('canonical tracker user counting', () => {
  test('100 interval uploads from one account count as one unique user', () => {
    const nowMs = Date.now();
    const result = computeCanonicalTrackerUsers({
      denghub2: validSession({ uploadRequestCount: 100 }, nowMs),
    }, { nowMs });
    assert.equal(result.rawUploadRows, 100);
    assert.equal(result.rawSessionRows, 1);
    assert.equal(result.uniqueKeysSeen, 1);
    assert.equal(result.currentBuildUniqueUsers, 1);
    assert.equal(result.onlineUniqueUsers, 1);
    assert.equal(result.duplicatesRemoved, 99);
    assert.equal(result.users[0].duplicateUploadCount, 99);
  });

  test('same userId with different username casing dedupes to one account', () => {
    const nowMs = Date.now();
    const result = computeCanonicalTrackerUsers({
      denghub2: validSession({ username: 'denghub2', uploadRequestCount: 12 }, nowMs),
      denghub2alt: validSession({ username: 'DENGhub2', uploadRequestCount: 8 }, nowMs),
    }, { nowMs });
    assert.equal(result.uniqueKeysSeen, 1);
    assert.equal(result.currentBuildUniqueUsers, 1);
    assert.equal(result.rawUploadRows, 20);
    assert.equal(result.duplicatesRemoved, 19);
  });

  test('old loader build uploads are excluded from current tracker count', () => {
    const nowMs = Date.now();
    const result = computeCanonicalTrackerUsers({
      legacyuser: validSession({
        username: 'legacyuser',
        userId: 888001,
        trackerBuild: 'BLOCKER10ZT6_LIVE_STATS_POLL_SYNC_LAYOUT_2026_06_10',
        trackerClientProof: {
          replionSourceOfTruth: true,
          trackerBuild: 'BLOCKER10ZT6_LIVE_STATS_POLL_SYNC_LAYOUT_2026_06_10',
        },
      }, nowMs),
    }, { nowMs });
    assert.equal(result.currentBuildUniqueUsers, 0);
    assert.equal(result.oldBuildIgnored, 1);
  });

  test('invalid tracker proof payloads are excluded when no heartbeat or upload timestamps', () => {
    const nowMs = Date.now();
    const result = computeCanonicalTrackerUsers({
      fakeuser: {
        username: 'fakeuser',
        userId: 777001,
        trackerBuild: EXPECTED_CLIENT_TRACKER_BUILD,
        isOnline: true,
        phase: 'live',
        trackerClientProof: null,
      },
    }, { nowMs });
    assert.equal(result.currentBuildUniqueUsers, 0);
    assert.equal(result.invalidPayloadIgnored, 1);
  });

  test('heartbeat-only sessions count without replionSourceOfTruth proof', () => {
    const nowMs = Date.now();
    const ts = isoAt(nowMs);
    const result = computeCanonicalTrackerUsers({
      heartonly: {
        username: 'heartonly',
        userId: 777002,
        trackerBuild: EXPECTED_CLIENT_TRACKER_BUILD,
        isOnline: true,
        phase: 'startup',
        lastPayloadType: 'tracker_status',
        lastSuccessfulUploadAt: ts,
        lastAccountSeenAt: ts,
        lastHeartbeatAt: ts,
      },
    }, { nowMs });
    assert.equal(result.currentBuildUniqueUsers, 1);
    assert.equal(result.onlineUniqueUsers, 1);
  });

  test('stale accepted users stay in unique count but not online count', () => {
    const nowMs = Date.now();
    const staleMs = nowMs - 160000;
    const result = computeCanonicalTrackerUsers({
      staleuser: validSession({
        username: 'staleuser',
        userId: 666001,
        lastSuccessfulUploadAt: isoAt(staleMs),
        lastAccountSeenAt: isoAt(staleMs),
        lastHeartbeatAt: isoAt(staleMs),
      }, nowMs),
    }, { nowMs });
    assert.equal(result.currentBuildUniqueUsers, 1);
    assert.equal(result.onlineUniqueUsers, 0);
    assert.equal(result.staleIgnored, 1);
  });

  test('replion_missing phase counts toward online unique users when heartbeat is fresh', () => {
    const nowMs = Date.now();
    const ts = isoAt(nowMs);
    const result = computeCanonicalTrackerUsers({
      replionuser: validSession({
        username: 'replionuser',
        userId: 444002,
        phase: 'replion_missing',
        lastSuccessfulUploadAt: ts,
        lastAccountSeenAt: ts,
        lastRealRobloxStatusAt: ts,
        lastHeartbeatAt: ts,
      }, nowMs),
    }, { nowMs });
    assert.equal(result.currentBuildUniqueUsers, 1);
    assert.equal(result.onlineUniqueUsers, 1);
  });

  test('player_data_selected phase counts toward online unique users', () => {
    const nowMs = Date.now();
    const ts = isoAt(nowMs);
    const result = computeCanonicalTrackerUsers({
      ingameuser: validSession({
        username: 'ingameuser',
        userId: 444001,
        phase: 'player_data_selected',
        lastSuccessfulUploadAt: ts,
        lastAccountSeenAt: ts,
        lastRealRobloxStatusAt: ts,
        lastHeartbeatAt: ts,
      }, nowMs),
    }, { nowMs });
    assert.equal(result.currentBuildUniqueUsers, 1);
    assert.equal(result.onlineUniqueUsers, 1);
  });

  test('online unique count uses the same presence logic as inventory', () => {
    const nowMs = Date.now();
    const db = {
      onlineuser: validSession({ username: 'onlineuser', userId: 555001 }, nowMs),
      offlineuser: validSession({
        username: 'offlineuser',
        userId: 555002,
        isOnline: false,
        lastAccountSeenAt: isoAt(nowMs - 1000),
      }, nowMs),
    };
    const result = computeCanonicalTrackerUsers(db, { nowMs });
    const onlineRow = result.users.find((row) => row.username === 'onlineuser');
    const offlineRow = result.users.find((row) => row.username === 'offlineuser');
    assert.equal(onlineRow.onlineFresh, deriveAccountPresenceLive(db.onlineuser, undefined, nowMs).live);
    assert.equal(offlineRow.onlineFresh, deriveAccountPresenceLive(db.offlineuser, undefined, nowMs).live);
    assert.equal(result.onlineUniqueUsers, 1);
  });

  test('public-network stats expose canonical counts instead of raw session keys', () => {
    const stats = trackerRoutes.collectPublicTrackerNetworkStats();
    assert.equal(typeof stats.rawUploadRows, 'number');
    assert.equal(typeof stats.uniqueKeysSeen, 'number');
    assert.equal(typeof stats.duplicatesRemoved, 'number');
    assert.equal(typeof stats.currentBuildUniqueUsers, 'number');
    assert.equal(typeof stats.onlineUniqueUsers, 'number');
    assert.equal(typeof stats.trackedUsernames, 'number');
    assert.ok(stats.trackedUsernames >= stats.currentBuildUniqueUsers);
    assert.equal(stats.onlineUsernames, stats.onlineUniqueUsers);
    assert.ok(stats.summary);
    assert.equal(stats.summary.currentBuildUniqueUsers, stats.currentBuildUniqueUsers);
  });

  test('public-network-proof endpoint returns per-user audit rows', async () => {
    const res = await request(app).get('/api/fishit-tracker/public-network-proof');
    assert.equal(res.status, 200);
    assert.equal(res.body.marker, 'CANONICAL_TRACKER_USER_COUNT_2026_06_12');
    assert.ok(Array.isArray(res.body.users));
    assert.ok(res.body.summary);
    assert.equal(typeof res.body.summary.rawUploadRows, 'number');
    assert.equal(typeof res.body.summary.duplicatesRemoved, 'number');
  });
});
