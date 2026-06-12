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
const fishitDbPath = require('./fishitDbPath');
const manualStatsFishImages = require('./fishitManualStatsFishImages');
const rodAssets = require('./fishitRodAssets');

// Default path resolves to the sibling "DENG Fish It" project on this host.
const DEFAULT_DB_PATH = path.join(
  __dirname, '..', '..', '..', 'DENG Fish It', 'data', 'deng-fish-it.sqlite',
);

function getDbPath() {
  return fishitDbPath.resolveFishitDbPath(false);
}

/** @deprecated use getDbPath() — kept for tests/logs */
const DB_PATH = DEFAULT_DB_PATH;

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
  if (_db) return _db;
  const now = Date.now();
  if (now - _dbTriedAt < CACHE_TTL_MS && _dbTriedAt > 0) return null;
  _dbTriedAt = now;
  const dbPath = getDbPath();
  try {
    if (!fs.existsSync(dbPath)) {
      console.warn('[fishit] open skipped — DB file not found', { dbPath });
      return null;
    }
    const { DatabaseSync } = require('node:sqlite');
    _db = new DatabaseSync(dbPath, { readOnly: true });
    return _db;
  } catch (err) {
    console.error('[fishit] open failed:', err && err.message ? err.message : err, { dbPath });
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

/** Export all DB-backed species images for canonical catalog backfill. */
function exportImageCatalog() {
  const idx = buildImageIndex();
  const seen = new Set();
  const out = [];
  for (const [key, hit] of idx.entries()) {
    if (!hit?.url || seen.has(hit.url)) continue;
    seen.add(hit.url);
    out.push({
      name: key,
      imageUrl: hit.url,
      source: hit.source || 'fishit_db',
    });
  }
  return out;
}

/** Export species→rarity hints from Secret/Forgotten caches (reliable bot data). */
function exportRarityHints() {
  const out = [];
  const seen = new Set();
  const add = (name, rarity, source) => {
    const k = normKey(name);
    if (!k || seen.has(k)) return;
    seen.add(k);
    out.push({ name, normalizedKey: k, rarity, source });
  };
  const fish = readBlob(KEY_FISH);
  if (fish && fish.byUser) {
    for (const u of Object.values(fish.byUser)) {
      if (!isRealUserId(String(u.userId))) continue;
      for (const n of Object.keys(u.secretFish || {})) add(n, 'Secret', 'fishit_db_secret');
      for (const n of Object.keys(u.forgottenFish || {})) add(n, 'Forgotten', 'fishit_db_forgotten');
    }
  }
  const forg = readBlob(KEY_FORGOTTEN);
  if (forg && Array.isArray(forg.fish)) {
    for (const f of forg.fish) add(f.name, 'Forgotten', 'forgotten_fish_catalog');
  }
  return out;
}

/** Test seam: drop the in-process caches + handle. */
function _resetCache() {
  try {
    if (_db && typeof _db.close === 'function') _db.close();
  } catch (_) { /* ignore */ }
  _blobCache.clear();
  _db = null;
  _dbTriedAt = 0;
  _imgIndex = null;
  _imgIndexAt = 0;
  fishitDbPath.invalidateResolvedPath();
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
    case '24h': return { from: now - 86_400_000, to: now, label: '24 Hours' };
    case 'tdy':
    case 'today': return { from: startOfTodayWib, to: now, label: 'Today' };
    case 'yesterday': return { from: startOfTodayWib - 86_400_000, to: startOfTodayWib, label: 'Yesterday' };
    case '7d': return { from: now - 7 * 86_400_000, to: now, label: '7 Days' };
    case '30d': return { from: now - 30 * 86_400_000, to: now, label: '30 Days' };
    case 'ytd': {
      const yearStart = Date.UTC(wib.getUTCFullYear(), 0, 1) - WIB_OFFSET_MS;
      return { from: yearStart, to: now, label: 'Year to Date' };
    }
    case 'all':
    default: return { from: 0, to: now + 1, label: 'All Time' };
  }
}

function normalizeDashboardPeriod(raw) {
  const p = String(raw || 'all').trim().toLowerCase().replace(/\s+/g, '_');
  if (p === 'all' || p === 'alltime' || p === 'all_time') return 'all';
  if (p === 'tdy' || p === 'today' || p === '1d' || p === 'day' || p === '24h') return 'tdy';
  if (p === '7d' || p === '7days' || p === 'week') return '7d';
  if (p === '30d' || p === '1m' || p === 'month' || p === '30days') return '30d';
  if (p === 'ytd' || p === 'year' || p === 'year_to_date') return 'ytd';
  if (p === 'custom') return 'custom';
  return 'all';
}

function parseDashboardDateInput(raw, endOfDay) {
  if (!raw) return NaN;
  const s = String(raw).trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
    return Date.parse(s + (endOfDay ? 'T23:59:59.999+07:00' : 'T00:00:00+07:00'));
  }
  const t = Date.parse(s);
  return Number.isFinite(t) ? t : NaN;
}

function customPeriodWindow(fromRaw, toRaw) {
  const from = parseDashboardDateInput(fromRaw, false);
  const to = parseDashboardDateInput(toRaw, true);
  if (!Number.isFinite(from) || !Number.isFinite(to) || from > to) return null;
  return { from, to: to + 1, label: 'Custom Range' };
}

function resolveDashboardWindow(period, opts = {}) {
  if (period === 'custom') return customPeriodWindow(opts.from, opts.to);
  const key = period === 'tdy' ? 'today' : period;
  return periodWindow(key);
}

function wibDateKeyFromMs(ms) {
  const wib = new Date(ms + WIB_OFFSET_MS);
  return `${wib.getUTCFullYear()}-${String(wib.getUTCMonth() + 1).padStart(2, '0')}-${String(wib.getUTCDate()).padStart(2, '0')}`;
}

function enumerateWibDateKeys(win) {
  const keys = [];
  const start = new Date(win.from + WIB_OFFSET_MS);
  let cursor = Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate());
  while (cursor < win.to) {
    keys.push(wibDateKeyFromMs(cursor - WIB_OFFSET_MS));
    cursor += 86_400_000;
  }
  if (!keys.length) keys.push(wibDateKeyFromMs(win.from));
  return keys;
}

