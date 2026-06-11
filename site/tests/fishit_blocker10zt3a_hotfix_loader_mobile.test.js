'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const express = require('express');
const request = require('supertest');

const trackerRouter = require('../src/fishitTrackerRoutes');
const {
  BLOCKER10ZT5_RUNTIME_LINE_FIX_MARKER,
  BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
} = require('../src/fishitTrackerBuild');
const {
  CLEAN_TRACKER_LOADSTRING,
  DEBUG_TRACKER_LOADSTRING,
  LOADER_BUILD,
  PROTECTED_DIST_RAW_URL_CACHE_BUST,
  PROTECTED_DIST_RAW_URL,
  buildCleanTrackerLoader,
  buildProofTrackerLoader,
} = require('../src/fishitTrackerLoadstring');

const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZT4 — proof loader + mobile account cards', () => {
  test('UI deploy marker points to BLOCKER10ZT4', () => {
    const { BLOCKER10ZT6_LIVE_STATS_POLL_SYNC_LAYOUT_MARKER } = require('../src/fishitTrackerBuild');
    assert.equal(BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, BLOCKER10ZT6_LIVE_STATS_POLL_SYNC_LAYOUT_MARKER);
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10/);
  });

  test('website copy loader is clean one-liner; debug proof loader is admin-only', () => {
    assert.equal(CLEAN_TRACKER_LOADSTRING, buildCleanTrackerLoader(PROTECTED_DIST_RAW_URL));
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /LOADER_BUILD=/);
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /FETCH_URL=/);
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /FETCHED_TRACKER_BUILD=/);
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /\?v=/);
    assert.equal(
      DEBUG_TRACKER_LOADSTRING,
      buildProofTrackerLoader(PROTECTED_DIST_RAW_URL_CACHE_BUST, LOADER_BUILD),
    );
  });

  test('/inventory serves clean loader in copy box', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    assert.match(res.text, new RegExp(CLEAN_TRACKER_LOADSTRING.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    assert.doesNotMatch(res.text, /LOADER_BUILD=/);
    assert.doesNotMatch(res.text, /FETCH_URL=/);
    assert.doesNotMatch(res.text, /FETCHED_TRACKER_BUILD=/);
    assert.doesNotMatch(res.text, /deng-tool-rejoin\/main\/dist\/tracker\.lua/);
  });

  test('/inventory?debug=1 serves separate debug loader box', async () => {
    const res = await request(makeApp()).get('/inventory?debug=1').expect(200);
    assert.match(res.text, /Debug loader &mdash; admin only/);
    assert.match(res.text, /LOADER_BUILD=/);
    assert.match(res.text, /FETCH_URL=/);
    assert.match(res.text, /FETCHED_TRACKER_BUILD=/);
  });

  test('mobile breakpoint hides desktop table and shows stacked account cards', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /id="accountsMobileList"/);
    assert.match(tpl, /class="accounts-mobile-list"/);
    assert.match(tpl, /function buildAccountMobileCardHtml/);
    assert.match(tpl, /@media \(max-width:768px\)[\s\S]*\.accounts-table-wrap \{ display:none/);
    assert.match(tpl, /@media \(max-width:768px\)[\s\S]*\.accounts-mobile-list \{ display:flex/);
    assert.match(tpl, /\.accounts-mobile-card__username[\s\S]*overflow-wrap:anywhere/);
    assert.doesNotMatch(tpl, /@media \(max-width:768px\)[\s\S]*table-layout:fixed/);
    assert.doesNotMatch(tpl, /@media \(max-width:768px\)[\s\S]*max-width:72px/);
    assert.doesNotMatch(tpl, /@media \(max-width:768px\)[\s\S]*text-overflow:ellipsis/);
  });

  test('mobile username is not forced into single-line ellipsis on primary label', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /\.accounts-mobile-card__username[\s\S]*white-space:normal/);
    assert.doesNotMatch(tpl, /\.accounts-mobile-card__username[\s\S]*text-overflow:ellipsis/);
  });

  test('desktop table container keeps min-width only on desktop table, not mobile cards', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /\.accounts-table[\s\S]*min-width:980px/);
    assert.doesNotMatch(tpl, /\.accounts-mobile-list[\s\S]*min-width:980px/);
    assert.doesNotMatch(tpl, /\.accounts-mobile-card[\s\S]*min-width:980px/);
  });

  test('APK embed forces mobile cards over desktop table', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /\.inventory-apk-embed[\s\S]*\.accounts-table-wrap \{ display:none/);
    assert.match(tpl, /\.inventory-apk-embed[\s\S]*\.accounts-mobile-list \{ display:flex/);
  });
});
