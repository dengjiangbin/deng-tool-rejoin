#!/usr/bin/env node
'use strict';

const path = require('path');
const fs = require('fs');
const topGrid = require('../src/fishitTrackerTopGridAssets');

const manifest = topGrid.syncTopGridAssets({ persist: true });
const resolved = topGrid.resolveTopSummaryIcons();

console.log(JSON.stringify({ manifest, urls: resolved }, null, 2));

for (const key of ['secret', 'forgotten', 'ruby', 'evolved', 'runic']) {
  const url = resolved[key];
  if (!url || !String(url).startsWith('/public/assets/tracker-top-grid/')) {
    console.error(`OWNED_URL_MISSING:${key}`, url);
    process.exit(2);
  }
  const full = path.join(__dirname, '..', url.replace(/^\//, ''));
  if (!fs.existsSync(full)) {
    console.error(`OWNED_FILE_MISSING:${key}`, full);
    process.exit(3);
  }
}

console.log('TRACKER_TOP_GRID_ASSETS_OK');

if (require.main === module) {
  // CLI entry only
}