function wibWeekKeyFromMs(ms) {
  const wib = new Date(ms + WIB_OFFSET_MS);
  const day = wib.getUTCDay();
  const diff = (day + 6) % 7;
  const monday = Date.UTC(wib.getUTCFullYear(), wib.getUTCMonth(), wib.getUTCDate() - diff);
  return wibDateKeyFromMs(monday - WIB_OFFSET_MS);
}

function enumerateChartBuckets(win) {
  const spanMs = Math.max(0, win.to - win.from);
  const MAX_BUCKETS = 60;
  let useWeekly = spanMs > 45 * 86_400_000;
  if (useWeekly) {
    const estWeeks = Math.ceil(spanMs / (7 * 86_400_000));
    if (estWeeks > MAX_BUCKETS) {
      const trimmedFrom = win.to - MAX_BUCKETS * 7 * 86_400_000;
      win = { ...win, from: Math.max(win.from, trimmedFrom) };
    }
  } else {
    const estDays = Math.ceil(spanMs / 86_400_000);
    if (estDays > MAX_BUCKETS) {
      const trimmedFrom = win.to - MAX_BUCKETS * 86_400_000;
      win = { ...win, from: Math.max(win.from, trimmedFrom) };
    }
  }
  if (!useWeekly) {
    return {
      bucket: 'day',
      keys: enumerateWibDateKeys(win),
      keyForMs: wibDateKeyFromMs,
      chartFrom: win.from,
    };
  }
  const keys = [];
  const start = new Date(win.from + WIB_OFFSET_MS);
  let cursor = Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate());
  cursor -= ((new Date(cursor + WIB_OFFSET_MS).getUTCDay() + 6) % 7) * 86_400_000;
  while (cursor < win.to) {
    keys.push(wibDateKeyFromMs(cursor - WIB_OFFSET_MS));
    cursor += 7 * 86_400_000;
  }
  if (!keys.length) keys.push(wibWeekKeyFromMs(win.from));
  return { bucket: 'week', keys, keyForMs: wibWeekKeyFromMs, chartFrom: win.from };
}

function listRealBotUsers(fish) {
  if (!fish || !fish.byUser) return [];
  return Object.values(fish.byUser).filter((u) => u && isRealUserId(String(u.userId)));
}

function buildTrackedAccountFilters(trackedAccounts) {
  const allowedUsernameKeys = new Set();
  const allowedRobloxIds = new Set();
  for (const acct of trackedAccounts || []) {
    if (!acct || typeof acct !== 'object') continue;
    const key = String(
      acct.roblox_username_key || acct.robloxUsernameKey || acct.key || acct.username || '',
    ).trim().toLowerCase();
    if (key) allowedUsernameKeys.add(key);
    const rid = acct.roblox_user_id != null ? acct.roblox_user_id : acct.robloxUserId;
    if (rid != null && rid !== '') allowedRobloxIds.add(String(rid));
  }
  return { allowedUsernameKeys, allowedRobloxIds };
}

function botUserMatchesTrackedAccount(u, mapKey, filters) {
  if (!u || !filters) return false;
  const usernameKey = String(u.username || '').trim().toLowerCase();
  const userId = String(u.userId || mapKey || '');
  const mapKeyStr = String(mapKey || '');
  return (usernameKey && filters.allowedUsernameKeys.has(usernameKey))
    || filters.allowedRobloxIds.has(userId)
    || filters.allowedRobloxIds.has(mapKeyStr)
    || (mapKeyStr && filters.allowedUsernameKeys.has(mapKeyStr.toLowerCase()));
}

/**
 * Resolve bot cache users for owner dashboard — same identity path as !d / !s.
 * Primary: fish.byUser[discordUserId] (Discord snowflake, never display name).
 * Secondary: tracked Roblox accounts in bot cache (only when Discord ID miss).
 */
function resolveDashboardBotUsers(fish, discordUserId, trackedAccounts) {
  if (!fish || !fish.byUser) return { users: [], match: null };
  const ownerId = String(discordUserId || '').trim();
  const seen = new Set();
  const users = [];
  let match = null;

  if (ownerId && isRealUserId(ownerId)) {
    const direct = fish.byUser[ownerId];
    if (direct) {
      const key = String(direct.userId || ownerId);
      seen.add(key);
      users.push(direct);
      match = {
        identityMatchMode: 'discord_id_direct',
        matchedBotUserId: key,
        matchedRobloxUsername: direct.username || null,
        mapKey: ownerId,
      };
      return { users, match };
    }
  }

  const filters = buildTrackedAccountFilters(trackedAccounts);
  if (filters.allowedUsernameKeys.size || filters.allowedRobloxIds.size) {
    for (const [mapKey, u] of Object.entries(fish.byUser)) {
      if (!botUserMatchesTrackedAccount(u, mapKey, filters)) continue;
      const id = String(u.userId || mapKey);
      if (!isRealUserId(id) || seen.has(id)) continue;
      seen.add(id);
      users.push(u);
      if (!match) {
        match = {
          identityMatchMode: 'tracked_roblox_account',
          matchedBotUserId: id,
          matchedRobloxUsername: u.username || null,
          mapKey,
        };
      }
    }
  }

  return { users, match };
}

/** @deprecated Use resolveDashboardBotUsers — kept for tests/back-compat. */
function resolveTrackedBotUsers(fish, trackedAccounts, discordOwnerId) {
  return resolveDashboardBotUsers(fish, discordOwnerId, trackedAccounts).users;
}

function collectAllTimeCatchRowStats(users) {
  let allTimeCatchRows = 0;
  let firstCatchAt = null;
  let lastCatchAt = null;
  for (const u of users) {
    const rows = [
      ...(u.details && u.details.secret || []),
      ...(u.details && u.details.forgotten || []),
    ];
    allTimeCatchRows += rows.length;
    for (const c of rows) {
      if (!c || !c.time) continue;
      if (!firstCatchAt || c.time < firstCatchAt) firstCatchAt = c.time;
      if (!lastCatchAt || c.time > lastCatchAt) lastCatchAt = c.time;
    }
  }
  return { allTimeCatchRows, firstCatchAt, lastCatchAt };
}

