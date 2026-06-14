'use strict';

/**
 * Documents the UPLOAD_HTTP_2XX_SUCCESS_FIX_2026_06_14 Lua patch applied to private tracker source.
 * Run manually only if private source was reset; normal workflow edits tracker.lua directly then build_tracker_dist.js.
 */

const fs = require('fs');
const path = require('path');
const { resolveRawTrackerSourcePath } = require('./trackerRawSourcePath');

const MARKER = 'UPLOAD_HTTP_2XX_SUCCESS_FIX_2026_06_14';
const rawPath = resolveRawTrackerSourcePath({ root: path.join(__dirname, '..') });

if (!rawPath || !fs.existsSync(rawPath)) {
  console.error('patch_upload_http_2xx_success_fix: private tracker source not found');
  process.exit(1);
}

const lua = fs.readFileSync(rawPath, 'utf8');
if (!lua.includes(MARKER)) {
  console.error(`patch_upload_http_2xx_success_fix: marker ${MARKER} missing from ${rawPath}`);
  console.error('Apply HttpDash.parseStatusCode / isHttpSuccess / uploadOkFromResult changes first.');
  process.exit(1);
}

if (lua.match(/local ok200\s*=\s*\(tostring\(code\) == "200"\)/)) {
  console.error('patch_upload_http_2xx_success_fix: legacy ok200==200 checks still present');
  process.exit(1);
}

console.log(`patch_upload_http_2xx_success_fix: verified ${MARKER} in ${rawPath}`);
