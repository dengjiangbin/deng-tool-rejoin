'use strict';
/**
 * Fish It Backpack Tracker – API routes + dashboard page.
 *
 * Public routes (no authentication required):
 *   GET  /tracker                        – serve the live dashboard UI
 *   POST /api/tracker/update-backpack    – receive payload from the Lua client
 *   GET  /api/tracker/get-backpack/:user – query live data for a username
 *
 * Security notes:
 *   - All data lives only in process memory (liveTrackDB). Nothing is
 *     persisted to disk or database.
 *   - Input is strictly validated and sanitised before storage.
 *   - Dedicated rate-limiters protect both endpoints independently so the
 *     global site limiter is not exhausted by the 2500 ms frontend polling.
 *   - Username keys are always lowercased; original casing is preserved
 *     inside the stored payload for display purposes only.
 */

const express   = require('express');
const rateLimit = require('express-rate-limit');

const catalogStore = require('./fishitCatalogStore');

// Optional Fish It DB image resolver (real fish artwork). Loaded lazily and
// defensively so the tracker keeps working even if the DB module is absent.
let fishitDb = null;
try { fishitDb = require('./fishitDb'); } catch (_) { fishitDb = null; }

function dbImageFor(name) {
  if (!fishitDb || typeof fishitDb.resolveSpeciesImage !== 'function') return null;
  try {
    const url = fishitDb.resolveSpeciesImage(name);
    return (typeof url === 'string' && /^https?:\/\//i.test(url)) ? url : null;
  } catch (_) { return null; }
}

const router = express.Router();

// ── In-memory live-data store ─────────────────────────────────────
// Key: lowercased Roblox username  |  Value: last received payload + server ts
const liveTrackDB = {};

// ── Rate limiters ─────────────────────────────────────────────────
// POST: Lua scripts fire every 3 s but only when data changes, so 5/10 s
// gives generous headroom while preventing abuse.
const postLimiter = rateLimit({
  windowMs: 10 * 1000,   // 10 seconds
  max: 5,
  skip: () => process.env.NODE_ENV === 'test',
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'too_many_requests', message: 'Slow down.' },
});

// GET: frontend polls every 2500 ms = ~24 req/min, so 60/min is comfortable.
const getLimiter = rateLimit({
  windowMs: 60 * 1000,   // 1 minute
  max: 60,
  skip: () => process.env.NODE_ENV === 'test',
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'too_many_requests', message: 'Slow down.' },
});

// ── Input validation ──────────────────────────────────────────────
// Roblox usernames: 3–20 chars, alphanumeric + underscore.
const USERNAME_RE = /^[A-Za-z0-9_]{3,20}$/;

function sanitiseUsername(raw) {
  if (typeof raw !== 'string') return null;
  const s = raw.trim();
  return USERNAME_RE.test(s) ? s : null;
}

function sanitiseItems(raw) {
  if (!Array.isArray(raw)) return [];
  const out = [];
  for (const item of raw.slice(0, 300)) {
    const name = typeof item.name === 'string'
      ? item.name
      : (typeof item.Name === 'string' ? item.Name : '');
    // Drop stat/UI labels (e.g. "Caught", "Rarest Fish") on the way in.
    if (!name || catalogStore.isStatLabel(name)) continue;

    const rawWeight = item.weight ?? item.maxWeight ?? item.totalWeight ?? item.Weight;
    const rawAmount = item.amount ?? item.count ?? item.Amount ?? item.Count ?? 1;
    const weight = Number(rawWeight);
    const amount = Number(rawAmount);

    const rarity = typeof item.rarity === 'string'
      ? item.rarity
      : (typeof item.tier === 'string' ? item.tier : null);

    out.push({
      name:     name.slice(0, 100),
      weight:   Number.isFinite(weight) ? weight : null,
      amount:   Number.isFinite(amount) && amount > 0 ? Math.floor(amount) : 1,
      category: typeof item.category === 'string' ? item.category.slice(0, 50)  : null,
      tab:      typeof item.tab === 'string'      ? item.tab.slice(0, 50)       : null,
      rarity:   rarity ? rarity.slice(0, 50) : null,
      imageUrl: typeof item.imageUrl === 'string' ? item.imageUrl.slice(0, 200) : null,
      itemId:   typeof item.itemId === 'string'   ? item.itemId.slice(0, 80)    : null,
      source:   typeof item.source === 'string'   ? item.source.slice(0, 120)   : null,
      shiny:    item.shiny === true               ? true                        : false,
    });
  }
  return out;
}

/**
 * Merge stored items with the persistent catalog so each card carries a real
 * name, tier/rarity and image — even for old snapshots. Also filters out any
 * stat labels that may have been stored before this filtering existed.
 */