function deriveOwnerDashboardEmptyReason(ctx) {
  if (!ctx.authDiscordId) return 'missing_auth_discord_id';
  if (!ctx.botDbConnected) return 'bot_db_not_connected';
  if (!ctx.fishCacheLoaded) return 'fish_cache_missing_or_empty';
  if (!ctx.matchedUsers.length) return 'no_bot_user_for_discord_id';
  if (ctx.allTimeCatchRows === 0) return 'no_catch_records_in_bot_db';
  if (ctx.filteredCatchRows === 0) return 'date_range_filtered_all_rows';
  return null;
}

function buildOwnerDashboardDebug(ctx) {
  return {
    authDiscordId: ctx.authDiscordId,
    authDiscordUsername: ctx.authDiscordUsername,
    dbSource: 'deng_fish_it_bot_db_d_command',
    botDbPath: getDbPath(),
    botDbConnected: ctx.botDbConnected,
    identityMatchMode: ctx.match && ctx.match.identityMatchMode ? ctx.match.identityMatchMode : null,
    matchedBotUserId: ctx.match && ctx.match.matchedBotUserId ? ctx.match.matchedBotUserId : null,
    matchedRobloxUsername: ctx.match && ctx.match.matchedRobloxUsername ? ctx.match.matchedRobloxUsername : null,
    selectedRange: ctx.period,
    allTimeCatchRows: ctx.allTimeCatchRows,
    filteredCatchRows: ctx.filteredCatchRows,
    secretCount: ctx.secretCaught,
    forgottenCount: ctx.forgottenCaught,
    firstCatchAt: ctx.firstCatchAt,
    lastCatchAt: ctx.lastCatchAt,
    queryMs: ctx.queryMs,
    emptyReason: ctx.emptyReason,
    store: 'app_kv',
    key: KEY_FISH,
    dbPath: getDbPath(),
    dbAvailable: ctx.botDbConnected,
    period: ctx.period,
    scope: 'owner',
    discordUserId: ctx.authDiscordId,
    trackedAccountCount: ctx.trackedCount,
    matchedBotUsers: ctx.matchedUsers.length,
    dateRange: ctx.dateRange,
    usersScanned: ctx.matchedUsers.length,
    totalCatchRows: ctx.allTimeCatchRows,
    detailRowsMatched: ctx.filteredCatchRows,
    caughtFishCount: ctx.caughtFishCount,
    fishCardCount: ctx.fishCardCount,
    dailyRows: ctx.dailyRows,
    chartBucket: ctx.chartBucket,
    method: 'discord_id_direct — byUser.details window filter (same as !d / !s)',
  };
}

function collectCatchRowStats(users, win) {
  let totalCatchRows = 0;
  let filteredCatchRows = 0;
  let firstCatchAt = null;
  let lastCatchAt = null;
  for (const u of users) {
    const rows = [
      ...(u.details && u.details.secret || []),
      ...(u.details && u.details.forgotten || []),
    ];
    totalCatchRows += rows.length;
    for (const c of rows) {
      if (!c || !c.time) continue;
      if (!inWindow(c.time, win)) continue;
      filteredCatchRows += 1;
      if (!firstCatchAt || c.time < firstCatchAt) firstCatchAt = c.time;
      if (!lastCatchAt || c.time > lastCatchAt) lastCatchAt = c.time;
    }
  }
  return { totalCatchRows, filteredCatchRows, firstCatchAt, lastCatchAt };
}

function aggregateDailyCaughtForUsers(users, win) {
  const { bucket, keys, keyForMs } = enumerateChartBuckets(win);
  const daily = Object.fromEntries(keys.map((k) => [k, 0]));
  let usedByDate = false;
  if (bucket === 'day') {
    for (const u of users) {
      for (const [date, agg] of Object.entries(u.byDate || {})) {
        if (!Object.prototype.hasOwnProperty.call(daily, date)) continue;
        const t = Date.parse(`${date}T00:00:00+07:00`);
        if (Number.isFinite(t) && t >= win.from && t < win.to) {
          daily[date] += num(agg && agg.total);
          if (daily[date] > 0) usedByDate = true;
        }
      }
    }
  }
  if (!usedByDate) {
    for (const u of users) {
      const rows = [
        ...(u.details && u.details.secret || []),
        ...(u.details && u.details.forgotten || []),
      ];
      for (const c of rows) {
        if (!c || !inWindow(c.time, win)) continue;
        const dk = keyForMs(Date.parse(c.time));
        if (Object.prototype.hasOwnProperty.call(daily, dk)) daily[dk] += 1;
      }
    }
  }
  return keys.map((date) => ({ date, totalCaught: num(daily[date]), bucket }));
}

function aggregateFishCardsForUsers(users, win) {
  const groups = new Map();
  const add = (c, rarity) => {
    const name = c.name || c.fishType;
    if (!name) return;
    const g = groups.get(name) || { name, rarity, count: 0, maxWeight: 0, latest: null, thumb: null };
    g.count += 1;
    const w = num(c.weight);
    if (w > g.maxWeight) g.maxWeight = w;
    if (c.time && (!g.latest || c.time > g.latest)) g.latest = c.time;
    if (!g.thumb && isValidImg(c.thumbnail)) g.thumb = c.thumbnail;
    groups.set(name, g);
  };
  for (const u of users) {
    const secret = (u.details && u.details.secret || []).filter((c) => inWindow(c.time, win));
    const forgotten = (u.details && u.details.forgotten || []).filter((c) => inWindow(c.time, win));
    secret.forEach((c) => add(c, 'Secret'));
    forgotten.forEach((c) => add(c, 'Forgotten'));
  }
  return sortFishCardsByRarity([...groups.values()].map((g) => ({
    speciesKey: speciesKey(g.name),
    name: g.name,
    rarity: g.rarity,
    count: g.count,
    amount: g.count,
    imageUrl: resolveSpeciesImage(g.name, g.thumb),
    maxWeight: formatWeight(g.maxWeight),
    latestCaughtAt: g.latest,
    fallback: g.rarity === 'Forgotten' ? 'forgotten' : 'secret',
  })));
}

