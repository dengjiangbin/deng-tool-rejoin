'use strict';

const { describe, test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const fs = require('fs');
const os = require('os');

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
    statsMod.recordDownload('deng-tool-rejoin-apk-v1.0.9.apk');
    const s = statsMod.getStats();
    assert.equal(s.ok, true);
    assert.equal(s.latest.version, '1.0.9');
    assert.equal(s.latest.downloads, 1);
    statsMod.recordDownload('deng-tool-rejoin-apk-v1.0.9.apk');
    assert.equal(statsMod.getStats().latest.downloads, 2);
  });

  test('stats API returns exact count with separators via consumer', () => {
    for (let i = 0; i < 1248; i++) statsMod.recordDownload('deng-tool-rejoin-apk-v1.0.9.apk');
    const { formatExact } = require('../src/formatNumbers');
    assert.equal(formatExact(statsMod.getStats().latest.downloads), '1,248');
  });

  test('_versionFromFilename parses semantic version', () => {
    assert.equal(statsMod._versionFromFilename('deng-tool-rejoin-apk-v1.0.9.apk'), '1.0.9');
  });
});
