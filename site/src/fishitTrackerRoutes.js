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
 * Normalise inventory from any shape the Lua tracker may send:
 *   - flat `items` array (primary — preferred)
 *   - grouped `owned.{fish,rods,items}` (fallback when flat is absent/empty)
 * This handles the case where `items` was accidentally omitted or empty.
 */
function normaliseInventoryItems(body) {
  const flat = Array.isArray(body.items) ? body.items : [];
  if (flat.length > 0) return sanitiseItems(flat);
  // Fallback: combine owned groups when flat is not present.
  const owned = (body.owned && typeof body.owned === 'object') ? body.owned : {};
  const combined = [
    ...(Array.isArray(owned.fish)  ? owned.fish  : []),
    ...(Array.isArray(owned.rods)  ? owned.rods  : []),
    ...(Array.isArray(owned.items) ? owned.items : []),
  ];
  return sanitiseItems(combined);
}

/**
 * Partition a flat sanitised-items array into { all, fish, rods, items } groups.
 * Mirrors the Lua buildOwnedGroups() output for frontend consumption.
 */
function buildInventoryGroups(items) {
  const fish = [], rods = [], inv = [];
  for (const it of items) {
    const cat = String(it.category || '').toLowerCase();
    if (cat === 'rod' || cat === 'bait') rods.push(it);
    else if (cat === 'items')            inv.push(it);
    else                                 fish.push(it);
  }
  return { all: items, fish, rods, items: inv };
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

// Validate and sanitise the numeric parse-stats block sent by the Lua tracker.
function sanitiseParseStats(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const num = (v) => (Number.isFinite(Number(v)) ? Math.floor(Number(v)) : 0);
  return {
    raw:          num(raw.raw),
    accepted:     num(raw.accepted),
    rejected:     num(raw.rejected),
    images:       num(raw.images),
    tiers:        num(raw.tiers),
    selectedPath: typeof raw.selectedPath === 'string' ? raw.selectedPath.slice(0, 80) : null,
  };
}

// Discovery phases reported by tracker_status. They drive the website's
// "script is running — locating Replion data..." messaging so the card never
// stays on "waiting to execute" once the script has started.
const ALLOWED_PHASES = new Set([
  'startup',
  'replion_client_found',
  'player_data_selected',
  'player_data_not_found',
  'inventory_path_missing',
  'inventory_empty',
  'inventory_parse_failed',
  'replion_missing',
  'live',
]);

function sanitisePhase(raw) {
  if (typeof raw !== 'string') return null;
  const s = raw.trim().toLowerCase().slice(0, 40);
  return ALLOWED_PHASES.has(s) ? s : null;
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
  express.json({ limit: '512kb' }),
  (req, res) => {
    const body = req.body || {};
    const { username, userId, isOnline, type } = body;
    const source = sanitiseSource(body.source);
    const phase  = sanitisePhase(body.phase);

    const cleanUser = sanitiseUsername(username);
    if (!cleanUser) {
      return res.status(400).json({ error: 'Invalid or missing username.' });
    }

    const key         = cleanUser.toLowerCase();
    const now         = new Date().toISOString();
    const online      = isOnline === true;
    const existing    = liveTrackDB[key];
    const isStatusOnly = type === 'tracker_status';
    const cleanUserId = Number.isFinite(Number(userId)) ? Number(userId) : 0;

    // ── tracker_status heartbeat ──────────────────────────────────
    // Proves the script is running. Creates the session when it does not yet
    // exist, and flips online/phase. NEVER clears inventory or parseStats.
    if (isStatusOnly) {
      const base = existing || { username: cleanUser, userId: cleanUserId, items: [], inventory: null };
      liveTrackDB[key] = {
        ...base,
        username:   cleanUser,
        userId:     cleanUserId || base.userId || 0,
        source:     source !== 'unknown' ? source : (base.source || source),
        items:      base.items     || [],   // NEVER clear on status-only
        inventory:  base.inventory || null, // NEVER clear on status-only
        isOnline:   online,
        phase:      phase || base.phase || 'startup',
        parseStats: sanitiseParseStats(body.parseStats) || base.parseStats || null,
        lastSeenAt: online ? now : (base.lastSeenAt || now),
        updatedAt:  now,
      };
      // Store userId→key alias so GET can resolve by userId if needed.
      if (cleanUserId) liveTrackDB['uid:' + cleanUserId] = key;
      // Server-side log.
      console.log(`[fishit-tracker] recv tracker_status user=${cleanUser} userId=${cleanUserId} phase=${liveTrackDB[key].phase} online=${online}`);
      return res.status(200).json({ status: 'success', note: 'status_only', phase: liveTrackDB[key].phase });
    }

    // ── Offline snapshot with no items ────────────────────────────
    // Keep the last known inventory; only flip the online flag.
    const rawFlatLen  = Array.isArray(body.items) ? body.items.length : 0;
    const rawOwnedLen = (body.owned && typeof body.owned === 'object')
      ? ['fish','rods','items'].reduce((s,k2)=> s + (Array.isArray(body.owned[k2]) ? body.owned[k2].length : 0), 0)
      : 0;

    if (!online && rawFlatLen === 0 && rawOwnedLen === 0 && existing) {
      existing.isOnline = online;
      existing.source   = source !== 'unknown' ? source : existing.source;
      existing.updatedAt = now;
      return res.status(200).json({ status: 'success', note: 'offline_keep' });
    }

    // ── Inventory snapshot ────────────────────────────────────────
    // Accepts both flat `items` array and grouped `owned.{fish,rods,items}`.
    const cleanItems = normaliseInventoryItems(body);
    const inventory  = buildInventoryGroups(cleanItems);
    const ps         = sanitiseParseStats(body.parseStats);

    // Server-side log — counts and first 3 samples (never full dump).
    const ownedFishLen  = Array.isArray(body.owned && body.owned.fish)  ? body.owned.fish.length  : 0;
    const ownedRodsLen  = Array.isArray(body.owned && body.owned.rods)  ? body.owned.rods.length  : 0;
    const ownedItemsLen = Array.isArray(body.owned && body.owned.items) ? body.owned.items.length : 0;
    console.log(
      `[fishit-tracker] recv ${type || 'snapshot'} user=${cleanUser} userId=${cleanUserId}` +
      ` flatItems=${rawFlatLen} ownedFish=${ownedFishLen} ownedRods=${ownedRodsLen} ownedItems=${ownedItemsLen}`
    );
    console.log(
      `[fishit-tracker] stored key=${key}` +
      ` items=${cleanItems.length} fish=${inventory.fish.length} rods=${inventory.rods.length}` +
      ` phase=${cleanItems.length ? 'live' : (phase || 'live')}` +
      (ps ? ` parseStats.raw=${ps.raw} accepted=${ps.accepted}` : '')
    );
    if (cleanItems.length > 0) {
      const samples = cleanItems.slice(0, 3).map(
        (it) => `${it.name}(x${it.amount},tier=${it.rarity || '-'},img=${!!it.imageUrl},cat=${it.category || '-'})`
      ).join(' | ');
      console.log(`[fishit-tracker] first 3 items: ${samples}`);
    }

    // Store under username key + userId alias.
    liveTrackDB[key] = {
      username:    cleanUser,
      userId:      cleanUserId,
      source,
      items:       cleanItems.length ? cleanItems : (existing ? existing.items     : []),
      inventory:   cleanItems.length ? inventory  : (existing ? existing.inventory : null),
      isOnline:    online,
      phase:       cleanItems.length ? 'live' : (phase || (existing && existing.phase) || 'live'),
      parseStats:  ps || (existing && existing.parseStats) || null,
      lastSeenAt:  online ? now : (existing ? existing.lastSeenAt : now),
      updatedAt:   now,
    };
    if (cleanUserId) liveTrackDB['uid:' + cleanUserId] = key;

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
// Also resolves userId aliases (uid:<number> keys created on POST).
router.get(
  '/api/tracker/get-backpack/:username',
  getLimiter,
  (req, res) => {
    const cleanUser = sanitiseUsername(req.params.username);
    if (!cleanUser) {
      return res.status(400).json({ error: 'Invalid username.' });
    }

    const key  = cleanUser.toLowerCase();
    let data    = liveTrackDB[key];

    // Fallback: if the param looks like a userId, resolve through the alias.
    if (!data && /^\d+$/.test(key)) {
      const uidTarget = liveTrackDB['uid:' + key];
      if (typeof uidTarget === 'string') data = liveTrackDB[uidTarget];
    }

    if (!data) {
      return res.status(404).json({ error: 'No tracking session active for this user.' });
    }

    // Enrich flat items array (catalog merge + stat-label strip).
    const enrichedFlat = enrichItemsFromCatalog(data.items);

    // Build enriched grouped inventory from the stored inventory object.
    // Fall back to partitioning the flat array when no grouped data exists.
    let enrichedInventory;
    if (data.inventory && Array.isArray(data.inventory.all)) {
      enrichedInventory = {
        all:   enrichItemsFromCatalog(data.inventory.all),
        fish:  enrichItemsFromCatalog(data.inventory.fish  || []),
        rods:  enrichItemsFromCatalog(data.inventory.rods  || []),
        items: enrichItemsFromCatalog(data.inventory.items || []),
      };
    } else {
      // Legacy sessions that pre-date the inventory grouping field.
      enrichedInventory = buildInventoryGroups(enrichedFlat);
    }

    const enriched = {
      ...data,
      items:     enrichedFlat,        // legacy flat array (backward compat)
      inventory: enrichedInventory,   // grouped: { all, fish, rods, items }
    };

    return res.status(200).json(enriched);
  },
);

// ── GET /api/fishit-tracker/debug/:username ───────────────────────
// Admin-safe diagnostic: returns only counts and first 5 items, never
// the full inventory dump. Helps distinguish backend-has-data vs
// frontend-render bugs without leaking sensitive inventory contents.
router.get('/api/fishit-tracker/debug/:username', getLimiter, (req, res) => {
  const cleanUser = sanitiseUsername(req.params.username);
  if (!cleanUser) return res.status(400).json({ error: 'Invalid username.' });

  const key   = cleanUser.toLowerCase();
  const data  = liveTrackDB[key];
  if (!data) return res.status(404).json({ found: false, key });

  const inv   = data.inventory || buildInventoryGroups(data.items || []);
  const first5 = (inv.all || data.items || []).slice(0, 5).map((i) => ({
    name:     i.name,
    amount:   i.amount,
    rarity:   i.rarity,
    hasImage: !!i.imageUrl,
    category: i.category,
    itemId:   i.itemId,
  }));

  return res.status(200).json({
    found:           true,
    sessionKey:      key,
    username:        data.username,
    userId:          data.userId,
    online:          data.isOnline,
    phase:           data.phase,
    parseStats:      data.parseStats || null,
    lastSeenAt:      data.lastSeenAt || null,
    lastInventoryAt: data.updatedAt  || null,
    counts: {
      flatItems:      (data.items || []).length,
      inventoryAll:   inv.all.length,
      inventoryFish:  inv.fish.length,
      inventoryRods:  inv.rods.length,
      inventoryItems: inv.items.length,
    },
    first5Items: first5,
  });
});

module.exports = router;
