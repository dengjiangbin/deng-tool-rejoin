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
const {
  LOADER_BUILD,
  EXPECTED_CLIENT_TRACKER_BUILD,
  CLEAN_TRACKER_LOADSTRING,
  PROTECTED_DIST_RAW_URL,
  PROTECTED_DIST_RAW_URL_CACHE_BUST,
  PUBLIC_TRACKER_GITHUB_REPO,
  CLEAN_PUBLIC_TRACKER_GITHUB_REPO,
  buildProofTrackerLoader,
} = require('../src/fishitTrackerLoadstring');

const ROOT = path.join(__dirname, '..', '..');
const LOADER_LUA = path.join(ROOT, 'scripts', 'loader.lua');
const DIST_PATH = path.join(ROOT, 'dist', 'tracker.lua');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZT3 loader/dist mismatch fix', () => {
  test('canonical loader targets deng-fishtracker-dist with cache-bust ZT3', () => {
    assert.equal(PUBLIC_TRACKER_GITHUB_REPO, CLEAN_PUBLIC_TRACKER_GITHUB_REPO);
    assert.match(PROTECTED_DIST_RAW_URL, /deng-fishtracker-dist\/main\/dist\/tracker\.lua$/);
    assert.equal(LOADER_BUILD, 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10');
    assert.equal(EXPECTED_CLIENT_TRACKER_BUILD, LOADER_BUILD);
    assert.match(PROTECTED_DIST_RAW_URL_CACHE_BUST, /\?v=BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10/);
    assert.match(CLEAN_TRACKER_LOADSTRING, /LOADER_BUILD=/);
    assert.match(CLEAN_TRACKER_LOADSTRING, /FETCH_URL=/);
    assert.match(CLEAN_TRACKER_LOADSTRING, /FETCHED_TRACKER_BUILD=/);
    assert.equal(
      CLEAN_TRACKER_LOADSTRING,
      buildProofTrackerLoader(PROTECTED_DIST_RAW_URL_CACHE_BUST, LOADER_BUILD),
    );
  });

  test('safe loader.lua uses deng-fishtracker-dist not legacy deng-tool-rejoin', () => {
    const loader = fs.readFileSync(LOADER_LUA, 'utf8');
    assert.match(loader, /deng-fishtracker-dist\/main\/dist\/tracker\.lua/);
    assert.doesNotMatch(loader, /deng-tool-rejoin\/main\/dist\/tracker\.lua/);
    assert.match(loader, /LOADER_BUILD = "BLOCKER10ZT4/);
    assert.match(loader, /print\("LOADER_BUILD="/);
    assert.match(loader, /FETCH_URL=/);
    assert.match(loader, /FETCHED_TRACKER_BUILD=/);
    assert.match(loader, /BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10/);
  });

  test('local dist/tracker.lua contains BLOCKER10ZT3 not stale ZW-only header', () => {
    const dist = fs.readFileSync(DIST_PATH, 'utf8');
    assert.match(dist, /BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10/);
    assert.doesNotMatch(dist, /^--\[\[ DENG protected tracker dist \| BLOCKER10ZW/m);
  });

  test('/inventory copy box serves cache-busted ZT3 proof loader', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    assert.match(res.text, /LOADER_BUILD=/);
    assert.match(res.text, /FETCH_URL=/);
    assert.match(res.text, /FETCHED_TRACKER_BUILD=/);
    assert.match(res.text, /deng-fishtracker-dist\/main\/dist\/tracker\.lua\?v=BLOCKER10ZT4/);
    assert.doesNotMatch(res.text, /deng-tool-rejoin\/main\/dist\/tracker\.lua/);
  });

  test('debug API exposes client build mismatch proof', async () => {
    const app = makeApp();
    const username = 'LoaderMismatchUser';
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username,
        userId: 5510,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10',
        phase: 'live',
      })
      .expect(200);

    const debug = await request(app).get(`/api/fishit-tracker/debug/${username}`).expect(200);
    assert.equal(debug.body.latestClientBuild, 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10');
    assert.equal(debug.body.expectedClientBuild, 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10');
    assert.equal(debug.body.buildMismatch, true);
    assert.equal(debug.body.mismatchReason, 'client_loader_still_executing_old_build');
  });

  test('debug API shows no mismatch when client uploads ZT3 build', async () => {
    const app = makeApp();
    const username = 'LoaderMatchUser';
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username,
        userId: 5511,
        isOnline: true,
        trackerBuild: 'BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_2026_06_10',
        phase: 'live',
      })
      .expect(200);

    const debug = await request(app).get(`/api/fishit-tracker/debug/${username}`).expect(200);
    assert.equal(debug.body.latestClientBuild, 'BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_2026_06_10');
    assert.equal(debug.body.buildMismatch, false);
    assert.equal(debug.body.mismatchReason, null);
  });
});
