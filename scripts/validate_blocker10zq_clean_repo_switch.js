#!/usr/bin/env node
/**
 * BLOCKER10ZQ: assert live /inventory uses clean dist repo loadstring and BLOCKER10ZQ marker.
 */
const http = require('http');
const {
  BLOCKER10ZS_GITHUB_CACHE_REQUEST_AND_TRANSCENDED_STONE_LIVE_IMAGE_MARKER,
} = require('../site/src/fishitTrackerBuild');
const {
  CLEAN_PUBLIC_TRACKER_GITHUB_REPO,
  CLEAN_TRACKER_LOADSTRING,
  PROTECTED_DIST_RAW_URL,
  LEGACY_TRACKER_GITHUB_REPO,
} = require('../site/src/fishitTrackerLoadstring');

const SITE_HOST = process.env.TOOL_SITE_HOST || '127.0.0.1';
const SITE_PORT = Number(process.env.TOOL_SITE_PORT || 8791);
const CLEAN_DIST_RAW = `https://raw.githubusercontent.com/${CLEAN_PUBLIC_TRACKER_GITHUB_REPO}/main/dist/tracker.lua`;
const LEGACY_ROOT_RAW = `https://raw.githubusercontent.com/${LEGACY_TRACKER_GITHUB_REPO}/main/tracker.lua`;
const OLD_LOADSTRING = `loadstring(game:HttpGet("https://raw.githubusercontent.com/${LEGACY_TRACKER_GITHUB_REPO}/main/dist/tracker.lua"))()`;

function fetchText(path) {
  return new Promise((resolve, reject) => {
    http.get({ hostname: SITE_HOST, port: SITE_PORT, path, headers: { Accept: 'text/html' } }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => resolve({ status: res.statusCode || 0, body }));
    }).on('error', reject);
  });
}

async function main() {
  const errors = [];

  if (BLOCKER10ZS_GITHUB_CACHE_REQUEST_AND_TRANSCENDED_STONE_LIVE_IMAGE_MARKER
    !== 'BLOCKER10ZS_GITHUB_CACHE_REQUEST_AND_TRANSCENDED_STONE_LIVE_IMAGE_2026_06_10') {
    errors.push('BLOCKER10ZS marker mismatch in fishitTrackerBuild.js');
  }
  if (!PROTECTED_DIST_RAW_URL.includes(CLEAN_PUBLIC_TRACKER_GITHUB_REPO)) {
    errors.push('PROTECTED_DIST_RAW_URL must use clean public repo');
  }
  if (CLEAN_TRACKER_LOADSTRING !== `loadstring(game:HttpGet("${CLEAN_DIST_RAW}"))()`) {
    errors.push('CLEAN_TRACKER_LOADSTRING must target clean repo dist URL');
  }

  const { status, body } = await fetchText('/inventory');
  if (status !== 200) errors.push(`/inventory HTTP ${status} (expected 200)`);

  if (!body.includes(BLOCKER10ZS_GITHUB_CACHE_REQUEST_AND_TRANSCENDED_STONE_LIVE_IMAGE_MARKER)) {
    errors.push('/inventory missing BLOCKER10ZS marker');
  }
  if (!body.includes(CLEAN_TRACKER_LOADSTRING)) {
    errors.push('/inventory missing clean repo canonical loadstring');
  }
  if (!body.includes(CLEAN_DIST_RAW)) {
    errors.push('/inventory missing clean repo dist raw URL');
  }
  if (body.includes(LEGACY_ROOT_RAW)) {
    errors.push('/inventory exposes legacy root raw tracker.lua URL');
  }
  if (body.includes('/main/tracker.lua')) {
    errors.push('/inventory exposes root /main/tracker.lua URL');
  }
  if (body.includes(OLD_LOADSTRING)) {
    errors.push('/inventory still contains legacy repo loadstring');
  }
  if (!body.includes('id="usernameInput"')) {
    errors.push('/inventory missing username input');
  }
  if (body.includes('id="usernameInput" disabled')) {
    errors.push('/inventory username input disabled');
  }
  if (!body.includes('copyTrackerScript')) {
    errors.push('/inventory missing copy script fallback');
  }

  if (errors.length) {
    console.error('BLOCKER10ZQ_CLEAN_REPO_SWITCH_VALIDATION FAILED');
    for (const err of errors) console.error('  -', err);
    process.exit(1);
  }

  console.log('BLOCKER10ZQ_CLEAN_REPO_SWITCH_VALIDATION OK');
  console.log('  marker:', BLOCKER10ZS_GITHUB_CACHE_REQUEST_AND_TRANSCENDED_STONE_LIVE_IMAGE_MARKER);
  console.log('  loadstring:', CLEAN_TRACKER_LOADSTRING);
  console.log('  /inventory HTTP:', status);
}

main().catch((e) => {
  console.error('BLOCKER10ZQ_CLEAN_REPO_SWITCH_VALIDATION FAILED:', e.message);
  process.exit(1);
});
