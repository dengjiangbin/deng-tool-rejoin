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

/**
 * Proof loader — prints LOADER_BUILD / FETCH_URL / FETCHED_TRACKER_BUILD before executing dist.
 * Prevents silent stale ZW execution when users copy from /inventory.
 */
function buildProofTrackerLoader(fetchUrl, loaderBuild) {
  const url = String(fetchUrl).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  const build = String(loaderBuild).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  return [
    `local LOADER_BUILD="${build}"`,
    `local FETCH_URL="${url}"`,
    'print("LOADER_BUILD="..LOADER_BUILD)',
    'print("FETCH_URL="..FETCH_URL)',
    'local __src=game:HttpGet(FETCH_URL)',
    'local FETCHED=(__src:match("DENG protected tracker dist | ([^%]]+)") or "unknown")',
    'print("FETCHED_TRACKER_BUILD="..tostring(FETCHED))',
    'loadstring(__src)()',
  ].join(';');
}

/** Public executor script served by website copy box. */
const CLEAN_TRACKER_LOADSTRING = buildProofTrackerLoader(PROTECTED_DIST_RAW_URL_CACHE_BUST, LOADER_BUILD);

/** @deprecated use PROTECTED_DIST_* */
const LURAPH_DIST_RAW_URL = PROTECTED_DIST_RAW_URL_CACHE_BUST;
const LURAPH_DIST_REL_PATH = PROTECTED_DIST_REL_PATH;

module.exports = {
  LOADER_BUILD,
  EXPECTED_CLIENT_TRACKER_BUILD,
  buildProofTrackerLoader,
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
