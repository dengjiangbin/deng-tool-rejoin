'use strict';
/**
 * Fish It stats API.
 *
 * Identity model (security-critical):
 *   - The trusted Discord user ID comes ONLY from the server side:
 *       1. the website cookie session (req.session.user.discord_user_id), or
 *       2. the Android app bearer token -> monitor_app_sessions.owner_discord_user_id.
 *   - A discord_id supplied by the client (query/body) is NEVER trusted for
 *     private data. Stats always match by the authenticated ID.
 *
 * All data is read from the Fish It bot's SQLite DB via ./fishitDb (read-only).
 * No bot tokens or DB secrets are ever exposed. Global stats are public; every
 * /me route requires a valid session/token.
 */

const express = require('express');
const crypto = require('crypto');
const path = require('path');
const fs = require('fs');
const rateLimit = require('express-rate-limit');
const supabase = require('./db');
const fishit = require('./fishitDb');
const manualStatsFishImages = require('./fishitManualStatsFishImages');

const router = express.Router();
const jsonParser = express.json({ limit: '16kb' });

const DAILY_PERIODS = new Set(['today', 'yesterday', '7d', '30d', 'all']);
const FISH_SORTS = new Set(['amount', 'name', 'rarity', 'value', 'recent']);
const RARITIES = new Set(['secret', 'forgotten']);

// Fallback image identifiers the client maps to bundled icons.
const FALLBACKS = {
  fish: '/public/img/fishit/fallback-fish.svg',
  rod: '/public/img/fishit/fallback-rod.svg',
  secret: '/public/img/fishit/fallback-secret.svg',
  forgotten: '/public/img/fishit/fallback-forgotten.svg',
};

const fishitLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 120,
  skip: () => process.env.NODE_ENV === 'test',
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'too_many_requests', message: 'Too many requests, please slow down.' },
});

function sha256(s) { return crypto.createHash('sha256').update(String(s)).digest('hex'); }

function extractBearer(req) {
  const h = req.headers.authorization || '';
  const m = /^Bearer\s+(.+)$/i.exec(h);
  return m ? m[1].trim() : null;
}

/** Resolve the trusted Discord ID from session or app token. Null if neither. */
async function resolveIdentity(req) {
  if (req.session && req.session.user && req.session.user.discord_user_id) {
    return String(req.session.user.discord_user_id);
  }
  const token = extractBearer(req);
  if (token) {
    try {
      const { data: row } = await supabase
        .from('monitor_app_sessions')
        .select('owner_discord_user_id, expires_at, revoked_at')
        .eq('token_hash', sha256(token))
        .maybeSingle();
      if (row && !row.revoked_at && row.owner_discord_user_id &&
          new Date(row.expires_at).getTime() > Date.now()) {
        return String(row.owner_discord_user_id);
      }
    } catch (_) { /* fall through to 401 */ }
  }
  return null;
}

async function requireFishUser(req, res, next) {
  try {
    const id = await resolveIdentity(req);
    if (!id) return res.status(401).json({ error: 'auth_required', message: 'Sign in with Discord to view your Fish It stats.' });
    req.fishOwner = id;
    return next();
  } catch (err) {
    return res.status(500).json({ error: 'auth_error' });
  }
}

function ok(res, payload) { return res.json(payload); }

// ── Public: global stats (cached at the DB layer; short browser cache) ───────
router.get('/api/fishit/global', fishitLimiter, (req, res) => {
  try {
    const g = fishit.getGlobal();
    res.set('Cache-Control', 'public, max-age=15');
    return ok(res, g && g.available ? g : { available: false });
  } catch (err) {
    return res.status(200).json({ available: false });
  }
});

// ── Public: manual verified stats fish images (BLOCKER10ZJ) ────────────────
router.get('/api/fishit/assets/stats-fish/:filename', fishitLimiter, (req, res) => {
  const file = path.basename(String(req.params.filename || ''));
  if (!file || !/^[a-zA-Z0-9._-]+$/.test(file)) {
    return res.status(404).json({ error: 'not_found' });
  }
  const full = path.join(manualStatsFishImages.getCacheDir(), file);
  if (!fs.existsSync(full)) return res.status(404).json({ error: 'not_found' });
  res.set('Cache-Control', 'public, max-age=86400');
  return res.sendFile(full);
});

