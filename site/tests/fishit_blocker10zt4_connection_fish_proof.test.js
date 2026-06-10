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

const trackerRouter = require('../src/fishitTrackerRoutes');
const partialSnapshot = require('../src/fishitPartialSnapshot');
const {
  BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_MARKER,
  BLOCKER10ZT5_RUNTIME_LINE_FIX_MARKER,
  BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
  EXPECTED_CLIENT_TRACKER_BUILD,
} = require('../src/fishitTrackerBuild');
const { LOADER_BUILD } = require('../src/fishitTrackerLoadstring');

const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');
const ROOT = path.join(__dirname, '..', '..');
const LOADER_LUA = path.join(ROOT, 'scripts', 'loader.lua');
const RAW_LUA = path.join(ROOT, '..', 'DENG PRIVATE SOURCE', 'fishtracker', 'tracker.lua');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZT4 connection/fish/playerStats proof', () => {
  test('ZT4 marker still defined alongside ZT5 canonical build', () => {
    assert.equal(BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_MARKER, 'BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_2026_06_10');
    assert.match(EXPECTED_CLIENT_TRACKER_BUILD, /BLOCKER10ZT5/);
    assert.equal(LOADER_BUILD, EXPECTED_CLIENT_TRACKER_BUILD);
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /BLOCKER10ZT[45]/);
  });

  test('itemsForSessionDisplay keeps lastGood fish when live items empty', () => {
    const items = partialSnapshot.itemsForSessionDisplay({
      items: [],
      rawItems: [],
      lastGoodFishItems: [{ name: 'Salmon', category: 'fish', itemId: '1', amount: 2 }],
    });
    assert.equal(items.length, 1);
    assert.equal(items[0].name, 'Salmon');
  });

  test('upload endpoint returns accepted success JSON with serverTime', async () => {
    const app = makeApp();
    const username = 'Zt4UploadUser';
    const res = await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username,
        userId: 8801,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_2026_06_10',
        phase: 'live',
      })
      .expect(200);
    assert.equal(res.body.ok, true);
    assert.equal(res.body.status, 'success');
    assert.equal(res.body.accepted, true);
    assert.ok(res.body.serverTime);
    assert.equal(res.body.heartbeatAccepted, true);
  });

  test('get-backpack returns fish and proven stats when connected', async () => {
    const app = makeApp();
    const username = 'Zt4LiveFishUser';
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username,
        userId: 8802,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_2026_06_10',
        clientOrigin: 'roblox_tracker',
        items: [{ itemId: '42', name: 'Deep Sea Crab', amount: 3, category: 'fish', rarity: 'Rare' }],
        playerStats: {
          totalCaught: 100,
          totalCaughtText: '100',
          rarestFishChance: '1/1K',
          source: 'leaderstats',
          build: 'BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_2026_06_10',
        },
      })
      .expect(200);

    const backpack = await request(app).get(`/api/fishit-tracker/get-backpack/${username}`).expect(200);
    assert.ok(backpack.body.fishItems.length >= 1 || backpack.body.publicFishItems.length >= 1);
    assert.equal(backpack.body.isOnline, true);
    assert.equal(backpack.body.playerStatsProven, true);
  });

  test('playerStatsProof is unproven when disconnected', () => {
    const { buildPlayerStatsProof } = require('../src/fishitTrackerRoutes');
    const stale = {
      trackerBuild: 'BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_2026_06_10',
      lastSeenAt: new Date(Date.now() - 120_000).toISOString(),
      playerStats: {
        coins: 500,
        coinsText: '500',
        source: 'leaderstats',
        build: 'BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_2026_06_10',
      },
    };
    const proof = buildPlayerStatsProof(stale.playerStats, stale);
    assert.equal(proof.proven, false);
    assert.equal(proof.reason, 'tracker_disconnected');
    assert.equal(proof.connected, false);
  });

  test('playerStatsProof is proven for real leaderstats when connected', () => {
    const { buildPlayerStatsProof } = require('../src/fishitTrackerRoutes');
    const now = new Date().toISOString();
    const session = {
      trackerBuild: 'BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_2026_06_10',
      lastSeenAt: now,
      lastUploadAcceptedAt: now,
      playerStats: {
        coins: 500,
        coinsText: '500',
        source: 'leaderstats',
        build: 'BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_2026_06_10',
      },
    };
    const proof = buildPlayerStatsProof(session.playerStats, session);
    assert.equal(proof.proven, true);
    assert.equal(proof.source, 'real_leaderstats');
    assert.equal(proof.connected, true);
  });

  test('frontend keeps last data on refresh failure and uses lastGoodPublicFishItems', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /function setCardRefreshFailed/);
    assert.match(tpl, /lastGoodPublicFishItems/);
    assert.match(tpl, /setCardRefreshFailed\(entry\)/);
    assert.match(tpl, /if \(!entry \|\| !isTrackerOnline\(entry\)\) return null/);
  });

  test('mobile public cards hide coin/caught/rarest debug rows', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /formatConnectionStatusLabel/);
    assert.match(tpl, /accounts-mobile-card__row-label">Fish/);
    assert.match(tpl, /accounts-mobile-card__row-label">Types/);
    assert.match(tpl, /accounts-mobile-card__row-label">Last sync/);
    assert.doesNotMatch(tpl, /accounts-mobile-card__row-label">Coins/);
    assert.doesNotMatch(tpl, /accounts-mobile-card__row-label">Caught/);
    assert.doesNotMatch(tpl, /accounts-mobile-card__row-label">Rarest/);
  });

  test('loader.lua and private Lua use BLOCKER10ZT4', () => {
    const loader = fs.readFileSync(LOADER_LUA, 'utf8');
    assert.match(loader, /BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10/);
    if (fs.existsSync(RAW_LUA)) {
      const raw = fs.readFileSync(RAW_LUA, 'utf8');
      assert.match(raw, /TRACKER_BUILD = "BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_2026_06_10"/);
      assert.match(raw, /UPLOAD_OK=/);
      assert.match(raw, /HEARTBEAT_ACCEPTED=/);
      assert.match(raw, /FISH_COUNT_UPLOADED=/);
    }
  });
});