function sortFishCardsByRarity(cards) {
  return [...cards].sort((a, b) =>
    rarityRank(a.rarity) - rarityRank(b.rarity)
    || (Number(b.count) || 0) - (Number(a.count) || 0)
    || String(a.name || '').localeCompare(String(b.name || '')));
}

function countRarityForUsers(users, win) {
  let secretCaught = 0;
  let forgottenCaught = 0;
  for (const u of users) {
    secretCaught += (u.details && u.details.secret || []).filter((c) => inWindow(c.time, win)).length;
    forgottenCaught += (u.details && u.details.forgotten || []).filter((c) => inWindow(c.time, win)).length;
  }
  return { secretCaught, forgottenCaught };
}

function aggregateGlobalDailyCaught(fish, win) {
  return aggregateDailyCaughtForUsers(listRealBotUsers(fish), win);
}

function aggregateGlobalFishCards(fish, win) {
  return aggregateFishCardsForUsers(listRealBotUsers(fish), win);
}

/**
 * Global dashboard stats for /inventory (same bot DB source as !d / public-summary).
 * Period: all | 30d | 7d | ytd | tdy | custom (default all).
 */
function getGlobalDashboard(periodInput = 'all', opts = {}) {
  const period = normalizeDashboardPeriod(periodInput);
  const win = resolveDashboardWindow(period, opts);
  if (!win) {
    return {
      available: false,
      period: 'custom',
      from: null,
      to: null,
      cards: { secretCaught: 0, forgottenCaught: 0 },
      fishCards: [],
      dailyCaught: [],
      source: 'deng_fish_it_bot_db_d_command',
      error: 'invalid_custom_range',
      debug: { period, invalidCustomRange: true },
    };
  }
  const fish = readBlob(KEY_FISH);
  const fromIso = new Date(win.from).toISOString();
  const toIso = new Date(win.to).toISOString();
  const chartBuckets = enumerateChartBuckets(win);
  const emptyDaily = chartBuckets.keys.map((date) => ({ date, totalCaught: 0, bucket: chartBuckets.bucket }));
  const empty = {
    available: false,
    period,
    periodLabel: win.label,
    from: fromIso,
    to: toIso,
    cards: { secretCaught: 0, forgottenCaught: 0 },
    fishCards: [],
    dailyCaught: emptyDaily,
    source: 'deng_fish_it_bot_db_d_command',
    debug: {
      source: 'deng_fish_it_bot_db_d_command',
      store: 'app_kv',
      key: KEY_FISH,
      dbPath: getDbPath(),
      dbAvailable: isAvailable(),
      period,
      dateRange: { from: fromIso, to: toIso },
      usersScanned: 0,
      detailRowsMatched: 0,
      dailyRows: emptyDaily.length,
    },
  };
  if (!fish || !fish.byUser) {
    console.warn('[fishit] getGlobalDashboard: bot DB fish cache missing or empty', { dbPath: getDbPath(), dbAvailable: isAvailable() });
    return empty;
  }

  const allUsers = listRealBotUsers(fish);
  const catchStats = collectCatchRowStats(allUsers, win);
  const { secretCaught, forgottenCaught } = countRarityForUsers(allUsers, win);
  const dailyCaught = aggregateDailyCaughtForUsers(allUsers, win);
  const fishCards = aggregateFishCardsForUsers(allUsers, win);

  return {
    available: true,
    period,
    periodLabel: win.label,
    from: fromIso,
    to: toIso,
    cards: {
      secretCaught,
      forgottenCaught,
    },
    fishCards,
    dailyCaught,
    source: 'deng_fish_it_bot_db_d_command',
    debug: {
      dbSource: 'deng_fish_it_bot_db_d_command',
      source: 'deng_fish_it_bot_db_d_command',
      store: 'app_kv',
      key: KEY_FISH,
      dbPath: getDbPath(),
      dbAvailable: true,
      selectedRange: period,
      period,
      dateRange: { from: fromIso, to: toIso },
      usersScanned: allUsers.length,
      totalCatchRows: catchStats.totalCatchRows,
      filteredCatchRows: catchStats.filteredCatchRows,
      detailRowsMatched: catchStats.filteredCatchRows,
      secretCount: secretCaught,
      forgottenCount: forgottenCaught,
      caughtFishCount: fishCards.reduce((sum, card) => sum + num(card.count), 0),
      fishCardCount: fishCards.length,
      firstCatchAt: catchStats.firstCatchAt,
      lastCatchAt: catchStats.lastCatchAt,
      dailyRows: dailyCaught.length,
      chartBucket: chartBuckets.bucket,
      method: 'byUser.details window filter + byDate daily buckets',
    },
  };
}

/**
 * Owner-scoped dashboard for /tracker — same bot DB identity path as !d / !s.
 * Primary lookup: fish.byUser[discordUserId]. Tracked Roblox accounts are fallback only.
 */
