'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');

process.env.NODE_ENV = 'test';

const fishitDb = require('../src/fishitDb');

const REAL_DB_PATH = path.join(__dirname, '..', '..', '..', 'DENG Fish It', 'data', 'deng-fish-it.sqlite');

function createBotDb(tmpPath, fishCache) {
  const { DatabaseSync } = require('node:sqlite');
  if (fs.existsSync(tmpPath)) fs.unlinkSync(tmpPath);
  const db = new DatabaseSync(tmpPath);
  db.exec('CREATE TABLE app_kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)');
  if (fishCache != null) {
    db.prepare('INSERT INTO app_kv (key, value) VALUES (?, ?)').run(
      'alltime_fish_cache',
      JSON.stringify(fishCache),
    );
  }
  if (typeof db.close === 'function') db.close();
}

function makeMockFishCache(discordId, secretCount, forgottenCount) {
  const secret = [];
  const forgotten = [];
  for (let i = 0; i < secretCount; i += 1) {
    secret.push({ name: `Secret Fish ${i}`, time: '2026-01-15T10:00:00.000Z', weight: 1000 });
  }
  for (let i = 0; i < forgottenCount; i += 1) {
    forgotten.push({ name: 'Thunderzilla', fishType: 'Thunderzilla', time: '2026-01-16T10:00:00.000Z' });
  }
  return {
    byUser: {
      [discordId]: {
        userId: discordId,
        username: 'mock_roblox_user',
        details: { secret, forgotten },
      },
    },
  };
}

describe('dashboard bot DB identity (same path as !d / !s)', () => {
  test('resolveDashboardBotUsers prefers Discord ID direct lookup', () => {
    const discordId = '987654321098765432';
    const fish = makeMockFishCache(discordId, 2, 1);
    fish.byUser.alt_roblox_key = {
      userId: '111111111111111111',
      username: 'altuser',
      details: { secret: [{ name: 'Other', time: '2026-01-01T00:00:00.000Z' }], forgotten: [] },
    };
    const tracked = [{ roblox_username_key: 'altuser', roblox_user_id: '111111111111111111' }];
    const { users, match } = fishitDb.resolveDashboardBotUsers(fish, discordId, tracked);
    assert.equal(users.length, 1);
    assert.equal(match.identityMatchMode, 'discord_id_direct');
    assert.equal(match.matchedBotUserId, discordId);
  });

  test('resolveDashboardBotUsers falls back to tracked Roblox account when Discord ID miss', () => {
    const robloxId = '111111111111111111';
    const fish = {
      byUser: {
        [robloxId]: {
          userId: robloxId,
          username: 'altuser',
          details: {
            secret: [{ name: 'Secret A', time: '2026-01-10T12:00:00.000Z' }],
            forgotten: [],
          },
        },
      },
    };
    const tracked = [{ roblox_username_key: 'altuser', roblox_user_id: robloxId }];
    const { users, match } = fishitDb.resolveDashboardBotUsers(fish, '999999999999999999', tracked);
    assert.equal(users.length, 1);
    assert.equal(match.identityMatchMode, 'tracked_roblox_account');
    assert.equal(match.matchedBotUserId, robloxId);
  });

  test('countRarityForUsers matches all-time detail row counts for ALL TIME window', () => {
    const discordId = '123456789012345678';
    const fish = makeMockFishCache(discordId, 3, 2);
    const user = fish.byUser[discordId];
    const win = fishitDb.resolveDashboardWindow('all');
    const counts = fishitDb.countRarityForUsers([user], win);
    assert.equal(counts.secretCaught, 3);
    assert.equal(counts.forgottenCaught, 2);
    const allTime = fishitDb.collectAllTimeCatchRowStats([user]);
    assert.equal(allTime.allTimeCatchRows, 5);
  });

  test('integration: getOwnerDashboard returns real bot DB counts for known Discord ID', () => {
    if (!fs.existsSync(REAL_DB_PATH)) return;
    const prevPath = process.env.FISHIT_DB_PATH;
    process.env.FISHIT_DB_PATH = REAL_DB_PATH;
    fishitDb._resetCache();
    try {
      const sampleId = '915851106280681492';
      const payload = fishitDb.getOwnerDashboard(sampleId, [], 'all', { authDiscordUsername: 'neptune_75' });
      assert.equal(payload.available, true);
      assert.equal(payload.period, 'all');
      assert.ok(payload.cards.secretCaught > 0, 'expected secret catches from bot DB');
      assert.equal(payload.debug.identityMatchMode, 'discord_id_direct');
      assert.equal(payload.debug.matchedBotUserId, sampleId);
      assert.equal(payload.debug.allTimeCatchRows, payload.debug.filteredCatchRows);
      assert.ok(payload.debug.secretCount === payload.cards.secretCaught);
    } finally {
      fishitDb._resetCache();
      if (prevPath === undefined) delete process.env.FISHIT_DB_PATH;
      else process.env.FISHIT_DB_PATH = prevPath;
    }
  });

  test('getOwnerDashboard reports no_bot_user_for_discord_id when ID missing from cache', () => {
    const isolatedDb = path.join(os.tmpdir(), `fishit-no-user-${Date.now()}.sqlite`);
    createBotDb(isolatedDb, { byUser: {} });
    const prevPath = process.env.FISHIT_DB_PATH;
    process.env.FISHIT_DB_PATH = isolatedDb;
    fishitDb._resetCache();
    try {
      const payload = fishitDb.getOwnerDashboard('000000000000000001', [], 'all');
      assert.equal(payload.available, true);
      assert.equal(payload.statsState, 'empty');
      assert.equal(payload.emptyReason, 'no_bot_user_for_discord_id');
      assert.equal(payload.cards.secretCaught, 0);
      assert.equal(payload.cards.forgottenCaught, 0);
      assert.equal(payload.debug.identityMatchMode, null);
    } finally {
      fishitDb._resetCache();
      if (prevPath === undefined) delete process.env.FISHIT_DB_PATH;
      else process.env.FISHIT_DB_PATH = prevPath;
      try { if (fs.existsSync(isolatedDb)) fs.unlinkSync(isolatedDb); } catch (_) { /* ignore */ }
    }
  });
});
