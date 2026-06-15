#!/usr/bin/env node
/**
 * BLOCKER10ZM: validate protected dist/tracker.lua before release.
 * Obfuscate private raw tracker.lua manually, then save output as dist/tracker.lua
 */
const fs = require('fs');
const path = require('path');
const { auditFile } = require('./audit_tracker_secrets');
const { resolveRawTrackerSourcePath } = require('./trackerRawSourcePath');

const root = path.join(__dirname, '..');
const rawPath = process.argv[2]
  ? path.resolve(process.argv[2])
  : resolveRawTrackerSourcePath({ root });
const distPath = path.resolve(process.argv[3] || path.join(root, 'dist', 'tracker.lua'));

const errors = [];

if (!fs.existsSync(distPath)) {
  errors.push(`missing protected dist — save obfuscated output to ${distPath}`);
}

let raw = '';
let dist = '';
if (rawPath && fs.existsSync(rawPath)) raw = fs.readFileSync(rawPath, 'utf8');
else console.log('SKIP raw compare: private tracker source is not present in public repo');
if (fs.existsSync(distPath)) dist = fs.readFileSync(distPath, 'utf8');

if (dist) {
  const distBytes = Buffer.byteLength(dist, 'utf8');
  if (distBytes < 4096) {
    errors.push(`dist too small (${distBytes} bytes) — expected obfuscated output`);
  }
  if (raw && dist.trim() === raw.trim()) {
    errors.push('dist/tracker.lua must not be an unchanged copy of private raw tracker.lua');
  }
  if (/^\s*--\s*=+\s*\n\s*--\s+Fish It Unified Tracker/m.test(dist)) {
    errors.push('dist still looks like raw dev header — re-run obfuscation on private raw source');
  }
  if (dist.includes('local TRACKER_BUILD = "BLOCKER10ZL_LURAPH_PROTECTED_RELEASE_2026_06_10"')
    && dist.includes('local function fishLog(msg')
    && dist.includes('scanPlayerDataGameItemDbInventory')) {
    errors.push('dist appears to be unobfuscated source — obfuscate private raw source first');
  }
  const audit = auditFile(distPath);
  if (!audit.ok && !audit.missing) {
    errors.push(`secret audit failed: ${audit.hits.join(', ')}`);
  }
  let productionTrackerBuild = null;
  try {
    productionTrackerBuild = require('../site/src/fishitTrackerBuild').PRODUCTION_TRACKER_BUILD;
  } catch (_) { productionTrackerBuild = null; }
  const legacyMarkerOk = dist.includes('BLOCKER10ZT5')
    || dist.includes('BLOCKER10ZT4')
    || dist.includes('BLOCKER10ZT3');
  const productionMarkerOk = !!productionTrackerBuild && dist.includes(productionTrackerBuild);
  if (!legacyMarkerOk && !productionMarkerOk) {
    errors.push(
      `dist header must contain the current production build marker (${productionTrackerBuild || 'unknown'})`
      + ' or a legacy BLOCKER10ZT3/ZT4/ZT5 marker',
    );
  }
}

if (raw) {
  const rawAudit = auditFile(rawPath);
  if (!rawAudit.ok) {
    errors.push(`raw secret audit failed: ${rawAudit.hits.join(', ')}`);
  }
}

if (errors.length) {
  console.error('DIST_TRACKER_VALIDATION FAILED');
  for (const err of errors) console.error('  -', err);
  console.error('');
  console.error('Manual steps:');
  console.error('  1. set TRACKER_RAW_SOURCE_PATH to private tracker.lua');
  console.error('  2. node scripts/validate_tracker_compile.js');
  console.error(`  3. Obfuscate private raw source`);
  console.error(`  4. Save output as ${distPath}`);
  process.exit(1);
}

console.log('DIST_TRACKER_VALIDATION OK');
console.log('  raw:', rawPath || '(skipped — private source not present)');
console.log('  dist:', distPath);
console.log('  dist bytes:', Buffer.byteLength(dist, 'utf8'));
const { PROTECTED_DIST_RAW_URL_CACHE_BUST } = require('../site/src/fishitTrackerLoadstring');
console.log('  public URL:', PROTECTED_DIST_RAW_URL_CACHE_BUST);
