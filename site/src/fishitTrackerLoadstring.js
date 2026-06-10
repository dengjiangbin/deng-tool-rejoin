'use strict';

/** Public executor script — loads protected dist from GitHub (never raw root tracker.lua). */
const CLEAN_TRACKER_LOADSTRING = 'loadstring(game:HttpGet("https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/dist/tracker.lua"))()';

const PROTECTED_DIST_RAW_URL = 'https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/dist/tracker.lua';
const PROTECTED_DIST_REL_PATH = 'dist/tracker.lua';

/** @deprecated use PROTECTED_DIST_* — kept for tests migrating off luraph filename */
const LURAPH_DIST_RAW_URL = PROTECTED_DIST_RAW_URL;
const LURAPH_DIST_REL_PATH = PROTECTED_DIST_REL_PATH;

module.exports = {
  CLEAN_TRACKER_LOADSTRING,
  PROTECTED_DIST_RAW_URL,
  PROTECTED_DIST_REL_PATH,
  LURAPH_DIST_RAW_URL,
  LURAPH_DIST_REL_PATH,
};
