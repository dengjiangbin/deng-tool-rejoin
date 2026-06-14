'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..', '..');
const SITE = path.join(ROOT, 'site');

const ALLOWLIST = new Set([
  path.normalize('site/src/wmicRuntimeGuard.js'),
  path.normalize('site/tests/no_wmic_static_scan.test.js'),
  path.normalize('site/tests/no_wmic_runtime_guard.test.js'),
  path.normalize('site/proofs/cloudflare_direct_ingest_proof.json'),
  path.normalize('site/proofs/no_wmic_runtime_proof.json'),
  path.normalize('_gh_tracker_check.lua'),
  path.normalize('scripts/no-wmic-runtime-proof.ps1'),
  path.normalize('scripts/stability-proof.ps1'),
]);

const SCAN_DIRS = ['site', 'scripts'];
const SCAN_EXT = new Set(['.js', '.ps1', '.bat', '.json', '.mjs', '.cjs']);

function walk(dir, files = []) {
  if (!fs.existsSync(dir)) return files;
  for (const name of fs.readdirSync(dir)) {
    if (name === 'node_modules' || name === '.git') continue;
    const full = path.join(dir, name);
    const rel = path.normalize(path.relative(ROOT, full));
    if (fs.statSync(full).isDirectory()) {
      walk(full, files);
      continue;
    }
    const ext = path.extname(name).toLowerCase();
    if (!SCAN_EXT.has(ext)) continue;
    if (ALLOWLIST.has(rel)) continue;
    files.push(full);
  }
  return files;
}

function findWmicReferences(filePath) {
  const text = fs.readFileSync(filePath, 'utf8');
  const hits = [];
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (/\bwmic(\.exe)?\b/i.test(line)) {
      hits.push({ line: i + 1, text: line.trim() });
    }
  }
  return hits;
}

describe('no wmic static scan', () => {
  test('source tree has no wmic references outside allowlist', () => {
    const offenders = [];
    for (const dirName of SCAN_DIRS) {
      const dir = path.join(ROOT, dirName);
      for (const file of walk(dir)) {
        const hits = findWmicReferences(file);
        if (hits.length > 0) {
          offenders.push({ file: path.relative(ROOT, file), hits });
        }
      }
    }
    if (offenders.length > 0) {
      const detail = offenders.map((o) => `${o.file}: ${o.hits.map((h) => `L${h.line}`).join(', ')}`).join('\n');
      assert.fail(`wmic references found outside allowlist:\n${detail}`);
    }
    assert.equal(offenders.length, 0);
  });
});
