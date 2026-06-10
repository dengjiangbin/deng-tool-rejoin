#!/usr/bin/env node
/**
 * BLOCKER10ZM/ZO: post-push guard — protected dist/tracker.lua must exist on GitHub raw;
 * root tracker.lua must NOT be public on main.
 */
const https = require('https');
const path = require('path');
const fs = require('fs');

const DIST_URL = 'https://raw.githubusercontent.com/dengjiangbin/deng-fishtracker-dist/main/dist/tracker.lua';
const LEGACY_DIST_URL = 'https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/dist/tracker.lua';
const ROOT_URL = 'https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/tracker.lua';

function fetchHead(fetchUrl) {
  return new Promise((resolve, reject) => {
    https.get(fetchUrl, (res) => {
      res.resume();
      resolve(res.statusCode || 0);
    }).on('error', reject);
  });
}

function fetch(fetchUrl) {
  return new Promise((resolve, reject) => {
    https.get(fetchUrl, (res) => {
      if (res.statusCode !== 200) {
        reject(new Error(`HTTP ${res.statusCode} from raw GitHub`));
        res.resume();
        return;
      }
      let data = '';
      res.on('data', (c) => { data += c; });
      res.on('end', () => resolve(data));
    }).on('error', reject);
  });
}

(async () => {
  const rootStatus = await fetchHead(`${ROOT_URL}?v=${Date.now()}`);
  if (rootStatus === 200) {
    console.error('GITHUB_DIST_RAW_VALIDATION FAILED');
    console.error('  - root tracker.lua is still public on GitHub main (expected 404)');
    process.exit(1);
  }
  console.log('GITHUB_ROOT_RAW_REMOVED OK');
  console.log('  url:', ROOT_URL);
  console.log('  status:', rootStatus);

  const src = await fetch(`${DIST_URL}?v=${Date.now()}`);
  const errors = [];
  if (src.includes('<!DOCTYPE') || src.includes('<html')) errors.push('raw content looks like HTML error page');
  if (Buffer.byteLength(src, 'utf8') < 4096) errors.push('dist/tracker.lua too small on GitHub raw');
  if (!src.includes('BLOCKER10ZT3')) errors.push('GitHub dist missing BLOCKER10ZT3 build marker');
  if (/BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10/.test(src) && !src.includes('BLOCKER10ZT3')) {
    errors.push('GitHub dist still looks like stale BLOCKER10ZW build');
  }
  if (/^\s*--\s*=+\s*\n\s*--\s+Fish It Unified Tracker/m.test(src)) {
    errors.push('GitHub dist still looks like unobfuscated dev header');
  }
  if (errors.length) {
    console.error('GITHUB_DIST_RAW_VALIDATION FAILED');
    for (const err of errors) console.error('  -', err);
    process.exit(1);
  }
  const tmp = path.join(__dirname, '..', '_dist_tracker_github_raw.lua');
  fs.writeFileSync(tmp, src, 'utf8');
  const { auditFile } = require('./audit_tracker_secrets');
  const audit = auditFile(tmp);
  if (!audit.ok) {
    console.error('GITHUB_DIST_RAW_VALIDATION FAILED');
    console.error('  - secret audit:', audit.hits.join(', '));
    process.exit(1);
  }
  const legacyStatus = await fetchHead(`${LEGACY_DIST_URL}?v=${Date.now()}`);
  console.log('GITHUB_LEGACY_DIST_NOTE');
  console.log('  url:', LEGACY_DIST_URL);
  console.log('  status:', legacyStatus, '(canonical loader uses deng-fishtracker-dist)');

  console.log('GITHUB_DIST_RAW_VALIDATION OK');
  console.log('  url:', DIST_URL);
  console.log('  bytes:', Buffer.byteLength(src, 'utf8'));
})().catch((e) => {
  console.error('GITHUB_DIST_RAW_VALIDATION FAILED:', e.message);
  process.exit(1);
});
