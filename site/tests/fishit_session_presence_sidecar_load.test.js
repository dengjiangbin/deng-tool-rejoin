'use strict';

const { describe, test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const os = require('os');

const sessionStore = require('../src/fishitSessionStore');
const shardedStore = require('../src/fishitSessionStoreSharded');

function writeJson(filePath, data) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(data), 'utf8');
}

describe('sharded session presence sidecar boot merge', () => {
  let tmpRoot;

  beforeEach(() => {
    tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'fishit-presence-load-'));
    process.env.FISHIT_LIVE_SESSIONS_DIR = tmpRoot;
    process.env.FISHIT_SESSION_SHARDED = '1';
    shardedStore.resetShardedForTests();
    sessionStore._invalidateReloadCursorForTests();
  });

  afterEach(() => {
    shardedStore.resetShardedForTests();
    delete process.env.FISHIT_LIVE_SESSIONS_DIR;
    delete process.env.FISHIT_SESSION_SHARDED;
    fs.rmSync(tmpRoot, { recursive: true, force: true });
  });

  test('loadIntoLiveTrackDB merges fresh presence sidecar over stale main shard', () => {
    const key = 'sidecaruser';
    const accountsDir = path.join(tmpRoot, 'accounts');
    writeJson(path.join(tmpRoot, 'index.json'), {
      updatedAt: new Date().toISOString(),
      accounts: { [key]: { updatedAt: '2026-06-26T00:00:00.000Z', bytes: 100 } },
      uidAliases: {},
    });
    writeJson(path.join(accountsDir, `${key}.json`), {
      username: key,
      userId: 123,
      isOnline: true,
      trackerBuild: 'REQUIRED_LANE_LOCAL_DEFER_FAIR_2026_06_19',
      lastRealRobloxStatusAt: '2026-06-26T00:00:00.000Z',
      lastAccountSeenAt: '2026-06-26T00:00:00.000Z',
    });
    writeJson(path.join(accountsDir, `${key}.presence.json`), {
      usernameKey: key,
      isOnline: true,
      trackerBuild: 'REQUIRED_LANE_LOCAL_DEFER_FAIR_2026_06_19',
      lastRealRobloxStatusAt: new Date().toISOString(),
      lastAccountSeenAt: new Date().toISOString(),
      lastHeartbeatAt: new Date().toISOString(),
    });

    const liveTrackDB = {};
    const result = sessionStore.loadIntoLiveTrackDB(liveTrackDB);
    assert.equal(result.loaded, 1);
    assert.ok(liveTrackDB[key]);
    assert.notEqual(liveTrackDB[key].lastRealRobloxStatusAt, '2026-06-26T00:00:00.000Z');
  });

  test('loadIntoLiveTrackDB restores presence-only sessions without a main shard', () => {
    const key = 'presenceonly';
    const accountsDir = path.join(tmpRoot, 'accounts');
    writeJson(path.join(tmpRoot, 'index.json'), {
      updatedAt: new Date().toISOString(),
      accounts: {},
      uidAliases: {},
    });
    writeJson(path.join(accountsDir, `${key}.presence.json`), {
      usernameKey: key,
      username: key,
      isOnline: true,
      trackerBuild: 'REQUIRED_LANE_LOCAL_DEFER_FAIR_2026_06_19',
      lastRealRobloxStatusAt: new Date().toISOString(),
      lastAccountSeenAt: new Date().toISOString(),
    });

    const liveTrackDB = {};
    const result = sessionStore.loadIntoLiveTrackDB(liveTrackDB);
    assert.equal(result.loaded, 1);
    assert.equal(liveTrackDB[key].usernameKey, key);
    assert.ok(liveTrackDB[key].lastRealRobloxStatusAt);
  });

  test('buildPublicStatsSessionSnapshot reads presence sidecars without full hydrate', () => {
    const key = 'statsuser';
    const accountsDir = path.join(tmpRoot, 'accounts');
    const stale = '2026-06-26T00:00:00.000Z';
    const fresh = new Date().toISOString();
    writeJson(path.join(tmpRoot, 'index.json'), {
      updatedAt: new Date().toISOString(),
      accounts: { [key]: { updatedAt: stale, bytes: 100 } },
      uidAliases: {},
    });
    writeJson(path.join(accountsDir, `${key}.json`), {
      username: key,
      userId: 456,
      isOnline: true,
      trackerBuild: 'REQUIRED_LANE_LOCAL_DEFER_FAIR_2026_06_19',
      lastRealRobloxStatusAt: stale,
      lastAccountSeenAt: stale,
    });
    writeJson(path.join(accountsDir, `${key}.presence.json`), {
      usernameKey: key,
      isOnline: true,
      trackerBuild: 'REQUIRED_LANE_LOCAL_DEFER_FAIR_2026_06_19',
      lastRealRobloxStatusAt: fresh,
      lastAccountSeenAt: fresh,
      lastHeartbeatAt: fresh,
      phase: 'player_data_selected',
    });

    const snapshot = sessionStore.buildPublicStatsSessionSnapshot();
    assert.ok(snapshot[key]);
    assert.equal(snapshot[key].lastRealRobloxStatusAt, fresh);
  });

  test('presenceOnly snapshot skips stale main shard fallback', () => {
    const key = 'presenceonlystats';
    const accountsDir = path.join(tmpRoot, 'accounts');
    const stale = '2026-06-26T00:00:00.000Z';
    writeJson(path.join(tmpRoot, 'index.json'), {
      updatedAt: new Date().toISOString(),
      accounts: { [key]: { updatedAt: stale, bytes: 100 } },
      uidAliases: {},
    });
    writeJson(path.join(accountsDir, `${key}.json`), {
      username: key,
      userId: 789,
      isOnline: true,
      trackerBuild: 'REQUIRED_LANE_LOCAL_DEFER_FAIR_2026_06_19',
      lastRealRobloxStatusAt: stale,
      lastAccountSeenAt: stale,
    });

    const snapshot = sessionStore.buildPublicStatsSessionSnapshot({ presenceOnly: true });
    assert.equal(snapshot[key], undefined);
  });
});
