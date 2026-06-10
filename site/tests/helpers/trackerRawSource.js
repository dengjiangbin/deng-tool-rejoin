'use strict';

const path = require('path');
const { test } = require('node:test');
const { resolveRawTrackerSourcePath } = require('../../../scripts/trackerRawSourcePath');

const REPO_ROOT = path.join(__dirname, '..', '..', '..');
const RAW_TRACKER_LUA = resolveRawTrackerSourcePath({ root: REPO_ROOT });

/** Run test only when private/local raw tracker source is present. */
const testIfRawTracker = RAW_TRACKER_LUA ? test : test.skip;

module.exports = {
  REPO_ROOT,
  RAW_TRACKER_LUA,
  testIfRawTracker,
};
