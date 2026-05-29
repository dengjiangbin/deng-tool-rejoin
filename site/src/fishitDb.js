'use strict';
/**
 * Read-only bridge to the DENG Fish It bot's SQLite database.
 *
 * The Fish It bot (a separate project at "..\DENG Fish It") stores all stats
 * as JSON blobs in a single `app_kv` table, keyed by Discord user ID. It has
 * NO HTTP API, so this module opens that SQLite file READ-ONLY and exposes a
 * small set of typed accessors. We never write to it.
 *
 * Hard rules honoured here:
 *   - Identity is the Discord user ID (snowflake) only — never username.
 *   - Stats are real (parsed straight from the bot's cache). Nothing invented.
 *   - The bot only tracks FORGOTTEN + SECRET species (plus Thunderzilla /
 *     Sea Eater), so "total fish" means total tracked catches, not every fish.
 *   - Missing DB / missing user / missing image never throws — callers get
 *     null / empty so the UI can show a clean empty state.
 *
 * Performance: the fish cache is large (tens of thousands of catch records),
 * so the parsed blobs are cached in-process for FISHIT_CACHE_TTL_MS and the
 * DB handle is opened lazily and reused.
 */

const path = require('path');
const fs = require('fs');

// Default path resolves to the sibling "DENG Fish It" project on this host.
// site/src/fishitDb.js -> ../../.. = Desktop -> DENG Fish It\data\...
const DEFAULT_DB_PATH = path.join(
  __dirname, '..', '..', '..', 'DENG Fish It', 'data', 'deng-fish-it.sqlite',
);
const DB_PATH = process.env.FISHIT_DB_PATH || DEFAULT_DB_PATH;

const CACHE_TTL_MS = Number(process.env.FISHIT_CACHE_TTL_MS || 15_000);
const WIB_OFFSET_MS = 7 * 60 * 60 * 1000; // UTC+7, matches the bot's day boundaries

// app_kv keys (mirror of the bot's KEYS constants).
const KEY_FISH = 'alltime_fish_cache';
const KEY_ROD = 'alltime_rod_cache';
const KEY_FORGOTTEN = 'forgotten_fish';

let _db = null;
let _dbTriedAt = 0;
const _blobCache = new Map(); // key -> { at, value }

// ── Low-level DB access ──────────────────────────────────────────────────────

function openDb() {
  // Re-try a failed open at most once per TTL window so a transiently missing
  // file (e.g. bot mid-deploy) doesn't hammer the disk.
  if (_db) return _db;
  const now = Date.now();
  if (now - _dbTriedAt < CACHE_TTL_MS) return null;
  _dbTriedAt = now;
  try {
    if (!fs.existsSync(DB_PATH)) return null;
    // node:sqlite is built-in on Node 22+. Open read-only so we can never
    // corrupt the bot's WAL database.
    const { DatabaseSync } = require('node:sqlite');
    _db = new DatabaseSync(DB_PATH, { readOnly: true });
    return _db;
  } catch (err) {
    console.warn('[fishit] open failed:', err && err.message ? err.message : err);
    _db = null;
    return null;
  }
}

function readBlob(key) {
  const cached = _blobCache.get(key);
  const now = Date.now();
  if (cached && now - cached.at < CACHE_TTL_MS) return cached.value;
  let value = null;
  try {
    const db = openDb();
    if (db) {
      const row = db.prepare('SELECT value FROM app_kv WHERE key = ?').get(key);
      if (row && row.value) value = JSON.parse(row.value);
    }
  } catch (err) {
    console.warn('[fishit] read', key, 'failed:', err && err.message ? err.message : err);
    // A read failure invalidates the handle so the next call re-opens.
    _db = null;
    value = null;
  }
  _blobCache.set(key, { at: now, value });
  return value;
}