function getOwnerDashboard(discordUserId, trackedAccounts, periodInput = 'all', opts = {}) {
  const queryStartedAt = opts.queryStartedAt || Date.now();
  const authDiscordUsername = opts.authDiscordUsername != null
    ? String(opts.authDiscordUsername)
    : null;
  const authDiscordId = String(discordUserId || '').trim();
  const period = normalizeDashboardPeriod(periodInput);
  const win = resolveDashboardWindow(period, opts);
  const trackedCount = Array.isArray(trackedAccounts) ? trackedAccounts.length : 0;
  const queryMs = () => Date.now() - queryStartedAt;

  if (!win) {
    return {
      available: false,
      emptyReason: 'invalid_custom_range',
      period: 'custom',
      from: null,
      to: null,
      cards: { secretCaught: 0, forgottenCaught: 0 },
      fishCards: [],
      dailyCaught: [],
      scope: 'owner',
      discordUserId: authDiscordId,
      trackedAccountCount: trackedCount,
      source: 'deng_fish_it_bot_db_d_command',
      error: 'invalid_custom_range',
      debug: buildOwnerDashboardDebug({
        authDiscordId,
        authDiscordUsername,
        botDbConnected: isAvailable(),
        fishCacheLoaded: false,
        period,
        matchedUsers: [],
        match: null,
        allTimeCatchRows: 0,
        filteredCatchRows: 0,
        secretCaught: 0,
        forgottenCaught: 0,
        firstCatchAt: null,
        lastCatchAt: null,
        trackedCount,
        dateRange: { from: null, to: null },
        caughtFishCount: 0,
        fishCardCount: 0,
        dailyRows: 0,
        chartBucket: null,
        queryMs: queryMs(),
        emptyReason: 'invalid_custom_range',
      }),
    };
  }

  const botDbConnected = isAvailable();
  const fish = readBlob(KEY_FISH);
  const fishCacheLoaded = !!(fish && fish.byUser);
  const fromIso = new Date(win.from).toISOString();
  const toIso = new Date(win.to).toISOString();
  const chartBuckets = enumerateChartBuckets(win);
  const emptyDaily = chartBuckets.keys.map((date) => ({ date, totalCaught: 0, bucket: chartBuckets.bucket }));
  const base = {
    period,
    periodLabel: win.label,
    from: fromIso,
    to: toIso,
    scope: 'owner',
    discordUserId: authDiscordId,
    trackedAccountCount: trackedCount,
    source: 'deng_fish_it_bot_db_d_command',
  };

  const { users: matchedUsers, match } = resolveDashboardBotUsers(fish, authDiscordId, trackedAccounts);
  const allTimeStats = collectAllTimeCatchRowStats(matchedUsers);
  const catchStats = collectCatchRowStats(matchedUsers, win);
  const { secretCaught, forgottenCaught } = countRarityForUsers(matchedUsers, win);
  const dailyCaught = aggregateDailyCaughtForUsers(matchedUsers, win);
  const fishCards = aggregateFishCardsForUsers(matchedUsers, win);
  const caughtFishCount = fishCards.reduce((sum, card) => sum + num(card.count), 0);

  const ctx = {
    authDiscordId,
    authDiscordUsername,
    botDbConnected,
    fishCacheLoaded,
    period,
    matchedUsers,
    match,
    allTimeCatchRows: allTimeStats.allTimeCatchRows,
    filteredCatchRows: catchStats.filteredCatchRows,
    secretCaught,
    forgottenCaught,
    firstCatchAt: catchStats.firstCatchAt || allTimeStats.firstCatchAt,
    lastCatchAt: catchStats.lastCatchAt || allTimeStats.lastCatchAt,
    trackedCount,
    dateRange: { from: fromIso, to: toIso },
    caughtFishCount,
    fishCardCount: fishCards.length,
    dailyRows: dailyCaught.length,
    chartBucket: chartBuckets.bucket,
    queryMs: queryMs(),
  };
  ctx.emptyReason = deriveOwnerDashboardEmptyReason(ctx);
  const debug = buildOwnerDashboardDebug(ctx);

  const fatalReasons = new Set([
    'missing_auth_discord_id',
    'bot_db_not_connected',
    'fish_cache_missing_or_empty',
  ]);
  const available = !fatalReasons.has(ctx.emptyReason);
  const statsState = available
    ? ((ctx.filteredCatchRows === 0 && ctx.allTimeCatchRows === 0) ? 'empty' : 'ok')
    : 'error';

  if (!available) {
    const emptyReason = ctx.emptyReason || 'dashboard_unavailable';
    const dbStatus = fishitDbPath.getDbStatus(true);
    if (ctx.emptyReason === 'bot_db_not_connected') {
      console.error('[fishit] getOwnerDashboard: bot DB not connected', {
        dbPath: getDbPath(),
        dbStatus,
        authDiscordId,
        candidates: fishitDbPath.candidateDbPaths(),
      });
    } else if (ctx.emptyReason === 'fish_cache_missing_or_empty') {
      console.error('[fishit] getOwnerDashboard: bot DB fish cache missing or empty', {
        dbPath: getDbPath(),
        dbStatus,
        authDiscordId,
        trackedAccountCount: trackedCount,
      });
    }

    return {
      ...base,
      available: false,
      statsState: 'error',
      emptyReason,
      cards: { secretCaught: 0, forgottenCaught: 0 },
      fishCards: [],
      dailyCaught: emptyDaily,
      debug,
    };
  }

  const userEmptyReasons = new Set([
    'no_bot_user_for_discord_id',
    'no_catch_records_in_bot_db',
    'date_range_filtered_all_rows',
  ]);
  if (ctx.emptyReason === 'no_bot_user_for_discord_id') {
    console.warn('[fishit] getOwnerDashboard: no bot DB user for Discord ID (returning zero stats)', {
      authDiscordId,
      authDiscordUsername,
      trackedAccountCount: trackedCount,
      dbPath: getDbPath(),
      selectedRange: period,
      identityMatchMode: match && match.identityMatchMode,
    });
  }

  const showEmptyReason = userEmptyReasons.has(ctx.emptyReason) ? ctx.emptyReason : null;

  return {
    ...base,
    available: true,
    statsState,
    emptyReason: showEmptyReason,
    cards: { secretCaught, forgottenCaught },
    fishCards,
    dailyCaught,
    debug,
  };
}

function inWindow(iso, win) {
  const t = Date.parse(iso);
  return Number.isFinite(t) && t >= win.from && t < win.to;
}

function num(v) { const n = Number(v); return Number.isFinite(n) ? n : 0; }

// ── Image resolution (mirrors the bot's !lb / !leaderboard resolver) ─────────
// Bot order: local PNG → fish_catalog_seen → forgotten_fish.imageUrl → every
// user's fishThumbnails. We can't read the bot's local PNG cache, but we read
// the same DB-backed sources (catalog table + all-user thumbnails + forgotten
// emoji) so Secret/Forgotten cards get real images, never a generic icon when
// a real one exists.

const DENG_LOGO_HINTS = [/deng[-_]hub/i, /qZ1thB4/i];

