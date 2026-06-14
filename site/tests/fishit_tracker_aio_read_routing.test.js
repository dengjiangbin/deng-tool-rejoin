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

  test('fishitTrackerRoutes registers tracker read aliases on web router', () => {
    const routesSrc = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'fishitTrackerRoutes.js'),
      'utf8',
    );
    assert.match(routesSrc, /router\.get\('\/api\/tracker\/get-backpack\/:username'/);
    assert.match(routesSrc, /function registerTrackerWebReadAliases\(/);
    assert.match(routesSrc, /router\.get\(aliasPath, \.\.\.handlers\)/);
  });
});
