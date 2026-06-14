'use strict';

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

describe('fishit live session bounded current state', () => {
  let tmpDir;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'fishit-shard-'));
    process.env.FISHIT_LIVE_SESSIONS_DIR = tmpDir;
    delete process.env.FISHIT_LIVE_SESSIONS_PATH;
    sessionStore._reset();
  });

  afterEach(() => {
    sessionStore._reset();
    delete process.env.FISHIT_LIVE_SESSIONS_DIR;
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (_) { /* ignore */ }
  });

  test('repeated uploads overwrite same account file without appending history', async () => {
    const key = 'overwriteuser';
    const live = {};
    for (let i = 0; i < 100; i += 1) {
      sessionStore.saveSession(key, {
        username: 'OverwriteUser',
        userId: 1,
        isOnline: true,
        playerStats: {
          coins: i,
          totalCaught: i,
          rarestFishChance: '1/50',
          source: 'leaderstats',
          build: MINIMUM_TRACKER_BUILD,
        },
        playerDataFishItems: [{ itemId: '1', name: 'Fish', quantity: 1, kind: 'fish' }],
        lastSeenAt: new Date().toISOString(),
      }, live);
    }
    await sessionStore.flushToDiskAsync({ priority: true });
    const metrics = shardedStore.getShardedMetrics();
    assert.equal(metrics.accountCount, 1);
    const accountRaw = JSON.parse(fs.readFileSync(shardedStore.indexPath(), 'utf8'));
    assert.equal(Object.keys(accountRaw.accounts).length, 1);
    const row = JSON.parse(fs.readFileSync(path.join(tmpDir, 'accounts', 'overwriteuser.json'), 'utf8'));
    assert.equal(row.playerStats.coins, 99);
    assert.ok(metrics.totalBytes < 50_000, `account bytes grew unbounded: ${metrics.totalBytes}`);
  });

  test('sanitiseSession drops debug bloat and legacy duplicate inventory', () => {
    const row = sessionStore.sanitiseSession('demo', {
      username: 'demo',
      playerDataFishItems: [{ itemId: '1', name: 'A', quantity: 2, kind: 'fish' }],
      items: [{ name: 'legacy', amount: 99 }],
      rawItems: [{ name: 'legacy', amount: 99 }],
      inventory: { fish: [] },
      playerStatsDebug: { huge: 'x'.repeat(5000) },
      inventoryItemClassificationDebug: { rows: Array.from({ length: 50 }, (_, i) => i) },
      totemPathAudit: { matches: Array(100).fill('x') },
      lastLoaderErrorMessage: 'e'.repeat(500),
    });
    assert.deepEqual(row.items, []);
    assert.equal(row.inventory, null);
    assert.equal(row.playerStatsDebug, undefined);
    assert.equal(row.inventoryItemClassificationDebug, undefined);
    assert.ok(row.lastLoaderErrorMessage.length <= 240);
  });

  test('Cloudflare HTML error body is truncated in session row', () => {
    const html502 = `<!DOCTYPE html><html><head><title>502 Bad Gateway</title></head><body>${'x'.repeat(8000)}</body></html>`;
    const row = sessionStore.sanitiseSession('erruser', {
      username: 'erruser',
      lastLoaderErrorMessage: html502,
    });
    assert.ok((row.lastLoaderErrorMessage || '').length <= 240);
    assert.ok((row.lastLoaderErrorMessage || '').length < html502.length);
  });

  test('reloadIfChanged loads latest overwrite from sharded disk', async () => {
    const key = 'reloadme';
    sessionStore.saveSession(key, {
      username: 'ReloadMe',
      userId: 5,
      playerStats: { coins: 10, totalCaught: 1, rarestFishChance: '1/50', source: 'leaderstats', build: MINIMUM_TRACKER_BUILD },
      lastSeenAt: new Date().toISOString(),
    }, {});
    await sessionStore.flushToDiskAsync({ priority: true });

    const live = {};
    sessionStore.loadIntoLiveTrackDB(live);
    sessionStore.saveSession(key, {
      ...live[key],
      playerStats: { coins: 999, totalCaught: 1, rarestFishChance: '1/50', source: 'leaderstats', build: MINIMUM_TRACKER_BUILD },
      lastSeenAt: new Date().toISOString(),
    }, live);
    await sessionStore.flushToDiskAsync({ priority: true });

    const fresh = {};
    sessionStore._invalidateReloadCursorForTests();
    const reload = sessionStore.reloadIfChanged(fresh);
    assert.equal(reload.reloaded, true);
    assert.equal(fresh[key].playerStats.coins, 999);
  });
});
