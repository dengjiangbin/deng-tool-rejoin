'use strict';
/**
 * Fish It Backpack Tracker – API routes + dashboard page.
 *
 * Public routes (no authentication required):
 *   GET  /tracker                        – serve the live dashboard UI
 *   POST /api/fishit-tracker/update-backpack – canonical inventory POST (Lua v10J2+)
 *   POST /api/tracker/update-backpack          – backward-compatible alias
 *   GET  /api/fishit-tracker/get-backpack/:user – canonical live query
 *   GET  /api/tracker/get-backpack/:user        – backward-compatible alias
 *   GET  /api/fishit-tracker/debug/:user        – lightweight diagnostic (counts only)
 *
 * Security notes:
 *   - All data lives only in process memory (liveTrackDB). Nothing is
 *     persisted to disk or database.
 *   - Input is strictly validated and sanitised before storage.
 *   - Dedicated rate-limiters protect both endpoints independently so the
 *     global site limiter is not exhausted by the 2500 ms frontend polling.
 *   - Username keys are always lowercased; original casing is preserved
 *     inside the stored payload for display purposes only.
 *
 * Deployment note:
 *   This router is mounted in app.js BEFORE the global express.json()  
 *   middleware so that the route-level 512 KB JSON parsers take effect.  
 *   Moving it after the global 16 KB parser would cause catalog and        
 *   inventory POSTs to receive a 413 before the route is matched.          
 */

const express   = require('express');
const rateLimit = require('express-rate-limit');
const path      = require('path');
const { execFileSync } = require('child_process');

const catalogStore = require('./fishitCatalogStore');
const fishImageAssets = require('./fishitFishImageAssets');
const learnedFishCatalog = require('./fishitLearnedFishCatalog');
const catchDelta = require('./fishitCatalogCatchDelta');
const packageJson = require('../package.json');

function resolveServerCommit() {
  if (process.env.GIT_COMMIT) return String(process.env.GIT_COMMIT).trim();
  try {
    const root = path.join(__dirname, '..', '..');
    return execFileSync('git', ['rev-parse', '--short', 'HEAD'], {
      cwd: root,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
  } catch (_) {
    return packageJson.version || 'unknown';
  }
}

// Commit hash injected by CI/deploy, git HEAD, or fallback to package version.
const SERVER_COMMIT = resolveServerCommit();

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

const NO_STORE_HEADERS = {
  'Cache-Control': 'no-store, no-cache, must-revalidate, proxy-revalidate',
  Pragma: 'no-cache',
  Expires: '0',
};
const PUBLIC_RENDER_BUILD = 'BLOCKER10K1_FISH_ONLY_UI';
const PUBLIC_IMAGE_BUILD = 'BLOCKER10L_IMAGE';
const PUBLIC_CATCH_DELTA_BUILD = 'BLOCKER10M_CATCH_DELTA';

router.use((req, res, next) => {
  const p = req.path || '';
  if (p === '/tracker' || p === '/fishit-tracker'
      || p.startsWith('/api/fishit-tracker/')
      || p.startsWith('/api/tracker/')) {
    res.set(NO_STORE_HEADERS);
  }
  next();
});

// ── In-memory live-data store ─────────────────────────────────────
// Key: lowercased Roblox username  |  Value: last received payload + server ts
const liveTrackDB = {};

// ── Rate limiters ─────────────────────────────────────────────────
// POST: one live Roblox tracker per user — allow startup burst + periodic sync.
const postLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 30,
  skip: () => process.env.NODE_ENV === 'test',
  standardHeaders: true,
  legacyHeaders: false,
  handler: (req, res) => {
    res.set('Cache-Control', 'no-store');
    return res.status(429).json({ error: 'too_many_requests', message: 'Slow down.' });
  },
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
      resolved: item.resolved === true             ? true                        : (item.resolved === false ? false : null),
      catalogSource: typeof item.catalogSource === 'string' ? item.catalogSource.slice(0, 120) : null,
      catalogReason: typeof item.catalogReason === 'string' ? item.catalogReason.slice(0, 80)  : null,
      rawProof:      sanitiseRawProof(item.rawProof),
    });
  }
  return out;
}

/** BLOCKER10C: incoming placeholder must not downgrade a stored real name/category. */
function mergeItemsNoDowngrade(incoming, existing) {
  if (!Array.isArray(incoming) || incoming.length === 0) return incoming;
  if (!Array.isArray(existing) || existing.length === 0) return incoming;
  const byId = new Map();
  for (const it of existing) {
    if (it && it.itemId) byId.set(String(it.itemId), it);
  }
  return incoming.map((it) => {
    if (!it || !it.itemId) return it;
    const prev = byId.get(String(it.itemId));
    if (!prev || !prev.name) return it;
    const prevReal = !catalogStore.isPlaceholderItemName(prev.name, it.itemId);
    const incPlaceholder = catalogStore.isPlaceholderItemName(it.name, it.itemId);
    if (prevReal && incPlaceholder) {
      return {
        ...it,
        name: prev.name,
        category: catalogStore.isFishCategory(prev.category) ? 'fish' : (prev.category || it.category),
        resolved: prev.resolved !== false ? true : prev.resolved,
        catalogReason: prev.catalogReason || it.catalogReason,
        catalogSource: prev.catalogSource || it.catalogSource,
      };
    }
    return it;
  });
}

