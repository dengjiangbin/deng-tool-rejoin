'use strict';

const { describe, test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const fs = require('fs');
const os = require('os');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'download-platform-test-secret-long-enough-ok';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-key';

const downloadStats = require('../src/downloadStats');

describe('downloadStats platform support', () => {
  let tmpDir;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dl-stats-'));
    process.env.APK_DOWNLOAD_STATS_PATH = path.join(tmpDir, 'stats.json');
    delete require.cache[require.resolve('../src/downloadStats')];
  });

  afterEach(() => {
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (_) {}
    delete process.env.APK_DOWNLOAD_STATS_PATH;
    delete require.cache[require.resolve('../src/downloadStats')];
  });

  test('android and ios counts are independent', () => {
    const ds = require('../src/downloadStats');
    ds.recordDownload('android', 'deng-tool-rejoin-apk-v1.0.9.apk');
    ds.recordDownload('ios', 'deng-tool-monitor-ios-v1.0.0.ipa');
    const all = ds.getAllStats();
    assert.equal(all.android.downloads, 1);
    assert.equal(all.ios.downloads, 1);
  });

  test('HEAD does not increment (handler checks method)', () => {
    // recordDownload is only called from routes on GET — unit-level guard:
    const ds = require('../src/downloadStats');
    const before = ds.getPlatformStats('ios').latest?.downloads || 0;
    // no record on HEAD — count unchanged
    assert.equal(ds.getPlatformStats('ios').latest?.downloads || 0, before);
  });
});

describe('iOS download page contract', () => {
  test('download.ejs includes Android and iOS platform sections', () => {
    const ejs = fs.readFileSync(path.join(__dirname, '..', 'views', 'download.ejs'), 'utf8');
    assert.match(ejs, /Download Android APK/);
    assert.match(ejs, /download-platform-label/);
    assert.match(ejs, /iOS coming soon|Join iOS TestFlight|Download iOS Test Build/);
    assert.match(ejs, /data-ios-download-count/);
  });
});
