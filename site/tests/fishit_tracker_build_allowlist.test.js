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

describe('tracker build allowlist', () => {
  test('production build is UPLOAD_COMPACT_FAST_PATH_2026_06_13 only', () => {
    assert.equal(PRODUCTION_TRACKER_BUILD, 'UPLOAD_COMPACT_FAST_PATH_2026_06_13');
    assert.equal(MINIMUM_TRACKER_BUILD, PRODUCTION_TRACKER_BUILD);
    assert.deepEqual(ALLOWED_TRACKER_BUILD_EXACT, [PRODUCTION_TRACKER_BUILD]);
  });

  test('accepts UPLOAD_COMPACT_FAST_PATH_2026_06_13', () => {
    assert.equal(isProductionTrackerBuild('UPLOAD_COMPACT_FAST_PATH_2026_06_13'), true);
    assert.equal(isAllowedTrackerBuild('UPLOAD_COMPACT_FAST_PATH_2026_06_13'), true);
    const gate = validateTrackerClientProof({
      trackerBuild: 'UPLOAD_COMPACT_FAST_PATH_2026_06_13',
      trackerChannel: ALLOWED_TRACKER_CHANNEL,
      scriptSource: ALLOWED_TRACKER_RAW_URL,
    });
    assert.equal(gate.ok, true, JSON.stringify(gate));
  });

  test('rejects TOTEM_UNSTACKED_ROW_AGG_2026_06_13 with OUTDATED_TRACKER_BUILD', () => {
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