/** BLOCKER10H: enrich incoming placeholders from persistent catalog before store. */
function catalogMetaForItemId(itemId) {
  const main = catalogStore.lookupById(itemId);
  if (main && !catalogStore.isPlaceholderItemName(main.name, itemId)) return main;
  const learned = learnedFishCatalog.lookupById(itemId);
  if (learned && learned.publicEligible) {
    return {
      name: learned.name,
      category: learned.category || 'fish',
      source: learned.source,
      tier: null,
      confidence: String(learned.confidence),
    };
  }
  return main;
}

function ingestLearnedFishEntry(raw) {
  const r = learnedFishCatalog.ingestEntry(raw, (id) => catalogStore.lookupById(id));
  if (r.updated && r.entry && r.entry.publicEligible) {
    catalogStore.upsertByItemId({
      itemId: r.entry.itemId,
      name: r.entry.name,
      category: 'fish',
      source: r.entry.source,
      confidence: 'catch_delta',
    });
  }
  return r;
}

function mergeItemsNoDowngradeFromCatalog(incoming) {
  if (!Array.isArray(incoming) || incoming.length === 0) return incoming;
  return incoming.map((it) => {
    if (!it || !it.itemId) return it;
    const meta = catalogMetaForItemId(it.itemId);
    if (!meta || catalogStore.isPlaceholderItemName(meta.name, it.itemId)) return it;
    const incPlaceholder = catalogStore.isPlaceholderItemName(it.name, it.itemId);
    if (!incPlaceholder) return it;
    if (catalogStore.isFishCategory(meta.category)
      && String(it.category || '').toLowerCase() === 'items') {
      console.log(
        `[FishTrackerAPI] CATALOG_DOWNGRADE_BLOCKED itemId=${it.itemId}` +
        ` existing=${meta.name} attempted=${it.name}`
      );
    }
    return {
      ...it,
      name: meta.name,
      category: catalogStore.isFishCategory(meta.category) ? 'fish' : (meta.category || it.category),
      resolved: true,
      catalogReason: 'catalog_hit',
      catalogSource: meta.source || 'catalog_cache',
      catalogEnrichmentSource: meta.source || 'catalog_cache',
    };
  });
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
    const isPlaceholder = catalogStore.isPlaceholderItemName(it.name, it.itemId);
    const trackerHasRealName = !isPlaceholder && !!it.name;

    let meta = it.itemId ? catalogMetaForItemId(it.itemId) : null;
    if (!meta) meta = catalogStore.lookup(it.name);
    if (!meta && isPlaceholder) {
      const idFromName = String(it.name).replace(/^Item #/, '');
      meta = catalogStore.lookupById(idFromName);
    }

    const name = trackerHasRealName ? it.name : ((meta && meta.name) || it.name);
    let rarity = it.rarity || (meta && meta.tier) || null;
    if (rarity) rarity = catalogStore.normalizeTier(rarity);

    let imageUrl = it.imageUrl || (meta && meta.imageUrl) || dbImageFor(name) || null;

    const resolved = trackerHasRealName
      ? true
      : (it.resolved != null ? it.resolved : !!meta);
    const catalogReason = it.catalogReason || (meta && !trackerHasRealName ? 'catalog_hit' : null);
    const catalogSource = it.catalogSource || (meta && meta.source) || null;
    const catalogEnrichmentSource = (!trackerHasRealName && meta)
      ? (meta.source || 'catalog_cache')
      : (it.catalogEnrichmentSource || null);

    let category = it.category || null;
    if (trackerHasRealName && catalogStore.isFishCategory(it.category)) {
      category = 'fish';
    } else if (meta && meta.category) {
      if (isPlaceholder || catalogStore.isFishCategory(meta.category)) {
        category = meta.category;
      } else if (!category) {
        category = meta.category;
      }
    }

    if (isPlaceholder && meta && catalogStore.isFishCategory(meta.category)
      && String(it.category || '').toLowerCase() === 'items') {
      console.log(
        `[FishTrackerAPI] CATALOG_DOWNGRADE_BLOCKED itemId=${it.itemId}` +
        ` existing=${meta.name} attempted=${it.name}`
      );
    }

    out.push({
      ...it,
      name,
      rarity,
      category,
      imageUrl,
      resolved,
      catalogReason,
      catalogSource,
      catalogEnrichmentSource,
    });
  }
  return out;
}

function debugItemSlice(items, limit = 5) {
  if (!Array.isArray(items)) return [];
  return items.slice(0, limit).map((i) => ({
    name: i.name,
    amount: i.amount,
    category: i.category,
    itemId: i.itemId || null,
    resolved: i.resolved != null ? i.resolved : null,
  }));
}

function inventoryCountsFromGroups(inv) {
  const g = inv || { all: [], fish: [], rods: [], items: [] };
  return {
    all:       (g.all   || []).length,
    fish:      (g.fish  || []).length,
    rods:      (g.rods  || []).length,
    itemsOnly: (g.items || []).length,
    items:     (g.all   || []).length,
  };
}

function catalogMapForItems(items) {
  const out = {};
  if (!Array.isArray(items)) return out;
  for (const i of items) {
    if (!i || !i.itemId) continue;
    const meta = catalogStore.catalogMetaForItemId(i.itemId);
    if (!meta) continue;
    out[String(i.itemId)] = {
      name: meta.name,
      category: meta.category || null,
      source: meta.source || null,
    };
  }
  return out;
}

