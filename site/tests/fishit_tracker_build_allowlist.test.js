'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');

const {
  MINIMUM_TRACKER_BUILD,
  PRODUCTION_TRACKER_BUILD,
  isAllowedTrackerBuild,
  isProductionTrackerBuild,
  ALLOWED_TRACKER_BUILD_EXACT,
} = require('../src/fishitTrackerBuild');
const {
  validateTrackerClientProof,
  ALLOWED_TRACKER_CHANNEL,
  ALLOWED_TRACKER_RAW_URL,
} = require('../src/fishitTrackerChannelEnforcement');
const {
  PUBLIC_TRACKER_RAW_URL,
  decodeDistTrackerBuild,
} = require('../src/fishitPublicTrackerBuild');

describe('tracker build allowlist', () => {
  test('production build matches current public raw marker', () => {
    assert.equal(PRODUCTION_TRACKER_BUILD, 'UPLOAD_STATUS_GRACE_AND_503_RECOVERY_2026_06_15');
    assert.equal(MINIMUM_TRACKER_BUILD, PRODUCTION_TRACKER_BUILD);
    assert.ok(ALLOWED_TRACKER_BUILD_EXACT.includes(PRODUCTION_TRACKER_BUILD));
    assert.ok(ALLOWED_TRACKER_BUILD_EXACT.includes('UPLOAD_INTERVAL_60S_AIO_2026_06_14'));
  });

  test('accepts current public build and previous rollout builds', () => {
    for (const build of ALLOWED_TRACKER_BUILD_EXACT) {
      assert.equal(isAllowedTrackerBuild(build), true, build);
      const gate = validateTrackerClientProof({
        trackerBuild: build,
        trackerChannel: ALLOWED_TRACKER_CHANNEL,
        scriptSource: ALLOWED_TRACKER_RAW_URL,
      });
      assert.equal(gate.ok, true, `${build}: ${JSON.stringify(gate)}`);
    }
  });

  test('isProductionTrackerBuild only true for current public marker', () => {
    assert.equal(isProductionTrackerBuild(PRODUCTION_TRACKER_BUILD), true);
    assert.equal(isProductionTrackerBuild('UPLOAD_INTERVAL_60S_AIO_2026_06_14'), false);
  });

  test('rejects unknown build with OUTDATED_TRACKER_BUILD', () => {
    assert.equal(isAllowedTrackerBuild('TOTEM_UNSTACKED_ROW_AGG_2026_06_13'), false);
    const gate = validateTrackerClientProof({
      trackerBuild: 'TOTEM_UNSTACKED_ROW_AGG_2026_06_13',
      trackerChannel: ALLOWED_TRACKER_CHANNEL,
      scriptSource: ALLOWED_TRACKER_RAW_URL,
    });
    assert.equal(gate.ok, false);
    assert.equal(gate.error, 'OUTDATED_TRACKER_BUILD');
    assert.ok(gate.reasons.includes('outdated_tracker_build'));
  });

  test('rejects LOADER_REGISTER_LIMIT_FIX legacy build', () => {
    assert.equal(isAllowedTrackerBuild('LOADER_REGISTER_LIMIT_FIX_2026_06_11'), false);
    const gate = validateTrackerClientProof({
      trackerBuild: 'LOADER_REGISTER_LIMIT_FIX_2026_06_11',
      trackerChannel: ALLOWED_TRACKER_CHANNEL,
      scriptSource: ALLOWED_TRACKER_RAW_URL,
    });
    assert.equal(gate.ok, false);
    assert.equal(gate.error, 'OUTDATED_TRACKER_BUILD');
  });

  test('rejects unknown legacy blocker builds', () => {
    assert.equal(isAllowedTrackerBuild('BLOCKER10ZT3_SYNC_STATUS_COIN_MOBILE_TABLE_2026_06_10'), false);
    const gate = validateTrackerClientProof({
      trackerBuild: 'BLOCKER10ZT3_SYNC_STATUS_COIN_MOBILE_TABLE_2026_06_10',
      trackerChannel: ALLOWED_TRACKER_CHANNEL,
      scriptSource: ALLOWED_TRACKER_RAW_URL,
    });
    assert.equal(gate.ok, false);
    assert.ok(gate.reasons.includes('outdated_tracker_build'));
  });
});

describe('public raw tracker build guard', () => {
  test('server allowlist includes build decoded from public raw tracker.lua', async () => {
    const { loadPublicTrackerBuildProof } = require('../src/fishitPublicTrackerBuild');
    const proof = await loadPublicTrackerBuildProof();
    assert.equal(proof.url, PUBLIC_TRACKER_RAW_URL);
    assert.ok(proof.sha256, 'sha256 required');
    assert.ok(proof.bytes > 100000, 'raw tracker should be large dist wrapper');
    assert.equal(proof.buildMarker, PRODUCTION_TRACKER_BUILD);
    assert.equal(isAllowedTrackerBuild(proof.buildMarker), true);
    const gate = validateTrackerClientProof({
      trackerBuild: proof.buildMarker,
      trackerChannel: ALLOWED_TRACKER_CHANNEL,
      scriptSource: ALLOWED_TRACKER_RAW_URL,
    });
    assert.equal(gate.ok, true, JSON.stringify(gate));
  });
});