// ── Public: safe asset/fallback URLs + forgotten species images ──────────────
router.get('/api/fishit/assets', fishitLimiter, (req, res) => {
  try {
    const species = fishit.getForgottenSpecies().map((f) => ({
      name: f.name,
      image: f.imageUrl || null, // only real stored URLs; never invented
    }));
    res.set('Cache-Control', 'public, max-age=60');
    return ok(res, { fallbacks: FALLBACKS, forgotten_species: species });
  } catch (err) {
    return ok(res, { fallbacks: FALLBACKS, forgotten_species: [] });
  }
});

// ── Private: profile summary ─────────────────────────────────────────────────
router.get('/api/fishit/me', fishitLimiter, requireFishUser, (req, res) => {
  const profile = fishit.getUserProfile(req.fishOwner);
  return ok(res, profile);
});

// ── Private: card-friendly stats (standardized — Part 11) ────────────────────
router.get('/api/fishit/me/stats', fishitLimiter, requireFishUser, (req, res) => {
  const stats = fishit.getUserStats(req.fishOwner);
  if (!stats || !stats.hasData) {
    return ok(res, { ok: true, hasData: false, summaryCards: [], rarityCards: [], rodCards: [] });
  }
  const attach = (c) => { c.fallbackUrl = FALLBACKS[c.fallback] || FALLBACKS.fish; };
  (stats.summaryCards || []).forEach(attach);
  (stats.rarityCards || []).forEach(attach);
  (stats.rodCards || []).forEach((c) => { c.fallbackUrl = FALLBACKS[c.fallback] || FALLBACKS.rod; });
  return ok(res, { ok: true, ...stats });
});

// ── Private: daily per-species cards with period filter (standardized) ───────
router.get('/api/fishit/me/daily', fishitLimiter, requireFishUser, (req, res) => {
  let period = String(req.query.period || 'today').toLowerCase();
  if (!DAILY_PERIODS.has(period)) period = 'today';
  const daily = fishit.getUserDaily(req.fishOwner, period);
  (daily.cards || []).forEach((c) => { c.fallbackUrl = FALLBACKS[c.fallback] || FALLBACKS.fish; });
  const emptyMessage = daily.hasData ? null : 'No catches found for this period.';
  return ok(res, { ok: true, emptyMessage, ...daily });
});

// ── Private: fish card grid (server-side search / filter / sort / paginate) ──
router.get('/api/fishit/me/fish', fishitLimiter, requireFishUser, (req, res) => {
  const result = fishit.getUserFish(req.fishOwner);
  let items = Array.isArray(result.items) ? result.items : [];

  const search = String(req.query.search || '').trim().toLowerCase();
  if (search) items = items.filter((f) => f.name.toLowerCase().includes(search));

  const rarity = String(req.query.rarity || '').trim().toLowerCase();
  if (RARITIES.has(rarity)) items = items.filter((f) => f.rarity.toLowerCase() === rarity);

  const sort = FISH_SORTS.has(String(req.query.sort)) ? String(req.query.sort) : 'amount';
  const cmp = {
    amount: (a, b) => b.count - a.count,
    name: (a, b) => a.name.localeCompare(b.name),
    rarity: (a, b) => String(a.rarity).localeCompare(String(b.rarity)) || b.count - a.count,
    value: (a, b) => (b.maxWeightGrams || 0) - (a.maxWeightGrams || 0),
    recent: (a, b) => String(b.latestCaughtAt || '').localeCompare(String(a.latestCaughtAt || '')),
  }[sort];
  items = items.slice().sort((a, b) => cmp(a, b) || a.name.localeCompare(b.name));

  const total = items.length;
  const limit = Math.min(Math.max(parseInt(req.query.limit, 10) || 60, 1), 200);
  const page = Math.max(parseInt(req.query.page, 10) || 1, 1);
  const start = (page - 1) * limit;
  const pageItems = items.slice(start, start + limit);
  pageItems.forEach((f) => { f.fallbackUrl = FALLBACKS[f.fallback] || FALLBACKS.fish; });

  return ok(res, {
    ok: true,
    hasData: total > 0,
    items: pageItems,
    total,
    totalSpecies: result.totalSpecies || total,
    page,
    limit,
    pages: Math.max(Math.ceil(total / limit), 1),
  });
});

module.exports = router;
module.exports.resolveIdentity = resolveIdentity; // exported for tests