const RAW_INSPECTOR_MAX = 40;
const RAW_PROOF_MAX_STR = 80;

function sanitiseRawProof(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const trimStr = (v, max) => (typeof v === 'string' ? v.trim().slice(0, max) : null);
  const nameFields = {};
  if (raw.rawNameFields && typeof raw.rawNameFields === 'object') {
    for (const [k, v] of Object.entries(raw.rawNameFields).slice(0, 8)) {
      if (typeof v === 'string' && v.length) nameFields[k.slice(0, 32)] = v.slice(0, RAW_PROOF_MAX_STR);
    }
  }
  const idFields = {};
  if (raw.extractedIdFields && typeof raw.extractedIdFields === 'object') {
    for (const [k, v] of Object.entries(raw.extractedIdFields).slice(0, 12)) {
      if (v != null && typeof v !== 'object') idFields[k.slice(0, 32)] = String(v).slice(0, RAW_PROOF_MAX_STR);
    }
  }
  let objPreview = null;
  if (raw.rawObjectPreview && typeof raw.rawObjectPreview === 'object') {
    objPreview = {};
    for (const [k, v] of Object.entries(raw.rawObjectPreview).slice(0, 12)) {
      objPreview[k.slice(0, 40)] = typeof v === 'string' ? v.slice(0, RAW_PROOF_MAX_STR) : String(v).slice(0, 40);
    }
  }
  return {
    rawKey: trimStr(raw.rawKey, 80),
    sourcePath: trimStr(raw.sourcePath, 120),
    rawType: trimStr(raw.rawType, 24),
    rawValuePreview: trimStr(raw.rawValuePreview, RAW_PROOF_MAX_STR),
    rawObjectPreview: objPreview,
    rawNameFields: Object.keys(nameFields).length ? nameFields : null,
    extractedIdFields: Object.keys(idFields).length ? idFields : null,
  };
}

function sumItemAmounts(items) {
  if (!Array.isArray(items)) return 0;
  return items.reduce((s, it) => s + (Number(it.amount) > 0 ? Math.floor(Number(it.amount)) : 1), 0);
}

function isPublicFishItem(item) {
  if (!item) return false;
  const cat = String(item.category || '').toLowerCase();
  if (cat === 'rod' || cat === 'bait') return false;
  if (item.itemId) {
    const learned = learnedFishCatalog.lookupById(item.itemId);
    if (learned && learned.publicEligible && learned.category === 'fish') {
      return true;
    }
    const meta = catalogStore.lookupById(item.itemId);
    if (meta && !catalogStore.isFishCategory(meta.category)) return false;
  }
  if (cat === 'items') return false;
  if (catalogStore.isPlaceholderItemName(item.name, item.itemId)) return false;
  if (catalogStore.isFishCategory(cat)) return true;
  return cat !== 'rod' && cat !== 'bait' && cat !== 'items';
}

/** Fish-only view for public website/API (storage keeps full inventory). */
function buildPublicFishFields(enrichedFlat) {
  const fishItems = fishImageAssets.attachFishImagesToItems(
    (enrichedFlat || []).filter(isPublicFishItem),
  );
  const hidden = (enrichedFlat || []).filter((it) => !isPublicFishItem(it));
  const fishCounts = {
    fishTypes: fishItems.length,
    fishInstances: sumItemAmounts(fishItems),
    hiddenNonFishTypes: hidden.length,
    hiddenNonFishInstances: sumItemAmounts(hidden),
  };
  return {
    fishItems,
    publicItems: fishItems,
    fishInventory: buildInventoryGroups(fishItems),
    fishCounts,
    publicCounts: fishCounts,
  };
}

/** Legacy `counts` shape for public UI — fish metrics only (never mixed type totals). */
function buildPublicLegacyCounts(fishCounts) {
  const types = fishCounts.fishTypes;
  const instances = fishCounts.fishInstances;
  return {
    fish: types,
    fishInstances: instances,
    all: instances,
    items: types,
    itemsOnly: 0,
    rods: 0,
  };
}

function rawHadNameFromItem(rawItem, proof) {
  const itemId = rawItem?.itemId;
  if (rawItem?.name && !catalogStore.isPlaceholderItemName(rawItem.name, itemId) && /[a-zA-Z]/.test(rawItem.name)) {
    return true;
  }
  const fields = proof?.rawNameFields;
  if (!fields || typeof fields !== 'object') return false;
  return Object.values(fields).some((v) => typeof v === 'string' && v.trim().length > 0);
}

