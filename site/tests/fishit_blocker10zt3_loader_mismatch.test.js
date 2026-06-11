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
  CLEAN_TRACKER_LOADSTRING,
  DEBUG_TRACKER_LOADSTRING,
  PROTECTED_DIST_RAW_URL,
  PROTECTED_DIST_RAW_URL_CACHE_BUST,
  PUBLIC_TRACKER_GITHUB_REPO,
  CLEAN_PUBLIC_TRACKER_GITHUB_REPO,
  buildCleanTrackerLoader,
  buildProofTrackerLoader,
} = require('../src/fishitTrackerLoadstring');
const { EXPECTED_CLIENT_TRACKER_BUILD, MINIMUM_TRACKER_BUILD } = require('../src/fishitTrackerBuild');

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
  test('canonical loader targets dengjiangbin/fish-it; public copy is clean one-liner', () => {
    assert.equal(PUBLIC_TRACKER_GITHUB_REPO, CLEAN_PUBLIC_TRACKER_GITHUB_REPO);
    assert.match(PROTECTED_DIST_RAW_URL, /dengjiangbin\/fish-it\/main\/tracker\.lua$/);
    assert.equal(EXPECTED_CLIENT_TRACKER_BUILD, LOADER_BUILD);
    assert.match(PROTECTED_DIST_RAW_URL_CACHE_BUST, /\?v=LOADER_FIX_REGISTER_LIMIT_2026_06_11/);
    assert.equal(CLEAN_TRACKER_LOADSTRING, buildCleanTrackerLoader(PROTECTED_DIST_RAW_URL_CACHE_BUST));
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /LOADER_BUILD=/);
    assert.equal(DEBUG_TRACKER_LOADSTRING, buildProofTrackerLoader(PROTECTED_DIST_RAW_URL_CACHE_BUST, LOADER_BUILD));
    assert.match(DEBUG_TRACKER_LOADSTRING, /FETCHED_TRACKER_BUILD=/);
  });

  test('safe loader.lua uses dengjiangbin/fish-it not legacy repos', () => {
    const loader = fs.readFileSync(LOADER_LUA, 'utf8');
    assert.match(loader, /dengjiangbin\/fish-it\/main\/tracker\.lua/);
    assert.doesNotMatch(loader, /deng-fishtracker-dist\/main\/dist\/tracker\.lua/);
    assert.doesNotMatch(loader, /deng-tool-rejoin\/main\/dist\/tracker\.lua/);
    assert.doesNotMatch(loader, /LOADER_BUILD/);
  });

  test('local dist/tracker.lua contains register-limit fix build marker', () => {
    const dist = fs.readFileSync(DIST_PATH, 'utf8');
    assert.match(dist, /LOADER_FIX_REGISTER_LIMIT_2026_06_11/);
    assert.doesNotMatch(dist, /^--\[\[ DENG protected tracker dist \| BLOCKER10ZW/m);
  });

  test('/inventory copy box serves clean public loader', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    assert.match(res.text, new RegExp(CLEAN_TRACKER_LOADSTRING.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    assert.doesNotMatch(res.text, /LOADER_BUILD=/);
    assert.doesNotMatch(res.text, /FETCH_URL=/);
    assert.doesNotMatch(res.text, /FETCHED_TRACKER_BUILD=/);
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
        trackerChannel: 'fish-it-main',
        scriptSource: 'https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua',
        phase: 'live',
      })
      .expect(403);
    assert.match(JSON.stringify({
      error: 'tracker_client_rejected',
    }), /tracker_client_rejected/);
  });

  test('debug API shows no mismatch when client uploads approved build', async () => {
    const app = makeApp();
    const username = 'LoaderMatchUser';
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username,
        userId: 5511,
        isOnline: true,
        trackerBuild: MINIMUM_TRACKER_BUILD,
        trackerChannel: 'fish-it-main',
        scriptSource: 'https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua',
        phase: 'live',
      })
      .expect(200);

    const debug = await request(app).get(`/api/fishit-tracker/debug/${username}`).expect(200);
    assert.equal(debug.body.latestClientBuild, MINIMUM_TRACKER_BUILD);
    assert.equal(debug.body.buildMismatch, false);
    assert.equal(debug.body.mismatchReason, null);
  });
});
