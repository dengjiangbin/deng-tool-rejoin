'use strict';

const {
  MINIMUM_TRACKER_BUILD,
  BLOCKER10ZTF_STAT_INTERVAL_SOURCE_HARDENING_MARKER,
} = require('./fishitTrackerBuild');

/** Canonical clean public repo (root tracker.lua only). */
const CLEAN_PUBLIC_TRACKER_GITHUB_REPO = 'dengjiangbin/fish-it';
const LEGACY_TRACKER_GITHUB_REPO = 'dengjiangbin/deng-tool-rejoin';
const DEPRECATED_DIST_GITHUB_REPO = 'dengjiangbin/deng-fishtracker-dist';

/** Active public repo — fish-it is canonical public loader. */
const PUBLIC_TRACKER_GITHUB_REPO = process.env.PUBLIC_TRACKER_GITHUB_REPO || CLEAN_PUBLIC_TRACKER_GITHUB_REPO;

const LOADER_BUILD = MINIMUM_TRACKER_BUILD;
const PROTECTED_TRACKER_REL_PATH = 'tracker.lua';
const PROTECTED_TRACKER_RAW_URL = `https://raw.githubusercontent.com/${PUBLIC_TRACKER_GITHUB_REPO}/main/${PROTECTED_TRACKER_REL_PATH}`;
const PROTECTED_TRACKER_RAW_URL_CACHE_BUST = `${PROTECTED_TRACKER_RAW_URL}?v=${encodeURIComponent(LOADER_BUILD)}`;
const LEGACY_DIST_RAW_URL = `https://raw.githubusercontent.com/${LEGACY_TRACKER_GITHUB_REPO}/main/dist/tracker.lua`;
const LEGACY_ROOT_RAW_URL = `https://raw.githubusercontent.com/${LEGACY_TRACKER_GITHUB_REPO}/main/tracker.lua`;
const DEPRECATED_DIST_RAW_URL = `https://raw.githubusercontent.com/${DEPRECATED_DIST_GITHUB_REPO}/main/dist/tracker.lua`;

/** @deprecated dist-only path retired — use PROTECTED_TRACKER_* */
const PROTECTED_DIST_RAW_URL = PROTECTED_TRACKER_RAW_URL;
const PROTECTED_DIST_RAW_URL_CACHE_BUST = PROTECTED_TRACKER_RAW_URL_CACHE_BUST;
const PROTECTED_DIST_REL_PATH = PROTECTED_TRACKER_REL_PATH;

/** One-line public executor script (no debug prints or visible cache-buster). */
function buildCleanTrackerLoader(fetchUrl) {
  const url = String(fetchUrl).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  return `loadstring(game:HttpGet("${url}"))()`;
}

/**
 * Proof loader — prints LOADER_BUILD / FETCH_URL / FETCHED_TRACKER_BUILD before executing dist.
 * Admin/debug only (?debug=1); never shown in the normal copy box.
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

/** Public executor script served by website copy box (clean URL, no visible cache-buster). */
const CLEAN_TRACKER_LOADSTRING = buildCleanTrackerLoader(PROTECTED_TRACKER_RAW_URL);

/** Debug/admin proof loader with cache-bust and build markers. */
const DEBUG_TRACKER_LOADSTRING = buildProofTrackerLoader(PROTECTED_TRACKER_RAW_URL_CACHE_BUST, LOADER_BUILD);

/** @deprecated use PROTECTED_TRACKER_* */
const LURAPH_DIST_RAW_URL = PROTECTED_TRACKER_RAW_URL_CACHE_BUST;
const LURAPH_DIST_REL_PATH = PROTECTED_TRACKER_REL_PATH;

module.exports = {
  LOADER_BUILD,
  MINIMUM_TRACKER_BUILD,
  BLOCKER10ZTF_STAT_INTERVAL_SOURCE_HARDENING_MARKER,
  buildCleanTrackerLoader,
  buildProofTrackerLoader,
  CLEAN_PUBLIC_TRACKER_GITHUB_REPO,
  DEPRECATED_DIST_GITHUB_REPO,
  PUBLIC_TRACKER_GITHUB_REPO,
  LEGACY_TRACKER_GITHUB_REPO,
  CLEAN_TRACKER_LOADSTRING,
  DEBUG_TRACKER_LOADSTRING,
  PROTECTED_TRACKER_REL_PATH,
  PROTECTED_TRACKER_RAW_URL,
  PROTECTED_TRACKER_RAW_URL_CACHE_BUST,
  PROTECTED_DIST_RAW_URL,
  PROTECTED_DIST_RAW_URL_CACHE_BUST,
  LEGACY_DIST_RAW_URL,
  LEGACY_ROOT_RAW_URL,
  DEPRECATED_DIST_RAW_URL,
  PROTECTED_DIST_REL_PATH,
  LURAPH_DIST_RAW_URL,
  LURAPH_DIST_REL_PATH,
};
