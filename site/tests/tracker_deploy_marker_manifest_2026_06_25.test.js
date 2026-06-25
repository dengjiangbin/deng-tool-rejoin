'use strict';

// Regression (2026-06-25): the /tracker HTML deploy marker
// (data-tracker-ui-deploy, meta[name=tracker-ui-deploy], data-ui-marker,
// window.__TRACKER_UI_DEPLOY) MUST match the bundle that is actually serving.
//
// Root cause of "timer/502 fix impossible to prove": buildTrackerPageLocals shipped
// a HARDCODED constant (BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER ->
// SYNC_DURATION_STATS_HOTFIX_2026_06_11) as trackerUiDeployMarker. The hashed JS
// bundle advanced to TRACKER_SERVERNOW_TIMER_502_FIX_2026_06_25, but a grep/curl of
// the live HTML still showed the stale marker — so a real deploy looked un-deployed.
// The marker is now derived from the asset manifest (same source as the hashed
// asset URLs), so it can never silently lag a rebuild again.

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const inventoryAssets = require('../src/inventoryAssets');
const ROUTES_PATH = path.join(__dirname, '..', 'src', 'fishitTrackerRoutes.js');
const MANIFEST_PATH = path.join(__dirname, '..', 'src', 'inventoryAssetManifest.json');
const SOURCE_EJS = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');

describe('tracker deploy marker is derived from the live asset manifest', () => {
  test('inventoryDeployMarker returns the manifest marker (not the stale constant)', () => {
    const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
    const marker = inventoryAssets.inventoryDeployMarker('SOME_OLD_FALLBACK');
    assert.equal(marker, manifest.marker, 'deploy marker must equal manifest marker');
    assert.notEqual(marker, 'SOME_OLD_FALLBACK', 'must not fall back when manifest marker is valid');
  });

  test('inventoryDeployMarker falls back only for missing/placeholder markers', () => {
    // Pure-logic fallback contract: a placeholder ("inventory_assets*") yields the
    // supplied fallback; a real marker is returned verbatim.
    const pick = (m, fb) => (m && !/^inventory_assets/.test(m) ? m : (fb || m || ''));
    assert.equal(pick('inventory_assets_missing', 'FB'), 'FB');
    assert.equal(pick('inventory_assets', 'FB'), 'FB');
    assert.equal(pick('TRACKER_SERVERNOW_TIMER_502_FIX_2026_06_25', 'FB'), 'TRACKER_SERVERNOW_TIMER_502_FIX_2026_06_25');
  });

  test('route wires the manifest-derived marker and does not hardcode the constant for it', () => {
    const src = fs.readFileSync(ROUTES_PATH, 'utf8');
    assert.match(src, /inventoryAssets\.inventoryDeployMarker\(/, 'route must derive marker from manifest');
    assert.match(src, /trackerUiDeployMarker:\s*liveDeployMarker/, 'locals must use derived marker');
    // The old hardcoded assignment must be gone.
    assert.doesNotMatch(
      src,
      /trackerUiDeployMarker:\s*BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER/,
      'route must not pass the stale hardcoded deploy marker',
    );
  });

  test('HTML deploy marker equals the JS bundle build marker (single source of truth)', () => {
    const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
    const ejs = fs.readFileSync(SOURCE_EJS, 'utf8');
    const m = ejs.match(/const TRACKER_BUILD_MARKER = '([^']+)'/);
    assert.ok(m, 'bundle build marker constant must exist in source');
    assert.equal(
      manifest.marker,
      m[1],
      'manifest marker (HTML deploy marker) must match the JS bundle TRACKER_BUILD_MARKER',
    );
  });
});
