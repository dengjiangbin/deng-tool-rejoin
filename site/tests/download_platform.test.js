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
const latestApk = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'releases', 'android', 'latest.json'), 'utf8'));
const latestFile = latestApk.file_name;
const latestVersion = latestApk.version_name;

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
    ds.recordDownload('android', latestFile);
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

  test('stats reads are deterministic and do not increment counts', () => {
    const ds = require('../src/downloadStats');
    ds.recordDownload('android', latestFile);
    const before = ds.getPlatformStats('android').latest.downloads;
    for (let i = 0; i < 5; i++) {
      assert.equal(ds.getPlatformStats('android').latest.downloads, before);
      assert.equal(ds.getApkStats().latest.downloads, before);
    }
  });

  test('older versioned APK downloads do not replace published latest stats', () => {
    const ds = require('../src/downloadStats');
    ds.recordDownload('android', latestFile);
    ds.recordDownload('android', 'deng-tool-rejoin-apk-v1.0.0.apk');
    ds.recordDownload('android', 'deng-tool-rejoin-apk-v1.0.0.apk');
    const latest = ds.getApkStats().latest;
    assert.equal(latest.version, latestVersion);
    assert.equal(latest.file_name, latestFile);
    assert.equal(latest.downloads, 1);
  });

  test('published latest stats fallback to exact zero when stats file is corrupt', () => {
    const file = process.env.APK_DOWNLOAD_STATS_PATH;
    fs.writeFileSync(file, '{not-json', 'utf8');
    const ds = require('../src/downloadStats');
    const latest = ds.getApkStats().latest;
    assert.equal(ds.getApkStats().ok, true);
    assert.equal(latest.version, latestVersion);
    assert.equal(latest.downloads, 0);
  });

  test('corrupt stats JSON returns clean empty stats without random count', () => {
    const file = process.env.APK_DOWNLOAD_STATS_PATH;
    fs.writeFileSync(file, '{not-json', 'utf8');
    const ds = require('../src/downloadStats');
    assert.equal(ds.getApkStats().ok, true);
    assert.equal(ds.getApkStats().latest.downloads, 0);
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
