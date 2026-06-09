'use strict';

const fs = require('fs');
const path = require('path');

const MANUAL_VERIFIED_SOURCE = 'manual_verified_image';
const CACHE_DIR = path.join(__dirname, '..', 'data', 'stats_fish_image_cache');
const CATALOG_PATH = path.join(__dirname, '..', 'data', 'fishit_manual_stats_fish_images.json');

const FALLBACK_HINTS = [
  /fallback/i,
  /placeholder/i,
  /default-fish/i,
  /fallback-fish\.svg/i,
];

let _catalog = null;

function normalizeFishName(name) {
  return String(name || '').trim().replace(/\s+/g, ' ');
}

function loadCatalog() {
  if (_catalog) return _catalog;
  try {
    const raw = JSON.parse(fs.readFileSync(CATALOG_PATH, 'utf8'));
    _catalog = {
      version: raw.version || 1,
      updatedAt: raw.updatedAt || null,
      byName: raw.byName && typeof raw.byName === 'object' ? raw.byName : {},
    };
  } catch {
    _catalog = { version: 1, updatedAt: null, byName: {} };
  }
  return _catalog;
}

function getCacheDir() {
  return CACHE_DIR;
}

function localStatsFishUrl(baseUrl, filename) {
  const base = String(baseUrl || '').replace(/\/$/, '');
  return `${base}/api/fishit/assets/stats-fish/${filename}`;
}

function isPlaceholderUrl(url) {
  const u = String(url || '');
  return !u || FALLBACK_HINTS.some((re) => re.test(u));
}

function assetFileExists(filename) {
  if (!filename) return false;
  const file = path.basename(String(filename));
  return fs.existsSync(path.join(CACHE_DIR, file));
}

function lookupByName(name) {
  const catalog = loadCatalog();
  const key = normalizeFishName(name);
  if (!key) return null;
  const entry = catalog.byName[key];
  if (!entry || !entry.filename || !assetFileExists(entry.filename)) return null;
  return {
    name: key,
    filename: entry.filename,
    assetId: entry.assetId || null,
    imageSource: entry.imageSource || MANUAL_VERIFIED_SOURCE,
    sourceFile: entry.sourceFile || null,
    quizBotBankId: entry.quizBotBankId || null,
  };
}

function resolveStatsFishImage(name, baseUrl) {
  const hit = lookupByName(name);
  if (!hit) return null;
  const imageUrl = localStatsFishUrl(baseUrl, hit.filename);
  if (isPlaceholderUrl(imageUrl)) return null;
  return {
    name: hit.name,
    imageUrl,
    imageSource: hit.imageSource,
    sourceFile: hit.sourceFile,
    assetId: hit.assetId,
    quizBotBankId: hit.quizBotBankId,
  };
}

function buildStatsFishImageProof(names = []) {
  const catalog = loadCatalog();
  const list = names.length ? names : Object.keys(catalog.byName);
  return list.map((name) => {
    const hit = lookupByName(name);
    return {
      name,
      imageUrl: hit ? localStatsFishUrl('', hit.filename).replace(/^\//, '/api/fishit/assets/stats-fish/') : null,
      imageSource: hit?.imageSource || null,
      sourceFile: hit?.sourceFile || null,
      assetId: hit?.assetId || null,
      cached: !!hit,
    };
  });
}

function seedImageIndex(putFn) {
  const catalog = loadCatalog();
  for (const [name, entry] of Object.entries(catalog.byName)) {
    if (!entry?.filename || !assetFileExists(entry.filename)) continue;
    const url = localStatsFishUrl('', entry.filename);
    if (isPlaceholderUrl(url)) continue;
    putFn(name, url, entry.imageSource || MANUAL_VERIFIED_SOURCE);
  }
}

module.exports = {
  MANUAL_VERIFIED_SOURCE,
  CACHE_DIR,
  CATALOG_PATH,
  loadCatalog,
  getCacheDir,
  lookupByName,
  resolveStatsFishImage,
  assetFileExists,
  isPlaceholderUrl,
  buildStatsFishImageProof,
  seedImageIndex,
  localStatsFishUrl,
};
