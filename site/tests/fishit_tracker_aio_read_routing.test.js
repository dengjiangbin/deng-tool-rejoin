'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const SOURCE_EJS = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const CF_REF = path.join(__dirname, '..', '..', 'config', 'cloudflared-ingress.reference.yml');
const UPLOAD_PATHS = require('../src/trackerUploadPaths');

function cloudflaredRefText() {
  return fs.readFileSync(CF_REF, 'utf8');
}

describe('tracker aio read routing', () => {
  test('frontend polls /api/tracker/get-backpack not ingest-only /api/fishit-tracker path', () => {
    const src = fs.readFileSync(SOURCE_EJS, 'utf8');
    assert.match(src, /const TRACKER_READ_API = '\/api\/tracker'/);
    assert.match(src, /\$\{TRACKER_READ_API\}\/get-backpack\//);
    assert.doesNotMatch(src, /fetch\(`\/api\/fishit-tracker\/get-backpack\//);
  });

  test('frontend image reads prefer /api/tracker paths', () => {
    const src = fs.readFileSync(SOURCE_EJS, 'utf8');
    assert.match(src, /\$\{TRACKER_READ_API\}\/image\//);
    assert.match(src, /function trackerReadPath\(/);
  });

  test('cloudflared reference routes only POST upload paths to ingest', () => {
    const ref = cloudflaredRefText();
    assert.doesNotMatch(ref, /path:\s*\^\/api\/fishit-tracker\/\.\*/);
    assert.doesNotMatch(ref, /path:\s*\^\/api\/fish-it-tracker\/\.\*/);
    assert.match(ref, /path:\s*\^\/api\/fishit-tracker\/update-backpack\$/);
    assert.match(ref, /path:\s*\^\/api\/tracker\/update-backpack/);
    assert.match(ref, /8792/);
    assert.match(ref, /8791/);
  });

  test('upload path registry is documented in cloudflared reference', () => {
    const ref = cloudflaredRefText();
    for (const uploadPath of UPLOAD_PATHS.UPLOAD_POST_PATHS) {
      const slug = uploadPath.replace(/^\//, '').replace(/\//g, '\\/');
      assert.match(ref, new RegExp(slug.replace(/\./g, '\\.')));
    }
  });

  test('trackerReadApp serves account-status on read lane without 8791 fallback', () => {
    const readSrc = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'trackerReadApp.js'),
      'utf8',
    );
    assert.match(readSrc, /\/api\/tracker\/account-status/);
    assert.match(readSrc, /precomputed-account-status/);
    assert.match(readSrc, /buildReadAccountStatusPayload/);
  });

  test('public homepage stats are cached to avoid per-request disk sync', () => {
    const routesSrc = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'fishitTrackerRoutes.js'),
      'utf8',
    );
    assert.match(routesSrc, /PUBLIC_STATS_CACHE_MS/);
    assert.match(routesSrc, /_publicNetworkStatsCache/);
    assert.match(routesSrc, /DISK_SYNC_MIN_MS/);
  });
});
