'use strict';

/** Canonical clean public repo (dist-only). Create on GitHub before switching loadstring. */
const CLEAN_PUBLIC_TRACKER_GITHUB_REPO = 'dengjiangbin/deng-fishtracker-dist';
const LEGACY_TRACKER_GITHUB_REPO = 'dengjiangbin/deng-tool-rejoin';

/** Active public repo — defaults to legacy until clean repo is live; override with PUBLIC_TRACKER_GITHUB_REPO. */
const PUBLIC_TRACKER_GITHUB_REPO = process.env.PUBLIC_TRACKER_GITHUB_REPO || LEGACY_TRACKER_GITHUB_REPO;

const PROTECTED_DIST_RAW_URL = `https://raw.githubusercontent.com/${PUBLIC_TRACKER_GITHUB_REPO}/main/dist/tracker.lua`;
const LEGACY_DIST_RAW_URL = `https://raw.githubusercontent.com/${LEGACY_TRACKER_GITHUB_REPO}/main/dist/tracker.lua`;
const PROTECTED_DIST_REL_PATH = 'dist/tracker.lua';

/** Public executor script — loads protected dist from GitHub (never raw root tracker.lua). */
const CLEAN_TRACKER_LOADSTRING = `loadstring(game:HttpGet("${PROTECTED_DIST_RAW_URL}"))()`;

/** @deprecated use PROTECTED_DIST_* — kept for tests migrating off luraph filename */
const LURAPH_DIST_RAW_URL = PROTECTED_DIST_RAW_URL;
const LURAPH_DIST_REL_PATH = PROTECTED_DIST_REL_PATH;

module.exports = {
  CLEAN_PUBLIC_TRACKER_GITHUB_REPO,
  PUBLIC_TRACKER_GITHUB_REPO,
  LEGACY_TRACKER_GITHUB_REPO,
  CLEAN_TRACKER_LOADSTRING,
  PROTECTED_DIST_RAW_URL,
  LEGACY_DIST_RAW_URL,
  PROTECTED_DIST_REL_PATH,
  LURAPH_DIST_RAW_URL,
  LURAPH_DIST_REL_PATH,
};
