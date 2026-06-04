#!/usr/bin/env node
/**
 * BLOCKER10J: post-push guard — raw GitHub tracker.lua must be valid Lua source.
 */
const https = require('https');
const { execFileSync } = require('child_process');
const path = require('path');

const BUILD_MARKER = process.argv[2] || 'BLOCKER10J_SAFE_LIGHT_SYNC_10S_2026_06_04';
const url = `https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/tracker.lua?t=${Date.now()}`;

function fetch(url) {
  return new Promise((resolve, reject) => {
    https.get(url, (res) => {
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
  const src = await fetch(url);
  const errors = [];
  if (!src.startsWith('--')) errors.push('raw content does not start with Lua comment');
  if (src.includes('<!DOCTYPE') || src.includes('<html')) errors.push('raw content looks like HTML error page');
  if (!src.includes('TRACKER_BOOT_BEGIN BLOCKER10J')) errors.push('TRACKER_BOOT_BEGIN BLOCKER10J missing on raw GitHub');
  if (!src.includes(BUILD_MARKER)) errors.push(`build marker ${BUILD_MARKER} missing on raw GitHub`);
  if (errors.length) {
    console.error('GITHUB_RAW_VALIDATION FAILED');
    for (const e of errors) console.error('  -', e);
    process.exit(1);
  }
  const tmp = path.join(__dirname, '..', '_tracker_github_raw.lua');
  require('fs').writeFileSync(tmp, src, 'utf8');
  execFileSync(process.execPath, [path.join(__dirname, 'validate_tracker_compile.js'), tmp], { stdio: 'inherit' });
  console.log('GITHUB_RAW_VALIDATION OK');
  console.log('  bytes:', Buffer.byteLength(src, 'utf8'));
  console.log('  build:', BUILD_MARKER);
})().catch((e) => {
  console.error('GITHUB_RAW_VALIDATION FAILED:', e.message);
  process.exit(1);
});
