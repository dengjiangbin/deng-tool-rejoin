#!/usr/bin/env node
'use strict';

const fs = require('fs');
const { resolveRawTrackerSourcePath } = require('./trackerRawSourcePath');

const trackerPath = resolveRawTrackerSourcePath();
if (!trackerPath) {
  console.error('Private tracker source not found');
  process.exit(1);
}

let src = fs.readFileSync(trackerPath, 'utf8');

const REPLACEMENTS = [
  [
    'TRACKER_URL tool.deng.my.id',
    'local TRACKER_URL = "https://tool.deng.my.id/api/fishit-tracker/update-backpack"',
    'local TRACKER_URL = "https://aio.deng.my.id/api/fishit-tracker/update-backpack"',
  ],
  [
    'CATALOG_URL tool.deng.my.id',
    'local CATALOG_URL = "https://tool.deng.my.id/api/tracker/update-catalog"',
    'local CATALOG_URL = "https://aio.deng.my.id/api/tracker/update-catalog"',
  ],
  [
    'lightSyncIntervalSeconds',
    'lightSyncIntervalSeconds = 10,',
    'lightSyncIntervalSeconds = 60,',
  ],
  [
    'lightSyncBackoffSeconds',
    'lightSyncBackoffSeconds = 30,',
    'lightSyncBackoffSeconds = 180,',
  ],
  [
    'throttleSec block',
    `    throttleSec = {
        required_leaderstats = 9,
        required_status      = 9,
        tracker_status       = 9,
        inventory_snapshot   = 30,
        catalog              = 60,
        default              = 9,
    },`,
    `    throttleSec = {
        required_leaderstats = 59,
        required_status      = 59,
        tracker_status       = 59,
        inventory_snapshot   = 59,
        catalog              = 300,
        default              = 59,
    },`,
  ],
  [
    'lightSyncMinGap default',
    'lightSyncMinGap = 8,',
    'lightSyncMinGap = 58,',
  ],
  [
    'required interval fallback',
    '        local intervalSec = 10',
    '        local intervalSec = 60',
  ],
  [
    'light sync loop fallback',
    '    local baseInterval = LiveSafe.lightSyncIntervalSeconds or 10',
    '    local baseInterval = LiveSafe.lightSyncIntervalSeconds or 60',
  ],
  [
    'heartbeat loop interval',
    '    local interval = (HttpDash.throttleSec and HttpDash.throttleSec.tracker_status) or 15',
    '    local interval = (LiveSafe.lightSyncIntervalSeconds or 60)',
  ],
  [
    'inventory minGap fallback',
    '        HttpDash.lightSyncMinGap = math.max(1, (LiveSafe.lightSyncIntervalSeconds or 10) - 2)',
    '        HttpDash.lightSyncMinGap = math.max(1, (LiveSafe.lightSyncIntervalSeconds or 60) - 2)',
  ],
  [
    'TRACKER_BUILD marker',
    'local TRACKER_BUILD = "UPLOAD_COMPACT_FAST_PATH_2026_06_13"',
    'local TRACKER_BUILD = "UPLOAD_INTERVAL_60S_AIO_2026_06_14"',
  ],
  [
    'light sync comment',
    '-- BLOCKER10J: light 10s sync — server resolves all names; no client catalog.',
    '-- BLOCKER10J: light 60s sync — server resolves all names; no client catalog.',
  ],
];

for (const [label, oldText, newText] of REPLACEMENTS) {
  if (!src.includes(oldText)) {
    console.error(`PATCH_FAIL missing block: ${label}`);
    process.exit(1);
  }
  src = src.replace(oldText, newText);
}

const MARKER = 'UPLOAD_INTERVAL_60S_AIO_LANE_2026_06_14';
if (!src.includes(MARKER)) {
  src = src.replace(
    'local TRACKER_BUILD = "UPLOAD_INTERVAL_60S_AIO_2026_06_14"',
    `local TRACKER_BUILD = "UPLOAD_INTERVAL_60S_AIO_2026_06_14"\nlocal UPLOAD_INTERVAL_LANE_MARKER = "${MARKER}"`,
  );
}

// Stamp intervalSeconds on status + inventory payloads when missing.
if (!src.includes('intervalSeconds = LiveSafe.lightSyncIntervalSeconds')) {
  src = src.replace(
    '        type = "tracker_status",',
    '        intervalSeconds = LiveSafe.lightSyncIntervalSeconds or 60,\n        syncIntervalSeconds = LiveSafe.lightSyncIntervalSeconds or 60,\n        type = "tracker_status",',
  );
  src = src.replace(
    '        type = "inventory_snapshot",\n        username = LocalPlayer.Name,',
    '        intervalSeconds = LiveSafe.lightSyncIntervalSeconds or 60,\n        syncIntervalSeconds = LiveSafe.lightSyncIntervalSeconds or 60,\n        type = "inventory_snapshot",\n        username = LocalPlayer.Name,',
  );
}

fs.writeFileSync(trackerPath, src, 'utf8');
console.log('PATCH_OK upload interval 60s + aio domain applied to', trackerPath);