function isValidImg(url) {
  const u = String(url || '').trim();
  if (u.startsWith('/api/fishit/assets/stats-fish/')) return true;
  if (!/^https?:\/\//i.test(u)) return false;
  if (DENG_LOGO_HINTS.some((re) => re.test(u))) return false;
  return true;
}

function normKey(name) {
  return String(name || '').toLowerCase().replace(/\s+/g, ' ').trim();
}

/** Aggressive fold for lookup: strip ellipsis, punctuation, collapse spaces. */
function foldKey(name) {
  return String(name || '')
    .toLowerCase()
    .replace(/\u2026/g, '')
    .replace(/\.{2,}/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

/** Common UI typos / truncations → canonical fold keys in DB. */
const FISH_NAME_ALIASES = {
  'elshark grand maja': 'elshark gran maja',
};

/** URL-safe species slug, e.g. "Strawberry Shenanigans" → "strawberry-shenanigans". */
function speciesKey(name) {
  return String(name || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'unknown';
}

/** Compact weight string, e.g. 1_100_000 → "1.1M". Null for non-positive. */
function formatWeight(n) {
  const v = Number(n);
  if (!Number.isFinite(v) || v <= 0) return null;
  const trim = (s) => s.replace(/\.0$/, '');
  if (v >= 1e9) return trim((v / 1e9).toFixed(1)) + 'B';
  if (v >= 1e6) return trim((v / 1e6).toFixed(1)) + 'M';
  if (v >= 1e3) return trim((v / 1e3).toFixed(1)) + 'K';
  return String(Math.round(v));
}

/** Discord custom emoji `<:name:id>` / `<a:name:id>` → CDN PNG URL, else null. */
function emojiCdnUrl(emoji) {
  const m = String(emoji || '').match(/^<a?:[^:>]+:(\d+)>$/);
  return m ? `https://cdn.discordapp.com/emojis/${m[1]}.png` : null;
}

let _imgIndex = null;
let _imgIndexAt = 0;

/** Build (and cache) a normalizedName → imageUrl map from all DB-backed sources. */
function buildImageIndex() {
  const now = Date.now();
  if (_imgIndex && now - _imgIndexAt < CACHE_TTL_MS) return _imgIndex;
  const idx = new Map();
  const put = (name, url, source) => {
    if (!isValidImg(url) && !String(url || '').startsWith('/api/fishit/assets/stats-fish/')) return;
    const u = String(url).trim();
    const nk = normKey(name);
    const fk = foldKey(name);
    if (nk && !idx.has(nk)) idx.set(nk, { url: u, source: source || 'index' });
    if (fk && fk !== nk && !idx.has(fk)) idx.set(fk, { url: u, source: source || 'index' });
  };

  // 0. Manual verified stats fish images (Quiz Bot bank copies — BLOCKER10ZJ).
  try {
    manualStatsFishImages.seedImageIndex(put);
  } catch (_) { /* optional catalog */ }

  // 1. fish_catalog_seen table (PokéMeow/kolam catalog — same as bot).
  try {
    const db = openDb();
    if (db) {
      const rows = db.prepare(
        'SELECT normalized_key, canonical_name, image_url FROM fish_catalog_seen WHERE image_url IS NOT NULL',
      ).all();
      for (const r of rows) {
        put(r.canonical_name, r.image_url, 'fish_catalog_seen');
        if (r.normalized_key) put(r.normalized_key, r.image_url, 'fish_catalog_seen');
      }
    }
  } catch (_) { /* table may not exist on old bots */ }

  // 2. every user's fishThumbnails + per-catch detail thumbnails.
  const fish = readBlob(KEY_FISH);
  if (fish && fish.byUser) {
    for (const u of Object.values(fish.byUser)) {
      const ft = u.fishThumbnails;
      if (ft && typeof ft === 'object') {
        for (const [n, url] of Object.entries(ft)) put(n, url, 'fishThumbnails');
      }
      const det = (u.details && typeof u.details === 'object') ? u.details : {};
      for (const arr of [det.secret, det.forgotten, det.thunder, det.sea]) {
        for (const c of (Array.isArray(arr) ? arr : [])) {
          if (c) put(c.name || c.fishType, c.thumbnail, 'catch_detail');
        }
      }
    }
  }

  // 3. forgotten_fish catalog (explicit imageUrl, then emoji artwork).
  const forg = readBlob(KEY_FORGOTTEN);
  if (forg && Array.isArray(forg.fish)) {
    for (const f of forg.fish) {
      if (f.imageUrl) put(f.name, f.imageUrl, 'forgotten_catalog');
      put(f.name, emojiCdnUrl(f.emoji), 'forgotten_emoji');
    }
  }

  _imgIndex = idx;
  _imgIndexAt = now;
  return idx;
}

function _lookupIndexed(name) {
  const idx = buildImageIndex();
  const tries = [];
  const folded = foldKey(name);
  const alias = FISH_NAME_ALIASES[folded];
  tries.push(normKey(name), folded);
  if (alias) tries.push(alias, foldKey(alias));
  for (const k of tries) {
    if (!k) continue;
    const hit = idx.get(k);
    if (hit && hit.url) return hit;
  }
  return null;
}

/**
 * Resolve a real image URL for a species. Priority:
 *   1. the species' own catch thumbnail (most specific), then
 *   2. the global DB image index (catalog + all-user thumbnails + forgotten).
 * Returns null when nothing real exists (client shows its fallback icon).
 */
function resolveSpeciesImage(name, perCatchThumb) {
  if (isValidImg(perCatchThumb)) return String(perCatchThumb).trim();
  const hit = _lookupIndexed(name);
  return hit ? hit.url : null;
}

/** Same as resolveSpeciesImage but returns { url, source } for audits. */
function resolveSpeciesImageSource(name, perCatchThumb) {
  if (isValidImg(perCatchThumb)) {
    return { url: String(perCatchThumb).trim(), source: 'catch_thumbnail' };
  }
  const hit = _lookupIndexed(name);
  return hit || { url: null, source: 'none' };
}

/** Audit all species keys in the fish cache. */
function auditSpeciesImages() {
  const fish = readBlob(KEY_FISH);
  const names = new Set();
  if (fish && fish.byUser) {
    for (const u of Object.values(fish.byUser)) {
      if (!isRealUserId(String(u.userId))) continue;
      for (const n of Object.keys(u.secretFish || {})) names.add(n);
      for (const n of Object.keys(u.forgottenFish || {})) names.add(n);
    }
  }
  const rows = [];
  let withImg = 0;
  for (const name of names) {
    const r = resolveSpeciesImageSource(name, null);
    if (r.url) withImg += 1;
    rows.push({ name, imageUrl: r.url, source: r.source });
  }
  return {
    total: rows.length,
    with_image: withImg,
    missing: rows.length - withImg,
    missing_names: rows.filter((r) => !r.imageUrl).map((r) => r.name).sort(),
    rows: rows.sort((a, b) => a.name.localeCompare(b.name)),
  };
}

/** First real image across a list of species names, or null. */
function firstSpeciesImage(names) {
  for (const n of (names || [])) {
    const u = resolveSpeciesImage(n, null);
    if (u) return u;
  }
  return null;
}

/**
 * Forgotten total that avoids double-counting Thunderzilla / Sea Eater.
 * forgottenFish{} is canonical; the dedicated thunderzilla/seaEater counters
 * are only added when that species is NOT already a key in the map.
 */
function forgottenTotal(u) {
  const map = (u && u.forgottenFish && typeof u.forgottenFish === 'object') ? u.forgottenFish : {};
  let sum = Object.values(map).reduce((a, c) => a + num(c), 0);
  if (!('Thunderzilla' in map)) sum += num(u && u.thunderzilla);
  if (!('Sea Eater' in map)) sum += num(u && u.seaEater);
  return sum;
}

/** Sum byDate.total for day buckets whose WIB midnight falls in the window. */
function sumByDateTotal(byDate, win) {
  if (!byDate || typeof byDate !== 'object') return 0;
  let sum = 0;
  for (const [date, agg] of Object.entries(byDate)) {
    const t = Date.parse(`${date}T00:00:00+07:00`);
    if (Number.isFinite(t) && t >= win.from && t < win.to) sum += num(agg && agg.total);
  }
  return sum;
}

// ── Public accessors ─────────────────────────────────────────────────────────

/** Is the Fish It database file reachable (readable app_kv)? Fish cache presence is checked separately. */
function isAvailable() {
  const status = fishitDbPath.getDbStatus(false);
  return !!(status.exists && status.readable && status.hasAppKv);
}

function getDbConnectionInfo() {
  return fishitDbPath.getDbStatus(true);
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

/** Global catch count for a calendar period (WIB), aggregated across all real users. */
function getGlobalPeriodCaught(period = 'yesterday') {
  const fish = readBlob(KEY_FISH);
  const win = periodWindow(period);
  const meta = {
    period,
    periodLabel: win.label,
    timezone: 'Asia/Jakarta',
    windowFrom: new Date(win.from).toISOString(),
    windowTo: new Date(win.to).toISOString(),
  };
  if (!fish || !fish.byUser) return { ...meta, count: 0 };

  let total = 0;
  for (const u of Object.values(fish.byUser)) {
    if (!isRealUserId(String(u.userId))) continue;
    let userTotal = sumByDateTotal(u.byDate, win);
    if (!userTotal) {
      const secret = (u.details && u.details.secret || []).filter((c) => inWindow(c.time, win));
      const forgotten = (u.details && u.details.forgotten || []).filter((c) => inWindow(c.time, win));
      userTotal = secret.length + forgotten.length;
    }
    total += userTotal;
  }
  return { ...meta, count: total };
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
    // Global rod cards reuse the same real-image resolver as the user stats.
    rod_cards: rod ? [
      buildRodCard('ghostfinn', rod.totalGhostfinn),
      buildRodCard('element', rod.totalElement),
      buildRodCard('diamond', rod.totalDiamond),
    ] : [],
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
  const forgottenCount = u ? forgottenTotal(u) : 0;

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

function rarityRank(r) {
  const s = String(r || '').trim().toLowerCase();
  if (s === 'secret') return 0;
  if (s === 'forgotten') return 1;
  if (s === 'mythic') return 2;
  if (s === 'legendary' || s === 'legend') return 3;
  if (s === 'epic') return 4;
  if (s === 'rare') return 5;
  if (s === 'uncommon') return 6;
  if (s === 'common') return 7;
  return 99;
}

/** A standardized rod card with a real channel-derived image (Part 8). */
function buildRodCard(key, count) {
  return {
    key,
    label: rodAssets.rodLabel(key),
    count: num(count),
    amount: num(count), // alias for clients that read `amount`
    imageUrl: rodAssets.rodImageUrl(key),
    fallback: 'rod',
  };
}

/**
 * /api/fishit/me/stats — standardized (Part 11).
 * summaryCards (Total/Secret/Forgotten) + rarityCards + rodCards. Rarity cards
 * carry a real representative species image; rod cards carry the real rod image.
 */
function getUserStats(discordId) {
  const profile = getUserProfile(discordId);
  if (!profile.has_data) return { hasData: false };
  const u = rawUser(discordId) || {};

  const secretImg = firstSpeciesImage(Object.keys(u.secretFish || {}));
  const forgottenImg = firstSpeciesImage([
    ...Object.keys(u.forgottenFish || {}),
    'Thunderzilla',
    'Sea Eater',
  ]);

  return {
    hasData: true,
    discordUserId: profile.discord_user_id,
    username: profile.username,
    totalFish: profile.total_fish,
    rank: profile.rank,
    summaryCards: [
      { key: 'total', label: 'Total Fish', amount: profile.total_fish, imageUrl: null, fallback: 'fish' },
      { key: 'secret', label: 'Secret', amount: profile.secret_fish, imageUrl: secretImg, fallback: 'secret' },
      { key: 'forgotten', label: 'Forgotten', amount: profile.forgotten_fish, imageUrl: forgottenImg, fallback: 'forgotten' },
    ],
    rarityCards: [
      { key: 'secret', label: 'Secret', amount: profile.secret_fish, imageUrl: secretImg, fallback: 'secret' },
      { key: 'forgotten', label: 'Forgotten', amount: profile.forgotten_fish, imageUrl: forgottenImg, fallback: 'forgotten' },
    ],
    rodCards: [
      buildRodCard('ghostfinn', profile.rods.ghostfinn),
      buildRodCard('element', profile.rods.element),
      buildRodCard('diamond', profile.rods.diamond),
    ],
  };
}

/** Per-species detail (max weight / latest time / mutation / thumb) from catch arrays. */
function buildSpeciesDetail(u) {
  const detail = {};
  const ingest = (arr) => {
    for (const c of (Array.isArray(arr) ? arr : [])) {
      const name = c && (c.name || c.fishType);
      if (!name) continue;
      const d = detail[name] || (detail[name] = { maxWeight: 0, lastTime: null, mutation: null, thumb: null });
      const w = num(c.weight);
      if (w > d.maxWeight) { d.maxWeight = w; d.mutation = c.mutation || d.mutation; }
      if (c.time && (!d.lastTime || c.time > d.lastTime)) d.lastTime = c.time;
      if (!d.thumb && isValidImg(c.thumbnail)) d.thumb = c.thumbnail;
    }
  };
  ingest(u.details && u.details.secret);
  ingest(u.details && u.details.forgotten);
  ingest(u.details && u.details.thunder);
  ingest(u.details && u.details.sea);
  return detail;
}

/**
 * /api/fishit/me/fish — standardized card list (Part 11). One card per tracked
 * species (Secret + Forgotten), each with a real image, rarity, count and a
 * compact max-weight string. Sorting/paging happens in the route.
 */
function getUserFish(discordId) {
  const u = rawUser(discordId);
  if (!u) return { hasData: false, items: [] };
  const detail = buildSpeciesDetail(u);

  const items = [];
  const seen = new Set();
  const addCard = (name, count, rarity) => {
    if (!name) return;
    const key = normKey(name);
    if (seen.has(key)) return; // avoid Thunderzilla appearing twice
    seen.add(key);
    const d = detail[name] || {};
    items.push({
      speciesKey: speciesKey(name),
      name,
      rarity: rarity === 'forgotten' ? 'Forgotten' : 'Secret',
      count: num(count),
      imageUrl: resolveSpeciesImage(name, d.thumb),
      maxWeight: formatWeight(d.maxWeight),
      maxWeightGrams: d.maxWeight || 0, // numeric, for server-side value sort
      mutation: d.mutation || null,
      latestCaughtAt: d.lastTime || null,
      fallback: rarity === 'forgotten' ? 'forgotten' : 'secret',
    });
  };
  for (const [name, count] of Object.entries(u.secretFish || {})) addCard(name, count, 'secret');
  for (const [name, count] of Object.entries(u.forgottenFish || {})) addCard(name, count, 'forgotten');
  // Thunderzilla / Sea Eater only if not already represented in forgottenFish.
  if (!('Thunderzilla' in (u.forgottenFish || {})) && num(u.thunderzilla) > 0) addCard('Thunderzilla', u.thunderzilla, 'forgotten');
  if (!('Sea Eater' in (u.forgottenFish || {})) && num(u.seaEater) > 0) addCard('Sea Eater', u.seaEater, 'forgotten');

  items.sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  return { hasData: items.length > 0, totalSpecies: items.length, items };
}

/**
 * /api/fishit/me/daily — standardized per-species cards (Part 11). For the
 * selected period, returns one card per Secret/Forgotten species caught (image
 * + name + rarity + count), plus summary counts. No "best catch".
 * `period` ∈ today | yesterday | 7d | 30d | all.
 */
function getUserDaily(discordId, period = 'today') {
  const u = rawUser(discordId);
  const fish = readBlob(KEY_FISH);
  const win = periodWindow(period);
  const lastUpdated = (fish && fish.lastUpdated) || null;
  if (!u) {
    return {
      hasData: false, period, periodLabel: win.label, timezone: 'Asia/Jakarta',
      summary: { totalFish: 0, secretFish: 0, forgottenFish: 0 }, cards: [], lastUpdated,
    };
  }
  const secret = (u.details && u.details.secret || []).filter((c) => inWindow(c.time, win));
  // details.forgotten already contains Thunderzilla / Sea Eater catches, so we
  // don't merge details.thunder/sea (that would double-count).
  const forgotten = (u.details && u.details.forgotten || []).filter((c) => inWindow(c.time, win));

  const groups = new Map();
  const add = (c, rarity) => {
    const name = c.name || c.fishType;
    if (!name) return;
    const g = groups.get(name) || { name, rarity, count: 0, maxWeight: 0, latest: null, thumb: null };
    g.count += 1;
    const w = num(c.weight);
    if (w > g.maxWeight) g.maxWeight = w;
    if (c.time && (!g.latest || c.time > g.latest)) g.latest = c.time;
    if (!g.thumb && isValidImg(c.thumbnail)) g.thumb = c.thumbnail;
    groups.set(name, g);
  };
  secret.forEach((c) => add(c, 'Secret'));
  forgotten.forEach((c) => add(c, 'Forgotten'));

  const cards = sortFishCardsByRarity([...groups.values()].map((g) => ({
    speciesKey: speciesKey(g.name),
    name: g.name,
    rarity: g.rarity,
    count: g.count,
    imageUrl: resolveSpeciesImage(g.name, g.thumb),
    maxWeight: formatWeight(g.maxWeight),
    latestCaughtAt: g.latest,
    fallback: g.rarity === 'Forgotten' ? 'forgotten' : 'secret',
  })));

  const totalFish = sumByDateTotal(u.byDate, win) || (secret.length + forgotten.length);
  return {
    hasData: cards.length > 0,
    period,
    periodLabel: win.label,
    timezone: 'Asia/Jakarta',
    summary: {
      totalFish,
      secretFish: secret.length,
      forgottenFish: forgotten.length,
    },
    cards,
    lastUpdated,
  };
}

module.exports = {
  DB_PATH: getDbPath,
  getDbPath,
  getDbConnectionInfo,
  isAvailable,
  getGlobal,
  getGlobalPeriodCaught,
  getGlobalDashboard,
  getOwnerDashboard,
  normalizeDashboardPeriod,
  customPeriodWindow,
  resolveDashboardWindow,
  resolveDashboardBotUsers,
  resolveTrackedBotUsers,
  collectAllTimeCatchRowStats,
  countRarityForUsers,
  sortFishCardsByRarity,
  collectCatchRowStats,
  getForgottenSpecies,
  getUserProfile,
  getUserStats,
  getUserFish,
  getUserDaily,
  // helpers exported for tests / reuse
  speciesKey,
  formatWeight,
  resolveSpeciesImage,
  resolveSpeciesImageSource,
  auditSpeciesImages,
  foldKey,
  normKey,
  buildImageIndex,
  exportImageCatalog,
  exportRarityHints,
  forgottenTotal,
  _resetCache,
};