function deriveResolution(rawItem, enrichedItem) {
  const itemId = String(enrichedItem?.itemId || rawItem?.itemId || '');
  const rawName = rawItem?.name || '';
  const finalName = enrichedItem?.name || rawName;
  const finalCategory = enrichedItem?.category || rawItem?.category || null;
  const proof = rawItem?.rawProof || null;
  const meta = itemId ? catalogStore.lookupById(itemId) : null;
  const rawHadName = rawHadNameFromItem(rawItem, proof);
  const catalogMatched = !!meta && !catalogStore.isPlaceholderItemName(meta.name, itemId);
  const catalogSource = catalogMatched ? (meta.source || enrichedItem?.catalogSource || null) : null;
  const isPlaceholderFinal = catalogStore.isPlaceholderItemName(finalName, itemId);

  let resolutionReason = 'raw_numeric_only_no_catalog_match';
  if (rawHadName && isPlaceholderFinal) {
    resolutionReason = 'raw_name_present_but_not_used_parser_bug';
  } else if (catalogMatched && catalogSource === 'seed_confirmed') {
    resolutionReason = 'raw_numeric_only_catalog_seed_confirmed';
  } else if (catalogMatched) {
    resolutionReason = 'raw_numeric_only_catalog_match';
  }

  return {
    rawName,
    finalName,
    category: finalCategory,
    rawHadName,
    catalogMatched,
    catalogSource,
    resolutionReason,
  };
}

function buildRawInspector(rawItems, enrichedItems, selectedPath) {
  const enrichedById = new Map();
  for (const it of enrichedItems || []) {
    if (it?.itemId) enrichedById.set(String(it.itemId), it);
  }
  const entries = [];
  let unresolvedInspectedCount = 0;
  for (const raw of (rawItems || []).slice(0, 300)) {
    if (!raw?.itemId) continue;
    const enriched = enrichedById.get(String(raw.itemId)) || raw;
    const resolution = deriveResolution(raw, enriched);
    const meta = catalogStore.lookupById(raw.itemId);
    const isUnresolved = catalogStore.isPlaceholderItemName(enriched.name, raw.itemId)
      && !catalogStore.isFishCategory(enriched.category);
    if (isUnresolved) unresolvedInspectedCount += 1;
    entries.push({
      itemId: String(raw.itemId),
      amount: Number(raw.amount) > 0 ? Math.floor(Number(raw.amount)) : 1,
      rawKey: raw.rawProof?.rawKey || null,
      sourcePath: raw.rawProof?.sourcePath || raw.source || selectedPath || null,
      rawType: raw.rawProof?.rawType || null,
      rawValuePreview: raw.rawProof?.rawValuePreview || null,
      rawObjectPreview: raw.rawProof?.rawObjectPreview || null,
      rawNameFields: raw.rawProof?.rawNameFields || null,
      extractedIdFields: raw.rawProof?.extractedIdFields || null,
      resolution: {
        rawHadName: resolution.rawHadName,
        catalogMatched: resolution.catalogMatched,
        catalogName: meta?.name || null,
        finalName: resolution.finalName,
        finalCategory: resolution.category,
        reason: resolution.resolutionReason,
      },
    });
    if (entries.length >= RAW_INSPECTOR_MAX) break;
  }
  return {
    selectedPath: selectedPath || null,
    inspectedCount: entries.length,
    unresolvedInspectedCount,
    entries,
  };
}

function buildUnresolvedRawProof(rawItems, enrichedItems, selectedPath) {
  const inspector = buildRawInspector(rawItems, enrichedItems, selectedPath);
  return inspector.entries
    .filter((e) => e.resolution && !e.resolution.catalogMatched
      && catalogStore.isPlaceholderItemName(e.resolution.finalName, e.itemId))
    .slice(0, 25)
    .map((e) => ({
      itemId: e.itemId,
      amount: e.amount,
      sourcePath: e.sourcePath,
      rawHadName: e.resolution.rawHadName,
      rawNameFields: e.rawNameFields,
      rawType: e.rawType,
      rawValuePreview: e.rawValuePreview,
      reason: e.resolution.reason,
    }));
}

function mapDebugItemWithResolution(raw, enriched) {
  const res = deriveResolution(raw, enriched);
  return {
    name: enriched.name,
    amount: enriched.amount,
    category: enriched.category,
    tier: enriched.rarity || null,
    imageUrlPresent: !!enriched.imageUrl,
    itemId: enriched.itemId || null,
    resolved: enriched.resolved != null ? enriched.resolved : null,
    catalogSource: enriched.catalogSource || null,
    catalogReason: enriched.catalogReason || null,
    catalogEnrichmentSource: enriched.catalogEnrichmentSource || null,
    rawName: res.rawName,
    finalName: res.finalName,
    rawHadName: res.rawHadName,
    catalogMatched: res.catalogMatched,
    resolutionReason: res.resolutionReason,
  };
}

// ── GET /tracker – serve the dashboard page ───────────────────────
function renderTrackerPage(_req, res) {
  res.render('fishit_tracker', {
    layout: false,
    title: '🎣 Fish It Live Inventory Tracker',
    renderBuild: PUBLIC_RENDER_BUILD,
    imageBuild: PUBLIC_IMAGE_BUILD,
  });
}

router.get('/tracker', renderTrackerPage);
router.get('/fishit-tracker', renderTrackerPage);

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
  const out = {
    raw:               num(raw.raw),
    accepted:          num(raw.accepted),
    acceptedInstances: num(raw.acceptedInstances),
    rejected:          num(raw.rejected),
    images:            num(raw.images),
    tiers:             num(raw.tiers),
    selectedPath:      typeof raw.selectedPath === 'string' ? raw.selectedPath.slice(0, 80) : null,
  };
  if (typeof raw.error === 'string' && raw.error.length > 0) {
    out.error = raw.error.slice(0, 500);
  }
  if (Array.isArray(raw.firstRejected)) {
    out.firstRejected = raw.firstRejected.slice(0, 10).map((r) => ({
      rawKey:     typeof r.rawKey === 'string'     ? r.rawKey.slice(0, 80)     : null,
      sourcePath: typeof r.sourcePath === 'string' ? r.sourcePath.slice(0, 120) : null,
      reason:     typeof r.reason === 'string'     ? r.reason.slice(0, 80)     : null,
    }));
  }
  return out;
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
  'targeted_diagnostics',
]);

