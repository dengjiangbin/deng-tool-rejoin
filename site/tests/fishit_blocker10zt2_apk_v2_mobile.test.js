'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const express = require('express');
const request = require('supertest');

const trackerRouter = require('../src/fishitTrackerRoutes');
const { BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_MARKER } = require('../src/fishitTrackerBuild');

const ROOT = path.join(__dirname, '..', '..');
const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');
const GRADLE_PATH = path.join(ROOT, 'android', 'app', 'build.gradle.kts');
const INVENTORY_KT = path.join(ROOT, 'android', 'app', 'src', 'main', 'kotlin', 'my', 'id', 'deng', 'monitor', 'ui', 'InventoryScreen.kt');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZT2 APK v2 mobile inventory UX', () => {
  test('APK version bumped to versionName 2.0 and versionCode 14', () => {
    const gradle = fs.readFileSync(GRADLE_PATH, 'utf8');
    assert.match(gradle, /versionName\s*=\s*"2\.0"/);
    assert.match(gradle, /versionCode\s*=\s*14\b/);
  });

  test('UI deploy marker includes latest side controls stat refresh marker', () => {
    const { BLOCKER10ZTC_SIDE_CONTROLS_STAT_REFRESH_MARKER } = require('../src/fishitTrackerBuild');
    assert.equal(BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, BLOCKER10ZTC_SIDE_CONTROLS_STAT_REFRESH_MARKER);
  });

  test('APK inventory screen removes Continue in Browser / Open in website CTA', () => {
    const kt = fs.readFileSync(INVENTORY_KT, 'utf8');
    assert.doesNotMatch(kt, /Open in website/i);
    assert.doesNotMatch(kt, /Continue in Browser/i);
    assert.match(kt, /\/inventory\?apk=1/);
  });

  test('/inventory?apk=1 hides browser back link and executor script card', async () => {
    const res = await request(makeApp()).get('/inventory?apk=1').expect(200);
    assert.match(res.text, /inventory-apk-embed/);
    assert.match(res.text, /data-apk-embed="1"/);
    assert.match(res.text, /id="sidebarScriptBtn"/);
    assert.doesNotMatch(res.text, />Back to DENG Tool</);
  });

  test('normal /inventory uses setup sidebar without browser back link', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    assert.match(res.text, /inventory-sidebar/);
    assert.match(res.text, /DENG Inventory/);
    assert.doesNotMatch(res.text, />Back to DENG Tool</);
    assert.doesNotMatch(res.text, /data-apk-embed="1"/);
  });

  test('mobile account cards include readable labels, stats, and actions', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /accounts-mobile-card__username/);
    assert.match(tpl, /accounts-mobile-card__row-label">Coin/);
    assert.match(tpl, /accounts-mobile-card__row-label">Caught/);
    assert.match(tpl, /accounts-mobile-card__row-label">Rare/);
    assert.match(tpl, /data-open-backpack/);
    assert.match(tpl, /data-remove-account/);
    assert.match(tpl, /id="viewFishGridBtn"[^>]*title="Fish grid"/);
  });
});
