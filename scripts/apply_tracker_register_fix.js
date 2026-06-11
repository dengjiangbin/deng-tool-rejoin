#!/usr/bin/env node
'use strict';

const fs = require('fs');
const { resolveRawTrackerSourcePath } = require('./trackerRawSourcePath');

const rawPath = resolveRawTrackerSourcePath();
if (!rawPath) {
  console.error('APPLY_TRACKER_REGISTER_FIX FAILED: source not found');
  process.exit(1);
}

let src = fs.readFileSync(rawPath, 'utf8');

src = src.replace(/\r\n/g, '\n');
src = src.replace(/\n-- LOADER_FIX_REGISTER_LIMIT_2026_06_11:[^\n]*\n(?:\(function\(\)\n| do\n)/, '\n');
src = src.replace(/\nend\)\(\)\s*$/, '\n');

src = src.replace(
  /local TRACKER_BUILD = "[^"]+"/,
  'local TRACKER_BUILD = "LOADER_REGISTER_LIMIT_FIX_2026_06_11"',
);

const topLocalFn = (src.match(/^local function /gm) || []).length;
if (topLocalFn > 0) {
  src = src.replace(/^local function /gm, 'function ');
}

const oldTail = /xpcall\(main, function\(err\)\s*\n\s*warn\("\[DENG TRACKER\] FATAL ERROR[^"]*"\)\s*\n\s*warn\(debug\.traceback\((err|msg)\)\)\s*\nend\)/;
const newTail = `xpcall(main, function(err)
    local msg = tostring(err)
    warn("[DENG TRACKER] FATAL ERROR — real traceback:")
    warn(debug.traceback(msg))
    pcall(function()
        if typeof(syncStatus) == "function" then
            syncStatus(false, "loader_error", {
                loaderError = {
                    loaderBuild = TRACKER_BUILD,
                    errorMessage = string.sub(msg, 1, 500),
                    phase = "startup",
                    timestamp = os.time(),
                },
            })
        end
    end)
end)`;

if (!oldTail.test(src)) {
  if (!src.includes('loaderError = {')) {
    console.error('APPLY_TRACKER_REGISTER_FIX FAILED: xpcall tail not found');
    process.exit(1);
  }
} else {
  src = src.replace(oldTail, newTail);
}

fs.writeFileSync(rawPath, src, 'utf8');

const topLocal = (src.match(/^local /gm) || []).length;
const topFn = (src.match(/^function /gm) || []).length;
console.log('APPLY_TRACKER_REGISTER_FIX OK');
console.log('  file:', rawPath);
console.log('  top-level locals:', topLocal);
console.log('  top-level functions:', topFn);