function sanitiseTrackerBuild(raw) {
  if (typeof raw !== 'string') return null;
  const s = raw.trim().slice(0, 80);
  return s.length > 0 ? s : null;
}

function effectivePhase(requestedPhase, parseStats, hasItems) {
  if (hasItems) return 'live';
  if (parseStats && parseStats.acceptedInstances > 0) return 'live';
  if (parseStats && parseStats.accepted > 0) return 'live';
  return sanitisePhase(requestedPhase) || 'startup';
}

function sanitisePhase(raw) {
  if (typeof raw !== 'string') return null;
  const s = raw.trim().toLowerCase().slice(0, 40);
  return ALLOWED_PHASES.has(s) ? s : null;
}

function ingestDiscoveredCatalog(entries) {
  if (!Array.isArray(entries) || entries.length === 0) return [];
  const results = [];
  for (const raw of entries.slice(0, 50)) {
    if (!raw || raw.itemId == null) continue;
    results.push(catalogStore.upsertByItemId(raw));
  }
  return results;
}

function ingestLearnedFishCatalogFromBody(body) {
  if (!Array.isArray(body.learnedFishCatalog) || body.learnedFishCatalog.length === 0) return [];
  const results = [];
  for (const raw of body.learnedFishCatalog.slice(0, 30)) {
    results.push(ingestLearnedFishEntry(raw));
  }
  return results;
}

function runCatchDeltaOnUpload(body, rawItems, existing) {
  const pending = body.pendingCatchName || body.pendingCatch;
  const prev = body.previousItemCounts || (existing && existing.lastItemCounts) || null;
  if (!pending && !prev) return null;
  return catchDelta.processCatchDelta({
    pendingCatch: pending,
    previousItemCounts: prev,
    currentItems: rawItems,
    ingestLearned: ingestLearnedFishEntry,
    mainCatalogLookup: (id) => catalogStore.lookupById(id),
  });
}

