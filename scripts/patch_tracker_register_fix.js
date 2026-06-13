#!/usr/bin/env node
'use strict';

const fs = require('fs');
const { resolveRawTrackerSourcePath } = require('./trackerRawSourcePath');

const rawPath = resolveRawTrackerSourcePath();
if (!rawPath) {
  console.error('patch failed: raw tracker source not found');
  process.exit(1);
}

let src = fs.readFileSync(rawPath, 'utf8');

src = src.replace(
  /\n-- LOADER_FIX_REGISTER_LIMIT_2026_06_11: main chunk locals isolated in do\/end\n do\n/,
  '\n',
);
src = src.replace(/\nend\n$/, '\n');

src = src.replace(
  /local TRACKER_BUILD = "[^"]+"/,
  'local TRACKER_BUILD = "LOADER_REGISTER_LIMIT_FIX_2026_06_11"',
);

if (!src.includes('LOADER_FIX_REGISTER_LIMIT_2026_06_11: isolate locals in IIFE')) {
  const anchor = 'end\n\n-- Version marker';
  const idx = src.indexOf(anchor);
  if (idx < 0) {
    console.error('patch failed: shim anchor missing');
    process.exit(1);
  }
  const insertAt = idx + 3;
  src = `${src.slice(0, insertAt)}\n-- LOADER_FIX_REGISTER_LIMIT_2026_06_11: isolate locals in IIFE\n(function()\n${src.slice(insertAt + 1)}`;
}

const oldTail = /xpcall\(main, function\(err\)\s*\n\s*warn\("\[DENG TRACKER\] FATAL ERROR[^"]*"\)\s*\n\s*warn\(debug\.traceback\(err\)\)\s*\nend\)/;
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
end)
end)()`;

if (!oldTail.test(src) && !src.includes('end)()')) {
  console.error('patch failed: xpcall tail missing');
  process.exit(1);
}
if (!src.includes('end)()')) {
  src = src.replace(oldTail, newTail);
}

fs.writeFileSync(rawPath, src, 'utf8');
console.log('PATCH_TRACKER_IIFE OK');
console.log('  file:', rawPath);
