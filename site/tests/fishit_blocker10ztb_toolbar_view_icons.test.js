'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const express = require('express');
const request = require('supertest');

const trackerRouter = require('../src/fishitTrackerRoutes');
const {
  BLOCKER10ZTB_TOOLBAR_VIEW_ICONS_MARKER,
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

describe('BLOCKER10ZTB toolbar view icons', () => {
  test('deploy marker points at latest stat interval source hardening build', () => {
    const { BLOCKER10ZTF_STAT_INTERVAL_SOURCE_HARDENING_MARKER } = require('../src/fishitTrackerBuild');
    assert.equal(BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, BLOCKER10ZTF_STAT_INTERVAL_SOURCE_HARDENING_MARKER);
  });

  test('first 3 view buttons share normalized icon wrapper and sizing rules', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    const manifest = require('../src/inventoryAssets').loadManifest();
    const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'assets', manifest.css), 'utf8');
    assert.match(css, /\.accounts-view-icon\s*\{/);
    assert.match(css, /\.accounts-view-group \.accounts-view-icon svg\s*\{/);
    assert.match(css, /width:20px;/);
    assert.match(css, /height:20px;/);
    assert.match(tpl, /class="accounts-icon-btn accounts-view-btn[^"]*" id="viewTableBtn"/);
    assert.match(tpl, /class="accounts-icon-btn accounts-view-btn[^"]*" id="viewFishGridBtn"/);
    assert.match(tpl, /class="accounts-icon-btn accounts-view-btn[^"]*" id="viewStoneGridBtn"/);
    ['viewTableBtn', 'viewFishGridBtn', 'viewStoneGridBtn'].forEach((id) => {
      assert.match(tpl, new RegExp(`id="${id}"[\\s\\S]*?<span class="accounts-view-icon"`));
    });
  });

  test('fish grid icon uses exact Lucide fish SVG path', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /data-toolbar-icon="fish"/);
    assert.match(tpl, /M6\.5 12c\.94-3\.46 4\.94-6 8\.5-6/);
    assert.match(tpl, /class="lucide lucide-fish"/);
    assert.doesNotMatch(tpl, /M6 12c2-3\.2 5\.6-4\.4 8\.4-4\.4/);
  });

  test('toolbar order remains table, fish, stone, copy, refresh', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    const order = ['viewTableBtn', 'viewFishGridBtn', 'viewStoneGridBtn', 'copyUsernamesBtn', 'refreshAccountsBtn'];
    const idx = order.map((id) => res.text.indexOf(`id="${id}"`));
    assert.ok(idx.every((i) => i >= 0));
    assert.ok(idx[0] < idx[1] && idx[1] < idx[2] && idx[2] < idx[3] && idx[3] < idx[4]);
  });

  test('buildTrackerPageLocals exposes toolbar icon proof metadata', () => {
    const { buildTrackerPageLocals } = require('../src/fishitTrackerRoutes');
    const locals = buildTrackerPageLocals({ query: {}, session: {} });
    assert.equal(locals.toolbarViewIconsProof.sharedViewIconClass, 'accounts-view-icon');
    assert.equal(locals.toolbarViewIconsProof.fishToolbarIcon, 'data-toolbar-icon="fish"');
    assert.deepEqual(locals.toolbarViewIconsProof.toolbarOrder, [
      'viewTableBtn', 'viewFishGridBtn', 'viewStoneGridBtn', 'copyUsernamesBtn', 'refreshAccountsBtn',
    ]);
  });
});