// ── POST update-backpack (canonical + legacy alias) ───────────────
// Accepts both:
//   • inventory_snapshot  – the Replion source-of-truth inventory. The items
//     array REPLACES the previous snapshot (counts never accumulate).
//   • tracker_status      – a lightweight online/offline + source ping with no
//     items; keeps the last known inventory and only flips flags.
function handleUpdateBackpack(req, res) {
    const body = req.body || {};
    const { username, userId, isOnline, type } = body;
    const source = sanitiseSource(body.source);
    const phase  = sanitisePhase(body.phase);
    const payloadType = type || 'inventory_snapshot';

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
      const ps = sanitiseParseStats(body.parseStats) || base.parseStats || null;
      const phaseOut = effectivePhase(phase || base.phase, ps, (base.items || []).length > 0);
      liveTrackDB[key] = {
        ...base,
        username:        cleanUser,
        userId:          cleanUserId || base.userId || 0,
        source:          source !== 'unknown' ? source : (base.source || source),
        items:           base.items     || [],
        inventory:       base.inventory || null,
        isOnline:        online,
        phase:           phaseOut,
        parseStats:      ps,
        trackerBuild:    sanitiseTrackerBuild(body.trackerBuild) || base.trackerBuild || null,
        lastPayloadType: 'tracker_status',
        lastSeenAt:      now,
        lastInventoryAt: base.lastInventoryAt || base.updatedAt || null,
        updatedAt:       now,
      };
      if (Array.isArray(body.unresolvedDiagnostics) && body.unresolvedDiagnostics.length) {
        liveTrackDB[key].unresolvedDiagnostics = body.unresolvedDiagnostics.slice(0, 30);
      }
      if (Array.isArray(body.discoveredCatalog) && body.discoveredCatalog.length) {
        liveTrackDB[key].discoveredCatalogIngest = ingestDiscoveredCatalog(body.discoveredCatalog);
      }
      // Store userId→key alias so GET can resolve by userId if needed.
      if (cleanUserId) liveTrackDB['uid:' + cleanUserId] = key;
      // Server-side log.
      console.log(
        `[fishit-tracker] POST hit route=${req.path} user=${cleanUser} sessionKey=${key}` +
        ` userId=${cleanUserId} payloadType=tracker_status accepted=0 ok=true` +
        ` lastSeenAt=${now} lastInventoryAt=${liveTrackDB[key].lastInventoryAt || 'n/a'} online=${online}`
      );
      return res.status(200).json({
        ok: true,
        status: 'success',
        note: 'status_only',
        phase: liveTrackDB[key].phase,
        lastSeenAt: now,
        lastInventoryAt: liveTrackDB[key].lastInventoryAt || null,
        online: isSessionLive(liveTrackDB[key]),
      });
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
    const rawItems = normaliseInventoryItems(body);
    const learnedIngest = ingestLearnedFishCatalogFromBody(body);
    const nameCatalogDiscovery = runCatchDeltaOnUpload(body, rawItems, existing);
    catalogStore.learnFromTrackerItems(rawItems);
    let cleanItems = mergeItemsNoDowngradeFromCatalog(rawItems);
    if (existing && existing.items && cleanItems.length) {
      cleanItems = mergeItemsNoDowngrade(cleanItems, existing.items);
    }
    const inventory  = buildInventoryGroups(cleanItems);
    const ps         = sanitiseParseStats(body.parseStats);

    // Server-side log — counts and first 3 samples (never full dump).
    const ownedFishLen  = Array.isArray(body.owned && body.owned.fish)  ? body.owned.fish.length  : 0;
    const ownedRodsLen  = Array.isArray(body.owned && body.owned.rods)  ? body.owned.rods.length  : 0;
    const ownedItemsLen = Array.isArray(body.owned && body.owned.items) ? body.owned.items.length : 0;
    console.log(
      `[fishit-tracker] POST hit route=${req.path} user=${cleanUser} sessionKey=${key}` +
      ` userId=${cleanUserId} payloadType=${payloadType} flatItems=${rawFlatLen}` +
      ` ownedFish=${ownedFishLen} ownedRods=${ownedRodsLen} ownedItems=${ownedItemsLen}` +
      (ps ? ` parseStats.accepted=${ps.accepted}` : '')
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

    const acceptedCount = cleanItems.length || (ps && ps.accepted) || 0;

    // Store under username key + userId alias.
    liveTrackDB[key] = {
      username:        cleanUser,
      userId:          cleanUserId,
      source,
      rawItems:        rawItems.length ? rawItems : (existing ? existing.rawItems : []),
      items:           cleanItems.length ? cleanItems : (existing ? existing.items     : []),
      inventory:       cleanItems.length ? inventory  : (existing ? existing.inventory : null),
      isOnline:        online,
      phase:           effectivePhase(phase, ps, cleanItems.length > 0),
      parseStats:      ps || (existing && existing.parseStats) || null,
      trackerBuild:    sanitiseTrackerBuild(body.trackerBuild) || (existing && existing.trackerBuild) || null,
      lastPayloadType: cleanItems.length ? 'inventory_snapshot' : (type || 'inventory_snapshot'),
      lastSeenAt:      now,
      lastInventoryAt: now,
      updatedAt:       now,
    };
    if (Array.isArray(body.unresolvedDiagnostics) && body.unresolvedDiagnostics.length) {
      liveTrackDB[key].unresolvedDiagnostics = body.unresolvedDiagnostics.slice(0, 20);
    }
    if (Array.isArray(body.discoveredCatalog) && body.discoveredCatalog.length) {
      liveTrackDB[key].discoveredCatalogIngest = ingestDiscoveredCatalog(body.discoveredCatalog);
    }
    if (learnedIngest.length) {
      liveTrackDB[key].learnedFishCatalogIngest = learnedIngest;
    }
    if (nameCatalogDiscovery) {
      liveTrackDB[key].nameCatalogDiscovery = nameCatalogDiscovery;
    }
    liveTrackDB[key].lastItemCounts = catchDelta.buildItemCountsFromItems(rawItems);
    if (cleanUserId) liveTrackDB['uid:' + cleanUserId] = key;

    console.log(
      `[fishit-tracker] POST ok=true user=${cleanUser} sessionKey=${key}` +
      ` accepted=${acceptedCount} lastSeenAt=${now} lastInventoryAt=${now} online=true` +
      (nameCatalogDiscovery && nameCatalogDiscovery.learnedMappings.length
        ? ` catchDeltaLearned=${nameCatalogDiscovery.learnedMappings.length}` : '')
    );

    return res.status(200).json({
      ok: true,
      status: 'success',
      accepted: acceptedCount,
      lastInventoryAt: now,
      lastSeenAt: now,
      online: true,
      nameCatalogDiscovery: nameCatalogDiscovery || undefined,
    });
}

const updateBackpackMiddleware = [
  postLimiter,
  express.json({ limit: '512kb' }),
  handleUpdateBackpack,
];

router.post('/api/fishit-tracker/update-backpack', updateBackpackMiddleware);
router.post('/api/tracker/update-backpack', updateBackpackMiddleware);

/** Session is live when a heartbeat arrived within the threshold (inventory or status). */
function isSessionLive(data, maxAgeMs = 45000) {
  if (!data) return false;
  const ts = data.lastSeenAt || data.lastInventoryAt || data.updatedAt;
  if (!ts) return data.isOnline === true;
  const age = Date.now() - new Date(ts).getTime();
  return data.isOnline !== false && Number.isFinite(age) && age >= 0 && age < maxAgeMs;
}

// ── POST /api/tracker/update-catalog ─────────────────────────────
// Accepts both:
//   • catalog_summary  (preferred, small payload) — Lua v8+ sends only counts
//     and up to 3 sample entries; imageUrls are NOT included, so the payload
//     stays well under 5 KB regardless of catalog size.
//   • fish_catalog_snapshot  (legacy, full catalog) — accepted for backward
//     compatibility but the Lua client should never send this any more since
//     it caused HTTP 413 (body exceeded the global 16 KB JSON limit in older
//     deployments).
router.post(
  '/api/tracker/update-catalog',
  postLimiter,
  express.json({ limit: '512kb' }),
  (req, res) => {
    const body = req.body || {};
    const { type } = body;

    // ── catalog_summary (preferred small payload) ─────────────────
    if (type === 'catalog_summary') {
      const stats = body.catalogStats || {};
      console.log(
        `[fishit-tracker] recv catalog_summary user=${body.playerName || '?'}` +
        ` fish=${stats.fish || 0} rods=${stats.rods || 0} items=${stats.items || 0}` +
        ` images=${stats.images || 0} metadataByIdKeys=${stats.metadataByIdKeys || 0}`
      );
      return res.status(200).json({ status: 'success', type: 'catalog_summary', stats });
    }

    // ── fish_catalog_snapshot (legacy full catalog) ───────────────
    if (type !== 'fish_catalog_snapshot' || !body.catalog || typeof body.catalog !== 'object') {
      return res.status(400).json({ error: 'Invalid catalog payload. Expected type=catalog_summary or fish_catalog_snapshot.' });
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

// ── GET get-backpack (canonical + legacy alias) ───────────────────
// Also resolves userId aliases (uid:<number> keys created on POST).
function handleGetBackpack(req, res) {
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

  // Enrich from raw tracker payload when available (BLOCKER10I).
  const sourceItems = (data.rawItems && data.rawItems.length) ? data.rawItems : data.items;
  const enrichedFlat = enrichItemsFromCatalog(sourceItems);
  const enrichedInventory = buildInventoryGroups(enrichedFlat);
  const rawInventory = buildInventoryGroups(sourceItems);
  const countsRaw = inventoryCountsFromGroups(rawInventory);
  const countsEnriched = inventoryCountsFromGroups(enrichedInventory);
  const publicFish = buildPublicFishFields(enrichedFlat);
  const imageResolutionProof = fishImageAssets.buildImageResolutionProof(publicFish.fishItems);

  const enriched = {
    ...data,
    renderBuild:     PUBLIC_RENDER_BUILD,
    imageBuild:      PUBLIC_IMAGE_BUILD,
    items:           publicFish.fishItems,
    inventory:       publicFish.fishInventory,
    counts:          buildPublicLegacyCounts(publicFish.fishCounts),
    fishItems:       publicFish.fishItems,
    publicItems:     publicFish.publicItems,
    fishInventory:   publicFish.fishInventory,
    fishCounts:      publicFish.fishCounts,
    publicCounts:    publicFish.publicCounts,
    imageResolutionProof,
    allItems:        enrichedFlat,
    fullItems:       enrichedFlat,
    enrichedItems:   enrichedFlat,
    debugItems:      enrichedFlat.slice(0, 50),
    rawItems:        data.rawItems || sourceItems,
    internalInventory: enrichedInventory,
    countsRaw,
    countsEnriched,
    countsInternal:  countsEnriched,
    lastInventoryAt: data.lastInventoryAt || data.updatedAt || null,
    isOnline:        isSessionLive(data),
  };

  return res.status(200).json(enriched);
}

router.get('/api/fishit-tracker/get-backpack/:username', getLimiter, handleGetBackpack);
router.get('/api/tracker/get-backpack/:username', getLimiter, handleGetBackpack);

// ── GET /api/fishit-tracker/debug/:username ───────────────────────
// Admin-safe diagnostic: returns only counts and first 5 items, never
// the full inventory dump. Helps distinguish backend-has-data vs
// frontend-render bugs without leaking sensitive inventory contents.
// Also returns serverCommit so the caller can verify which build is live.
router.get('/api/fishit-tracker/debug/:username', getLimiter, (req, res) => {
  const cleanUser = sanitiseUsername(req.params.username);
  if (!cleanUser) return res.status(400).json({ ok: false, error: 'Invalid username.' });

  const key   = cleanUser.toLowerCase();
  const data  = liveTrackDB[key];

  if (!data) {
    // Enumerate known keys (usernames only, strip uid: aliases and limit count).
    const knownKeys = Object.keys(liveTrackDB)
      .filter((k) => !k.startsWith('uid:'))
      .slice(0, 100);
    return res.status(404).json({ ok: false, error: 'not_found', key, knownKeys, serverCommit: SERVER_COMMIT });
  }

  const rawItemsArr = data.rawItems || data.items || [];
  const rawInv = buildInventoryGroups(rawItemsArr);
  const enrichedAll = enrichItemsFromCatalog(rawItemsArr);
  const enrichedInv = buildInventoryGroups(enrichedAll);
  const rawSlice = debugItemSlice(rawItemsArr);
  const enrichedSlice = debugItemSlice(enrichedAll);
  const countsRaw = inventoryCountsFromGroups(rawInv);
  const countsEnriched = inventoryCountsFromGroups(enrichedInv);
  const catalogForItems = catalogMapForItems(enrichedAll);

  const enrichedById = new Map();
  for (const it of enrichedAll) {
    if (it?.itemId) enrichedById.set(String(it.itemId), it);
  }
  const mapDebugItem = (i, useResolution) => {
    const raw = rawItemsArr.find((r) => r.itemId && String(r.itemId) === String(i.itemId)) || i;
    if (useResolution) return mapDebugItemWithResolution(raw, i);
    return {
      name: i.name,
      amount: i.amount,
      category: i.category,
      tier: i.rarity || null,
      imageUrlPresent: !!i.imageUrl,
      itemId: i.itemId || null,
      resolved: i.resolved != null ? i.resolved : null,
      catalogSource: i.catalogSource || null,
      catalogReason: i.catalogReason || null,
      catalogEnrichmentSource: i.catalogEnrichmentSource || null,
    };
  };

  const firstItems = enrichedAll.slice(0, 5).map((i) => mapDebugItem(i, true));
  const rawFirstItems = rawItemsArr.slice(0, 5).map((i) => mapDebugItem(i, false));
  const selectedPath = data.parseStats?.selectedPath || null;
  const rawInspector = buildRawInspector(rawItemsArr, enrichedAll, selectedPath);
  const unresolvedRawProof = buildUnresolvedRawProof(rawItemsArr, enrichedAll, selectedPath);
  const publicFishDbg = buildPublicFishFields(enrichedAll);
  const imageResolutionProof = fishImageAssets.buildImageResolutionProof(publicFishDbg.fishItems);

  const diags = Array.isArray(data.unresolvedDiagnostics) ? data.unresolvedDiagnostics : [];
  const unresolvedIds = diags.filter((d) => d && !d.found).map((d) => d.id);
  const resolvedFromDiag = diags.filter((d) => d && d.found).map((d) => ({
    id: d.id,
    name: (d.candidateKeys && d.candidateKeys[0]) || null,
    path: d.candidatePath || null,
  }));

  return res.status(200).json({
    ok:              true,
    serverCommit:    SERVER_COMMIT,
    trackerBuild:    data.trackerBuild || null,
    sessionKey:      key,
    username:        data.username,
    userId:          data.userId,
    online:          isSessionLive(data),
    phase:           data.phase,
    parseStats:      data.parseStats || null,
    acceptedInstances: data.parseStats ? data.parseStats.acceptedInstances : null,
    uniqueAccepted:  data.parseStats ? data.parseStats.accepted : null,
    lastSeenAt:      data.lastSeenAt || null,
    lastInventoryAt: data.lastInventoryAt || data.updatedAt || null,
    lastPayloadType: data.lastPayloadType || null,
    counts: countsEnriched,
    countsRaw,
    countsEnriched,
    firstItems,
    rawFirstItems,
    rawItems: rawSlice,
    enrichedItems: enrichedSlice,
    catalogForItems,
    rawInspector,
    unresolvedRawProof,
    imageResolutionProof,
    fishImageAssetCatalogCount: fishImageAssets.getCatalogEntryCount(),
    unresolvedDiagnostics: diags.length ? diags : null,
    unresolvedIds,
    stillUnresolvedIds: unresolvedIds,
    resolvedFromDiagnostics: resolvedFromDiag.length ? resolvedFromDiag : null,
    discoveredCatalogIngest: data.discoveredCatalogIngest || null,
    nameCatalogDiscovery: catchDelta.buildNameCatalogDiscoveryForDebug(
      data.nameCatalogDiscovery,
      learnedFishCatalog,
    ),
    catchDeltaBuild: PUBLIC_CATCH_DELTA_BUILD,
    learnedFishCatalogCount: learnedFishCatalog.getAllMappings().length,
  });
});

// Optional manual catalog probe request (BLOCKER10M-G) — off by default on client.
router.post('/api/fishit-tracker/request-catalog-scan/:username', postLimiter, (req, res) => {
  const cleanUser = sanitiseUsername(req.params.username);
  if (!cleanUser) return res.status(400).json({ ok: false, error: 'Invalid username.' });
  const key = cleanUser.toLowerCase();
  if (!liveTrackDB[key]) return res.status(404).json({ ok: false, error: 'not_found' });
  liveTrackDB[key].catalogScanRequested = true;
  liveTrackDB[key].catalogScanRequestedAt = new Date().toISOString();
  return res.status(200).json({
    ok: true,
    note: 'catalog_scan_requested',
    enableOnClient: 'LiveSafe.enableManualCatalogProbe',
    defaultEnabled: false,
  });
});

module.exports = router;
module.exports.mergeItemsNoDowngradeFromCatalog = mergeItemsNoDowngradeFromCatalog;
module.exports.enrichItemsFromCatalog = enrichItemsFromCatalog;
module.exports.inventoryCountsFromGroups = inventoryCountsFromGroups;
module.exports.catalogMapForItems = catalogMapForItems;
module.exports.debugItemSlice = debugItemSlice;
module.exports.resolveServerCommit = resolveServerCommit;
module.exports.isSessionLive = isSessionLive;
module.exports.buildPublicFishFields = buildPublicFishFields;
module.exports.buildPublicLegacyCounts = buildPublicLegacyCounts;
module.exports.PUBLIC_RENDER_BUILD = PUBLIC_RENDER_BUILD;
module.exports.buildRawInspector = buildRawInspector;
module.exports.deriveResolution = deriveResolution;
module.exports.sanitiseRawProof = sanitiseRawProof;
module.exports.isPublicFishItem = isPublicFishItem;
module.exports.PUBLIC_IMAGE_BUILD = PUBLIC_IMAGE_BUILD;
module.exports.PUBLIC_CATCH_DELTA_BUILD = PUBLIC_CATCH_DELTA_BUILD;
module.exports.ingestLearnedFishEntry = ingestLearnedFishEntry;
module.exports.runCatchDeltaOnUpload = runCatchDeltaOnUpload;
module.exports.catalogMetaForItemId = catalogMetaForItemId;
