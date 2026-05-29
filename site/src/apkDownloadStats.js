'use strict';
/** @deprecated Use downloadStats.js — kept for backward compatibility. */
const downloadStats = require('./downloadStats');
const path = require('path');

const STATS_PATH = process.env.APK_DOWNLOAD_STATS_PATH
  || path.join(__dirname, '..', 'data', 'apk_download_stats.json');

module.exports = {
  STATS_PATH,
  recordDownload: downloadStats.recordApkDownload,
  getStats: downloadStats.getApkStats,
  _reset: () => downloadStats._reset(),
  _versionFromFilename: downloadStats._versionFromFilename,
};
