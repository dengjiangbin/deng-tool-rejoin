'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const MOJIBAKE_PATTERNS = [
  /ΓÇ/,
  /â€“/,
  /â€™/,
  /â€œ/,
  /â€/,
  /Â(?![a-z]{2,})/,
  /\uFFFD/,
];

const SCAN_ROOTS = [
  path.join(__dirname, '..', 'src'),
  path.join(__dirname, '..', 'views'),
];

const manifest = require('../src/inventoryAssetManifest.json');
const ACTIVE_ASSETS = [
  path.join(__dirname, '..', 'public', 'assets', manifest.js),
  path.join(__dirname, '..', 'public', 'assets', manifest.css),
];

function walk(dir, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else if (/\.(ejs|js|css|json|html)$/i.test(entry.name)) out.push(full);
  }
  return out;
}

describe('site-wide mojibake regression', () => {
  test('inventory source uses UTF-8 dash constants not corrupted bytes', () => {
    const source = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs'),
      'utf8',
    );
    assert.match(source, /const EMPTY_STAT = '\\u2014'/);
    assert.match(source, /const EN_DASH = '\\u2013'/);
    assert.match(source, /const EM_DASH = '\\u2014'/);
    for (const re of MOJIBAKE_PATTERNS) {
      assert.doesNotMatch(source, re, `mojibake pattern ${re} found in inventory source`);
    }
  });

  test('active built inventory assets contain no mojibake markers', () => {
    for (const file of ACTIVE_ASSETS) {
      const text = fs.readFileSync(file, 'utf8');
      for (const re of MOJIBAKE_PATTERNS) {
        assert.doesNotMatch(text, re, `mojibake pattern ${re} found in ${path.basename(file)}`);
      }
    }
  });

  test('site templates and src files contain no mojibake markers', () => {
    const files = SCAN_ROOTS.flatMap((root) => walk(root));
    const offenders = [];
    for (const file of files) {
      const text = fs.readFileSync(file, 'utf8');
      for (const re of MOJIBAKE_PATTERNS) {
        if (re.test(text)) {
          offenders.push(`${path.relative(path.join(__dirname, '..'), file)}:${re}`);
          break;
        }
      }
    }
    assert.deepEqual(offenders, []);
  });
});
