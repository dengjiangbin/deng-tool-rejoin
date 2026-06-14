'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

process.env.NODE_ENV = 'test';

const ROOT = path.join(__dirname, '..', '..');
const MANIFEST_PATH = path.join(ROOT, 'releases', 'android', 'latest.json');
const RELEASES_DIR = path.join(ROOT, 'releases', 'android');
const OLD_APK = 'deng-tool-rejoin-apk-v1.0.13.apk';
const MARKER = 'APK_SYSTEM_BROWSER_DISCORD_AUTH_AIO_2026_06_14';

function loadManifest() {
  const raw = fs.readFileSync(MANIFEST_PATH, 'utf8').replace(/^\uFEFF/, '');
  return JSON.parse(raw);
}

function sha256File(filePath) {
  const hash = crypto.createHash('sha256');
  hash.update(fs.readFileSync(filePath));
  return hash.digest('hex');
}

describe('APK release aio 2026-06-14', () => {
  test('latest.json points to deng-all-in-one canonical filename and v2.2.0', () => {
    const m = loadManifest();
    assert.match(m.file_name, /^deng-all-in-one-apk-v2\.2\.0\.apk$/);
    assert.equal(m.version_name, '2.2.0');
    assert.equal(m.version_code, 17);
    assert.match(m.build_marker, new RegExp(MARKER));
    assert.doesNotMatch(m.file_name, /deng-tool-rejoin-apk/);
  });

  test('published APK file exists and checksum matches manifest', () => {
    const m = loadManifest();
    const apkPath = path.join(RELEASES_DIR, m.file_name);
    assert.ok(fs.existsSync(apkPath), `missing ${apkPath}`);
    const sha = sha256File(apkPath);
    assert.equal(sha, m.sha256.toLowerCase());
    assert.equal(fs.statSync(apkPath).size, m.size_bytes);
    const bytes = fs.readFileSync(apkPath);
    assert.ok(bytes.includes(Buffer.from(MARKER)), 'APK must embed release marker string');
  });

  test('old deng-tool-rejoin APK artifact is not the published latest', () => {
    const m = loadManifest();
    assert.notEqual(m.file_name, OLD_APK);
    assert.notEqual(m.version_name, '1.0.13');
    assert.ok(!fs.existsSync(path.join(RELEASES_DIR, OLD_APK)), 'legacy APK file must be removed');
  });

  test('download.ejs uses canonical latest APK link only', () => {
    const ejs = fs.readFileSync(path.join(__dirname, '..', 'views', 'download.ejs'), 'utf8');
    assert.match(ejs, /\/downloads\/deng-all-in-one-apk-latest\.apk/);
    assert.doesNotMatch(ejs, /deng-tool-rejoin-apk-latest/);
    assert.doesNotMatch(ejs, /tool\.deng\.my\.id/);
    assert.match(ejs, /DENG All In One/);
  });

  test('android build.gradle defaults to aio.deng.my.id', () => {
    const gradle = fs.readFileSync(
      path.join(ROOT, 'android', 'app', 'build.gradle.kts'),
      'utf8',
    );
    assert.match(gradle, /versionCode\s*=\s*17/);
    assert.match(gradle, /versionName\s*=\s*"2\.2\.0"/);
    assert.match(gradle, new RegExp(MARKER));
    assert.match(gradle, /"https:\/\/aio\.deng\.my\.id"/);
    assert.doesNotMatch(
      gradle,
      /bridgeUrl[\s\S]*?\?:\s*"https:\/\/tool\.deng\.my\.id"/,
    );
  });

  test('OAuthUrlHelper opens Discord and auth paths externally', () => {
    const helper = fs.readFileSync(
      path.join(ROOT, 'android', 'app', 'src', 'main', 'kotlin', 'my', 'id', 'deng', 'monitor', 'ui', 'OAuthUrlHelper.kt'),
      'utf8',
    );
    assert.match(helper, /discord\.com/);
    assert.match(helper, /\/auth\/discord/);
    assert.match(helper, /\/api\/aio\/auth\/callback/);
    assert.match(helper, /aio\.deng\.my\.id/);
  });

  test('LoginWebViewScreen uses Custom Tabs and external OAuth helper', () => {
    const login = fs.readFileSync(
      path.join(ROOT, 'android', 'app', 'src', 'main', 'kotlin', 'my', 'id', 'deng', 'monitor', 'ui', 'LoginWebViewScreen.kt'),
      'utf8',
    );
    assert.match(login, /CustomTabsIntent/);
    assert.match(login, /isExternalOAuthUrl/);
    assert.match(login, /PUBLIC_WEB_URL/);
    assert.doesNotMatch(login, /loadUrl\(.*discord/);
  });
});
