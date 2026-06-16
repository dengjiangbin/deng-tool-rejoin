'use strict';

// P0 backend regression: a corrupt sharded index.json must NOT permanently
// block every upload persist. The ingest previously threw on every
// saveAccount because readIndexFromDisk() JSON.parse'd a NUL/space-filled
// index with no recovery, so heartbeat/session persists all failed and every
// user's snapshot went stale. The store must self-heal by quarantining the
// bad index and rebuilding it from the account shards (the source of truth).

const { describe, test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const os = require('os');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_SESSION_SYNC_SAVE = '0';

const { MINIMUM_TRACKER_BUILD } = require('../src/fishitTrackerBuild');
const sessionStore = require('../src/fishitSessionStore');
const shardedStore = require('../src/fishitSessionStoreSharded');

function sampleSession(name, coins) {
  return {
    username: name,
    userId: coins + 1,
    isOnline: true,
    playerStats: {
      coins,
      totalCaught: coins,
      rarestFishChance: '1/50',
      source: 'leaderstats',
      build: MINIMUM_TRACKER_BUILD,
    },
    playerDataFishItems: [{ itemId: '1', name: 'Fish', quantity: 1, kind: 'fish' }],
    lastSeenAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
}

describe('tracker ingest corrupt index self-heal', () => {
  let tmpDir;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'fishit-corrupt-'));
    process.env.FISHIT_LIVE_SESSIONS_DIR = tmpDir;
    delete process.env.FISHIT_LIVE_SESSIONS_PATH;
    process.env.FISHIT_SESSION_SHARDED = '1';
    sessionStore._reset();
  });

  afterEach(() => {
    sessionStore._reset();
    delete process.env.FISHIT_LIVE_SESSIONS_DIR;
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (_) { /* ignore */ }
  });

  test('write path uses the same directory the read path loads from', () => {
    const live = {};
    sessionStore.saveSession('denghub2', sampleSession('denghub2', 10), live);
    const meta = shardedStore.getShardedMetrics();
    assert.equal(path.resolve(meta.path), path.resolve(tmpDir));
  });

  test('corrupt index.json self-heals: persists are not blocked, accounts rebuilt from shards', async () => {
    const live = {};
    sessionStore.saveSession('alpha', sampleSession('alpha', 1), live);
    sessionStore.saveSession('bravo', sampleSession('bravo', 2), live);
    await sessionStore.flushToDiskAsync({ priority: true });

    const idx = shardedStore.indexPath();
    assert.ok(fs.existsSync(idx), 'index.json should exist after flush');
    // Corrupt the index exactly like the production failure: NUL/space bytes.
    fs.writeFileSync(idx, Buffer.alloc(4096, 0x20));

    // Simulate a fresh process: drop in-memory index, keep on-disk shards.
    shardedStore.dropInMemoryIndexForTests();

    // The previously-fatal path: a save must NOT throw now.
    const live2 = {};
    assert.doesNotThrow(() => {
      sessionStore.saveSession('charlie', sampleSession('charlie', 3), live2);
    });
    await sessionStore.flushToDiskAsync({ priority: true });

    // Corrupt index was quarantined.
    const quarantined = fs.readdirSync(tmpDir).filter((f) => f.startsWith('index.json.corrupt-'));
    assert.ok(quarantined.length >= 1, 'corrupt index should be quarantined');

    // Index.json is valid JSON again and includes the rebuilt + new accounts.
    const healed = JSON.parse(fs.readFileSync(idx, 'utf8'));
    assert.ok(healed.accounts.alpha, 'alpha rebuilt from shard');
    assert.ok(healed.accounts.bravo, 'bravo rebuilt from shard');
    assert.ok(healed.accounts.charlie, 'charlie persisted after heal');

    // Read path loads all three accounts back.
    const live3 = {};
    const res = sessionStore.loadIntoLiveTrackDB(live3);
    assert.ok(res.loaded >= 3, `expected >=3 accounts loaded, got ${res.loaded}`);
    assert.ok(live3.alpha && live3.bravo && live3.charlie);
  });

  test('rebuildIndexFromAccounts recovers account list directly from shard files', async () => {
    const live = {};
    sessionStore.saveSession('rebuilduser', sampleSession('rebuilduser', 7), live);
    await sessionStore.flushToDiskAsync({ priority: true });

    const rebuilt = shardedStore.rebuildIndexFromAccounts();
    assert.ok(rebuilt.accounts.rebuilduser, 'shard scan should recover the account');
    assert.ok(rebuilt.accounts.rebuilduser.bytes > 0);
  });
});
