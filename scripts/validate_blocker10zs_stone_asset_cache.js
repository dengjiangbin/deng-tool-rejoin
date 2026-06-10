#!/usr/bin/env node
/**
 * BLOCKER10ZS: Transcended Stone local asset + live cache/version validation.
 */
const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const https = require('https');
const path = require('path');

const {
  BLOCKER10ZS_GITHUB_CACHE_REQUEST_AND_TRANSCENDED_STONE_LIVE_IMAGE_MARKER,
} = require('../site/src/fishitTrackerBuild');
const stoneDisplayMap = require('../site/src/fishitStoneDisplayMap');
const stoneImageAssets = require('../site/src/fishitStoneImageAssets');
const { buildTrackerPageLocals } = require('../site/src/fishitTrackerRoutes');
const ejs = require(path.join(__dirname, '..', 'site', 'node_modules', 'ejs'));

const STONE_FILE = 'stone_246_transcended.png';
const OLD_SIZE = 242830;
const EXPECTED_SIZE = 274134;
const LOCAL_PATH = path.join(__dirname, '..', 'site', 'data', 'stone_image_cache', STONE_FILE);
const SITE_HOST = process.env.TOOL_SITE_HOST || '127.0.0.1';
const SITE_PORT = Number(process.env.TOOL_SITE_PORT || 8791);
const LIVE_HOST = process.env.TOOL_SITE_PUBLIC_HOST || 'tool.deng.my.id';
const LIVE_PROTO = process.env.TOOL_SITE_PUBLIC_PROTO || 'https';
const trackerPath = path.join(__dirname, '..', 'site', 'views', 'fishit_tracker.ejs');

function sha256File(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function fetchBuffer(url) {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith('https') ? https : http;
    lib.get(url, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode || 0, headers: res.headers, body: Buffer.concat(chunks) }));
    }).on('error', reject);
  });
}

async function main() {
  const errors = [];

  if (BLOCKER10ZS_GITHUB_CACHE_REQUEST_AND_TRANSCENDED_STONE_LIVE_IMAGE_MARKER
    !== 'BLOCKER10ZS_GITHUB_CACHE_REQUEST_AND_TRANSCENDED_STONE_LIVE_IMAGE_2026_06_10') {
    errors.push('BLOCKER10ZS marker mismatch in fishitTrackerBuild.js');
  }

  if (!fs.existsSync(LOCAL_PATH)) {
    errors.push(`local stone file missing: ${LOCAL_PATH}`);
  }

  let localSize = 0;
  let localSha = '';
  if (fs.existsSync(LOCAL_PATH)) {
    localSize = fs.statSync(LOCAL_PATH).size;
    localSha = sha256File(LOCAL_PATH);
    if (localSize === OLD_SIZE) errors.push(`local stone file still old size (${OLD_SIZE})`);
    if (localSize !== EXPECTED_SIZE) errors.push(`local stone file size ${localSize} != expected ${EXPECTED_SIZE}`);
  }

  const meta = stoneDisplayMap.resolvePublicStoneMeta({ itemId: '246', stoneType: 'Double' });
  if (!meta || meta.displayName !== 'Transcended Stone') {
    errors.push('stone display map must resolve item 246/Double to Transcended Stone');
  }
  if (!meta || meta.imageFilename !== STONE_FILE) {
    errors.push('stone display map must resolve item 246/Double to stone_246_transcended.png');
  }

  const version = stoneImageAssets.getStoneAssetVersion(STONE_FILE);
  const versionedUrl = stoneImageAssets.getStoneAssetUrl('', STONE_FILE);
  if (!/\?v=\d+/.test(versionedUrl)) {
    errors.push('getStoneAssetUrl must append ?v=<mtime> query');
  }

  const stones = stoneImageAssets.attachStoneImagesToItems([
    { itemId: '246', stoneType: 'Double', name: 'Transcended Stone' },
  ], '');
  if (!stones[0].imageUrl || !stones[0].imageUrl.includes(`?v=${version}`)) {
    errors.push('attachStoneImagesToItems must emit versioned stone image URL');
  }

  const html = await ejs.renderFile(trackerPath, buildTrackerPageLocals({ query: {} }), { async: true });
  if (!html.includes(BLOCKER10ZS_GITHUB_CACHE_REQUEST_AND_TRANSCENDED_STONE_LIVE_IMAGE_MARKER)) {
    errors.push('/inventory template missing BLOCKER10ZS marker');
  }

  const localEndpoint = `http://${SITE_HOST}:${SITE_PORT}/api/fishit-tracker/assets/stones/${STONE_FILE}?v=${version}`;
  try {
    const localRes = await fetchBuffer(localEndpoint);
    if (localRes.status !== 200) errors.push(`local stone endpoint HTTP ${localRes.status}`);
    const cc = String(localRes.headers['cache-control'] || '');
    if (cc.includes('immutable')) errors.push('local stone route must not use immutable cache without content hash');
    if (localRes.body.length !== localSize) {
      errors.push(`local endpoint bytes ${localRes.body.length} != local file ${localSize}`);
    }
    if (localSha && crypto.createHash('sha256').update(localRes.body).digest('hex') !== localSha) {
      errors.push('local endpoint SHA256 does not match local file');
    }
  } catch (e) {
    errors.push(`local stone endpoint fetch failed: ${e.message}`);
  }

  const liveEndpoint = `${LIVE_PROTO}://${LIVE_HOST}/api/fishit-tracker/assets/stones/${STONE_FILE}?v=BLOCKER10ZS_${version}`;
  try {
    const liveRes = await fetchBuffer(liveEndpoint);
    if (liveRes.status !== 200) errors.push(`live stone endpoint HTTP ${liveRes.status}`);
    const liveSha = crypto.createHash('sha256').update(liveRes.body).digest('hex');
    if (liveRes.body.length !== localSize) {
      errors.push(`live endpoint bytes ${liveRes.body.length} != local file ${localSize}`);
    }
    if (localSha && liveSha !== localSha) {
      errors.push(`live endpoint SHA256 ${liveSha} != local ${localSha}`);
    }
    const cc = String(liveRes.headers['cache-control'] || '');
    if (cc.includes('immutable')) errors.push('live stone route must not use immutable cache without content hash');
  } catch (e) {
    errors.push(`live stone endpoint fetch failed: ${e.message}`);
  }

  if (errors.length) {
    console.error('BLOCKER10ZS_STONE_ASSET_CACHE_VALIDATION FAILED');
    for (const err of errors) console.error('  -', err);
    console.error('  local path:', LOCAL_PATH);
    console.error('  local size:', localSize, 'local sha256:', localSha);
    console.error('  versioned URL:', versionedUrl);
    process.exit(1);
  }

  console.log('BLOCKER10ZS_STONE_ASSET_CACHE_VALIDATION OK');
  console.log('  marker:', BLOCKER10ZS_GITHUB_CACHE_REQUEST_AND_TRANSCENDED_STONE_LIVE_IMAGE_MARKER);
  console.log('  local path:', LOCAL_PATH);
  console.log('  local size:', localSize);
  console.log('  local sha256:', localSha);
  console.log('  versioned URL example:', versionedUrl);
  console.log('  live endpoint:', liveEndpoint);
}

main().catch((e) => {
  console.error('BLOCKER10ZS_STONE_ASSET_CACHE_VALIDATION FAILED:', e.message);
  process.exit(1);
});
