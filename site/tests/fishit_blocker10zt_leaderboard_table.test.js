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

const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZT leaderboard-style account table', () => {
  test('inventory page renders required table columns and controls', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    for (const label of [
      'Status', 'Username', 'Coins', 'Total Caught', 'Rarest Fish',
      'Ruin', 'Artifact', 'Backpack', 'Actions',
    ]) {
      assert.match(res.text, new RegExp(`>${label}<`));
    }
    assert.match(res.text, /placeholder="Search players\.\.\."/);
    assert.match(res.text, /data-account-filter="all"[^>]*>All</);
    assert.match(res.text, /data-account-filter="online"[^>]*>Online</);
    assert.match(res.text, /data-account-filter="offline"[^>]*>Offline</);
    assert.match(res.text, /id="accountsTableBody"/);
    assert.match(res.text, /data-open-backpack/);
    assert.match(res.text, /data-remove-account/);
    assert.match(res.text, /function renderAccountsTable/);
    assert.match(res.text, /function formatSyncStatusAgo/);
  });

  test('quest and crossed icon/check columns are not present', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.doesNotMatch(tpl, />Quest</);
    assert.doesNotMatch(tpl, /quest progress/i);
    assert.doesNotMatch(tpl, /data-quest/i);
    assert.doesNotMatch(tpl, /Quest \(\d/i);
    assert.doesNotMatch(tpl, /elementFlags/i);
    assert.doesNotMatch(tpl, />Remove</);
    assert.doesNotMatch(tpl, /Last sync:/i);
  });

  test('update-backpack stores and returns playerStats without quest fields', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'LeaderTableUser',
        userId: 991,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZT_TEST',
        items: [],
        playerStats: {
          coins: 201200,
          coinsText: '201.2K',
          totalCaught: 450,
          rarestFishChance: '1/4.50K',
          ruin: { current: 0, max: 4 },
          artifact: { current: 4, max: 4 },
          quest: { current: 1, max: 4 },
        },
      })
      .expect(200);

    const res = await request(app).get('/api/fishit-tracker/get-backpack/LeaderTableUser').expect(200);
    assert.equal(res.body.playerStats.coinsText, '201.2K');
    assert.equal(res.body.playerStats.totalCaught, 450);
    assert.equal(res.body.playerStats.rarestFishChance, '1/4.50K');
    assert.deepEqual(res.body.playerStats.ruin, { current: 0, max: 4 });
    assert.deepEqual(res.body.playerStats.artifact, { current: 4, max: 4 });
    assert.equal(res.body.playerStats.quest, undefined);
  });
});
