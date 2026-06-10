'use strict';

const { EXPECTED_CLIENT_TRACKER_BUILD } = require('./fishitTrackerBuild');

/** Canonical clean public repo (dist-only). */
const CLEAN_PUBLIC_TRACKER_GITHUB_REPO = 'dengjiangbin/deng-fishtracker-dist';
const LEGACY_TRACKER_GITHUB_REPO = 'dengjiangbin/deng-tool-rejoin';

/** Active public repo — clean dist-only repo is canonical. */
const PUBLIC_TRACKER_GITHUB_REPO = process.env.PUBLIC_TRACKER_GITHUB_REPO || CLEAN_PUBLIC_TRACKER_GITHUB_REPO;

const LOADER_BUILD = EXPECTED_CLIENT_TRACKER_BUILD;
const PROTECTED_DIST_RAW_URL = `https://raw.githubusercontent.com/${PUBLIC_TRACKER_GITHUB_REPO}/main/dist/tracker.lua`;
const PROTECTED_DIST_RAW_URL_CACHE_BUST = `${PROTECTED_DIST_RAW_URL}?v=${encodeURIComponent(LOADER_BUILD)}`;
const LEGACY_DIST_RAW_URL = `https://raw.githubusercontent.com/${LEGACY_TRACKER_GITHUB_REPO}/main/dist/tracker.lua`;
const PROTECTED_DIST_REL_PATH = 'dist/tracker.lua';

/** Public executor script — cache-busted dist fetch from deng-fishtracker-dist. */
const CLEAN_TRACKER_LOADSTRING = `loadstring(game:HttpGet("${PROTECTED_DIST_RAW_URL_CACHE_BUST}"))()`;

/** @deprecated use PROTECTED_DIST_* */
const LURAPH_DIST_RAW_URL = PROTECTED_DIST_RAW_URL_CACHE_BUST;
const LURAPH_DIST_REL_PATH = PROTECTED_DIST_REL_PATH;

module.exports = {
  LOADER_BUILD,
  EXPECTED_CLIENT_TRACKER_BUILD,
  CLEAN_PUBLIC_TRACKER_GITHUB_REPO,
  PUBLIC_TRACKER_GITHUB_REPO,
  LEGACY_TRACKER_GITHUB_REPO,
  CLEAN_TRACKER_LOADSTRING,
  PROTECTED_DIST_RAW_URL,
  PROTECTED_DIST_RAW_URL_CACHE_BUST,
  LEGACY_DIST_RAW_URL,
  PROTECTED_DIST_REL_PATH,
  LURAPH_DIST_RAW_URL,
  LURAPH_DIST_REL_PATH,
};