function enrichItemsFromCatalog(items) {
  if (!Array.isArray(items)) return [];
  const out = [];
  for (const it of items) {
    if (!it || !it.name || catalogStore.isStatLabel(it.name)) continue;
    const meta = catalogStore.lookup(it.name);

    const name = (meta && meta.name) || it.name;
    let rarity = it.rarity || (meta && meta.tier) || null;
    if (rarity) rarity = catalogStore.normalizeTier(rarity);

    // Image priority: explicit item image → catalog image → Fish It DB resolver.
    let imageUrl = it.imageUrl || (meta && meta.imageUrl) || dbImageFor(name) || null;

    out.push({
      ...it,
      name,
      rarity,
      category: it.category || (meta && meta.category) || null,
      imageUrl,
    });
  }
  return out;
}

// ── GET /tracker – serve the dashboard page ───────────────────────
router.get('/tracker', (_req, res) => {
  res.render('fishit_tracker', {
    layout: false,
    title: '🎣 Fish It Live Inventory Tracker',
  });
});

// Allowed inventory sources. "replion" is the source of truth.
const ALLOWED_SOURCES = new Set(['replion', 'replion_missing', 'event', 'legacy', 'unknown']);

function sanitiseSource(raw) {
  if (typeof raw !== 'string') return 'unknown';
  const s = raw.trim().toLowerCase().slice(0, 30);
  return ALLOWED_SOURCES.has(s) ? s : 'unknown';
}

// ── POST /api/tracker/update-backpack ────────────────────────────
// Accepts both:
//   • inventory_snapshot  – the Replion source-of-truth inventory. The items
//     array REPLACES the previous snapshot (counts never accumulate).
//   • tracker_status      – a lightweight online/offline + source ping with no
//     items; keeps the last known inventory and only flips flags.
router.post(
  '/api/tracker/update-backpack',
  postLimiter,
  express.json({ limit: '128kb' }),
  (req, res) => {
    const body = req.body || {};
    const { username, userId, items, isOnline, type } = body;
    const source = sanitiseSource(body.source);

    const cleanUser = sanitiseUsername(username);
    if (!cleanUser) {
      return res.status(400).json({ error: 'Invalid or missing username.' });
    }

    const key = cleanUser.toLowerCase();
    const now = new Date().toISOString();
    const online = isOnline === true;
    const existing = liveTrackDB[key];
    const isStatusOnly = type === 'tracker_status';
    const cleanItems = isStatusOnly ? [] : sanitiseItems(items);

    // Status-only ping, OR an offline/empty update: keep the last known
    // inventory visible and only flip the online flag + source. Never clear
    // inventory just because the player went offline or sent no items.
    if ((isStatusOnly || (!online && !cleanItems.length)) && existing) {
      existing.isOnline = online;
      existing.source = source !== 'unknown' ? source : existing.source;
      if (online) existing.lastSeenAt = now;
      existing.updatedAt = now;
      return res.status(200).json({ status: 'success', note: 'status_only' });
    }

    // Inventory snapshot REPLACES the stored items (Replion source of truth).
    liveTrackDB[key] = {
      username:    cleanUser,
      userId:      Number.isFinite(Number(userId)) ? Number(userId) : 0,
      source,
      // Keep last known inventory if a live snapshot arrives empty.
      items:       cleanItems.length ? cleanItems : (existing ? existing.items : []),
      isOnline:    online,
      lastSeenAt:  online ? now : (existing ? existing.lastSeenAt : now),
      updatedAt:   now,
    };

    return res.status(200).json({ status: 'success' });
  },
);

// ── POST /api/tracker/update-catalog ─────────────────────────────
// Receives the recursive ReplicatedStorage fish_catalog_snapshot from the Lua
// tracker and merges it into the persistent catalog store (real name + tier +
// image for every scanned fish/rod/item).
router.post(
  '/api/tracker/update-catalog',
  postLimiter,
  express.json({ limit: '512kb' }),
  (req, res) => {
    const body = req.body || {};
    if (body.type !== 'fish_catalog_snapshot' || !body.catalog || typeof body.catalog !== 'object') {
      return res.status(400).json({ error: 'Invalid catalog snapshot.' });
    }
    const summary = catalogStore.ingestSnapshot(body);
    return res.status(200).json({ status: 'success', ...summary });
  },
);

// ── GET /api/fishit-tracker/catalog ──────────────────────────────
// Expose the stored catalog (debugging / verification).
router.get('/api/fishit-tracker/catalog', getLimiter, (_req, res) => {
  return res.status(200).json(catalogStore.getCatalog());
});

// ── GET /api/tracker/get-backpack/:username ───────────────────────
router.get(
  '/api/tracker/get-backpack/:username',
  getLimiter,
  (req, res) => {
    const cleanUser = sanitiseUsername(req.params.username);
    if (!cleanUser) {
      return res.status(400).json({ error: 'Invalid username.' });
    }

    const key   = cleanUser.toLowerCase();
    const data  = liveTrackDB[key];

    if (!data) {
      return res.status(404).json({ error: 'No tracking session active for this user.' });
    }

    // Merge each item with the persistent catalog (real name + tier + image)
    // and strip any stat labels stored before read-time filtering existed.
    const enriched = {
      ...data,
      items: enrichItemsFromCatalog(data.items),
    };

    return res.status(200).json(enriched);
  },
);

module.exports = router;
