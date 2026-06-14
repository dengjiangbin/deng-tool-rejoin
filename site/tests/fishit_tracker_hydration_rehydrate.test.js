'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const snapshotCompleteness = require('../src/fishitSnapshotCompleteness');
const playerStatsStore = require('../src/fishitPlayerStats');
const uploadStatus = require('../src/fishitTrackerUploadStatus');
const serializer = require('../src/fishitLiveTrackerSerializer');
const sessionStore = require('../src/fishitSessionStore');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const SESSIONS_PATH = path.join(__dirname, '..', 'data', 'fishit_live_sessions.json');

describe('tracker hydration / snapshot rehydrate', () => {
  test('denghub2 persisted session rehydrates inventoryReady and snapshotComplete', () => {
    if (!fs.existsSync(SESSIONS_PATH)) {
      return;
    }
    const raw = JSON.parse(fs.readFileSync(SESSIONS_PATH, 'utf8'));
    const session = raw.sessions && raw.sessions.denghub2;
    if (!session) return;

    const sanitised = sessionStore.sanitiseSession('denghub2', session);
    assert.ok(sanitised, 'sanitiseSession must return denghub2 row');
    assert.equal(sanitised.inventoryReady, true);
    assert.equal(sanitised.snapshotComplete, true);
    assert.equal(sanitised.hasLeaderstatsSnapshot, true);
  });

  test('account-status row for rehydrated denghub2 exposes stats and ready inventory state', () => {
    if (!fs.existsSync(SESSIONS_PATH)) {
      return;
    }
    const raw = JSON.parse(fs.readFileSync(SESSIONS_PATH, 'utf8'));
    const session = sessionStore.sanitiseSession('denghub2', raw.sessions.denghub2);
    if (!session) return;

    const proof = uploadStatus.deriveTrackerUploadAccountStatus(session, { serverNowMs: Date.now() });
    const liveAccountStats = serializer.serializeLiveTrackerAccountStats(
      { ...session, ...proof, statusColor: proof.statusColor },
      playerStatsStore,
      playerStatsStore.normalizePlayerStatsForApi,
    );
    const row = {
      ...proof,
      ...liveAccountStats,
      liveAccountStats,
      statsProven: liveAccountStats.statsProven === true,
      inventoryDisplayState: uploadStatus.resolveInventoryDisplayState({ ...session, ...proof }),
    };

    assert.equal(row.statsProven, true);
    assert.equal(row.inventoryReady, true);
    assert.equal(row.snapshotComplete, true);
    assert.equal(row.inventoryDisplayState, 'ready');
    assert.ok(row.coins > 0);
    assert.ok(row.totalCaught > 0);
    assert.ok(row.rarestFishChance);
  });

  test('inventory source never renders Waiting for snapshot inside table stat helpers', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    const displayCoins = source.match(/function displayCoinsStat\([^)]*\)\s*\{[\s\S]*?\n  \}/);
    const displayCaught = source.match(/function displayTotalCaughtStat\([^)]*\)\s*\{[\s\S]*?\n  \}/);
    const displayRare = source.match(/function displayRarestFishStat\([^)]*\)\s*\{[\s\S]*?\n  \}/);
    assert.ok(displayCoins && displayCaught && displayRare, 'display helpers must exist');
    assert.doesNotMatch(displayCoins[0], /WAITING_SNAPSHOT_STAT/);
    assert.doesNotMatch(displayCaught[0], /WAITING_SNAPSHOT_STAT/);
    assert.doesNotMatch(displayRare[0], /WAITING_SNAPSHOT_STAT/);
  });

  test('applyRehydratedCompleteness sets flags from playerData inventory evidence', () => {
    const now = new Date().toISOString();
    const session = {
      username: 'proofuser',
      lastUploadAcceptedAt: now,
      lastStatsUploadAt: now,
      playerStatsUpdatedAt: now,
      playerStats: {
        coins: 1000,
        totalCaught: 50,
        coinsText: '1K',
        totalCaughtText: '50',
        rarestFishChance: '1/100',
        source: 'leaderstats',
        build: 'UPLOAD_COMPACT_FAST_PATH_2026_06_13',
      },
      playerDataFishItems: [{ kind: 'fish', name: 'Clownfish', quantity: 1 }],
      playerDataStoneItems: [{ kind: 'stone', name: 'Stone', quantity: 1 }],
      lastGoodPublicFishCount: 1,
    };
    const out = snapshotCompleteness.applyRehydratedCompleteness(session, playerStatsStore);
    assert.equal(out.inventoryReady, true);
    assert.equal(out.snapshotComplete, true);
    assert.equal(out.hasLeaderstatsSnapshot, true);
  });
});
