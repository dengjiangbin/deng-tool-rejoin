'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const express = require('express');
const request = require('supertest');

const trackerRouter = require('../src/fishitTrackerRoutes');
const {
  BLOCKER10ZTA_INVENTORY_DESKTOP_SIDEBAR_MARKER,
  BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
} = require('../src/fishitTrackerBuild');

const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZTA inventory desktop setup sidebar', () => {
  test('deploy marker points at latest live tracker UI build', () => {
    const { BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER: deployMarker } = require('../src/fishitTrackerBuild');
    assert.match(deployMarker, /^BLOCKER10Z/);
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /inventory-sidebar/);
  });

  test('/inventory renders brand-only sidebar without middle nav or back link', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    const html = res.text;
    assert.match(html, /class="inventory-sidebar"/);
    assert.match(html, /inventory-sidebar__title">DENG Inventory</);
    assert.match(html, /inventory-sidebar__subtitle">Fish It</);
    assert.match(html, /id="hideUsernamesBtn"/);
    assert.match(html, /id="sidebarScriptBtn"/);
    assert.match(html, /inventory-profile-card/);
    assert.doesNotMatch(html, />Guest</);
    assert.doesNotMatch(html, />Sign in</);
    assert.doesNotMatch(html, /Sign in to sync profile/);
    assert.doesNotMatch(html, /class="nav-list"/);
    assert.doesNotMatch(html, />Dashboard</);
    assert.doesNotMatch(html, />Solver</);
    assert.doesNotMatch(html, />Shared Cloud</);
    assert.doesNotMatch(html, />Bypass Link</);
    assert.doesNotMatch(html, />Sailor Piece</);
  });

  test('hide username control uses single icon slot', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /id="hideUsernameIcon"/);
    assert.match(tpl, /HIDE_USERNAME_EYE_OFF_SVG/);
    assert.doesNotMatch(tpl, /data-icon="eye-off"/);
    assert.doesNotMatch(tpl, /theme-toggle-track/);
  });

  test('desktop hides large loadstring panel while APK hides visible script card', async () => {
    const desktop = await request(makeApp()).get('/inventory').expect(200);
    assert.match(desktop.text, /loadstring-box--desktop-hidden/);
    const apk = await request(makeApp()).get('/inventory?apk=1').expect(200);
    assert.match(apk.text, /inventory-sidebar/);
    assert.match(apk.text, /id="sidebarScriptBtn"/);
    assert.doesNotMatch(apk.text, />Back to DENG Tool</);
  });

  test('buildTrackerPageLocals exposes sidebar setup proof metadata', () => {
    const { buildTrackerPageLocals } = require('../src/fishitTrackerRoutes');
    const locals = buildTrackerPageLocals({ query: {}, session: { csrfToken: 'test-token' } });
    assert.equal(locals.inventorySidebarSetupProof.hasBrandSection, true);
    assert.equal(locals.inventorySidebarSetupProof.hasMiddleNav, false);
    assert.equal(locals.inventorySidebarSetupProof.hasBackLink, false);
    assert.equal(locals.inventorySidebarSetupProof.marker, BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER);
  });

  test('existing inventory poll/status helpers remain intact', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /const POLL_MS\s*=\s*10000/);
    assert.match(tpl, /function applyInventoryPollPayload/);
    assert.match(tpl, /DENG Inventory Tracker/);
    assert.match(tpl, /Track Your Fish It Accounts/);
    assert.match(tpl, /id="viewTableBtn"/);
    assert.match(tpl, /id="viewFishGridBtn"/);
    assert.match(tpl, /id="viewStoneGridBtn"/);
    assert.match(tpl, /id="copyUsernamesBtn"/);
    assert.match(tpl, /id="refreshAccountsBtn"/);
  });
});
