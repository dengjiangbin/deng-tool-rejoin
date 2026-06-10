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
const playerStatsStore = require('../src/fishitPlayerStats');

const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

function loadUsernameDisplayFns() {
  const tpl = fs.readFileSync(TPL_PATH, 'utf8');
  const script = tpl.slice(tpl.indexOf('<script>') + 8, tpl.indexOf('</script>'));
  const fn = script.match(/function formatUsernameForDisplay\([^)]*\)\s*\{[\s\S]*?\n  \}/);
  assert.ok(fn, 'formatUsernameForDisplay must exist');
  const displayCoins = script.match(/function displayCoinsStat\([^)]*\)\s*\{[\s\S]*?\n  \}/);
  const displayCaught = script.match(/function displayTotalCaughtStat\([^)]*\)\s*\{[\s\S]*?\n  \}/);
  const displayRarest = script.match(/function displayRarestFishStat\([^)]*\)\s*\{[\s\S]*?\n  \}/);
  const getStats = script.match(/function getEntryPlayerStats\([^)]*\)\s*\{[\s\S]*?\n  \}/);
  const resolveStats = script.match(/function resolveEntryPlayerStatsSource\([^)]*\)\s*\{[\s\S]*?\n  \}/);
  assert.ok(displayCoins && displayCaught && displayRarest && getStats && resolveStats, 'playerStats display helpers must exist');
  return new Function(`
    let hideUsernames = false;
    ${resolveStats[0]}
    ${getStats[0]}
    ${displayCoins[0]}
    ${displayCaught[0]}
    ${displayRarest[0]}
    ${fn[0]}
    return { formatUsernameForDisplay, displayCoinsStat, displayTotalCaughtStat, displayRarestFishStat, getEntryPlayerStats };
  `)();
}

describe('BLOCKER10ZU username hide + playerStats table display', () => {
  test('formatUsernameForDisplay shows full username when hide is off', () => {
    const fns = loadUsernameDisplayFns();
    assert.equal(fns.formatUsernameForDisplay('denghub2', { hideUsernames: false }), 'denghub2');
    assert.equal(fns.formatUsernameForDisplay('john12345', { hideUsernames: false }), 'john12345');
    assert.doesNotMatch(fns.formatUsernameForDisplay('denghub2', { hideUsernames: false }), /\*/);
  });

  test('formatUsernameForDisplay uses partial mask when hide is on', () => {
    const fns = loadUsernameDisplayFns();
    assert.equal(fns.formatUsernameForDisplay('denghub2', { hideUsernames: true }), 'de***b2');
    assert.equal(fns.formatUsernameForDisplay('john12345', { hideUsernames: true }), 'jo***45');
    assert.equal(fns.formatUsernameForDisplay('abc', { hideUsernames: true }), 'a*c');
    assert.equal(fns.formatUsernameForDisplay('ab', { hideUsernames: true }), 'a*');
    assert.equal(fns.formatUsernameForDisplay('a', { hideUsernames: true }), '*');
    assert.doesNotMatch(fns.formatUsernameForDisplay('denghub2', { hideUsernames: true }), /^\*+$/);
    assert.doesNotMatch(fns.formatUsernameForDisplay('denghub2', { hideUsernames: true }), /^•+$/);
  });

  test('inventory template uses shared username helper for table and card header', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /function formatUsernameForDisplay/);
    assert.match(tpl, /function refreshAllUsernameDisplays/);
    assert.match(tpl, /formatUsernameForDisplay\(entry\.displayName, \{ hideUsernames \}\)/);
    assert.match(tpl, /formatUsernameForDisplay\(username, \{ hideUsernames \}\)/);
    assert.doesNotMatch(tpl, /function maskUsername/);
    assert.doesNotMatch(tpl, /••••••/);
  });

  test('hide username button toggles aria-label and title', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    assert.match(res.text, /id="hideUsernamesBtn"[^>]*aria-label="Hide usernames"/);
    assert.match(res.text, /id="hideUsernamesBtn"[^>]*title="Hide usernames"/);
    assert.match(res.text, /Show usernames/);
    assert.match(res.text, /refreshAllUsernameDisplays\(\)/);
  });

  test('table stat display helpers read coinsText totalCaughtText and rarestFishChance', () => {
    const fns = loadUsernameDisplayFns();
    const stats = {
      coinsText: '201.2K',
      totalCaughtText: '450',
      rarestFishChance: '1/4.50K',
    };
    assert.equal(fns.displayCoinsStat(stats), '201.2K');
    assert.equal(fns.displayTotalCaughtStat(stats), '450');
    assert.equal(fns.displayRarestFishStat(stats), '1/4.50K');
    const numeric = playerStatsStore.sanitisePlayerStats({
      coins: 33440000,
      totalCaught: 68885,
      rarestFishChance: '1/1.20M',
    });
    assert.equal(fns.displayCoinsStat(numeric), '33.4M');
    assert.equal(fns.displayTotalCaughtStat(numeric), '68.885');
    assert.equal(fns.displayRarestFishStat(numeric), '1/1.20M');
  });

  test('get-backpack returns persisted playerStats for table rendering', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'StatsProofUser',
        userId: 992,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZU_TEST',
        items: [],
        playerStats: {
          coins: 653200000,
          coinsText: '653.2M',
          totalCaught: 3077845,
          totalCaughtText: '3.077.845',
          rarestFishChance: '1/25M',
        },
      })
      .expect(200);

    const res = await request(app).get('/api/fishit-tracker/get-backpack/StatsProofUser').expect(200);
    assert.equal(res.body.playerStats.coinsText, '653.2M');
    assert.equal(res.body.playerStats.totalCaughtText, '3.077.845');
    assert.equal(res.body.playerStats.rarestFishChance, '1/25M');
  });

  test('debug API exposes playerStats proof without polluting public inventory HTML', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'DebugStatsUser',
        userId: 993,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZU_TEST',
        items: [],
        playerStats: {
          coins: 201200,
          coinsText: '201.2K',
          totalCaught: 450,
          rarestFishChance: '1/4.50K',
        },
      })
      .expect(200);

    const dbg = await request(app).get('/api/fishit-tracker/debug/DebugStatsUser').expect(200);
    assert.equal(dbg.body.playerStats.coinsText, '201.2K');
    assert.equal(dbg.body.playerStatsProof.hasPlayerStats, true);
    assert.equal(dbg.body.playerStatsProof.rarestFishChance, '1/4.50K');

    const page = await request(app).get('/inventory').expect(200);
    assert.doesNotMatch(page.text, /playerStatsProof/);
  });
});
