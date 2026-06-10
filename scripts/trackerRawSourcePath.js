'use strict';

const fs = require('fs');
const path = require('path');

const DEFAULT_PRIVATE_RAW_PATH = path.join(
  'C:',
  'Users',
  'Administrator',
  'Desktop',
  'DENG PRIVATE SOURCE',
  'fishtracker',
  'tracker.lua',
);

function resolveRawTrackerSourcePath(options = {}) {
  const root = options.root || path.join(__dirname, '..');
  const candidates = [
    process.env.TRACKER_RAW_SOURCE_PATH,
    process.env.PRIVATE_TRACKER_SOURCE_PATH,
    DEFAULT_PRIVATE_RAW_PATH,
    path.join(root, 'tracker.lua'),
  ].filter(Boolean);

  for (const candidate of candidates) {
    const abs = path.resolve(candidate);
    if (fs.existsSync(abs)) return abs;
  }
  return null;
}

module.exports = {
  DEFAULT_PRIVATE_RAW_PATH,
  resolveRawTrackerSourcePath,
};
