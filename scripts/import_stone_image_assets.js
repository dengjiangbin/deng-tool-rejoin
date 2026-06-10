#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const ASSETS_DIR = path.join(
  __dirname,
  '..',
  '..',
  '.cursor',
  'projects',
  'c-Users-Administrator-Desktop-DENG-Tool-Rejoin',
  'assets',
);
const FALLBACK_ASSETS = path.join(
  process.env.USERPROFILE || '',
  '.cursor',
  'projects',
  'c-Users-Administrator-Desktop-DENG-Tool-Rejoin',
  'assets',
);
const CACHE_DIR = path.join(__dirname, '..', 'site', 'data', 'stone_image_cache');

const IMPORTS = [
  { match: 'Enchant_Stone-f5e0aef4', out: 'stone_10_normal.png', itemId: '10' },
  { match: 'Transcended_Stone', out: 'stone_246_transcended.png', itemId: '246', fallback: '73883190545629' },
  { match: 'Evolved_Enchant_Stone', out: 'stone_558_evolved.png', itemId: '558', fallback: 'image-4fb3b4a7' },
  { match: 'Eggy_Enchant_Stone', out: 'stone_873_eggy.png', itemId: '873', fallback: 'image-c65db86d' },
  { match: 'image-4ab52ac6', out: 'stone_929_runic.png', itemId: '929' },
];

function resolveAssetsDir() {
  for (const dir of [ASSETS_DIR, FALLBACK_ASSETS]) {
    if (dir && fs.existsSync(dir)) return dir;
  }
  return null;
}

function findAssetFile(dir, token) {
  let entries = [];
  try {
    entries = fs.readdirSync(dir);
  } catch {
    return null;
  }
  const hit = entries.find((name) => name.includes(token));
  if (!hit) return null;
  const full = path.join(dir, hit);
  try {
    fs.accessSync(full, fs.constants.R_OK);
    return full;
  } catch {
    return null;
  }
}

function main() {
  const srcDir = resolveAssetsDir();
  if (!srcDir) {
    console.error('assets directory not found');
    process.exit(1);
  }
  if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });
  let copied = 0;
  for (const row of IMPORTS) {
    let src = findAssetFile(srcDir, row.match);
    if (!src && row.fallback) src = findAssetFile(srcDir, row.fallback);
    if (!src) {
      console.warn('missing source for', row.out);
      continue;
    }
    const dest = path.join(CACHE_DIR, row.out);
    fs.copyFileSync(src, dest);
    copied += 1;
    console.log('copied', row.out, '<-', path.basename(src));
  }
  console.log('stone import done copied=', copied, 'cache=', CACHE_DIR);
  if (copied === 0) process.exit(1);
}

main();