/** Test seam: drop the in-process caches + handle. */
function _resetCache() {
  _blobCache.clear();
  _db = null;
  _dbTriedAt = 0;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function isRealUserId(id) {
  // Bot stores webhook-only catches under "webhook_<name>"; those are excluded
  // from leaderboards and are never a logged-in Discord identity.
  return typeof id === 'string' && /^\d{5,32}$/.test(id);
}

function periodWindow(period) {
  const now = Date.now();
  const wib = new Date(now + WIB_OFFSET_MS);
  const startOfTodayWib = Date.UTC(wib.getUTCFullYear(), wib.getUTCMonth(), wib.getUTCDate()) - WIB_OFFSET_MS;
  switch (period) {
    case 'today': return { from: startOfTodayWib, to: now, label: 'Today' };
    case 'yesterday': return { from: startOfTodayWib - 86_400_000, to: startOfTodayWib, label: 'Yesterday' };
    case '7d': return { from: now - 7 * 86_400_000, to: now, label: '7 Days' };
    case '30d': return { from: now - 30 * 86_400_000, to: now, label: '30 Days' };
    case 'all':
    default: return { from: 0, to: now + 1, label: 'All Time' };
  }
}

function inWindow(iso, win) {
  const t = Date.parse(iso);
  return Number.isFinite(t) && t >= win.from && t < win.to;
}

function num(v) { const n = Number(v); return Number.isFinite(n) ? n : 0; }

function rodImageless(name, count) { return { name, count: num(count) }; }

// ── Public accessors ─────────────────────────────────────────────────────────

/** Is the Fish It database reachable at all? */
function isAvailable() {
  return readBlob(KEY_FISH) != null || fs.existsSync(DB_PATH);
}

function getForgottenSpecies() {
  const blob = readBlob(KEY_FORGOTTEN);
  const fish = blob && Array.isArray(blob.fish) ? blob.fish : [];
  return fish.map((f) => ({
    name: String(f.name || ''),
    emoji: typeof f.emoji === 'string' ? f.emoji : null,
    imageUrl: typeof f.imageUrl === 'string' ? f.imageUrl : null,
    maxtonWeight: num(f.maxtonWeight) || null,
  })).filter((f) => f.name);
}

/** Global headline stats for the public homepage. */
function getGlobal() {
  const fish = readBlob(KEY_FISH);
  const rod = readBlob(KEY_ROD);
  if (!fish && !rod) return { available: false };

  const totals = (fish && fish.totals) || {};
  const forgottenFish = (totals.forgottenFish && typeof totals.forgottenFish === 'object') ? totals.forgottenFish : {};
  const topForgotten = Object.entries(forgottenFish)
    .map(([name, count]) => ({ name, count: num(count) }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 8);

  return {
    available: true,
    last_updated: (fish && fish.lastUpdated) || (rod && rod.lastUpdated) || null,
    total_players: num(totals.totalParticipants),
    total_fish: num(totals.totalFish),
    secret_fish: num(totals.secretFish),
    forgotten_fish: Object.values(forgottenFish).reduce((a, c) => a + num(c), 0),
    thunderzilla: num(totals.thunderzilla),
    sea_eater: num(totals.seaEater),
    maxton: num(totals.maxton),
    top_forgotten: topForgotten,
    rods: rod ? {
      ghostfinn: num(rod.totalGhostfinn),
      element: num(rod.totalElement),
      diamond: num(rod.totalDiamond),
      total: num(rod.totalRod),
      participants: num(rod.totalParticipants),
    } : null,
  };
}

function rawUser(discordId) {
  if (!isRealUserId(discordId)) return null;
  const fish = readBlob(KEY_FISH);
  if (!fish || !fish.byUser) return null;
  return fish.byUser[discordId] || null;
}

function rawRodUser(discordId) {
  if (!isRealUserId(discordId)) return null;
  const rod = readBlob(KEY_ROD);
  if (!rod || !rod.users) return null;
  return rod.users[discordId] || null;
}

/** Rank a user by totalFish across all real participants (1-based, or null). */
function fishRank(discordId) {
  const fish = readBlob(KEY_FISH);
  if (!fish || !fish.byUser) return null;
  const rows = Object.values(fish.byUser)
    .filter((u) => isRealUserId(String(u.userId)) && num(u.totalFish) > 0)
    .map((u) => ({ id: String(u.userId), tf: num(u.totalFish) }))
    .sort((a, b) => b.tf - a.tf || a.id.localeCompare(b.id));
  const idx = rows.findIndex((r) => r.id === String(discordId));
  return idx >= 0 ? { rank: idx + 1, of: rows.length } : null;
}

/** Profile summary for /api/fishit/me. Returns { has_data:false } when none. */
function getUserProfile(discordId) {
  const u = rawUser(discordId);
  const rodU = rawRodUser(discordId);
  if (!u && !rodU) return { has_data: false };

  const secretCount = u ? Object.values(u.secretFish || {}).reduce((a, c) => a + num(c), 0) : 0;
  const forgottenCount = u ? Object.values(u.forgottenFish || {}).reduce((a, c) => a + num(c), 0)
    + num(u.thunderzilla) + num(u.seaEater) : 0;

  return {
    has_data: true,
    discord_user_id: String(discordId),
    username: (u && u.username) || (rodU && rodU.username) || null,
    total_fish: u ? num(u.totalFish) : 0,
    secret_fish: secretCount,
    forgotten_fish: forgottenCount,
    thunderzilla: u ? num(u.thunderzilla) : 0,
    sea_eater: u ? num(u.seaEater) : 0,
    maxton: u ? num(u.maxtonCount) : 0,
    rank: fishRank(discordId),
    rods: rodU ? {
      ghostfinn: num(rodU.ghostfinn),
      element: num(rodU.element),
      diamond: num(rodU.diamond),
      total: num(rodU.totalRod),
    } : { ghostfinn: 0, element: 0, diamond: 0, total: 0 },
  };
}

/**
 * /api/fishit/me/stats — card-friendly groupings: rarity cards, rod cards,
 * total fish. Reuses the profile + adds a representative image per rarity.
 */
function getUserStats(discordId) {
  const profile = getUserProfile(discordId);
  if (!profile.has_data) return profile;
  const u = rawUser(discordId) || {};
  const thumbs = (u.fishThumbnails && typeof u.fishThumbnails === 'object') ? u.fishThumbnails : {};

  // A representative image for each rarity card: the bot has no dedicated
  // rarity icons, so we surface a real caught-fish thumbnail of that rarity
  // (per the user's "use a Secret/Forgotten fish image" instruction).
  const secretSample = Object.keys(u.secretFish || {}).map((n) => thumbs[n]).find(Boolean) || null;
  const forgottenSample = (() => {
    const det = (u.details && u.details.forgotten) || [];
    const withThumb = det.find((d) => d && d.thumbnail);
    return (withThumb && withThumb.thumbnail) || thumbs['Thunderzilla'] || null;
  })();

  return {
    has_data: true,
    discord_user_id: profile.discord_user_id,
    username: profile.username,
    total_fish: profile.total_fish,
    rank: profile.rank,
    rarity_cards: [
      { key: 'secret', label: 'Secret', amount: profile.secret_fish, image: secretSample, fallback: 'secret' },
      { key: 'forgotten', label: 'Forgotten', amount: profile.forgotten_fish, image: forgottenSample, fallback: 'forgotten' },
    ],
    // Rod images are NOT stored by the bot (only counts + emoji); cards use a
    // category fallback icon. Never invents an image URL.
    rod_cards: [
      { key: 'ghostfinn', label: 'Ghostfinn Rod', amount: profile.rods.ghostfinn, image: null, fallback: 'rod' },
      { key: 'element', label: 'Element Rod', amount: profile.rods.element, image: null, fallback: 'rod' },
      { key: 'diamond', label: 'Diamond Rod', amount: profile.rods.diamond, image: null, fallback: 'rod' },
    ],
  };
}

/**
 * /api/fishit/me/fish — the fish card grid. One card per tracked species the
 * user has caught, with the real thumbnail, rarity, the user's count, and a
 * sample value (heaviest catch weight) when available.
 */
function getUserFish(discordId) {
  const u = rawUser(discordId);
  if (!u) return { has_data: false, fish: [] };
  const thumbs = (u.fishThumbnails && typeof u.fishThumbnails === 'object') ? u.fishThumbnails : {};

  // Heaviest weight + latest time per species, derived from the catch details.
  const detail = {};
  const ingest = (arr, rarity) => {
    for (const c of (arr || [])) {
      const name = c && (c.name || c.fishType);
      if (!name) continue;
      const d = detail[name] || (detail[name] = { rarity, maxWeight: 0, lastTime: null, mutation: null });
      const w = num(c.weight);
      if (w > d.maxWeight) { d.maxWeight = w; d.mutation = c.mutation || d.mutation; }
      if (c.time && (!d.lastTime || c.time > d.lastTime)) d.lastTime = c.time;
    }
  };
  ingest(u.details && u.details.secret, 'secret');
  ingest(u.details && u.details.forgotten, 'forgotten');

  const cards = [];
  const addCard = (name, count, rarity) => {
    if (!name) return;
    const d = detail[name] || {};
    cards.push({
      name,
      rarity: d.rarity || rarity,
      amount: num(count),
      image: thumbs[name] || (d && d.thumbnail) || null,
      max_weight: d.maxWeight || null,
      mutation: d.mutation || null,
      last_caught: d.lastTime || null,
      fallback: (d.rarity || rarity) === 'forgotten' ? 'forgotten' : 'secret',
    });
  };
  for (const [name, count] of Object.entries(u.secretFish || {})) addCard(name, count, 'secret');
  for (const [name, count] of Object.entries(u.forgottenFish || {})) addCard(name, count, 'forgotten');

  cards.sort((a, b) => b.amount - a.amount || a.name.localeCompare(b.name));
  return { has_data: cards.length > 0, total_species: cards.length, fish: cards };
}

/**
 * /api/fishit/me/daily — fish caught in a period (WIB day boundaries).
 * `period` ∈ today | yesterday | 7d | 30d | all.
 */
function getUserDaily(discordId, period = 'today') {
  const u = rawUser(discordId);
  const fish = readBlob(KEY_FISH);
  const win = periodWindow(period);
  if (!u) {
    return { has_data: false, period, period_label: win.label, total: 0, secret: 0, forgotten: 0, last_updated: (fish && fish.lastUpdated) || null };
  }
  const secret = (u.details && u.details.secret || []).filter((c) => inWindow(c.time, win));
  const forgotten = (u.details && u.details.forgotten || []).filter((c) => inWindow(c.time, win));

  const bySecret = {};
  for (const c of secret) { const n = c.name || 'Unknown'; bySecret[n] = (bySecret[n] || 0) + 1; }
  const byForgotten = {};
  for (const c of forgotten) { const n = c.name || c.fishType || 'Unknown'; byForgotten[n] = (byForgotten[n] || 0) + 1; }

  // Best catch in the window (heaviest), across both rarities.
  let best = null;
  for (const c of [...secret, ...forgotten]) {
    const w = num(c.weight);
    if (w > 0 && (!best || w > best.weight)) {
      best = { name: c.name || c.fishType || 'Unknown', weight: w, mutation: c.mutation || null, thumbnail: c.thumbnail || null };
    }
  }

  return {
    has_data: secret.length + forgotten.length > 0,
    period,
    period_label: win.label,
    total: secret.length + forgotten.length,
    secret: secret.length,
    forgotten: forgotten.length,
    best_catch: best,
    secret_breakdown: Object.entries(bySecret).map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count),
    forgotten_breakdown: Object.entries(byForgotten).map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count),
    last_updated: (fish && fish.lastUpdated) || null,
  };
}

module.exports = {
  DB_PATH,
  isAvailable,
  getGlobal,
  getForgottenSpecies,
  getUserProfile,
  getUserStats,
  getUserFish,
  getUserDaily,
  _resetCache,
};
