#!/usr/bin/env node
/**
 * Post-deploy guard: download canonical GitHub tracker.lua and Luau-compile
 * both the public dist wrapper and its decoded payload.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const https = require('https');
const crypto = require('crypto');
const { execFileSync } = require('child_process');

const ROOT = path.join(__dirname, '..');
const CANONICAL_URL = process.env.FISH_IT_TRACKER_RAW_URL
  || 'https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua';
const OUT_DIST = path.join(ROOT, '_gh_tracker_compile_check.lua');
const OUT_DECODED = path.join(ROOT, '_gh_tracker_compile_decoded.lua');

function fetch(url) {
  return new Promise((resolve, reject) => {
    https.get(url, (res) => {
      if (res.statusCode !== 200) {
        reject(new Error(`HTTP ${res.statusCode} from ${url}`));
        res.resume();
        return;
      }
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => resolve(data));
    }).on('error', reject);
  });
}

function fetchGithubContents(repo, filePath, ref = 'main') {
  return new Promise((resolve, reject) => {
    https.get({
      hostname: 'api.github.com',
      path: `/repos/${repo}/contents/${encodeURIComponent(filePath)}?ref=${encodeURIComponent(ref)}`,
      headers: {
        'User-Agent': 'deng-github-tracker-compile',
        Accept: 'application/vnd.github+json',
      },
    }, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        if (res.statusCode !== 200) {
          reject(new Error(`GitHub contents API HTTP ${res.statusCode}`));
          return;
        }
        try {
          const parsed = JSON.parse(data);
          resolve(Buffer.from(String(parsed.content || '').replace(/\n/g, ''), 'base64').toString('utf8'));
        } catch (e) {
          reject(e);
        }
      });
    }).on('error', reject);
  });
}

function decodeDistPayload(distSrc) {
  const m = distSrc.match(/local __B=\[\[([\s\S]*?)\]\]\nlocal __A=/);
  if (!m) throw new Error('dist decode anchor missing');
  return Buffer.from(m[1], 'base64').toString('utf8');
}

function compileWithLuau(filePath) {
  const bins = [
    path.join(ROOT, '_luau', 'luau-compile.exe'),
    path.join(ROOT, '_luau', 'luau-compile'),
    'luau-compile',
  ];
  for (const bin of bins) {
    try {
      execFileSync(bin, [filePath], { stdio: 'pipe', encoding: 'utf8' });
      return bin;
    } catch (e) {
      const msg = (e.stderr || e.stdout || e.message || '').toString();
      if (e.code === 'ENOENT') continue;
      throw new Error(msg.trim().split('\n').slice(0, 4).join(' | '));
    }
  }
  throw new Error('luau-compile not available');
}

(async () => {
  const errors = [];
  const repo = 'dengjiangbin/fish-it';
  const dist = await fetchGithubContents(repo, 'tracker.lua', 'main');
  fs.writeFileSync(OUT_DIST, dist, 'utf8');
  const expectedSha = crypto.createHash('sha256').update(dist, 'utf8').digest('hex');

  let rawMain = '';
  try {
    rawMain = await fetch(`${CANONICAL_URL}?v=${Date.now()}`);
    const rawSha = crypto.createHash('sha256').update(rawMain, 'utf8').digest('hex');
    if (rawSha !== expectedSha) {
      errors.push(`raw.githubusercontent.com/main is stale (api=${expectedSha.slice(0, 12)} raw=${rawSha.slice(0, 12)})`);
    } else {
      console.log('GITHUB_TRACKER_COMPILE raw main CDN matches API blob');
    }
  } catch (e) {
    errors.push(`raw main fetch failed: ${e.message}`);
  }

  if (dist.includes('<!DOCTYPE') || dist.includes('<html')) {
    errors.push('raw content looks like HTML error page');
  }
  if (Buffer.byteLength(dist, 'utf8') < 4096) {
    errors.push('tracker.lua too small on GitHub raw');
  }
  if (!dist.includes('local __B=[[')) {
    errors.push('canonical tracker.lua is not a protected dist wrapper');
  }

  let decoded = '';
  try {
    decoded = decodeDistPayload(dist);
    fs.writeFileSync(OUT_DECODED, decoded, 'utf8');
  } catch (e) {
    errors.push(e.message);
  }

  if (decoded) {
    if (!/;\(function\(\)/m.test(decoded)) {
      errors.push('decoded payload missing ;(function() IIFE semicolon guard');
    }
    if (!decoded.includes('totems=%d totemQty=%d')) {
      errors.push('decoded payload missing PLAYERDATA_GAMEITEMDB_UPLOAD_OK totem proof');
    }
    if (/PLAYERDATA_GAMEITEMDB_UPLOAD fish=%d stones=%d unresolved=%d/.test(decoded)
      && !/PLAYERDATA_GAMEITEMDB_UPLOAD fish=%d stones=%d totems=%d unresolved=%d/.test(decoded)) {
      errors.push('decoded payload still uses legacy upload log without totems=');
    }
    if (/^\(function\(\)/m.test(decoded.replace(/^[\s\S]*?TRACKER_BOOT_BEGIN[^\n]*\n/, ''))) {
      errors.push('decoded payload has bare (function() without leading semicolon');
    }
  }

  try {
    const distBin = compileWithLuau(OUT_DIST);
    console.log('GITHUB_TRACKER_COMPILE dist ok via', distBin);
  } catch (e) {
    errors.push(`dist luau-compile failed: ${e.message}`);
  }

  if (decoded) {
    try {
      const decodedBin = compileWithLuau(OUT_DECODED);
      console.log('GITHUB_TRACKER_COMPILE decoded ok via', decodedBin);
    } catch (e) {
      errors.push(`decoded luau-compile failed: ${e.message}`);
    }
  }

  if (errors.length) {
    console.error('GITHUB_TRACKER_COMPILE FAILED');
    for (const err of errors) console.error('  -', err);
    process.exit(1);
  }

  console.log('GITHUB_TRACKER_COMPILE OK');
  console.log('  url:', CANONICAL_URL);
  console.log('  dist bytes:', Buffer.byteLength(dist, 'utf8'));
  console.log('  decoded bytes:', Buffer.byteLength(decoded, 'utf8'));
  console.log('  build:', (decoded.match(/TRACKER_BUILD\s*=\s*"([^"]+)"/) || [])[1] || '?');
})().catch((e) => {
  console.error('GITHUB_TRACKER_COMPILE FAILED:', e.message);
  process.exit(1);
});
