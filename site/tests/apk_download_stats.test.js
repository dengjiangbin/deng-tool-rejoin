'use strict';

const { describe, test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const fs = require('fs');
const os = require('os');

const latestApk = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'releases', 'android', 'latest.json'), 'utf8'));
const latestFile = latestApk.file_name;
const latestVersion = latestApk.version_name;

describe('apkDownloadStats', () => {
  let tmpDir;
  let statsMod;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'apk-stats-'));
    process.env.APK_DOWNLOAD_STATS_PATH = path.join(tmpDir, 'stats.json');
    delete require.cache[require.resolve('../src/apkDownloadStats')];
    statsMod = require('../src/apkDownloadStats');
    statsMod._reset();
  });

  afterEach(() => {
    statsMod._reset();
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (_) {}
    delete process.env.APK_DOWNLOAD_STATS_PATH;
    delete require.cache[require.resolve('../src/apkDownloadStats')];
  });

  test('GET download increments count', () => {
    statsMod.recordDownload(latestFile);
    const s = statsMod.getStats();
    assert.equal(s.ok, true);
    assert.equal(s.latest.version, latestVersion);
    assert.equal(s.latest.downloads, 1);
    statsMod.recordDownload(latestFile);
    assert.equal(statsMod.getStats().latest.downloads, 2);
  });

  test('stats API returns exact count with separators via consumer', () => {
    for (let i = 0; i < 1248; i++) statsMod.recordDownload(latestFile);
    const { formatExact } = require('../src/formatNumbers');
    assert.equal(formatExact(statsMod.getStats().latest.downloads), '1,248');
  });

  test('_versionFromFilename parses semantic version', () => {
    assert.equal(statsMod._versionFromFilename(latestFile), latestVersion);
  });
});
