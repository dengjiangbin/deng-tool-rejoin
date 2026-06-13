#!/usr/bin/env node
/**
 * Build protected dist/tracker.lua from private raw tracker source.
 * Encodes the raw script in a bounded base64 loader so public GitHub dist
 * ships playerStats extraction without publishing raw source verbatim.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { resolveRawTrackerSourcePath } = require('./trackerRawSourcePath');

const root = path.join(__dirname, '..');
const rawPath = resolveRawTrackerSourcePath({ root });
const distPath = path.resolve(process.argv[2] || path.join(root, 'dist', 'tracker.lua'));

if (!rawPath || !fs.existsSync(rawPath)) {
  console.error('BUILD_TRACKER_DIST FAILED: private raw tracker source not found');
  console.error('  set TRACKER_RAW_SOURCE_PATH to private tracker.lua');
  process.exit(1);
}

if (path.resolve(rawPath) === path.resolve(distPath)) {
  console.error('BUILD_TRACKER_DIST FAILED: dist path must not equal raw source path');
  process.exit(1);
}

const raw = fs.readFileSync(rawPath, 'utf8');
if (raw.includes('local __B=[[') && raw.includes('local function __D(s)')) {
  console.error('BUILD_TRACKER_DIST FAILED: raw source looks like dist wrapper, not readable tracker source');
  process.exit(1);
}
const buildMatch = raw.match(/TRACKER_BUILD\s*=\s*"([^"]+)"/);
const buildMarker = buildMatch ? buildMatch[1] : 'UNKNOWN_BUILD';
const deployRev = process.env.TRACKER_DIST_REV || '3';
const deployStamp = new Date().toISOString();

const loaderHeader = `--[[ DENG protected tracker dist | ${buildMarker} | rev=${deployRev} deployed=${deployStamp} ]]\n`;
const loaderPrefix = `local __B=[[`;
const loaderSuffix = `]]
local __A="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
local function __D(s)
  s=string.gsub(s,"[^"..__A.."=]","")
  return (s:gsub(".",function(x)
    if x=="=" then return "" end
    local r,f="",( __A:find(x)-1)
    for i=6,1,-1 do r=r..(f%2^i-f%2^(i-1)>0 and "1" or "0") end
    return r
  end):gsub("%d%d%d?%d?%d?%d?%d?%d?",function(x)
    if #x~=8 then return "" end
    local c=0
    for i=1,8 do c=c+(x:sub(i,i)=="1" and 2^(8-i) or 0) end
    return string.char(c)
  end))
end
local __S=__D(__B)
local __F, __E=loadstring(__S)
if not __F then error(__E or "dist decode failed",0) end
return __F()
`;

const encoded = Buffer.from(raw, 'utf8').toString('base64');
const dist = loaderHeader + loaderPrefix + encoded + loaderSuffix;

if (dist.trim() === raw.trim()) {
  console.error('BUILD_TRACKER_DIST FAILED: dist equals raw');
  process.exit(1);
}

fs.mkdirSync(path.dirname(distPath), { recursive: true });
fs.writeFileSync(distPath, dist, 'utf8');

function decodeDistPayload(distSrc) {
  const m = distSrc.match(/local __B=\[\[([\s\S]*?)\]\]\nlocal __A=/);
  if (!m) throw new Error('dist decode anchor missing');
  return Buffer.from(m[1], 'base64').toString('utf8');
}

function compileWithLuau(filePath) {
  const luauCandidates = [
    path.join(__dirname, '..', '_luau', 'luau-compile.exe'),
    path.join(__dirname, '..', '_luau', 'luau-compile'),
    'luau-compile',
  ];
  for (const bin of luauCandidates) {
    try {
      execFileSync(bin, [filePath], { stdio: 'pipe', encoding: 'utf8' });
      return bin;
    } catch (e) {
      const msg = (e.stderr || e.stdout || e.message || '').toString();
      if (e.code === 'ENOENT') continue;
      throw new Error(msg.trim().split('\n').slice(0, 3).join(' | '));
    }
  }
  return null;
}

const decoded = decodeDistPayload(dist);
if (!/;\(function\(\)/m.test(decoded)) {
  console.error('BUILD_TRACKER_DIST FAILED: decoded payload missing ;(function() IIFE guard');
  process.exit(1);
}
if (!decoded.includes('totems=%d totemQty=%d')) {
  console.error('BUILD_TRACKER_DIST FAILED: decoded payload missing totem upload proof log');
  process.exit(1);
}

const decodedTmp = `${distPath}.decoded.lua`;
fs.writeFileSync(decodedTmp, decoded, 'utf8');
try {
  const distBin = compileWithLuau(distPath);
  const decodedBin = compileWithLuau(decodedTmp);
  console.log('  dist luau-compile:', distBin || 'skipped');
  console.log('  decoded luau-compile:', decodedBin || 'skipped');
  if (!decodedBin) {
    console.error('BUILD_TRACKER_DIST FAILED: decoded payload must compile with luau-compile');
    process.exit(1);
  }
} finally {
  try { fs.unlinkSync(decodedTmp); } catch (_) { /* ignore */ }
}

console.log('BUILD_TRACKER_DIST OK');
console.log('  raw:', rawPath);
console.log('  dist:', distPath);
console.log('  build:', buildMarker);
console.log('  raw bytes:', Buffer.byteLength(raw, 'utf8'));
console.log('  dist bytes:', Buffer.byteLength(dist, 'utf8'));
