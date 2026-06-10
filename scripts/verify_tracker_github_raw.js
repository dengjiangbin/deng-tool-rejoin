#!/usr/bin/env node
/**
 * BLOCKER10ZM: post-push guard — protected dist/tracker.lua must exist on GitHub raw.
 */
const https = require('https');
const path = require('path');
const fs = require('fs');

const url = 'https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/dist/tracker.lua';

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
  const src = await fetch(`${url}?v=${Date.now()}`);
  const errors = [];
  if (src.includes('<!DOCTYPE') || src.includes('<html')) errors.push('raw content looks like HTML error page');
  if (Buffer.byteLength(src, 'utf8') < 4096) errors.push('dist/tracker.lua too small on GitHub raw');
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
  console.log('GITHUB_DIST_RAW_VALIDATION OK');
  console.log('  url:', url);
  console.log('  bytes:', Buffer.byteLength(src, 'utf8'));
})().catch((e) => {
  console.error('GITHUB_DIST_RAW_VALIDATION FAILED:', e.message);
  process.exit(1);
});
