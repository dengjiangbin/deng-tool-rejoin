'use strict';
/**
 * BLOCKER10U6 — DENG Quiz Bot Fish It image catalog (primary image source).
 *
 * Quiz Bot stores fish images as:
 *   data/fishit_bank.json  — name, assetId, localFile, id (fi####)
 *   assets/FishItFish/     — local .webp files keyed by localFile
 *
 * deng-quiz.sqlite holds quiz scores/settings only — NOT fish images.
 */

const path = require('path');
const fs = require('fs');
const catchNameParser = require('./fishitCatchNameParser');

const QUIZ_BOT_ROOT = process.env.QUIZ_BOT_ROOT
  || path.join(__dirname, '..', '..', '..', 'DENG Quiz');
const BANK_PATH = process.env.QUIZ_BOT_FISHIT_BANK_PATH
  || path.join(QUIZ_BOT_ROOT, 'data', 'fishit_bank.json');
const ASSETS_DIR = process.env.QUIZ_BOT_FISHIT_ASSETS_DIR
  || path.join(QUIZ_BOT_ROOT, 'assets', 'FishItFish');

const SOURCE_ID = 'quiz_bot_fishit_bank';
const SOURCE_TABLE = 'data/fishit_bank.json';

let _byLower = null;
let _byPunct = null;
let _entryCount = 0;

function normalizeName(raw) {
  return String(raw || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

function normalizeNamePunct(raw) {
  return normalizeName(raw)
    .replace(/[''`]/g, '')
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function _bankEntryToRow(entry) {
  if (!entry || !entry.name) return null;
  const localFile = String(entry.localFile || '').trim();
  const localPath = localFile ? path.join(ASSETS_DIR, localFile) : null;
  const hasFile = localPath && fs.existsSync(localPath);
  return {
    bankId: entry.id || null,
    name: String(entry.name).trim(),
    assetId: entry.assetId ? String(entry.assetId).trim() : null,
    localFile: localFile || null,
    localPath: hasFile ? localPath : null,
    difficulty: entry.difficulty ?? null,
    imageSource: SOURCE_ID,
    sourceDb: `quiz_bot:${SOURCE_TABLE}`,
    sourceTable: SOURCE_TABLE,
    sourceFile: localFile,
    cachedInQuizBot: hasFile,
  };
}

function _loadMaps() {
  if (_byLower) return;
  _byLower = new Map();
  _byPunct = new Map();
  _entryCount = 0;
  try {
    if (!fs.existsSync(BANK_PATH)) {
      console.warn('[fishit] quiz bot fishit bank not found:', BANK_PATH);
      return;
    }
    const raw = JSON.parse(fs.readFileSync(BANK_PATH, 'utf8'));
    const arr = Array.isArray(raw) ? raw : [];
    for (const entry of arr) {
      const row = _bankEntryToRow(entry);
      if (!row) continue;
      _entryCount += 1;
      const lower = normalizeName(row.name);
      const punct = normalizeNamePunct(row.name);
      if (!_byLower.has(lower) || row.cachedInQuizBot) _byLower.set(lower, row);
      if (punct && punct !== lower) {
        if (!_byPunct.has(punct) || row.cachedInQuizBot) _byPunct.set(punct, row);
      }
    }
  } catch (err) {
    console.warn('[fishit] quiz bot fishit bank load failed:', err && err.message ? err.message : err);
  }
}

function lookupByFishName(name) {
  _loadMaps();
  const raw = String(name || '').trim();
  if (!raw) return null;
  const lower = normalizeName(raw);
  const punct = normalizeNamePunct(raw);
  return _byLower.get(lower) || _byPunct.get(punct) || _byPunct.get(lower) || null;
}

function collectAliases(itemOrName) {
  if (typeof itemOrName === 'string') return [String(itemOrName).trim()].filter(Boolean);
  const item = itemOrName || {};
  const out = [];
  const seen = new Set();
  const add = (n) => {
    const s = String(n || '').trim();
    if (!s || seen.has(s.toLowerCase())) return;
    seen.add(s.toLowerCase());
    out.push(s);
  };
  add(item.cardName);
  add(item.baseFishName);
  const canon = catchNameParser.canonicalizeFishName(item.name || '');
  add(canon.baseFishName);
  add(item.name);
  add(item.displayName);
  if (item.mutation && item.baseFishName) {
    add(`${item.mutation} ${item.baseFishName}`);
  }
  return out;
}

function resolveForItem(item) {
  const aliases = collectAliases(item);
  const searchedAliases = [];
  for (const alias of aliases) {
    searchedAliases.push(alias);
    const hit = lookupByFishName(alias);
    if (hit && (hit.localPath || hit.assetId)) {
      return { ...hit, matchedAlias: alias, triedAliases: searchedAliases };
    }
  }
  return {
    bankId: null,
    name: null,
    assetId: null,
    localFile: null,
    localPath: null,
    imageSource: null,
    sourceDb: null,
    matchedAlias: null,
    triedAliases: searchedAliases,
  };
}

function resolveByFishName(name) {
  const hit = lookupByFishName(name);
  if (!hit) return { triedAliases: [name], matchedAlias: null };
  return { ...hit, matchedAlias: name, triedAliases: [name] };
}

function getCatalogMeta() {
  _loadMaps();
  let assetCount = 0;
  try {
    if (fs.existsSync(ASSETS_DIR)) {
      assetCount = fs.readdirSync(ASSETS_DIR).filter((f) => /\.(webp|png|jpe?g)$/i.test(f)).length;
    }
  } catch (_) { /* */ }
  return {
    quizBotRoot: QUIZ_BOT_ROOT,
    bankPath: BANK_PATH,
    assetsDir: ASSETS_DIR,
    bankExists: fs.existsSync(BANK_PATH),
    assetsDirExists: fs.existsSync(ASSETS_DIR),
    bankEntryCount: _entryCount,
    assetFileCount: assetCount,
    sourceId: SOURCE_ID,
    sourceTable: SOURCE_TABLE,
    note: 'deng-quiz.sqlite stores quiz scores only; fish images are fishit_bank.json + FishItFish webp',
  };
}

function auditNames(names) {
  return (names || []).map((name) => {
    const hit = lookupByFishName(name);
    return {
      baseFishName: name,
      matched: !!hit,
      bankId: hit?.bankId || null,
      canonicalName: hit?.name || null,
      matchedAlias: hit ? name : null,
      localFile: hit?.localFile || null,
      localPath: hit?.localPath || null,
      assetId: hit?.assetId || null,
      sourceDb: hit?.sourceDb || null,
      cachedInQuizBot: hit?.cachedInQuizBot || false,
      reason: hit ? 'quiz_bot_bank_match' : 'quiz_bot_bank_missing',
    };
  });
}

function _reset() {
  _byLower = null;
  _byPunct = null;
  _entryCount = 0;
}

module.exports = {
  QUIZ_BOT_ROOT,
  BANK_PATH,
  ASSETS_DIR,
  SOURCE_ID,
  SOURCE_TABLE,
  normalizeName,
  normalizeNamePunct,
  lookupByFishName,
  collectAliases,
  resolveForItem,
  resolveByFishName,
  getCatalogMeta,
  auditNames,
  _reset,
};
