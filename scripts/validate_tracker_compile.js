#!/usr/bin/env node
/**
 * BLOCKER10F: static compile guard for tracker.lua
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
if (!src.includes('TRACKER_BOOT_BEGIN BLOCKER10G')) {
  errors.push('TRACKER_BOOT_BEGIN BLOCKER10G marker missing');
}
if (!src.includes('BLOCKER10G_TARGETED_ITEM_DIAGNOSTICS_NO_FREEZE_2026_06_03')) {
  errors.push('BLOCKER10G build marker missing');
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
if (!src.includes('enableHeavyCatalog = false')) {
  errors.push('enableHeavyCatalog must default false');
}
if (!src.includes('enablePhaseBItemUpgrade = false')) {
  errors.push('enablePhaseBItemUpgrade must default false');
}
if (!src.includes('debugRemoteHooks = false')) {
  errors.push('debugRemoteHooks must default false');
}
if (!src.includes('enableTargetedItemDiagnostics = true')) {
  errors.push('enableTargetedItemDiagnostics must default true');
}
if (/^<<<<<<<|^>>>>>>>|^=======\s*$/.test(src)) {
  errors.push('merge conflict markers detected');
}
if (/return true\nend\n\n\s+return true\nend/.test(src)) {
  errors.push('orphan duplicate return/end block detected');
}
if (/loadstring\(game:HttpGet\([^)]+\)\)\(\)/.test(src) && !src.includes('loadstring(game:HttpGet("https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/tracker.lua"))()')) {
  errors.push('documented loader command shape missing from tracker header');
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
