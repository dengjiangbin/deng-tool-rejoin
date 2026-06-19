'use strict';

// 2026-06-19 P0 — required upload lane local-throttle classification + fairness.
//
// Root cause of the Roblox `HTTP_RESPONSE_BAD lane=required_leaderstats status=0
// body=throttled-local` + `REQUIRED_LEADERSTATS_UPLOAD_FAIL reason=throttled-local`
// was twofold:
//   1) The required-lane per-lane cooldown (minGap) was intervalSec-1 (51s on a
//      52s base), which sat ABOVE the light-sync loop's fastest wait of
//      intervalSec-3 (49s). A jittered cycle therefore tripped the lane's OWN
//      driving loop into throttled-local, skipping the upload (~104s effective
//      cadence — the "1m35s" stale symptom).
//   2) A locally-deferred send (status=0 throttled-local — NO HTTP request left
//      the client) was logged as HTTP_RESPONSE_BAD / a server failure.
//
// Fix: minGap = intervalSec-5 (2s+ below the loop's fastest wait) so the required
// lane never self-throttles under normal cadence; and local defers are logged as
// UPLOAD_DEFERRED_LOCAL_THROTTLE, never HTTP_RESPONSE_BAD.

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const {
  PRODUCTION_TRACKER_BUILD,
  ALLOWED_TRACKER_BUILD_EXACT,
} = require('../src/fishitTrackerBuild');

const PRIVATE_LUA = path.join('C:', 'Users', 'Administrator', 'Desktop', 'DENG PRIVATE SOURCE', 'fishtracker', 'tracker.lua');
const hasPrivate = fs.existsSync(PRIVATE_LUA);

describe('required-lane local-defer classification + fairness (Lua client)', () => {
  test('server allowlist ships the new required-lane-fair build and keeps the prior build during rollout', () => {
    assert.equal(PRODUCTION_TRACKER_BUILD, 'REQUIRED_LANE_LOCAL_DEFER_FAIR_2026_06_19');
    assert.ok(ALLOWED_TRACKER_BUILD_EXACT.includes('STATUS_LANE_NEVER_THROTTLED_2026_06_19'),
      'previous build must stay accepted during client rollout');
  });

  test('dist/tracker.lua decodes to the new build marker', () => {
    const { decodeDistTrackerBuild } = require('../src/fishitPublicTrackerBuild');
    const distPath = path.join(__dirname, '..', '..', 'dist', 'tracker.lua');
    const decoded = decodeDistTrackerBuild(fs.readFileSync(distPath, 'utf8'));
    assert.equal(decoded, 'REQUIRED_LANE_LOCAL_DEFER_FAIR_2026_06_19');
  });

  // The following assert the authoritative private source (local dev). They are
  // skipped gracefully where the private source tree is not checked out.
  test('required-lane minGap is set safely below the light-sync loop minimum wait', { skip: !hasPrivate }, () => {
    const lua = fs.readFileSync(PRIVATE_LUA, 'utf8');
    // The required-interval cooldown must be intervalSec - 5 (margin below the
    // light-sync loop's intervalSec + random(-3, 1) minimum wait), NOT intervalSec - 1.
    assert.match(lua, /minGap\s*=\s*math\.max\(1,\s*intervalSec\s*-\s*5\)/);
    assert.doesNotMatch(lua, /minGap\s*=\s*math\.max\(1,\s*intervalSec\s*-\s*1\)/);
  });

  test('local-defer reasons are classified separately from real HTTP failures', { skip: !hasPrivate }, () => {
    const lua = fs.readFileSync(PRIVATE_LUA, 'utf8');
    assert.match(lua, /function HttpDash\.isLocalDeferReason\(reason\)/);
    // throttled-local / client-backoff / stale-run / no-http-runtime are local defers.
    assert.match(lua, /isLocalDeferReason[\s\S]*?throttled-local[\s\S]*?client-backoff/);
  });

  test('postRequiredLeaderstats logs UPLOAD_DEFERRED_LOCAL_THROTTLE for local defers and HTTP_RESPONSE_BAD only for real responses', { skip: !hasPrivate }, () => {
    const lua = fs.readFileSync(PRIVATE_LUA, 'utf8');
    const fn = lua.match(/function HttpDash\.postRequiredLeaderstats\(encoded, logMeta\)[\s\S]*?\nend/);
    assert.ok(fn, 'postRequiredLeaderstats must exist');
    const body = fn[0];
    assert.match(body, /elseif HttpDash\.isLocalDeferReason\(uploadWhy\) then/);
    assert.match(body, /UPLOAD_DEFERRED_LOCAL_THROTTLE lane=required_leaderstats/);
    const deferIdx = body.indexOf('isLocalDeferReason(uploadWhy)');
    const badIdx = body.indexOf('logHttpResponseBad');
    assert.ok(deferIdx > 0 && badIdx > deferIdx,
      'local-defer classification must guard the HTTP_RESPONSE_BAD log');
    const syncFn = lua.match(/function LiveSafe\.syncPlayerDataDashboard\(\)[\s\S]*?return requiredOk[\s\S]*?\nend/);
    assert.ok(syncFn);
    assert.match(syncFn[0], /leaderstatsDeferredLocal/);
    assert.doesNotMatch(lua, /warn\(LOG, \("SYNC_UPLOAD warn streak=/);
  });

  test('the build marker advanced from the previous status-lane build', { skip: !hasPrivate }, () => {
    const lua = fs.readFileSync(PRIVATE_LUA, 'utf8');
    assert.match(lua, /local TRACKER_BUILD = "REQUIRED_LANE_LOCAL_DEFER_FAIR_2026_06_19"/);
  });
});
