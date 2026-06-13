#!/usr/bin/env node
/**
 * BLOCKER10J: static compile guard for private raw tracker.lua
 * - luaparse catches Lua syntax errors (continue stripped for parse-only)
 * - luau-compile (when present) catches Luau register-limit failures
 *
 * Public repo: raw source is private/local-only. Set TRACKER_RAW_SOURCE_PATH or
 * PRIVATE_TRACKER_SOURCE_PATH to compile; otherwise raw compile is skipped.
 */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const luaparse = require(path.join(__dirname, '..', 'site', 'node_modules', 'luaparse'));
const { resolveRawTrackerSourcePath } = require('./trackerRawSourcePath');

const explicitPath = process.argv[2];
const trackerPath = explicitPath
  ? path.resolve(explicitPath)
  : resolveRawTrackerSourcePath();

if (!trackerPath || !fs.existsSync(trackerPath)) {
  console.log('SKIP raw compile: private tracker source is not present in public repo');
  console.log('  hint: set TRACKER_RAW_SOURCE_PATH to your private tracker.lua');
  process.exit(0);
}

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
if (!src.includes('BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10')
  && !src.includes('BLOCKER10ZT4_CONNECTION_FISH_PLAYERSTATS_PROOF_2026_06_10')
  && !src.includes('BLOCKER10ZT3_SYNC_STATUS_COIN_MOBILE_TABLE_2026_06_10')
  && !src.includes('BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10')
  && !src.includes('BLOCKER10ZW_PLAYERSTATS_REAL_ONLY_2026_06_10')
  && !src.includes('BLOCKER10ZV_PLAYERSTATS_REPLION_LEADERSTATS_2026_06_10')
  && !src.includes('BLOCKER10ZU_PLAYERSTATS_LEADERSTATS_2026_06_10')
  && !src.includes('BLOCKER10ZL_LURAPH_PROTECTED_RELEASE_2026_06_10')) {
  errors.push('BLOCKER10ZT4/ZT3/ZW/10ZV/10ZU/10ZL tracker build marker missing');
}
if (!src.includes('buildPlayerStatsPayload')) {
  errors.push('buildPlayerStatsPayload missing — Replion/leaderstats player stats required');
}
if (!src.includes('buildPlayerStatsDebugPayload')) {
  errors.push('buildPlayerStatsDebugPayload missing — real stat debug proof required');
}
if (!src.includes('coinProbe')) {
  errors.push('coinProbe debug proof missing — coin path tracing required');
}
if (!src.includes('resolveReplionStatData')) {
  errors.push('resolveReplionStatData missing — direct Replion coin read required');
}
if (!/local function readReplionData\(replion\)/.test(src) && !/^function readReplionData\(replion\)/m.test(src)) {
  errors.push('readReplionData early definition missing — line 951 runtime fix required');
}
if (!src.includes('runtimeLineFixProof')) {
  errors.push('runtimeLineFixProof log missing — reported line fix proof required');
}
if (!src.includes('ZidEulFJFvuuEFDERxXTMbGj')) {
  errors.push('RUNTIME_LINE_FIX_MARKER missing — failing log marker proof required');
}
if (!src.includes('payload.playerStats')) {
  errors.push('payload.playerStats upload missing');
}
if (!src.includes('payload.playerStatsDebug')) {
  errors.push('payload.playerStatsDebug upload missing');
}
if (!src.includes('parseCompactNumber')) {
  errors.push('parseCompactNumber missing — compact stat parsing required');
}
if (src.includes('scanPlayerGuiStatFallback') || src.includes('player_gui_fallback')) {
  errors.push('player_gui_fallback/screenshot stat path must not exist');
}
if (!src.includes('getDataReplionDirect')) {
  errors.push('getDataReplionDirect missing — direct Replion path required');
}
if (!src.includes('REPLION_DIRECT_OK')) {
  errors.push('REPLION_DIRECT_OK log missing');
}
if (!src.includes('PLAYERDATA_INVENTORY_READ')) {
  errors.push('PLAYERDATA_INVENTORY_READ log missing');
}
if (!src.includes('PLAYERDATA_GAMEITEMDB_UPLOAD_OK')) {
  errors.push('PLAYERDATA_GAMEITEMDB_UPLOAD_OK log missing');
}
if (!src.includes('totems=%d totemQty=%d')) {
  errors.push('PLAYERDATA_GAMEITEMDB_UPLOAD_OK totem proof fields missing');
}
if (!src.includes('TOTEM_SCAN_FOUND count=%d names=%s')) {
  errors.push('TOTEM_SCAN_FOUND debug proof log missing');
}
if (!src.includes('LiveSafe.classifyNonStoneInventoryItem')) {
  errors.push('LiveSafe.classifyNonStoneInventoryItem missing — totem scan classifier required');
}
if (!src.includes('LiveSafe.isTotemName')) {
  errors.push('LiveSafe.isTotemName missing — totem name matcher required');
}
if (!/;\(function\(\)/m.test(src)) {
  errors.push('IIFE wrapper missing semicolon — main chunk register isolation required');
}
if (!src.includes('LiveSafe.runDirectStartup')) {
  errors.push('LiveSafe.runDirectStartup missing');
}
if (/task\.spawn\(runReplionStartupPhase\)/.test(src)) {
  errors.push('main() must not spawn legacy runReplionStartupPhase');
}
if (/task\.spawn\(runDirectPlayerDataStartupPhase\)/.test(src)) {
  errors.push('main() must spawn LiveSafe.runDirectStartup');
}
if (!src.includes('scanPlayerDataGameItemDbInventory')) {
  errors.push('scanPlayerDataGameItemDbInventory missing — GameItemDB scan required');
}
if (!src.includes('LiveSafe.GetIcon')) {
  errors.push('LiveSafe.GetIcon missing — GameItemDB icon resolver required');
}
if (!src.includes('LiveSafe.buildGameItemDB')) {
  errors.push('LiveSafe.buildGameItemDB missing — ReplicatedStorage.Items scan required');
}
if (!src.includes('playerdata_gameitemdb')) {
  errors.push('playerdata_gameitemdb source missing');
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
