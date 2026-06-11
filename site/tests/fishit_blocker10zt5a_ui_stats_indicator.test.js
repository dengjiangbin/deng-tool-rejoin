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
const { isSessionLive } = require('../src/fishitTrackerRoutes');

const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZT5A UI stats + indicator regression', () => {
  test('table template keeps coin/caught/rarest columns and hides sync age text', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /col-coins/);
    assert.match(tpl, /col-caught/);
    assert.match(tpl, /col-rare/);
    assert.match(tpl, /data-table-sync-text/);
    assert.doesNotMatch(tpl, /accounts-mobile-card__row-label">Last sync/);
  });

  test('fresh inventory timestamp marks session live even when heartbeat is stale', () => {
    const now = Date.now();
    const staleHeartbeat = new Date(now - 120_000).toISOString();
    const freshInventory = new Date(now - 5_000).toISOString();
    const session = {
      lastSeenAt: staleHeartbeat,
      lastInventoryAt: freshInventory,
      updatedAt: freshInventory,
    };
    assert.equal(isSessionLive(session), true);
  });

  test('debug API exposes connectionIndicatorProof only in debug route', async () => {
    const app = makeApp();
    const username = 'UiIndicatorProofUser';
    const now = new Date().toISOString();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username,
        userId: 99101,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
        fishItems: [{ itemId: '1', name: 'Clownfish', quantity: 2, source: 'playerdata_gameitemdb' }],
        playerStats: {
          coins: 1200,
          coinsText: '1.2K',
          totalCaught: 50,
          totalCaughtText: '50',
          rarestFishChance: '1/500',
          source: 'leaderstats',
          build: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
        },
      })
      .expect(200);

    const debug = await request(app).get(`/api/fishit-tracker/debug/${username}`).expect(200);
    assert.equal(debug.body.connectionIndicatorProof.connected, true);
    assert.equal(debug.body.connectionIndicatorProof.indicatorColor, 'green');
    assert.ok(debug.body.connectionIndicatorProof.timestampUsed);

    const backpack = await request(app).get(`/api/fishit-tracker/get-backpack/${username}`).expect(200);
    assert.equal(backpack.body.connectionLive, true);
    assert.equal(backpack.body.isOnline, true);
    assert.equal(backpack.body.playerStats.coinsText, '1.2K');
    assert.equal(backpack.body.connectionIndicatorProof, undefined);
  });
});
