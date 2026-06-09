#!/usr/bin/env node
/**
 * BLOCKER10J: static compile guard for tracker.lua
 * - luaparse catches Lua syntax errors (continue stripped for parse-only)
 * - luau-compile (when present) catches Luau register-limit failures
 */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const luaparse = require(path.join(__dirname, '..', 'site', 'node_modules', 'luaparse'));

const trackerPath = path.resolve(process.argv[2] || path.join(__dirname, '..', 'tracker.lua'));
let src = fs.readFileSync(trackerPath, 'utf8');

const errors = [];

if (src.charCodeAt(0) === 0xfeff) {
  errors.push('UTF-8 BOM present at start of file');
}
if (/^\s*loadstring\s*\(/.test(src)) {
  errors.push('tracker.lua must not begin with loadstring() wrapper');
}
if (!src.includes('TRACKER_BOOT_BEGIN BLOCKER10Z7_METADATA_SPECIES_EXTRACTION_2026_06_08')) {
  errors.push('TRACKER_BOOT_BEGIN BLOCKER10Z7 marker missing');
}
if (!src.includes('BLOCKER10ZA_PLAYERDATA_ITEMUTILITY_STONES_UPLOAD_2026_06_09')) {
  errors.push('BLOCKER10ZA build marker missing');
}
if (!src.includes('scanPlayerDataItemUtilityInventory')) {
  errors.push('scanPlayerDataItemUtilityInventory missing — PlayerData ItemUtility scan required');
}
if (!src.includes('BLOCKER10Z7_METADATA_SPECIES_EXTRACTION_2026_06_08')) {
  errors.push('BLOCKER10Z7 build marker missing');
}
if (!src.includes('LiveSafe.extractFishMetadata')) {
  errors.push('LiveSafe.extractFishMetadata missing — metadata species extraction required');
}
if (!src.includes('LiveSafe.extractReplionMetaFields')) {
  errors.push('LiveSafe.extractReplionMetaFields missing — metadata fish identity required');
}
if (!src.includes('LiveSafe.resolveOwnedStorageKey')) {
  errors.push('LiveSafe.resolveOwnedStorageKey missing — UUID-per-instance amount fix required');
}
if (src.includes('payload.inventoryUiHints')) {
  errors.push('payload.inventoryUiHints must not be sent — Replion is source of truth');
}
if (!src.includes('parseCatchNameFull')) {
  errors.push('parseCatchNameFull catch normalizer missing');
}
if (src.includes('TRACKER_BOOT_BEGIN BLOCKER10J')) {
  errors.push('stale TRACKER_BOOT_BEGIN BLOCKER10J must be removed');
}
if (!src.includes('buildRawProof')) {
  errors.push('buildRawProof helper missing');
}
if (!src.includes('/api/fishit-tracker/update-backpack')) {
  errors.push('canonical fishit-tracker POST URL missing');
}
if (!src.includes('SYNC_UPLOAD_DEBUG reason=') || !src.includes('requestFn=')) {
  errors.push('SYNC_UPLOAD_DEBUG extended fields missing');
}
if (!/TRACKER_BUILD\s*=/.test(src)) {
  errors.push('TRACKER_BUILD assignment missing');
}
if (!src.includes('local LiveSafe = {')) {
  errors.push('LiveSafe register-pack table missing (Luau 200-register guard)');
}
if (!src.includes('safeMinimalMode = true')) {
  errors.push('safeMinimalMode must default true');
}
if (!src.includes('lightSyncEnabled = true')) {
  errors.push('lightSyncEnabled must default true');
}
if (!src.includes('lightSyncIntervalSeconds = 10')) {
  errors.push('lightSyncIntervalSeconds must default 10');
}
if (!src.includes('repeatUpload = true')) {
  errors.push('repeatUpload must default true');
}
if (!src.includes('oneShot = false')) {
  errors.push('oneShot must default false');
}
if (!src.includes('enableHeavyCatalog = false')) {
  errors.push('enableHeavyCatalog must default false');
}
if (!src.includes('playerDataOnly = true')) {
  errors.push('playerDataOnly must default true');
}
if (!src.includes('clientCatalogResolution = false')) {
  errors.push('clientCatalogResolution must default false');
}
if (!src.includes('SYNC_LOOP_STARTED interval=')) {
  errors.push('SYNC_LOOP_STARTED log missing');
}
if (!src.includes('SYNC_UPLOAD ok=true')) {
  errors.push('SYNC_UPLOAD log missing');
}
if (/^<<<<<<<|^>>>>>>>|^=======\s*$/.test(src)) {
  errors.push('merge conflict markers detected');
}
if (/return true\nend\n\n\s+return true\nend/.test(src)) {
  errors.push('orphan duplicate return/end block detected');
}

const forLuaparse = src.replace(/\bthen\s+continue\s+end\b/g, 'then end');
try {
  luaparse.parse(forLuaparse);
} catch (e) {
  errors.push(`luaparse compile error: ${e.message} (line ${e.line}, column ${e.column})`);
}

const luauCandidates = [
  path.join(__dirname, '..', '_luau', 'luau-compile.exe'),
  path.join(__dirname, '..', '_luau', 'luau-compile'),
  'luau-compile',
];
let luauChecked = false;
for (const bin of luauCandidates) {
  try {
    execFileSync(bin, [trackerPath], { stdio: 'pipe', encoding: 'utf8' });
    luauChecked = true;
    break;
  } catch (e) {
    const msg = (e.stderr || e.stdout || e.message || '').toString();
    if (msg.includes('CompileError') || msg.includes('Exceeded')) {
      errors.push(`luau-compile error: ${msg.split('\n').find((l) => l.includes('CompileError') || l.includes('Exceeded')) || msg.trim()}`);
      luauChecked = true;
      break;
    }
    if (e.code !== 'ENOENT') {
      errors.push(`luau-compile failed: ${msg.trim().slice(0, 240)}`);
      luauChecked = true;
      break;
    }
  }
}
if (!luauChecked) {
  errors.push('luau-compile not available — install to _luau/ (see scripts/setup_luau_compile.ps1)');
}

if (errors.length) {
  console.error('TRACKER_COMPILE_VALIDATION FAILED');
  for (const err of errors) console.error('  -', err);
  process.exit(1);
}

console.log('TRACKER_COMPILE_VALIDATION OK');
console.log('  file:', trackerPath);
console.log('  bytes:', Buffer.byteLength(src, 'utf8'));
console.log('  boot:', (src.match(/TRACKER_BOOT_BEGIN[^\n]*/) || [''])[0]);
console.log('  build:', (src.match(/TRACKER_BUILD\s*=\s*"([^"]+)"/) || [])[1] || '?');
console.log('  luau-compile:', luauChecked ? 'passed' : 'skipped');
