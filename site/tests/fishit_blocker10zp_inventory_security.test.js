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

const {
  CLEAN_TRACKER_LOADSTRING,
  PROTECTED_DIST_RAW_URL,
  PROTECTED_DIST_REL_PATH,
  PUBLIC_TRACKER_GITHUB_REPO,
  PROTECTED_DIST_RAW_URL_CACHE_BUST,
  LOADER_BUILD,
  buildProofTrackerLoader,
} = require('../src/fishitTrackerLoadstring');
const {
  BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_MARKER,
  BLOCKER10ZR_FIX_INVENTORY_BUTTON_BINDINGS_CLEAN_COPY_UI_MARKER,
  BLOCKER10ZQ_CLEAN_DIST_REPO_LIVE_CACHE_REQUEST_PM2_HEALTH_MARKER,
  BLOCKER10ZP_CLEAN_PUBLIC_REPO_HISTORY_PURGE_INVENTORY_COPY_FIX_MARKER,
  BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
} = require('../src/fishitTrackerBuild');
const { buildTrackerPageLocals } = require('../src/fishitTrackerRoutes');
const trackerRouter = require('../src/fishitTrackerRoutes');

const LAYOUT_PATH = path.join(__dirname, '..', 'views', 'layout.ejs');
const TRACKER_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZP inventory/security/copy hotfix', () => {
  test('build marker is BLOCKER10ZT3A hotfix deploy marker', () => {
    assert.equal(
      BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_MARKER,
      'BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_2026_06_10',
    );
    assert.equal(BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_MARKER);
    assert.equal(
      BLOCKER10ZR_FIX_INVENTORY_BUTTON_BINDINGS_CLEAN_COPY_UI_MARKER,
      'BLOCKER10ZR_FIX_INVENTORY_BUTTON_BINDINGS_CLEAN_COPY_UI_2026_06_10',
    );
    assert.equal(
      BLOCKER10ZQ_CLEAN_DIST_REPO_LIVE_CACHE_REQUEST_PM2_HEALTH_MARKER,
      'BLOCKER10ZQ_CLEAN_DIST_REPO_LIVE_CACHE_REQUEST_PM2_HEALTH_2026_06_10',
    );
    assert.equal(
      BLOCKER10ZP_CLEAN_PUBLIC_REPO_HISTORY_PURGE_INVENTORY_COPY_FIX_MARKER,
      'BLOCKER10ZP_CLEAN_PUBLIC_REPO_HISTORY_PURGE_INVENTORY_COPY_FIX_2026_06_10',
    );
  });

  test('canonical loadstring uses dist path from public repo constant', () => {
    assert.match(PROTECTED_DIST_RAW_URL, new RegExp(`raw\\.githubusercontent\\.com/${PUBLIC_TRACKER_GITHUB_REPO.replace('/', '\\/')}/main/dist/tracker\\.lua`));
    assert.equal(PROTECTED_DIST_REL_PATH, 'dist/tracker.lua');
    assert.equal(CLEAN_TRACKER_LOADSTRING, buildProofTrackerLoader(PROTECTED_DIST_RAW_URL_CACHE_BUST, LOADER_BUILD));
    assert.match(CLEAN_TRACKER_LOADSTRING, /LOADER_BUILD=/);
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /\/main\/tracker\.lua/);
  });

  test('sidebar Inventory menu links to /inventory not /tracker', () => {
    const layout = fs.readFileSync(LAYOUT_PATH, 'utf8');
    assert.match(layout, /href="\/inventory"/);
    assert.match(layout, /activePage === 'inventory'/);
    assert.doesNotMatch(layout, /Inventory[\s\S]{0,120}href="\/tracker"/);
  });

  test('/inventory renders enabled username input and copy fallback UI', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    assert.match(res.text, /BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_2026_06_10/);
    assert.match(res.text, /data-inventory-js="pending"/);
    assert.match(res.text, /id="usernameInput"/);
    assert.doesNotMatch(res.text, /id="usernameInput" disabled/);
    assert.match(res.text, /id="addBtn"/);
    assert.match(res.text, /id="copyBtn"/);
    assert.match(res.text, /id="loadstringCode"/);
    assert.doesNotMatch(res.text, /id="copyScriptTextarea"/);
    assert.doesNotMatch(res.text, /id="selectScriptBtn"/);
    assert.match(res.text, /copyTrackerScript/);
    assert.match(res.text, /safeBind\(/);
    assert.match(res.text, new RegExp(CLEAN_TRACKER_LOADSTRING.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    assert.doesNotMatch(res.text, /\/main\/tracker\.lua/);
  });

  test('/inventory?username=denghub2 bootstraps query username', async () => {
    const res = await request(makeApp()).get('/inventory?username=denghub2').expect(200);
    assert.match(res.text, /INITIAL_USERNAME = "denghub2"/);
    assert.match(res.text, /initFromQueryUsername/);
  });

  test('/tracker legacy route still serves inventory page', async () => {
    const res = await request(makeApp()).get('/tracker?u=denghub2').expect(200);
    assert.match(res.text, /id="usernameInput"/);
    assert.match(res.text, /dist\/tracker\.lua/);
  });

  test('buildTrackerPageLocals exposes canonical inventory path and loadstring', () => {
    const locals = buildTrackerPageLocals({ query: { username: 'TestUser1' } });
    assert.equal(locals.canonicalInventoryPath, '/inventory');
    assert.equal(locals.initialUsername, 'TestUser1');
    assert.equal(locals.trackerLoadstring, CLEAN_TRACKER_LOADSTRING);
    assert.equal(locals.trackerUiDeployMarker, BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_MARKER);
  });

  test('tracker template documents username validation feedback', () => {
    const tpl = fs.readFileSync(TRACKER_PATH, 'utf8');
    assert.match(tpl, /showUsernameError/);
    assert.match(tpl, /Username must be 3–20 characters/);
    assert.match(tpl, /No inventory data yet for this username/);
  });
});
