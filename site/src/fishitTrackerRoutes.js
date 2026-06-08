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
const fs        = require('fs');
const { execFileSync } = require('child_process');

const catalogStore = require('./fishitCatalogStore');
const fishImageAssets = require('./fishitFishImageAssets');
const learnedFishCatalog = require('./fishitLearnedFishCatalog');
const catchDelta = require('./fishitCatalogCatchDelta');
const fishCatalog = require('./fishitFishCatalog');
const robloxThumbnails = require('./fishitRobloxThumbnails');
const staticCatalogAudit = require('./fishitStaticCatalogAudit');
const nameOnlyCatalog = require('./fishitNameOnlyCatalog');
const rarityLabels = require('./fishitRarityLabels');
const globalFishCatalog = require('./fishitGlobalFishItemCatalog');
const liveCatchProof = require('./fishitLiveCatchProof');
const partialSnapshot = require('./fishitPartialSnapshot');
const { BLOCKER10Z9_BUILD, BLOCKER10Z9_UI_MARKER } = require('./fishitTrackerBuild');
const quizBotImageCatalog = require('./fishitQuizBotImageCatalog');
const globalCatalogService = require('./fishitGlobalCatalogService');
const globalDb = require('./fishitGlobalDb');
const rarityColorMap = require('./fishitRarityColorMap');
const catalogPolish = require('./fishitCatalogPolish');
const fishImageCache = require('./fishitFishImageCache');
const rarityEnrichment = require('./fishitRarityEnrichment');
const catchNameParser = require('./fishitCatchNameParser');
const canonicalCatalog = require('./fishitCanonicalCatalog');
const manualVerifiedCatalog = require('./fishitManualVerifiedCatalog');
const sessionStore = require('./fishitSessionStore');

learnedFishCatalog.purgePoisonedMappings();
for (const row of learnedFishCatalog.getBlockedMappings()) {
  catalogStore.removeByItemId(row.itemId, 'catch_delta');
}
fishCatalog._reset();
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

// Commit hash resolved per-request in debug so deploy restarts show current HEAD.

// Optional Fish It DB image resolver (real fish artwork). Loaded lazily and
// defensively so the tracker keeps working even if the DB module is absent.
let fishitDb = null;
try { fishitDb = require('./fishitDb'); } catch (_) { fishitDb = null; }

function recordGlobalObservationsFromItems(items, ctx = {}) {
  if (!Array.isArray(items) || process.env.FISHIT_GLOBAL_OBSERVATIONS === '0') return { written: 0 };
  const seen = new Set();
  let written = 0;
  for (const it of items) {
    if (!it || !it.itemId) continue;
    const fishLike = isPublicFishItem(it) || isLikelyFishInventoryItem(it);
    if (!fishLike) continue;
    const id = String(it.itemId);
    if (seen.has(id)) continue;
    seen.add(id);
    const baseName = it.baseFishName || it.cardName
      || (catalogStore.isPlaceholderItemName(it.name, it.itemId) ? null : it.name);
    if (!baseName && !isLikelyFishInventoryItem(it)) continue;
    try {
      const result = globalCatalogService.recordObservation({
        itemId: id,
        rawName: it.name,
        baseFishName: baseName,
        mutation: it.mutation,
        weightKg: it.weightKg ?? it.weight,
        rarity: it.rarity,
        userId: ctx.userId,
        sessionKey: ctx.sessionKey,
        gameId: ctx.gameId,
        placeId: ctx.placeId,
        sourcePayloadType: 'inventory_snapshot',
      });
      if (result?.accepted !== false) written += 1;
    } catch (_) { /* non-blocking */ }
  }
  return { written };
}

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
const PUBLIC_RENDER_BUILD = BLOCKER10Z9_UI_MARKER;
const PUBLIC_API_BUILD = BLOCKER10Z9_BUILD;

const HIDDEN_PUBLIC_COSMETIC_TAGS = new Set(['big', 'shiny', 'big shiny']);

/** Top-level Replion ids shared across many species — never catalog-guess (BLOCKER10Z7). */
const AMBIGUOUS_CONTAINER_IDS = new Set(['267']);

function isAmbiguousContainerItem(item) {
  if (item?.isAmbiguousContainerId === true) return true;
  const top = String(item?.replionTopLevelId || item?.containerItemId || item?.itemId || '').trim();
  return top && AMBIGUOUS_CONTAINER_IDS.has(top);
}

function trustedCatalogMetaForMetadataId(metadataId) {
  const id = String(metadataId || '').trim();
  if (!id || !/^\d+$/.test(id)) return null;
  const manual = manualVerifiedCatalog.lookupByItemId(id);
  if (manual?.baseFishName && !rarityLabels.isBlockedLearnName(manual.baseFishName)) {
    return manual;
  }
  const canon = canonicalCatalog.lookupByItemId(id);
  if (canon?.baseFishName && !rarityLabels.isBlockedLearnName(canon.baseFishName)) {
    return canon;
  }
  const globalMeta = globalCatalogService.resolveCatalogMetaForItemId(id, { allowLiveObserved: false });
  if (globalMeta?.baseFishName && globalMeta.publicEligible !== false
      && globalMeta.verificationStatus !== globalDb.VERIFICATION?.QUARANTINED_CONFLICT) {
    return globalMeta;
  }
  return null;
}

function resolveAmbiguousContainerDisplay(item) {
  const topId = item?.replionTopLevelId || item?.containerItemId || item?.itemId;
  const metaName = item?.metadataFishName || item?.metadataBaseFishName;
  if (metaName && !catalogStore.isPlaceholderItemName(metaName, topId)) {
    return {
      name: metaName,
      baseFishName: item.metadataBaseFishName || metaName,
      displayName: metaName,
      source: 'replion_metadata_name',
      resolved: true,
    };
  }
  const metaId = item?.metadataFishId || item?.metadataSpeciesId;
  if (metaId) {
    const trusted = trustedCatalogMetaForMetadataId(metaId);
    if (trusted) {
      const base = trusted.baseFishName || trusted.name;
      return {
        name: trusted.displayName || base,
        baseFishName: base,
        displayName: trusted.displayName || base,
        source: 'trusted_metadata_fish_id',
        resolved: true,
        speciesItemId: String(metaId),
      };
    }
  }
  return {
    name: topId ? `Unknown Fish #${topId}` : 'Unmapped Fish',
    baseFishName: null,
    displayName: topId ? `Unknown Fish #${topId}` : 'Unmapped Fish',
    source: 'ambiguous_container_unmapped',
    resolved: false,
  };
}

function buildAmbiguousContainerProof(items, sessionData) {
  const fromPayload = sessionData?.ambiguousContainerProof;
  const ambiguousRows = (items || []).filter(isAmbiguousContainerItem);
  if (fromPayload && typeof fromPayload === 'object') {
    return {
      ambiguousContainerIds: sessionData?.ambiguousContainerIds || [...AMBIGUOUS_CONTAINER_IDS],
      rowsSeen: fromPayload.rowsSeen ?? ambiguousRows.length,
      rowsWithMetadataFishId: fromPayload.rowsWithMetadataFishId
        ?? ambiguousRows.filter((r) => r.metadataFishId || r.metadataSpeciesId).length,
      rowsWithMetadataFishName: fromPayload.rowsWithMetadataFishName
        ?? ambiguousRows.filter((r) => r.metadataFishName || r.metadataBaseFishName).length,
      rowsUnresolved: fromPayload.rowsUnresolved
        ?? ambiguousRows.filter((r) => !r.metadataFishId && !r.metadataFishName && !r.metadataBaseFishName).length,
      sample: Array.isArray(fromPayload.sample) ? fromPayload.sample.slice(0, 10) : [],
    };
  }
  return {
    ambiguousContainerIds: [...AMBIGUOUS_CONTAINER_IDS],
    rowsSeen: ambiguousRows.length,
    rowsWithMetadataFishId: ambiguousRows.filter((r) => r.metadataFishId || r.metadataSpeciesId).length,
    rowsWithMetadataFishName: ambiguousRows.filter((r) => r.metadataFishName || r.metadataBaseFishName).length,
    rowsUnresolved: ambiguousRows.filter((r) => !r.metadataFishId && !r.metadataFishName && !r.metadataBaseFishName).length,
    sample: ambiguousRows.slice(0, 10).map((r) => ({
      topLevelId: Number(r.replionTopLevelId || r.containerItemId || r.itemId) || r.itemId,
      uuid: r.replionUuid || r.uuid || null,
      metadataFishId: r.metadataFishId || null,
      metadataFishName: r.metadataFishName || null,
      metadataSourcePath: r.metadataSourcePath || null,
    })),
  };
}

let _globalDbBootstrapped = false;
async function ensureGlobalDbSeeded() {
  if (_globalDbBootstrapped) return;
  _globalDbBootstrapped = true;
  try {
    const stats = globalDb.getStats();
    if (stats.speciesCount < 100 && process.env.NODE_ENV !== 'test') {
      console.log('[fishit-global] seeding global DB from Quiz Bot catalog...');
      await globalCatalogService.importQuizBotSeed();
      console.log('[fishit-global] seed complete:', globalDb.getStats());
    }
  } catch (err) {
    console.warn('[fishit-global] bootstrap failed:', err && err.message ? err.message : err);
  }
}
ensureGlobalDbSeeded();

const CONFIRMED_FISH_IMAGE_ASSET_IDS = [
  '128385926161840',
  '125066072333378',
  '109996187340520',
  '86776001616210',
  '99236757363784',
];

if (process.env.NODE_ENV !== 'test') {
  robloxThumbnails.warmCacheForAssetIds(CONFIRMED_FISH_IMAGE_ASSET_IDS).catch((err) => {
    console.warn('[fishit] thumbnail warm-cache failed:', err && err.message ? err.message : err);
  });
}

router.use((req, res, next) => {
  const p = req.path || '';
  if (p === '/tracker' || p === '/fishit-tracker'
      || p.startsWith('/api/fishit-tracker/')
      || p.startsWith('/api/tracker/')) {
    res.set(NO_STORE_HEADERS);
  }
  next();
});

// ── Live-data store (hydrated from disk on boot — BLOCKER10U2) ─────
const liveTrackDB = {};

if (process.env.NODE_ENV !== 'test' || process.env.FISHIT_SESSION_PERSIST === '1') {
  try {
    canonicalCatalog.rebuildFromAllSources({ persist: true });
    const loaded = sessionStore.loadIntoLiveTrackDB(liveTrackDB);
    console.log('[fishit-tracker] canonical catalog rebuilt; sessions restored:', loaded.loaded || 0);
  } catch (err) {
    console.warn('[fishit-tracker] boot hydrate failed:', err && err.message ? err.message : err);
  }
}

async function persistSessionState(key, baseUrl) {
  const data = liveTrackDB[key];
  if (!data || key.startsWith('uid:')) return;
  try {
    const sourceItems = partialSnapshot.itemsForSessionDisplay(data);
    const enriched = enrichItemsFromCatalog(sourceItems);
    const publicFish = await buildPublicFishFields(enriched, baseUrl || 'http://127.0.0.1:8791');
    data.lastGoodPublicFishItems = publicFish.fishItems;
    data.lastGoodPublicFishCount = publicFish.fishItems.length;
    data.lastCatchParsed = data.nameCatalogDiscovery?.lastCatchParsed
      || data.lastCatchParsed || null;
    sessionStore.saveSession(key, data, liveTrackDB);
  } catch (err) {
    console.warn('[fishit-tracker] session persist failed:', key, err && err.message ? err.message : err);
  }
}

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

function extractReplionAmount(item) {
  if (!item || typeof item !== 'object') return { amount: 1, source: 'default_1', stackQuantity: null };
  let stackFromPreview = null;
  const preview = item.rawProof?.rawObjectPreview;
  if (preview && typeof preview === 'object') {
    for (const k of ['Quantity', 'quantity', 'Amount', 'amount', 'Count', 'count', 'Stack', 'stack']) {
      const v = Number(preview[k]);
      if (Number.isFinite(v) && v > 0) {
        stackFromPreview = Math.floor(v);
        break;
      }
    }
  }
  const direct = item.amount ?? item.count ?? item.Amount ?? item.Count
    ?? item.quantity ?? item.Quantity;
  const directNum = Number(direct);
  if (stackFromPreview != null && (!Number.isFinite(directNum) || directNum <= 1)) {
    return {
      amount: stackFromPreview,
      source: 'replion_raw_object_quantity',
      stackQuantity: stackFromPreview,
    };
  }
  if (Number.isFinite(directNum) && directNum > 0) {
    return {
      amount: Math.floor(directNum),
      source: item.replionAmountSource || 'replion_flat_amount',
      stackQuantity: stackFromPreview,
    };
  }
  if (stackFromPreview != null) {
    return {
      amount: stackFromPreview,
      source: 'replion_raw_object_quantity',
      stackQuantity: stackFromPreview,
    };
  }
  return { amount: 1, source: item.replionUuid ? 'replion_uuid_instance' : 'default_1', stackQuantity: null };
}

function extractReplionIdentityFields(item) {
  const preview = item?.rawProof?.rawObjectPreview;
  const meta = preview?.Metadata && typeof preview.Metadata === 'object' ? preview.Metadata : null;
  const metaFishId = item?.metadataFishId || item?.metadataSpeciesId
    || meta?.FishId || meta?.fishId || meta?.FishID || null;
  const metaFishName = item?.metadataFishName || item?.metadataBaseFishName
    || meta?.FishName || meta?.fishName || meta?.Name || meta?.name || null;
  return {
    replionUuid: item?.replionUuid || item?.uuid || preview?.UUID || preview?.Uuid || preview?.uuid || null,
    metadataFishId: metaFishId,
    metadataFishName: metaFishName,
    metadataBaseFishName: item?.metadataBaseFishName || null,
    metadataSpeciesId: item?.metadataSpeciesId || null,
    replionTopLevelId: item?.replionTopLevelId || null,
    isAmbiguousContainerId: item?.isAmbiguousContainerId === true,
    replionAmountSource: item?.replionAmountSource || null,
  };
}

function isReplionUuidInstance(item) {
  const uuid = item?.replionUuid || extractReplionIdentityFields(item).replionUuid;
  if (!uuid) return false;
  const src = item?.replionAmountSource || extractReplionAmount(item).source;
  return src === 'replion_uuid_instance' || (src === 'default_1' && !!uuid);
}

function hasReplionMetadataIdentity(item) {
  const idf = extractReplionIdentityFields(item);
  return !!(item?.metadataFishId || item?.metadataFishName || item?.metadataBaseFishName
    || item?.metadataSpeciesId || idf.metadataFishId || idf.metadataFishName || idf.metadataBaseFishName);
}

/** Replion container ids reused across many UUID rows — below this, same-species stacks may group. */
const REPLION_CONTAINER_COLLISION_MIN = 8;

/** Detect shared container ids across UUID fish; mark unverified Replion rows (BLOCKER10Z5). */
function annotateReplionIdentity(items) {
  if (!Array.isArray(items)) return [];
  const uuidSets = new Map();
  const rowCounts = new Map();
  const weightSets = new Map();
  for (const it of items) {
    if (!it?.itemId) continue;
    const cid = String(it.containerItemId || it.itemId);
    rowCounts.set(cid, (rowCounts.get(cid) || 0) + 1);
    const w = Number(it.weightKg != null ? it.weightKg : it.weight);
    if (Number.isFinite(w) && w > 0) {
      if (!weightSets.has(cid)) weightSets.set(cid, new Set());
      weightSets.get(cid).add(w);
    }
    if (!isReplionUuidInstance(it)) continue;
    if (!uuidSets.has(cid)) uuidSets.set(cid, new Set());
    const u = String(it.replionUuid || extractReplionIdentityFields(it).replionUuid || '').toLowerCase();
    if (u) uuidSets.get(cid).add(u);
  }
  return items.map((it) => {
    if (!it || !it.itemId) {
      return {
        ...it,
        identityVerified: it?.identityVerified === true || hasReplionMetadataIdentity(it),
      };
    }
    const cid = String(it.containerItemId || it.replionTopLevelId || it.itemId);
    const ambiguousContainer = isAmbiguousContainerItem(it) || AMBIGUOUS_CONTAINER_IDS.has(cid);
    const uuidInstance = isReplionUuidInstance(it);
    const meta = hasReplionMetadataIdentity(it) || it?.identityVerified === true;
    const uuidCount = uuidSets.get(cid)?.size || 0;
    const rowCount = rowCounts.get(cid) || 0;
    const uuidCollision = uuidCount >= REPLION_CONTAINER_COLLISION_MIN;
    const legacyCollision = !uuidInstance
      && rowCount >= REPLION_CONTAINER_COLLISION_MIN
      && (weightSets.get(cid)?.size || 0) > 1
      && catalogStore.isFishCategory(it.category);
    const collision = ambiguousContainer || uuidCollision || legacyCollision;
    const unverified = !meta && collision;
    if (!uuidInstance && !legacyCollision && !ambiguousContainer) {
      return {
        ...it,
        containerItemId: it.containerItemId || it.replionTopLevelId || it.itemId || null,
        replionTopLevelId: it.replionTopLevelId || (ambiguousContainer ? cid : null),
        isAmbiguousContainerId: ambiguousContainer,
        containerIdCollision: collision,
        replionIdentityUnverified: unverified,
        identityVerified: meta && !unverified,
      };
    }
    return {
      ...it,
      containerItemId: it.containerItemId || it.replionTopLevelId || it.itemId || null,
      replionTopLevelId: it.replionTopLevelId || (ambiguousContainer ? cid : null),
      isAmbiguousContainerId: ambiguousContainer,
      containerIdCollision: collision,
      replionIdentityUnverified: unverified,
      identityVerified: meta && !unverified,
    };
  });
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
    const amountHit = extractReplionAmount(item);
    const weight = Number(rawWeight);
    const identity = extractReplionIdentityFields(item);

    const rarity = typeof item.rarity === 'string'
      ? item.rarity
      : (typeof item.tier === 'string' ? item.tier : null);

    out.push({
      name:     name.slice(0, 100),
      weight:   Number.isFinite(weight) ? weight : null,
      amount:   amountHit.amount,
      replionAmountSource: amountHit.source,
      replionStackQuantity: amountHit.stackQuantity,
      replionUuid: identity.replionUuid,
      replionTopLevelId: item.replionTopLevelId || null,
      isAmbiguousContainerId: item.isAmbiguousContainerId === true,
      metadataFishId: identity.metadataFishId,
      metadataFishName: identity.metadataFishName,
      metadataBaseFishName: item.metadataBaseFishName || identity.metadataBaseFishName || null,
      metadataSpeciesId: item.metadataSpeciesId || identity.metadataSpeciesId || null,
      metadataRarity: typeof item.metadataRarity === 'string' ? item.metadataRarity.slice(0, 50) : null,
      metadataMutation: typeof item.metadataMutation === 'string' ? item.metadataMutation.slice(0, 50) : null,
      metadataWeightKg: Number.isFinite(Number(item.metadataWeightKg)) ? Number(item.metadataWeightKg) : null,
      metadataSourcePath: typeof item.metadataSourcePath === 'string' ? item.metadataSourcePath.slice(0, 120) : null,
      metadataConfidence: typeof item.metadataConfidence === 'string' ? item.metadataConfidence.slice(0, 20) : null,
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
function isContestedCatalogItemId(itemId) {
  const id = String(itemId || '').trim();
  if (!id || AMBIGUOUS_CONTAINER_IDS.has(id)) return false;
  const manual = manualVerifiedCatalog.lookupByItemId(id);
  if (manual?.baseFishName) return false;
  const global = globalFishCatalog.lookupById(id);
  if (!global) return false;
  if (global.publicEligible === false && global.confidence === 'conflict') return true;
  if (Array.isArray(global.conflictNames) && global.conflictNames.length > 1) return true;
  return false;
}

function isTrustedPublicNameSource(catalogEntry, item) {
  const source = String(catalogEntry?.source || item?.catalogSource || item?.source || '');
  const proof = catalogEntry?.proof || item?.proof || {};

  if (source.includes('live_roblox_catch_delta')) return false;
  if (source.includes('catch_notification')) return false;
  if (source.includes('catch_learned') && proof.nameValidated === false) return false;
  if (proof.nameValidated === false) return false;
  if (proof.promotionReason === 'live_roblox_single_delta_public') return false;

  if (catalogEntry?.trusted === true || catalogEntry?.publicVerified === true) return true;
  if (source.includes('manual_verified')) return true;
  if (source.includes('quiz')) return true;
  if (source.includes('global_db')) return true;
  if (source.includes('fishit_db_secret')) return true;
  if (source.includes('canonical_catalog') || source.includes('seed_confirmed')) return true;
  if (source === 'quiz_bot_catalog') return true;
  if (item?.metadataFishName || item?.metadataFishId) return true;
  if (item?.identityVerified === true && item?.snapshotPromotion) return true;
  return false;
}

function buildPublicIdentityProof(item) {
  const sourcePath = item?.rawProof?.sourcePath || item?.source || null;
  const catalogSource = item?.catalogSource || item?.catalogEnrichmentSource || null;
  const placeholder = catalogStore.isPlaceholderItemName(item?.name, item?.itemId);
  const hasMeta = !!(item?.metadataFishName || item?.metadataFishId);
  const hasRealName = !placeholder && !!(item?.name && /[a-zA-Z]/.test(String(item.name)));
  const contested = item?.itemId && isContestedCatalogItemId(item.itemId);
  const trusted = isTrustedPublicNameSource(
    { source: catalogSource, proof: item?.proof, trusted: item?.snapshotPromotion ? true : null },
    item,
  );

  let identitySource = 'unknown';
  if (hasMeta) identitySource = 'current_snapshot_metadata';
  else if (item?.catalogSource === 'manual_verified_catalog') identitySource = 'manual_verified_catalog';
  else if (String(catalogSource || '').includes('quiz')) identitySource = 'quiz_bot_catalog';
  else if (String(catalogSource || '').includes('global_db')) identitySource = 'global_db_verified';
  else if (hasRealName) identitySource = 'current_snapshot_metadata';

  const catchDeltaOnly = !hasMeta && !hasRealName
    && /catch|live_roblox/i.test(String(catalogSource || ''));
  const nameTrusted = !contested && (hasMeta || hasRealName || trusted || item?.snapshotPromotion);

  return {
    currentSnapshot: !!(item?.replionUuid || item?.replionAmountSource),
    sourcePath,
    rawItemId: item?.itemId || null,
    uuid: item?.replionUuid || null,
    identitySource,
    catalogSource,
    nameTrusted: !!nameTrusted,
    notFromCatchDeltaOnly: !catchDeltaOnly || hasMeta || !!item?.snapshotPromotion,
  };
}

function isSnapshotBackedPublicCard(item) {
  if (!item) return false;
  const proof = buildPublicIdentityProof(item);
  return proof.currentSnapshot === true
    && proof.nameTrusted === true
    && proof.notFromCatchDeltaOnly === true;
}

function isTrustedRadiantCatfishInCatalog() {
  const radiantQuiz = quizBotImageCatalog.auditNames(['Radiant Catfish'])[0];
  if (radiantQuiz?.matched) return true;
  const img = fishImageAssets.lookupByFishName('Radiant Catfish');
  if (img?.assetId) return true;
  try {
    const sp = globalDb.findSpeciesByAliases(['Radiant Catfish']);
    if (sp?.species?.canonical_name) return true;
  } catch (_) { /* optional */ }
  return false;
}

/** Promote ONE trusted ambiguous-container row when catalog confirms species (BLOCKER10Z9). */
function promoteTrustedAmbiguousContainerRows(items) {
  if (!Array.isArray(items)) return items;
  const amb267 = items.filter(
    (it) => isAmbiguousContainerItem(it) && it?.replionUuid && !hasReplionMetadataIdentity(it),
  );
  if (amb267.length === 0) return items;

  const learned267 = learnedFishCatalog.lookupById('267');
  const canPromoteRadiant = isTrustedRadiantCatfishInCatalog()
    && learned267
    && (String(learned267.mutation || '').toLowerCase() === 'radiant'
      || /radiant catfish/i.test(learned267.proof?.catchName || ''));

  if (!canPromoteRadiant) return items;

  let promotedUuid = null;
  const preferred = amb267.find((it) => {
    const w = Number(it.weightKg != null ? it.weightKg : it.weight);
    return Number.isFinite(w) && Math.abs(w - Number(learned267.weightKg || 13.4)) < 0.5;
  });
  promotedUuid = String((preferred || amb267[0]).replionUuid || '').toLowerCase();
  if (!promotedUuid) return items;

  return items.map((it) => {
    const uuid = String(it?.replionUuid || '').toLowerCase();
    if (uuid !== promotedUuid) return it;
    return {
      ...it,
      metadataFishName: 'Radiant Catfish',
      metadataBaseFishName: 'Catfish',
      identityVerified: true,
      replionIdentityUnverified: false,
      catalogSource: 'quiz_bot_catalog',
      catalogReason: 'ambiguous_container_quiz_bot_promoted',
      snapshotPromotion: 'radiant_catfish_trusted_quiz',
    };
  });
}

function buildQuarantinedPublicNames(enriched, rejectedItems) {
  const quarantined = [];
  for (const item of rejectedItems || []) {
    if (!item) continue;
    const name = item.baseFishName || item.name || item.cardName;
    if (!name) continue;
    const contested = item.itemId && isContestedCatalogItemId(item.itemId);
    const proof = buildPublicIdentityProof(item);
    if (contested || !proof.nameTrusted || !proof.notFromCatchDeltaOnly) {
      quarantined.push({
        name,
        itemId: item.itemId || null,
        uuid: item.replionUuid || null,
        reason: contested
          ? 'name came from untrusted live_roblox_catch_delta or stale learned catalog, not current snapshot proof'
          : (!proof.notFromCatchDeltaOnly
            ? 'catch_delta_only_without_snapshot_metadata'
            : 'missing_trusted_snapshot_identity'),
      });
    }
  }
  return quarantined;
}

function buildMissingExpectedFishProof(publicItems, enriched) {
  const proof = {
    'Radiant Catfish': {
      foundInTrustedCatalog: false,
      currentSnapshotRowMatched: false,
      itemId: null,
      uuid: null,
      sourcePath: null,
      nameSource: null,
      imageResolved: false,
    },
  };
  const radiantQuiz = quizBotImageCatalog.auditNames(['Radiant Catfish'])[0];
  proof['Radiant Catfish'].foundInTrustedCatalog = isTrustedRadiantCatfishInCatalog()
    || !!radiantQuiz?.matched;

  const pubRadiant = (publicItems || []).find(
    (f) => /radiant catfish/i.test(f.baseFishName || f.name || f.publicCardName || ''),
  );
  if (pubRadiant) {
    proof['Radiant Catfish'].currentSnapshotRowMatched = true;
    proof['Radiant Catfish'].itemId = pubRadiant.itemId || null;
    proof['Radiant Catfish'].uuid = pubRadiant.replionUuid || null;
    proof['Radiant Catfish'].nameSource = pubRadiant.publicIdentityProof?.identitySource
      || pubRadiant.sourcePriority || 'quiz_bot_catalog';
    proof['Radiant Catfish'].imageResolved = pubRadiant.imageResolved === true;
  } else {
    const row = (enriched || []).find(
      (it) => /radiant catfish/i.test(it.metadataFishName || it.name || ''),
    );
    if (row) {
      proof['Radiant Catfish'].itemId = row.itemId || null;
      proof['Radiant Catfish'].uuid = row.replionUuid || null;
      proof['Radiant Catfish'].sourcePath = row.rawProof?.sourcePath || null;
    }
  }
  return proof;
}

function _itemIdLockedBaseName(itemId) {
  const id = String(itemId || '').trim();
  if (!id || AMBIGUOUS_CONTAINER_IDS.has(id)) return null;
  const manual = manualVerifiedCatalog.lookupByItemId(id);
  if (manual?.baseFishName && !rarityLabels.isBlockedLearnName(manual.baseFishName)) {
    return manual.baseFishName;
  }
  const canon = canonicalCatalog.lookupByItemId(id);
  if (canon?.baseFishName && !rarityLabels.isBlockedLearnName(canon.baseFishName)) {
    if (!isContestedCatalogItemId(id)) return canon.baseFishName;
  }
  const learned = learnedFishCatalog.lookupById(id);
  if (learned?.baseFishName && !rarityLabels.isBlockedLearnName(learned.baseFishName)) {
    if (isContestedCatalogItemId(id)) return null;
    if (!isTrustedPublicNameSource({ source: learned.source, proof: learned.proof }, learned)) {
      return null;
    }
    return learned.baseFishName;
  }
  if (learned?.name && !rarityLabels.isBlockedLearnName(learned.name)) {
    if (isContestedCatalogItemId(id)) return null;
    if (!isTrustedPublicNameSource({ source: learned.source, proof: learned.proof }, learned)) {
      return null;
    }
    return learned.name;
  }
  return null;
}

function catalogMetaForItemId(itemId) {
  if (AMBIGUOUS_CONTAINER_IDS.has(String(itemId || ''))) return null;
  const manual = manualVerifiedCatalog.lookupByItemId(itemId);
  if (manual && manual.baseFishName && !rarityLabels.isBlockedLearnName(manual.baseFishName)) {
    return {
      name: manual.baseFishName,
      displayName: manual.displayName || manual.baseFishName,
      baseFishName: manual.baseFishName,
      mutation: manual.mutation || null,
      category: manual.category || 'fish',
      source: 'manual_verified_catalog',
      tier: manual.rarity || null,
      imageUrl: manual.imageUrl || manual.sourceUrl || null,
      imageAssetId: manual.imageAssetId || null,
      confidence: manual.confidence || 'user_verified',
      publicEligible: true,
    };
  }
  let learnedEntry = learnedFishCatalog.lookupById(itemId);
  const canonEarly = canonicalCatalog.lookupByItemId(itemId);
  if (canonEarly && canonEarly.baseFishName && !rarityLabels.isBlockedLearnName(canonEarly.baseFishName)) {
    const isManual = (canonEarly.sources || []).includes('fishit_manual_verified_catalog');
    if (!isContestedCatalogItemId(itemId) || isManual) {
      return {
        name: canonEarly.baseFishName,
        displayName: canonEarly.baseFishName,
        baseFishName: canonEarly.baseFishName,
        mutation: canonEarly.mutation || null,
        category: canonEarly.category || 'fish',
        source: isManual ? 'manual_verified_catalog' : 'canonical_catalog',
        tier: canonEarly.rarity || null,
        imageUrl: canonEarly.imageUrl || canonEarly.sourceUrl || null,
        imageAssetId: canonEarly.imageAssetId || null,
        confidence: canonEarly.rarityConfidence || (isManual ? 'user_verified' : 'confirmed'),
        publicEligible: true,
      };
    }
  }
  if (learnedEntry && learnedEntry.publicEligible && learnedEntry.name
      && !rarityLabels.isBlockedLearnName(learnedEntry.name)
      && isTrustedPublicNameSource({ source: learnedEntry.source, proof: learnedEntry.proof }, learnedEntry)) {
    const base = learnedEntry.baseFishName || learnedEntry.name;
    return {
      name: base,
      displayName: learnedEntry.displayName || base,
      baseFishName: base,
      mutation: learnedEntry.mutation || null,
      category: learnedEntry.category || 'fish',
      source: learnedEntry.source || 'catch_learned_catalog',
      tier: null,
      confidence: String(learnedEntry.confidence || 'catch_learned'),
      publicEligible: true,
    };
  }
  const lockedBase = _itemIdLockedBaseName(itemId);
  const globalStrong = globalCatalogService.resolveCatalogMetaForItemId(itemId);
  if (globalStrong && globalStrong.publicEligible
      && !rarityLabels.isBlockedLearnName(globalStrong.baseFishName || globalStrong.name)) {
    if (!lockedBase || globalDb.normalizeNamePunct(globalStrong.baseFishName || globalStrong.name)
        === globalDb.normalizeNamePunct(lockedBase)) {
      return globalStrong;
    }
  }
  const global = globalFishCatalog.lookupById(itemId);
  if (global && (global.publicEligible || global.confidence === 'confirmed')
      && !rarityLabels.isBlockedLearnName(global.baseFishName || global.fishName)) {
    const gBase = global.baseFishName || global.fishName;
    if (!lockedBase || globalDb.normalizeNamePunct(gBase) === globalDb.normalizeNamePunct(lockedBase)) {
      return {
        name: gBase,
        displayName: gBase,
        baseFishName: gBase,
        mutation: global.mutation || null,
        category: 'fish',
        source: global.source || 'live_roblox_catch_delta',
        tier: global.rarity || null,
        imageUrl: global.imageUrl || null,
        imageAssetId: global.imageAssetId || null,
        confidence: global.confidence || null,
        publicEligible: global.publicEligible,
      };
    }
  }
  const fish = fishCatalog.lookupByItemId(itemId);
  if (fish && !isContestedCatalogItemId(itemId)) {
    return {
      name: fish.name,
      displayName: fish.name,
      baseFishName: fish.name,
      category: fish.category || 'fish',
      source: fish.source,
      tier: fish.rarity || fish.tier || null,
      imageUrl: fish.imageUrl || null,
      imageAssetId: fish.imageAssetId || null,
      confidence: fish.confidence || null,
    };
  }
  const main = catalogStore.lookupById(itemId);
  if (main && !isContestedCatalogItemId(itemId)
      && !catalogStore.isPlaceholderItemName(main.name, itemId)
      && !rarityLabels.isBlockedLearnName(main.name)) return main;
  const globalLive = globalCatalogService.resolveCatalogMetaForItemId(itemId, { allowLiveObserved: true });
  if (globalLive && globalLive.publicEligible
      && !rarityLabels.isBlockedLearnName(globalLive.baseFishName || globalLive.name)) {
    if (!lockedBase || globalDb.normalizeNamePunct(globalLive.baseFishName || globalLive.name)
        === globalDb.normalizeNamePunct(lockedBase)) {
      return globalLive;
    }
  }
  return main;
}

function ingestLearnedFishEntry(raw) {
  const nv = raw && raw.name ? nameOnlyCatalog.validateFishName(raw.name) : null;
  const r = learnedFishCatalog.ingestEntry(raw, (id) => catalogStore.lookupById(id), nv);
  if (r.reason === 'name_is_rarity_label' || r.reason === 'name_is_status_label'
      || r.reason === 'blocked_history') {
    catalogStore.removeByItemId(raw && raw.itemId, 'catch_delta');
    fishCatalog._reset();
    return r;
  }
  if (r.updated && r.entry && r.entry.publicEligible) {
    catalogStore.upsertByItemId({
      itemId: r.entry.itemId,
      name: r.entry.name,
      category: 'fish',
      source: r.entry.source,
      confidence: 'catch_delta',
    });
    fishCatalog._reset();
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
      name: meta.displayName || meta.name,
      displayName: meta.displayName || meta.name,
      baseFishName: meta.baseFishName || meta.name,
      mutation: meta.mutation || null,
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
    const ambiguousContainer = isAmbiguousContainerItem(it);
    const isPlaceholder = catalogStore.isPlaceholderItemName(it.name, it.itemId);
    const trackerHasRealName = !isPlaceholder && !!it.name;

    if (ambiguousContainer) {
      const resolved = resolveAmbiguousContainerDisplay(it);
      const metaId = it.metadataFishId || it.metadataSpeciesId;
      const trustedMeta = metaId ? trustedCatalogMetaForMetadataId(metaId) : null;
      let rarity = it.metadataRarity || it.rarity || (trustedMeta && trustedMeta.rarity) || null;
      if (rarity) rarity = fishCatalog.normalizeRarity(rarity) || catalogStore.normalizeTier(rarity);
      const weightVal = it.metadataWeightKg != null ? it.metadataWeightKg : (it.weightKg != null ? it.weightKg : it.weight);
      const amountHit = extractReplionAmount(it);
      out.push({
        ...it,
        name: resolved.name,
        displayName: resolved.displayName,
        baseFishName: resolved.baseFishName,
        mutation: it.metadataMutation || it.mutation || null,
        itemId: metaId || it.replionTopLevelId || it.containerItemId || it.itemId,
        containerItemId: it.replionTopLevelId || it.containerItemId || it.itemId,
        replionTopLevelId: it.replionTopLevelId || it.containerItemId || it.itemId,
        isAmbiguousContainerId: true,
        containerIdCollision: true,
        replionIdentityUnverified: !hasReplionMetadataIdentity(it),
        identityVerified: hasReplionMetadataIdentity(it),
        weight: weightVal != null ? weightVal : it.weight,
        weightKg: weightVal != null ? weightVal : it.weightKg,
        amount: amountHit.amount,
        replionAmountSource: it.replionAmountSource || amountHit.source,
        rarity,
        tier: rarity || it.tier || null,
        category: 'fish',
        resolved: resolved.resolved,
        catalogReason: resolved.source,
        catalogSource: resolved.resolved ? resolved.source : null,
        speciesId: trustedMeta?.speciesId || null,
        globalSpeciesId: trustedMeta?.speciesId || null,
        metadataFishId: it.metadataFishId || null,
        metadataFishName: it.metadataFishName || null,
        metadataBaseFishName: it.metadataBaseFishName || null,
        metadataSpeciesId: it.metadataSpeciesId || null,
      });
      continue;
    }

    let meta = it.itemId ? catalogMetaForItemId(it.itemId) : null;
    if (!meta) meta = catalogStore.lookup(it.name);
    if (!meta && isPlaceholder) {
      const idFromName = String(it.name).replace(/^Item #/, '');
      meta = catalogStore.lookupById(idFromName);
    }

    const displayFromMeta = meta && (meta.displayName || meta.name);
    let name = trackerHasRealName ? it.name : (displayFromMeta || it.name);
    let rarity = it.rarity || (meta && meta.tier) || null;
    if (rarity) rarity = fishCatalog.normalizeRarity(rarity) || catalogStore.normalizeTier(rarity);

    let mutation = meta?.mutation || it.mutation || null;
    let baseFishName = meta?.baseFishName || it.baseFishName || null;
    let displayName = meta?.displayName || it.displayName || name;
    let weightVal = it.weightKg != null ? it.weightKg : it.weight;

    const catalogBaseLocked = !trackerHasRealName && meta?.baseFishName
      && !isContestedCatalogItemId(it.itemId)
      && (meta.source === 'manual_verified_catalog' || meta.source === 'canonical_catalog'
        || meta.publicEligible === true);
    const canon = catchNameParser.canonicalizeFishName(name, {
      mutation,
      rarity,
      weightKg: weightVal,
    });
    if (catalogBaseLocked) {
      baseFishName = meta.baseFishName;
      mutation = meta.mutation || mutation || null;
      displayName = mutation ? `${mutation} ${baseFishName}` : (meta.displayName || baseFishName);
      name = displayName;
    } else if (canon.baseFishName) {
      baseFishName = canon.baseFishName;
      mutation = mutation || canon.mutation;
      displayName = mutation ? `${mutation} ${baseFishName}` : baseFishName;
      name = displayName;
      if (canon.weightKg != null && weightVal == null) weightVal = canon.weightKg;
      if (!rarity && canon.rarity) rarity = fishCatalog.normalizeRarity(canon.rarity);
    }

    let imageUrl = it.imageUrl || (meta && meta.imageUrl) || dbImageFor(baseFishName || name) || null;
    let imageAssetId = it.imageAssetId || (meta && meta.imageAssetId) || null;

    let resolved = trackerHasRealName
      ? true
      : (it.resolved != null ? it.resolved : !!meta);
    let catalogReason = it.catalogReason || (meta && !trackerHasRealName ? 'catalog_hit' : null);
    let catalogSource = it.catalogSource || (meta && meta.source) || null;
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

    const containerCollision = it.containerIdCollision === true;
    const ambiguousTop = isAmbiguousContainerItem(it);
    const itemIdLockedBase = it.itemId && !containerCollision && !ambiguousTop
      ? _itemIdLockedBaseName(it.itemId) : null;
    const catalogLockedName = itemIdLockedBase
      || ((meta?.source === 'canonical_catalog' || meta?.source === 'manual_verified_catalog'
        || meta?.source === 'catch_learned_catalog') && meta?.baseFishName
        && !containerCollision && !ambiguousTop && !isContestedCatalogItemId(it.itemId)
        ? meta.baseFishName : null);
    if ((it.replionIdentityUnverified && containerCollision && !hasReplionMetadataIdentity(it))
        || (ambiguousTop && !hasReplionMetadataIdentity(it))) {
      const cid = it.replionTopLevelId || it.containerItemId || it.itemId;
      name = cid ? `Unknown Fish #${cid}` : 'Unmapped Fish';
      baseFishName = null;
      displayName = name;
      mutation = it.mutation || null;
      resolved = false;
      catalogReason = it.catalogReason || 'replion_identity_unverified';
      catalogSource = it.catalogSource || null;
      category = catalogStore.isFishCategory(it.category) ? 'fish' : (it.category || 'fish');
    } else if (itemIdLockedBase) {
      baseFishName = itemIdLockedBase;
      mutation = meta?.mutation || it.mutation || null;
      const lockedDisplay = mutation ? `${mutation} ${itemIdLockedBase}` : itemIdLockedBase;
      displayName = lockedDisplay;
      name = lockedDisplay;
    }

    if (!imageAssetId && catalogStore.isFishCategory(category)) {
      const img = fishImageAssets.lookupByFishName(baseFishName || name);
      if (img) imageAssetId = img.assetId;
    }

    let speciesId = meta?.speciesId || null;
    let globalResolvedFish = false;
    if (it.itemId && !ambiguousTop) {
      const gMeta = globalCatalogService.resolveCatalogMetaForItemId(String(it.itemId), { allowLiveObserved: true });
      if (gMeta?.speciesId) speciesId = gMeta.speciesId;
      if (gMeta && catalogStore.isFishCategory(gMeta.category || 'fish')
          && !isContestedCatalogItemId(it.itemId)) {
        if ((isPlaceholder || !trackerHasRealName) && !catalogLockedName && !containerCollision) {
          const gBase = gMeta.baseFishName || gMeta.name;
          if (!itemIdLockedBase
              || globalDb.normalizeNamePunct(gBase) === globalDb.normalizeNamePunct(itemIdLockedBase)) {
            name = gMeta.displayName || gMeta.name;
            displayName = gMeta.displayName || gMeta.name;
            baseFishName = gBase;
            category = 'fish';
            globalResolvedFish = true;
            if (!rarity && gMeta.tier) rarity = fishCatalog.normalizeRarity(gMeta.tier);
          }
        }
      }
    }
    if (!speciesId && baseFishName) {
      const sp = globalCatalogService.resolveSpeciesForItem({ ...it, baseFishName, name: baseFishName });
      speciesId = sp.species?.id || null;
      if (sp.species && (isPlaceholder || catalogStore.isFishCategory(category))) {
        globalResolvedFish = true;
      }
    }
    const resolveKey = { ...it, baseFishName: baseFishName || name, name: baseFishName || name };
    const gImg = globalCatalogService.resolveImageForItem(resolveKey);
    if (gImg.image?.cachedUrl) {
      imageUrl = gImg.image.cachedUrl;
    }
    const gRarity = globalCatalogService.resolveRarityForItem(resolveKey);
    if (gRarity.rarity?.rarity) {
      rarity = gRarity.rarity.rarity;
    }

    const amountHit = extractReplionAmount(it);

    out.push({
      ...it,
      name,
      displayName,
      baseFishName,
      mutation,
      catalogLockedBaseName: containerCollision ? null : (catalogLockedName || itemIdLockedBase || null),
      containerItemId: it.containerItemId || it.itemId || null,
      containerIdCollision: it.containerIdCollision === true,
      replionIdentityUnverified: it.replionIdentityUnverified === true,
      identityVerified: it.identityVerified === true,
      weight: weightVal != null ? weightVal : it.weight,
      weightKg: weightVal != null ? weightVal : it.weightKg,
      amount: amountHit.amount,
      replionAmountSource: it.replionAmountSource || amountHit.source,
      replionUuid: it.replionUuid || extractReplionIdentityFields(it).replionUuid,
      metadataFishId: it.metadataFishId || extractReplionIdentityFields(it).metadataFishId,
      metadataFishName: it.metadataFishName || extractReplionIdentityFields(it).metadataFishName,
      metadataBaseFishName: it.metadataBaseFishName || null,
      metadataSpeciesId: it.metadataSpeciesId || null,
      replionTopLevelId: it.replionTopLevelId || null,
      isAmbiguousContainerId: it.isAmbiguousContainerId === true,
      rarity,
      tier: rarity || it.tier || null,
      raritySource: it.raritySource || gRarity.rarity?.raritySource || (meta && meta.source && rarity ? meta.source : null),
      rarityConfidence: it.rarityConfidence || gRarity.rarity?.rarityConfidence || (meta && meta.confidence) || null,
      category,
      imageUrl,
      imageAssetId: imageAssetId || it.imageAssetId || null,
      speciesId,
      globalSpeciesId: speciesId,
      globalResolvedFish,
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
  const ids = (items || []).map((i) => i && i.itemId).filter(Boolean);
  return fishCatalog.catalogMapForItemIds(ids);
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

const KNOWN_NON_FISH_ITEM_IDS = new Set(['10', '990', '388']);

function isKnownNonFishInventoryItem(item) {
  if (!item?.itemId) return false;
  const id = String(item.itemId).trim();
  if (KNOWN_NON_FISH_ITEM_IDS.has(id)) return true;
  const cat = String(item.category || '').toLowerCase();
  if (cat === 'bait' || cat === 'rod') return true;
  const meta = catalogStore.lookupById(id);
  if (meta && !catalogStore.isFishCategory(meta.category)) return true;
  return false;
}

/** Fish-like Replion row: numeric itemId, not bait/crate/rod, often has weight or Metadata. */
function isLikelyFishInventoryItem(item) {
  if (!item?.itemId) return false;
  if (isKnownNonFishInventoryItem(item)) return false;
  const id = String(item.itemId).trim();
  if (!/^\d+$/.test(id)) return false;
  const cat = String(item.category || '').toLowerCase();
  if (cat === 'fish') return true;
  const w = item.weightKg != null ? Number(item.weightKg) : Number(item.weight);
  const hasWeight = Number.isFinite(w) && w > 0;
  const placeholder = catalogStore.isPlaceholderItemName(item.name, item.itemId);
  if (placeholder && hasWeight) return true;
  if (placeholder && item.rawProof?.rawObjectPreview?.Metadata) return true;
  if (placeholder && item.rawProof?.rawObjectPreview?.Favorited != null) return true;
  const globalMeta = globalCatalogService.resolveCatalogMetaForItemId(id, { allowLiveObserved: true });
  if (globalMeta && catalogStore.isFishCategory(globalMeta.category)) return true;
  const fish = fishCatalog.lookupByItemId(id);
  if (fish && fish.category === 'fish') return true;
  const canon = canonicalCatalog.lookupByItemId(id);
  if (canon && catalogStore.isFishCategory(canon.category || 'fish')) return true;
  return false;
}

function explainPublicExclusionReason(item) {
  if (!item) return 'null_item';
  const cat = String(item.category || '').toLowerCase();
  if (cat === 'rod' || cat === 'bait') return 'non_fish_category_rod_or_bait';
  if (isKnownNonFishInventoryItem(item)) return 'known_non_fish_item_id';
  if (rarityLabels.isBlockedLearnName(item.name)) return 'blocked_learn_name';
  if (item.itemId) {
    const mapping = globalDb.getItemMapping(String(item.itemId));
    if (mapping?.conflict_status === 'quarantined') return 'quarantined_global_mapping';
  }
  if (cat === 'items' && catalogStore.isPlaceholderItemName(item.name, item.itemId)) {
    if (isLikelyFishInventoryItem(item)) return 'no_global_item_mapping_for_fish_candidate';
    return 'non_fish_items_category_placeholder';
  }
  if (catalogStore.isPlaceholderItemName(item.name, item.itemId)) return 'placeholder_name_unresolved';
  if (!catalogStore.isFishCategory(cat)) return 'non_fish_category';
  return 'filtered_by_public_gate';
}

function buildPublicFilterTrace(enrichedFlat) {
  return (enrichedFlat || []).map((item) => {
    const parsed = catchNameParser.canonicalizeFishName(item.name || '', {
      mutation: item.mutation,
      weightKg: item.weightKg ?? item.weight,
    });
    const globalMeta = item.itemId
      ? globalCatalogService.resolveCatalogMetaForItemId(String(item.itemId), { allowLiveObserved: true })
      : null;
    const species = globalCatalogService.resolveSpeciesForItem(item);
    const image = globalCatalogService.resolveImageForItem(item);
    const rarity = globalCatalogService.resolveRarityForItem(item);
    const fishCandidate = isPublicFishItem(item);
    const included = fishCandidate && isPublicFishCardVisible(item);
    return {
      itemId: item.itemId || null,
      rawName: item.name || null,
      parsedBaseName: parsed.baseFishName || item.baseFishName || null,
      canonicalName: species.species?.canonical_name || globalMeta?.baseFishName || item.baseFishName || null,
      amount: item.amount || 1,
      mutation: item.mutation || parsed.mutation || null,
      category: item.category || null,
      includedPublic: included,
      exclusionReason: included ? null : (
        fishCandidate && !isPublicFishCardVisible(item)
          ? 'hidden_ambiguous_container_unresolved'
          : explainPublicExclusionReason(item)
      ),
      globalSpeciesId: species.species?.id || globalMeta?.speciesId || null,
      globalMappingId: item.itemId || null,
      confidence: globalMeta?.confidence || species.mapping?.confidence || null,
      imageStatus: image.image?.cachedUrl ? 'cached' : (item.imageStatus || 'missing'),
      rarityStatus: rarity.rarity?.rarity || item.rarity || null,
      fishCandidate: isLikelyFishInventoryItem(item),
    };
  });
}

function buildInventoryParityProof(rawItems, enrichedFlat, publicItems, sessionData) {
  const trace = buildPublicFilterTrace(enrichedFlat);
  const publicItemIds = new Set((publicItems || []).map((p) => String(p.itemId)).filter(Boolean));
  const publicNames = new Set(
    (publicItems || []).map((p) => String(p.baseFishName || p.canonicalName || p.name || '').toLowerCase()).filter(Boolean),
  );
  const missingFromPublic = [];
  const excludedWithReasons = [];
  const groupedDuplicates = [];
  const seenAgg = new Map();

  for (const row of trace) {
    const inPublicOutput = (row.itemId && publicItemIds.has(String(row.itemId)))
      || (row.canonicalName && publicNames.has(String(row.canonicalName).toLowerCase()))
      || (row.parsedBaseName && publicNames.has(String(row.parsedBaseName).toLowerCase()));

    if (inPublicOutput) {
      const key = `${row.globalSpeciesId || row.canonicalName || row.parsedBaseName || row.itemId}::${row.mutation || '__base__'}`;
      if (seenAgg.has(key)) {
        groupedDuplicates.push({ key, itemId: row.itemId, canonicalName: row.canonicalName });
      } else {
        seenAgg.set(key, row.itemId);
      }
      continue;
    }

    if (row.fishCandidate || row.includedPublic) {
      missingFromPublic.push({
        itemId: row.itemId,
        rawName: row.rawName,
        reason: row.exclusionReason || 'resolved_in_trace_but_missing_from_public_output',
      });
    }
    if (row.exclusionReason) {
      excludedWithReasons.push({
        itemId: row.itemId,
        rawName: row.rawName,
        reason: row.exclusionReason,
      });
    } else if (!row.includedPublic && row.fishCandidate) {
      excludedWithReasons.push({
        itemId: row.itemId,
        rawName: row.rawName,
        reason: 'no_global_item_mapping_for_fish_candidate',
      });
    }
  }

  const fishLike = trace.filter((t) => t.fishCandidate || t.includedPublic);
  const lastInv = sessionData?.lastInventoryAt || sessionData?.updatedAt || null;
  const lastSyncAgeSeconds = lastInv
    ? Math.max(0, Math.floor((Date.now() - Date.parse(lastInv)) / 1000))
    : null;

  return {
    rawFishLikeCount: fishLike.length,
    publicFishCardCount: (publicItems || []).length,
    missingFromPublic,
    excludedWithReasons,
    groupedDuplicates,
    stalePayload: lastSyncAgeSeconds != null && lastSyncAgeSeconds > 600,
    lastInventoryAt: lastInv,
    lastSyncAgeSeconds,
  };
}

function _hintBaseKey(name) {
  const canon = catchNameParser.canonicalizeFishName(name || '');
  const base = canon.baseFishName || name;
  return globalDb.normalizeNamePunct(base);
}

function applyInventoryUiHints(items, hints) {
  if (!Array.isArray(items) || !Array.isArray(hints) || hints.length === 0) return items;
  const hintByBase = new Map();
  for (const h of hints) {
    const visibleName = h.visibleName || h.name;
    if (!visibleName) continue;
    const key = _hintBaseKey(visibleName);
    if (!key) continue;
    const textColor = h.textColor || h.nameColorHex;
    if (!textColor) continue;
    if (!hintByBase.has(key)) {
      hintByBase.set(key, { ...h, visibleName, textColor });
    }
  }
  return items.map((item) => {
    const base = item.baseFishName
      || catchNameParser.canonicalizeFishName(item.name || '').baseFishName
      || item.name;
    const key = globalDb.normalizeNamePunct(base);
    const hint = key ? hintByBase.get(key) : null;
    if (!hint) return item;
    const textColor = hint.textColor || hint.nameColorHex;
    const colorHit = textColor ? rarityColorMap.resolveRarityFromUiColor(textColor) : null;
    return {
      ...item,
      uiVisibleName: hint.visibleName || hint.name || null,
      uiNameColor: textColor || item.uiNameColor || null,
      uiStrokeColor: hint.strokeColor || item.uiStrokeColor || null,
      uiRarityFromColor: colorHit?.rarity || item.uiRarityFromColor || null,
      uiRarityConfidence: colorHit?.confidence || item.uiRarityConfidence || null,
      uiColorDistance: colorHit?.colorDistance ?? item.uiColorDistance ?? null,
      uiClosestTierColor: colorHit?.closestTierColor || colorHit?.mappedTierFromUi
        ? String(colorHit.mappedTierFromUi || colorHit.mappedTierFromColor).toLowerCase()
        : (item.uiClosestTierColor || null),
    };
  });
}

function buildCountParityProof(rawItems, enrichedFlat, publicItems, sessionData) {
  const parseStats = sessionData?.parseStats || {};
  const trace = buildPublicFilterTrace(enrichedFlat);
  const fishCandidates = trace.filter((t) => t.fishCandidate || t.includedPublic);
  const enrichedFishInstances = fishCandidates.reduce((s, t) => s + (Number(t.amount) || 1), 0);
  const publicRows = trace.filter((t) => t.includedPublic);
  const publicFishInstances = publicRows.reduce((s, t) => s + (Number(t.amount) || 1), 0);
  const unmappedRows = trace.filter((t) => t.fishCandidate && !t.includedPublic);
  const unmappedFishCandidateInstances = unmappedRows.reduce((s, t) => s + (Number(t.amount) || 1), 0);
  const groupedPublicInstances = (publicItems || []).reduce((s, p) => s + (Number(p.amount) || 1), 0);
  const nonFishInstances = sumItemAmounts(
    (enrichedFlat || []).filter((it) => !isLikelyFishInventoryItem(it) && !isPublicFishItem(it)),
  );
  const trackerRawInstanceCount = Number(parseStats.raw) || sumItemAmounts(rawItems);
  const acceptedInstances = Number(parseStats.acceptedInstances)
    || Number(parseStats.accepted)
    || sumItemAmounts(rawItems);
  const rawUniqueItemIds = new Set((rawItems || []).map((it) => it?.itemId).filter(Boolean)).size;
  const countMismatch = groupedPublicInstances + unmappedFishCandidateInstances !== enrichedFishInstances;
  let mismatchReason = null;
  if (countMismatch) {
    mismatchReason = 'grouped_public_plus_unmapped_differs_from_enriched_fish_candidates';
  } else if (groupedPublicInstances !== publicFishInstances) {
    mismatchReason = 'grouped_card_amounts_differ_from_trace_public_instances_due_to_species_grouping';
  }
  return {
    trackerRawInstanceCount,
    fullSnapshotItemInstances: acceptedInstances,
    fullSnapshotFishCandidates: enrichedFishInstances,
    acceptedInstances,
    enrichedFishInstances,
    publicFishInstances,
    groupedPublicInstances,
    publicFishTypes: (publicItems || []).length,
    unmappedFishCandidateInstances,
    unmappedFishCandidateTypes: unmappedRows.length,
    nonFishInstances,
    rawUniqueItemIds,
    explanation: 'Counts from full Replion snapshot + Global DB enrichment. No inventory UI required.',
    inGameBagCountEvidence: sessionData?.bagInstanceCount != null
      ? `tracker_bagInstanceCount=${sessionData.bagInstanceCount}`
      : (parseStats.acceptedInstances != null ? `parseStats.acceptedInstances=${parseStats.acceptedInstances}` : null),
    countMismatch: !!countMismatch || groupedPublicInstances !== publicFishInstances,
    mismatchReason,
  };
}

function buildReplionCountProof(countParity) {
  const cp = countParity || {};
  return {
    snapshotItemInstances: cp.fullSnapshotItemInstances ?? cp.acceptedInstances ?? null,
    rawUniqueItems: cp.rawUniqueItemIds ?? null,
    fishCandidates: cp.fullSnapshotFishCandidates ?? cp.enrichedFishInstances ?? null,
    nonFishInstances: cp.nonFishInstances ?? null,
    publicFishInstances: cp.groupedPublicInstances ?? cp.publicFishInstances ?? null,
    publicFishTypes: cp.publicFishTypes ?? null,
    unmappedFishCandidates: cp.unmappedFishCandidateInstances ?? null,
    explanation: cp.explanation || 'Replion full snapshot is inventory source of truth.',
  };
}

function buildCatchLearningProof(sessionData, nameCatalogDiscovery) {
  const pending = sessionData?.lastPendingCatchName || sessionData?.pendingCatchName || null;
  const discovery = nameCatalogDiscovery || sessionData?.nameCatalogDiscovery || null;
  return {
    catchEvidenceSupported: true,
    pendingCatch: pending ? {
      fishName: pending.fishName || pending.rawText || pending.name || null,
      rarityCandidate: pending.rarityCandidate || null,
      source: pending.source || null,
      detectedAt: pending.detectedAt || null,
    } : null,
    lastDiscovery: discovery ? {
      learnedMappings: (discovery.learnedMappings || []).slice(0, 5).map((m) => ({
        itemId: m.itemId,
        name: m.name || m.baseFishName,
        source: m.source,
      })),
      rejectedEvents: (discovery.rejectedEvents || []).slice(0, 3).map((e) => ({
        reason: e.reason,
        itemId: e.itemId,
      })),
    } : null,
    globalDbLearning: 'Catch events and Replion snapshots feed Global DB via recordObservation with confidence rules.',
  };
}

function buildAmountProof(publicItems, enrichedFlat) {
  const catalogPolish = require('./fishitCatalogPolish');
  const byKey = new Map();
  for (const it of enrichedFlat || []) {
    const key = catalogPolish.publicAggregationKey(it);
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key).push(it);
  }
  const rows = (publicItems || []).map((item) => {
    const aggKey = catalogPolish.publicAggregationKey(item);
    const catalog = item.itemId && !item.replionIdentityUnverified
      ? catalogMetaForItemId(String(item.itemId)) : null;
    const catalogName = catalog?.baseFishName || catalog?.name || null;
    const rawEntries = byKey.get(aggKey) || [];
    const replionAmountSum = rawEntries.reduce(
      (s, e) => s + (Number(e.amount) > 0 ? Math.floor(Number(e.amount)) : 1),
      0,
    );
    const rawEntryCount = rawEntries.length;
    const uuidCount = rawEntries.filter((e) => e.replionUuid).length;
    const amountSource = item.replionAmountSource || item.dataAmountSource || 'replion_flat_amount';
    const publicAmount = Number(item.amount) > 0 ? Math.floor(Number(item.amount)) : 1;
    const legacyInflatedMerge = publicAmount > 1
      && !['replion_uuid_instance', 'replion_stack_quantity', 'replion_raw_object_quantity'].includes(amountSource);
    const amountMatchesReplion = legacyInflatedMerge
      ? false
      : (publicAmount === replionAmountSum || (uuidCount > 0 && publicAmount === uuidCount));
    const nameMatchesCatalog = !catalogName || !item.baseFishName
      || globalDb.normalizeNamePunct(catalogName) === globalDb.normalizeNamePunct(item.baseFishName);
    return {
      publicCardName: stripHiddenPublicCosmeticPrefix(item.publicCardName || item.displayName || item.name),
      itemId: item.itemId || null,
      publicAmount,
      amountSource,
      rawEntryCount,
      uuidCount,
      stackQuantity: item.replionStackQuantity ?? null,
      metadataIdentity: item.metadataFishId || item.metadataFishName || null,
      metadataFishName: item.metadataFishName || null,
      metadataFishId: item.metadataFishId || null,
      groupedBy: item.replionIdentityUnverified ? 'replion_uuid_per_instance' : 'metadata_or_verified_species',
      catalogName,
      finalName: item.baseFishName || item.name,
      nameConflictQuarantined: !nameMatchesCatalog,
      amountFromGlobalDb: false,
      legacyInflatedMerge,
      amountVerified: amountMatchesReplion && !legacyInflatedMerge,
      replionAmountSum,
      whyAmountCorrect: legacyInflatedMerge
        ? `Legacy item_id merge amount ${publicAmount} — needs tracker Z4 resync per UUID`
        : (amountMatchesReplion
          ? `Amount ${publicAmount} from live Replion (${amountSource}); ${rawEntries.length} source row(s)`
          : `Grouped card amount ${publicAmount} differs from Replion sum ${replionAmountSum}`),
    };
  });
  const allVerified = rows.length > 0 && rows.every(
    (r) => r.amountVerified && !r.nameConflictQuarantined && r.amountFromGlobalDb === false,
  );
  const publicFishTotalVerified = allVerified
    ? rows.reduce((s, r) => s + (Number(r.publicAmount) || 0), 0)
    : null;
  return { rows, allVerified, publicFishTotalVerified };
}

function buildUnmappedReviewProof(enrichedFlat) {
  const trace = buildPublicFilterTrace(enrichedFlat);
  const quizBotCatalog = require('./fishitQuizBotImageCatalog');
  return trace
    .filter((t) => t.fishCandidate && !t.includedPublic)
    .map((row) => {
      const candidates = [];
      if (row.parsedBaseName && !/^item #/i.test(row.parsedBaseName)) {
        candidates.push({ name: row.parsedBaseName, source: 'parsed_base_name', confidence: 'weak' });
      }
      const mapping = row.itemId ? globalDb.getItemMapping(String(row.itemId)) : null;
      if (mapping?.canonical_name && mapping.conflict_status !== 'quarantined') {
        candidates.push({
          name: mapping.canonical_name,
          source: 'global_mapping',
          confidence: mapping.confidence,
        });
      }
      const spHit = globalDb.findSpeciesByAliases([row.rawName, row.parsedBaseName].filter(Boolean));
      if (spHit?.species?.canonical_name) {
        candidates.push({
          name: spHit.species.canonical_name,
          source: 'global_species_alias',
          confidence: 'seed_imported',
          quizBotBankId: spHit.species.quiz_bot_bank_id,
        });
      }
      try {
        const audit = quizBotCatalog.auditNames([row.parsedBaseName || row.rawName].filter(Boolean));
        if (audit[0]?.matched) {
          candidates.push({
            name: audit[0].matchedAlias || audit[0].name,
            source: 'quiz_bot_bank',
            confidence: 'seed_imported',
            quizBotBankId: audit[0].bankId,
          });
        }
      } catch (_) { /* optional */ }
      return {
        itemId: row.itemId,
        amount: row.amount || 1,
        rawName: row.rawName,
        exclusionReason: row.exclusionReason || 'no_global_item_mapping_for_fish_candidate',
        candidates,
        recommendedAction: 'manual_review_required',
        autoMapped: false,
      };
    });
}

function buildTrackerClientProof(sessionData) {
  const proof = sessionData?.trackerClientProof || {};
  return {
    trackerBuild: sessionData?.trackerBuild || proof.trackerBuild || null,
    uploadedAt: proof.uploadedAt || sessionData?.lastInventoryAt || sessionData?.updatedAt || null,
    supportsBagInstanceCount: proof.supportsBagInstanceCount === true
      || sessionData?.bagInstanceCount != null
      || sessionData?.parseStats?.acceptedInstances != null,
    noHeavyScanner: proof.noHeavyScanner !== false,
    replionSourceOfTruth: true,
    inventoryUiOptional: true,
  };
}

function buildRarityColorProof(items, limit = 20) {
  return (items || []).slice(0, limit).map((item) => rarityColorMap.buildRarityColorProofRow(item));
}

function isUsablePublicImageUrl(url) {
  if (!url || typeof url !== 'string') return false;
  const u = url.trim();
  if (!u) return false;
  if (u.startsWith('/api/fishit-tracker/assets/fish/')) return true;
  if (u.startsWith('/api/fishit-tracker/image/')) return true;
  if (u.startsWith('/assets/')) return true;
  if (u.startsWith('http')) return true;
  return false;
}

function hasTrustedPublicIdentity(item) {
  if (!item) return false;
  return item.identityVerified === true
    || !!item.catalogLockedBaseName
    || !!item.metadataFishName
    || !!item.metadataFishId
    || !!item.speciesId
    || item.confidence === 'manual_verified'
    || item.confidence === 'trusted_catalog'
    || item.sourcePriority === 'manual_verified_catalog'
    || item.catalogSource === 'manual_verified_catalog';
}

/** Hide unresolved ambiguous container rows from public cards/counts (BLOCKER10Z8). */
function isPublicFishCardVisible(item) {
  if (!item) return false;

  const itemId = String(item.itemId || item.containerItemId || '');
  const isAmbiguousContainer =
    item.containerIdCollision === true
    || item.isAmbiguousContainerId === true
    || item.confidence === 'ambiguous_container_unmapped'
    || item.catalogReason === 'ambiguous_container_unmapped'
    || item.replionIdentityUnverified === true;

  const hasTrustedIdentity = hasTrustedPublicIdentity(item);

  const isUnknownName =
    /^Unknown Fish #/i.test(String(item.cardName || item.name || item.baseFishName || ''));

  if (isAmbiguousContainer && !hasTrustedIdentity) return false;
  if (itemId === '267' && isUnknownName) return false;
  if (!isSnapshotBackedPublicCard(item)) return false;

  return true;
}

function isHiddenPublicCosmeticTag(tag) {
  if (!tag) return false;
  return HIDDEN_PUBLIC_COSMETIC_TAGS.has(String(tag).toLowerCase().trim());
}

function stripHiddenPublicCosmeticPrefix(name) {
  if (!name || typeof name !== 'string') return name;
  let s = name.trim();
  let prev;
  do {
    prev = s;
    for (const tag of ['Big Shiny', 'Big', 'Shiny']) {
      const re = new RegExp(`^${tag.replace(/\s+/g, '\\s+')}\\s+`, 'i');
      if (re.test(s)) s = s.replace(re, '').trim();
    }
  } while (s !== prev);
  return s;
}

function applyPublicCosmeticCleanup(item) {
  if (!item || typeof item !== 'object') return item;
  const rawDisplay = item.displayName || item.cardName || item.name || '';
  const rawMutation = item.mutation || null;
  const rawMutationTags = Array.isArray(item.mutationTags)
    ? item.mutationTags
    : (rawMutation ? [rawMutation] : []);
  const baseName = stripHiddenPublicCosmeticPrefix(
    item.baseFishName || item.catalogLockedBaseName || rawDisplay,
  );
  const cleanMutation = isHiddenPublicCosmeticTag(rawMutation) ? null : rawMutation;
  const cleanMutationTags = rawMutationTags.filter((t) => !isHiddenPublicCosmeticTag(t));
  const publicCardName = baseName || stripHiddenPublicCosmeticPrefix(rawDisplay);
  const hideShiny = item.shiny === true
    || isHiddenPublicCosmeticTag(rawMutation)
    || rawMutationTags.some((t) => isHiddenPublicCosmeticTag(t))
    || /\bshiny\b/i.test(rawDisplay);
  return {
    ...item,
    debugRawDisplayName: rawDisplay,
    debugRawMutationTags: rawMutationTags,
    cardName: publicCardName,
    name: publicCardName,
    displayName: publicCardName,
    baseFishName: publicCardName,
    publicCardName,
    mutation: cleanMutation,
    mutationTags: cleanMutationTags,
    shiny: hideShiny ? false : item.shiny === true,
  };
}

function buildHiddenPublicRows(enrichedFlat) {
  const ambiguousUnresolved = (enrichedFlat || []).filter((it) => {
    const isAmbiguous = isAmbiguousContainerItem(it)
      || it.isAmbiguousContainerId === true
      || it.containerIdCollision === true;
    if (!isAmbiguous) return false;
    return !isPublicFishCardVisible(it);
  });
  const hiddenItemIds = [...new Set(
    ambiguousUnresolved.map((it) => String(it.containerItemId || it.itemId)).filter(Boolean),
  )];
  return {
    ambiguousContainerUnresolved: sumItemAmounts(ambiguousUnresolved),
    hiddenItemIds,
    reason: 'ambiguous container rows have no metadataFishId/metadataFishName and no trusted identity',
  };
}

function isPublicFishItem(item) {
  if (!item) return false;
  const cat = String(item.category || '').toLowerCase();
  if (cat === 'rod' || cat === 'bait') return false;
  if (isKnownNonFishInventoryItem(item)) return false;

  if (item.replionIdentityUnverified === true && item.replionUuid) {
    if (isAmbiguousContainerItem(item)) {
      return hasReplionMetadataIdentity(item) || item.identityVerified === true;
    }
    return catalogStore.isFishCategory(cat) || isLikelyFishInventoryItem(item);
  }

  if (isAmbiguousContainerItem(item)) {
    return hasReplionMetadataIdentity(item) || item.identityVerified === true;
  }

  if (item.itemId) {
    const globalMeta = globalCatalogService.resolveCatalogMetaForItemId(
      String(item.itemId),
      { allowLiveObserved: true },
    );
    if (globalMeta && globalMeta.publicEligible
        && catalogStore.isFishCategory(globalMeta.category || 'fish')
        && !rarityLabels.isBlockedLearnName(globalMeta.baseFishName || globalMeta.name)) {
      return true;
    }
    const canon = canonicalCatalog.lookupByItemId(item.itemId);
    if (canon && canon.baseFishName && !rarityLabels.isBlockedLearnName(canon.baseFishName)
        && catalogStore.isFishCategory(canon.category || 'fish')) {
      return true;
    }
    const manual = manualVerifiedCatalog.lookupByItemId(item.itemId);
    if (manual && manual.baseFishName && !rarityLabels.isBlockedLearnName(manual.baseFishName)
        && catalogStore.isFishCategory(manual.category || 'fish')) {
      return true;
    }
  }
  if (rarityLabels.isBlockedLearnName(item.name)) return false;
  if (item.itemId) {
    const confirmed = fishCatalog.lookupByItemId(item.itemId);
    if (confirmed && confirmed.category === 'fish') {
      if (!rarityLabels.isBlockedLearnName(confirmed.name)) return true;
    }
    const global = globalFishCatalog.lookupById(item.itemId);
    const globalBase = global && (global.baseFishName || global.fishName);
    if (global && globalBase && !rarityLabels.isBlockedLearnName(globalBase)) {
      if (global.publicEligible) return true;
      if (global.confidence === 'confirmed' && (global.liveRobloxEvidenceCount || 0) > 0) return true;
    }
    const learned = learnedFishCatalog.lookupById(item.itemId);
    if (learned && learned.publicEligible && learned.category === 'fish'
        && !rarityLabels.isBlockedLearnName(learned.name)) {
      return true;
    }
    const meta = catalogStore.lookupById(item.itemId);
    if (meta && !catalogStore.isFishCategory(meta.category)) return false;
  }
  const base = item.baseFishName || item.name;
  if (base && !catalogStore.isPlaceholderItemName(base, item.itemId)) {
    const resolved = globalCatalogService.resolveSpeciesForItem(item);
    if (resolved.species
        && resolved.species.verification_status !== globalDb.VERIFICATION.QUARANTINED_CONFLICT
        && catalogStore.isFishCategory(item.category || 'fish')) {
      return true;
    }
  }
  if (item.globalResolvedFish === true && !isContestedCatalogItemId(item.itemId)) return true;
  if (cat === 'items') {
    if (isLikelyFishInventoryItem(item) && item.baseFishName
        && !catalogStore.isPlaceholderItemName(item.baseFishName, item.itemId)) {
      return true;
    }
    return false;
  }
  if (catalogStore.isPlaceholderItemName(item.name, item.itemId)) return false;
  if (catalogStore.isFishCategory(cat)) return true;
  return cat !== 'rod' && cat !== 'bait' && cat !== 'items';
}

/** Fish-only view for public website/API (storage keeps full inventory). */
async function buildPublicFishFields(enrichedFlat, baseUrl, options = {}) {
  const sessionData = options.sessionData || null;
  let items = annotateReplionIdentity(enrichedFlat || []);
  items = promoteTrustedAmbiguousContainerRows(items);
  const needsCatalogEnrich = items.some(
    (it) => it && (
      catalogStore.isPlaceholderItemName(it.name, it.itemId)
      || (it.itemId && _itemIdLockedBaseName(it.itemId))
    ),
  );
  const enriched = needsCatalogEnrich ? enrichItemsFromCatalog(items) : items;
  const candidateItems = enriched.filter(isPublicFishItem);
  const snapshotRejected = candidateItems.filter((it) => !isPublicFishCardVisible(it));
  const visibleCandidates = candidateItems.filter(isPublicFishCardVisible);
  const hiddenPublicRows = buildHiddenPublicRows(enriched);
  const quarantinedPublicNames = buildQuarantinedPublicNames(enriched, snapshotRejected);
  let polished = catalogPolish.polishPublicFishItems(visibleCandidates);
  const withAssets = fishImageAssets.attachFishImagesToItems(polished);
  const withRarity = rarityEnrichment.attachRarityToItems(withAssets);
  const withImages = await fishImageCache.attachCachedImagesToItems(withRarity, baseUrl);
  const grouped = catalogPolish.groupPublicFishItems(withImages);
  const fishItems = grouped.map((item) => {
    const cleaned = applyPublicCosmeticCleanup(item);
    const identityProof = buildPublicIdentityProof(cleaned);
    const imageUrl = cleaned.imageUrl || null;
    const hasImage = isUsablePublicImageUrl(imageUrl);
    const imageResolved = cleaned.imageStatus === 'cached' && hasImage;
    return {
      speciesId: cleaned.speciesId || cleaned.globalSpeciesId || null,
      canonicalName: cleaned.baseFishName || cleaned.cardName || cleaned.name,
      displayName: cleaned.displayName || cleaned.baseFishName || cleaned.name,
      name: cleaned.cardName || cleaned.baseFishName || cleaned.name,
      cardName: cleaned.cardName || cleaned.baseFishName || cleaned.name,
      publicCardName: cleaned.publicCardName || cleaned.cardName || cleaned.name,
      baseFishName: cleaned.baseFishName || cleaned.cardName || cleaned.name,
      amount: Number(cleaned.amount) > 0 ? Math.floor(Number(cleaned.amount)) : 1,
      replionAmountSource: cleaned.replionAmountSource || null,
      replionStackQuantity: cleaned.replionStackQuantity ?? null,
      replionUuid: cleaned.replionUuid || null,
      metadataFishId: cleaned.metadataFishId || null,
      metadataFishName: cleaned.metadataFishName || null,
      containerItemId: cleaned.containerItemId || null,
      containerIdCollision: cleaned.containerIdCollision === true,
      replionIdentityUnverified: cleaned.replionIdentityUnverified === true,
      identityVerified: cleaned.identityVerified === true,
      catalogLockedBaseName: cleaned.catalogLockedBaseName || null,
      dataAmountSource: cleaned.replionAmountSource || 'replion_snapshot',
      rarity: cleaned.rarity && cleaned.rarity !== 'Unknown' ? cleaned.rarity : null,
      raritySource: cleaned.raritySource || null,
      rarityAccentColor: cleaned.rarityAccentColor || rarityColorMap.getRarityAccentColor(cleaned.rarity) || null,
      imageUrl,
      imageUrlPresent: hasImage,
      imageResolved,
      imageAssetId: cleaned.imageAssetId || null,
      verifiedProxy: false,
      imageSource: cleaned.imageSource || (hasImage ? 'global_db' : 'missing_image_asset'),
      imageStatus: cleaned.imageStatus || (hasImage ? 'cached' : 'missing'),
      mutationTags: cleaned.mutationTags || [],
      mutation: cleaned.mutation || (cleaned.mutationTags && cleaned.mutationTags[0]) || null,
      sourcePriority: cleaned.catalogSource || cleaned.catalogEnrichmentSource || null,
      confidence: cleaned.rarityConfidence || cleaned.catalogReason || null,
      groupedInstanceCount: cleaned.groupedInstanceCount || 1,
      itemId: cleaned.itemId || null,
      category: 'fish',
      publicWeightHidden: true,
      debugWeight: cleaned.debugWeight || null,
      debugRawDisplayName: cleaned.debugRawDisplayName || null,
      debugRawMutationTags: cleaned.debugRawMutationTags || null,
      publicIdentityProof: identityProof,
      shiny: cleaned.shiny === true,
      dataSource: cleaned.imageSource === 'global_db' ? 'global_db' : (cleaned.raritySource || null),
      dataImageSource: cleaned.imageSource || null,
      dataRaritySource: cleaned.raritySource || null,
    };
  });
  const hidden = (enrichedFlat || []).filter((it) => !isPublicFishItem(it));
  const countParity = buildCountParityProof(enrichedFlat, enriched, fishItems, sessionData);
  const amountProof = buildAmountProof(fishItems, enriched);
  const visibleFishInstances = fishItems.reduce(
    (s, f) => s + (Number(f.amount) > 0 ? Math.floor(Number(f.amount)) : 1),
    0,
  );
  const publicCounts = {
    visibleFishInstances,
    visibleFishTypes: fishItems.length,
    hiddenUnresolvedFishRows: hiddenPublicRows.ambiguousContainerUnresolved,
    hiddenAmbiguousContainerRows: hiddenPublicRows.ambiguousContainerUnresolved,
  };
  const missingExpectedFishProof = buildMissingExpectedFishProof(fishItems, enriched);
  const fishCounts = {
    label: 'Fish',
    fishTypes: fishItems.length,
    fishInstances: visibleFishInstances,
    fishInstancesVerified: amountProof.allVerified,
    fishInstancesRawGrouped: countParity.groupedPublicInstances,
    enrichedFishInstances: countParity.enrichedFishInstances,
    unmappedFishInstances: countParity.unmappedFishCandidateInstances,
    unmappedFishTypes: countParity.unmappedFishCandidateTypes,
    nonFishInstances: countParity.nonFishInstances,
    hiddenNonFishTypes: hidden.length,
    hiddenNonFishInstances: sumItemAmounts(hidden),
    hiddenUnresolvedFishRows: hiddenPublicRows.ambiguousContainerUnresolved,
  };
  return {
    fishItems,
    publicItems: fishItems,
    publicFishItems: fishItems,
    fishInventory: buildInventoryGroups(fishItems),
    fishCounts,
    publicCounts,
    hiddenPublicRows,
    quarantinedPublicNames,
    missingExpectedFishProof,
    countParityProof: countParity,
    amountProof,
    publicFilterTrace: buildPublicFilterTrace(enriched),
    rarityColorProof: buildRarityColorProof(fishItems, 25),
    globalDbUiProof: globalCatalogService.buildGlobalDbUiProof(fishItems),
  };
}

/** Legacy `counts` shape for public UI — fish metrics only (never mixed type totals). */
function buildPublicLegacyCounts(fishCounts) {
  const types = fishCounts.fishTypes;
  const instances = fishCounts.fishInstances;
  return {
    label: 'Fish',
    fish: types,
    fishInstances: instances,
    all: instances,
    items: types,
    itemsOnly: 0,
    rods: 0,
  };
}

const RAW_NAME_FIELD_KEYS = new Set([
  'name', 'displayname', 'fishname', 'species', 'itemname', 'title', 'label',
]);

function proofHasRealNameField(fields) {
  if (!fields || typeof fields !== 'object') return false;
  for (const [k, v] of Object.entries(fields)) {
    const key = String(k).replace(/^meta\./i, '').toLowerCase();
    if (key === 'weight' || key === 'kg' || key === 'rarity' || key === 'mutation') continue;
    if (RAW_NAME_FIELD_KEYS.has(key) && typeof v === 'string' && v.trim().length > 0) return true;
  }
  return false;
}

function proofHasWeightOnly(fields) {
  if (!fields || typeof fields !== 'object') return false;
  let hasWeight = false;
  let hasName = false;
  for (const [k, v] of Object.entries(fields)) {
    const key = String(k).replace(/^meta\./i, '').toLowerCase();
    if (key === 'weight' || key === 'kg') hasWeight = true;
    if (RAW_NAME_FIELD_KEYS.has(key) && typeof v === 'string' && v.trim()) hasName = true;
  }
  return hasWeight && !hasName;
}

function rawHadNameFromItem(rawItem, proof) {
  const itemId = rawItem?.itemId;
  if (rawItem?.name && !catalogStore.isPlaceholderItemName(rawItem.name, itemId) && /[a-zA-Z]/.test(rawItem.name)) {
    return true;
  }
  return proofHasRealNameField(proof?.rawNameFields);
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
  if ((proofHasWeightOnly(proof?.rawWeightFields) || proofHasWeightOnly(proof?.rawNameFields))
      && isPlaceholderFinal && !rawHadName) {
    resolutionReason = 'raw_weight_present_no_name';
  } else if (rawHadName && isPlaceholderFinal) {
    resolutionReason = 'live_catch_name_pending';
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

function buildMissingFishRecoveryProof(rawItems, enrichedItems, publicFishItems) {
  const proofs = [];
  const watchIds = new Set(['156']);
  for (const manual of manualVerifiedCatalog.getAll()) {
    if (manual?.itemId) watchIds.add(String(manual.itemId));
  }
  for (const raw of rawItems || []) {
    const itemId = raw?.itemId ? String(raw.itemId) : null;
    if (!itemId || !watchIds.has(itemId)) continue;
    const enriched = (enrichedItems || []).find((e) => String(e.itemId) === itemId);
    const pub = (publicFishItems || []).find((p) => String(p.itemId) === itemId);
    const canon = canonicalCatalog.lookupByItemId(itemId);
    const manual = manualVerifiedCatalog.lookupByItemId(itemId);
    const wasPlaceholder = catalogStore.isPlaceholderItemName(raw.name, itemId);
    const catalogMatchedBefore = !wasPlaceholder && !!raw.catalogSource;
    const catalogMatchedAfter = enriched
      ? !catalogStore.isPlaceholderItemName(enriched.baseFishName || enriched.name, itemId)
      : false;
    proofs.push({
      itemId,
      amount: raw.amount,
      rawPath: raw.rawProof?.sourcePath || null,
      rawObjectPreview: raw.rawProof?.rawObjectPreview || null,
      rawNameFields: raw.rawProof?.rawNameFields || null,
      metadataKeys: raw.rawProof?.rawObjectPreview?.Metadata
        ? Object.keys(raw.rawProof.rawObjectPreview.Metadata)
        : null,
      catalogMatchedBefore,
      suspectedName: manual?.baseFishName || canon?.baseFishName || null,
      suspectedRarity: manual?.rarity || canon?.rarity || null,
      recoverySource: manual ? 'manual_verified_catalog' : (canon ? 'canonical_catalog' : null),
      confidence: manual?.confidence || canon?.rarityConfidence || null,
      shouldShowPublic: !!pub,
      reason: pub ? 'manual_verified_public_eligible' : 'not_recovered',
      catalogMatchedAfter,
      finalNameBefore: raw.name,
      finalNameAfter: enriched?.baseFishName || enriched?.name || null,
      finalCategoryBefore: raw.category || null,
      finalCategoryAfter: enriched?.category || null,
      rarity: pub?.rarity || enriched?.rarity || manual?.rarity || canon?.rarity || null,
    });
  }
  return proofs;
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
function buildTrackerPageLocals() {
  const build = PUBLIC_API_BUILD;
  return {
    layout: false,
    title: '🎣 Fish It Live Inventory Tracker',
    renderBuild: PUBLIC_RENDER_BUILD,
    publicApiBuild: PUBLIC_API_BUILD,
    blocker10vBuild: build,
    blocker10u6Build: build,
    blocker10u5Build: build,
    blocker10u3u4Build: build,
    blocker10u2Build: build,
    blocker10uBuild: build,
    blocker10tBuild: build,
    blocker10sBuild: build,
    blocker10rBuild: build,
    blocker10qBuild: build,
  };
}

function renderTrackerPage(_req, res) {
  try {
    return res.render('fishit_tracker', buildTrackerPageLocals());
  } catch (err) {
    console.error('[fishit-tracker] /tracker render failed:',
      err && err.stack ? err.stack : err);
    if (!res.headersSent) {
      return res.status(200).render('fishit_tracker', buildTrackerPageLocals());
    }
  }
}

router.get('/tracker', renderTrackerPage);
router.get('/fishit-tracker', renderTrackerPage);

// ── GET /api/fishit-tracker/assets/fish/:filename — local cached fish images (BLOCKER10U) ──
router.get('/api/fishit-tracker/assets/fish/:filename', async (req, res) => {
  const file = path.basename(String(req.params.filename || ''));
  if (!file || !/^[a-zA-Z0-9._-]+$/.test(file)) {
    return res.status(400).type('text/plain').send('invalid_filename');
  }
  const full = path.join(fishImageCache.getCacheDir(), file);
  if (!fs.existsSync(full)) {
    try {
      const repaired = await fishImageCache.repairMissingAssetFile(file);
      if (repaired && fs.existsSync(full)) {
        res.set('Cache-Control', 'public, max-age=86400, immutable');
        return res.sendFile(full);
      }
    } catch (err) {
      console.warn('[fishit] asset repair failed for', file, err && err.message ? err.message : err);
    }
    return res.redirect(302, '/assets/img/fishit/fallback-fish.svg');
  }
  res.set('Cache-Control', 'public, max-age=86400, immutable');
  return res.sendFile(full);
});

// ── GET /api/fishit-tracker/image/:assetId — thumbnail proxy (BLOCKER10N2) ──
router.get('/api/fishit-tracker/image/:assetId', async (req, res) => {
  const assetId = robloxThumbnails.sanitiseAssetId(req.params.assetId);
  if (!assetId) {
    console.warn('[fishit] image proxy invalid_asset_id:', req.params.assetId);
    return res.status(400).type('text/plain').send('invalid_asset_id');
  }
  res.set('Cache-Control', 'public, max-age=3600, stale-while-revalidate=86400');
  const cached = robloxThumbnails.getCached(assetId);
  if (cached && cached.imageUrl) {
    return res.redirect(302, cached.imageUrl);
  }
  try {
    const resolved = await robloxThumbnails.resolveThumbnailUrl(assetId);
    if (resolved.imageUrl) {
      return res.redirect(302, resolved.imageUrl);
    }
    console.warn('[fishit] image proxy unresolved assetId=%s status=%s reason=%s',
      assetId, resolved.imageStatus, resolved.failureReason || 'none');
  } catch (err) {
    console.warn('[fishit] image proxy error assetId=%s:', assetId, err && err.message ? err.message : err);
  }
  return res.redirect(302, '/assets/img/fishit/fallback-fish.svg');
});

// ── GET /api/fishit-tracker/image-debug/:assetId — image pipeline diagnostic ──
router.get('/api/fishit-tracker/image-debug/:assetId', getLimiter, async (req, res) => {
  const assetId = robloxThumbnails.sanitiseAssetId(req.params.assetId);
  if (!assetId) {
    return res.status(400).json({ ok: false, assetId: null, error: 'invalid_asset_id' });
  }
  const baseUrl = `${req.protocol}://${req.get('host')}`;
  const dbg = await robloxThumbnails.debugImageAsset(assetId, baseUrl);
  return res.status(200).json({
    ...dbg,
    publicApiBuild: PUBLIC_API_BUILD,
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
  const out = {
    raw:               num(raw.raw),
    accepted:          num(raw.accepted),
    acceptedInstances: num(raw.acceptedInstances),
    rejected:          num(raw.rejected),
    images:            num(raw.images),
    tiers:             num(raw.tiers),
    selectedPath:      typeof raw.selectedPath === 'string' ? raw.selectedPath.slice(0, 80) : null,
    selectedGeneralPath: typeof raw.selectedGeneralPath === 'string' ? raw.selectedGeneralPath.slice(0, 80) : null,
    selectedFishPath: typeof raw.selectedFishPath === 'string' ? raw.selectedFishPath.slice(0, 80) : null,
    fishPathAccepted: Number.isFinite(Number(raw.fishPathAccepted)) ? Math.floor(Number(raw.fishPathAccepted)) : null,
    fish: Number.isFinite(Number(raw.fish)) ? Math.floor(Number(raw.fish)) : null,
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

function runCatchDeltaOnUpload(body, rawItems, existing, sessionKey) {
  const pending = body.pendingCatchName || body.pendingCatch;
  const prev = body.previousItemCounts || (existing && existing.lastItemCounts) || null;
  if (!pending && !prev) return null;
  const evidenceSourceMode = liveCatchProof.resolveEvidenceSourceMode(body);
  return catchDelta.processCatchDelta({
    pendingCatch: pending,
    previousItemCounts: prev,
    currentItems: rawItems,
    ingestLearned: ingestLearnedFishEntry,
    mainCatalogLookup: (id) => catalogStore.lookupById(id),
    globalContext: {
      enabled: true,
      userId: body.userId,
      userIdHash: globalFishCatalog.hashContributorId(body.userId),
      gameId: body.gameId || body.game_id || null,
      placeId: body.placeId || body.place_id || null,
      gameVersion: body.gameVersion || body.game_version || null,
      evidenceSourceMode,
      sessionKey: sessionKey || null,
    },
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
    let rawItems = normaliseInventoryItems(body);
    const learnedIngest = ingestLearnedFishCatalogFromBody(body);
    const nameCatalogDiscovery = runCatchDeltaOnUpload(body, rawItems, existing, key);
    catalogStore.learnFromTrackerItems(rawItems);
    let cleanItems = mergeItemsNoDowngradeFromCatalog(rawItems);
    if (existing && existing.items && cleanItems.length) {
      cleanItems = mergeItemsNoDowngrade(cleanItems, existing.items);
    }
    let inventory  = buildInventoryGroups(cleanItems);
    const ps         = sanitiseParseStats(body.parseStats);
    const fishPathDiscovery = partialSnapshot.sanitiseFishPathDiscovery(body.fishPathDiscovery
      || body.parseStats?.fishPathDiscovery);

    const priorPublicFishCount = existing?.lastGoodPublicFishCount || 0;
    let partialInfo = partialSnapshot.detectPartialZeroFishSnapshot({
      ps,
      cleanItems,
      existing,
      priorPublicFishCount,
    });
    if (partialInfo.isPartial) {
      const preserved = partialSnapshot.applyPartialSnapshotPreservation({
        cleanItems,
        rawItems,
        inventory,
        existing,
        partialInfo,
      });
      cleanItems = preserved.cleanItems;
      rawItems = preserved.rawItems;
      inventory = preserved.inventory;
      partialInfo = preserved.partialInfo;
      console.log(
        `[fishit-tracker] PARTIAL_SNAPSHOT_PRESERVED user=${cleanUser} reason=${partialInfo.partialSnapshotReason}` +
        ` prevGood=${partialInfo.previousGoodFishCount} accepted=${partialInfo.currentRawAccepted}`,
      );
    }

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

    const hasDisplayItems = cleanItems.length > 0
      || (partialInfo.lastGoodFishPreserved && existing && existing.items?.length);
    const sessionPhase = partialInfo.lastGoodFishPreserved
      ? 'live'
      : effectivePhase(phase, ps, hasDisplayItems);

    // Store under username key + userId alias.
    liveTrackDB[key] = {
      username:        cleanUser,
      userId:          cleanUserId,
      source,
      rawItems:        rawItems.length ? rawItems : (existing ? existing.rawItems : []),
      items:           cleanItems.length ? cleanItems : (existing ? existing.items     : []),
      inventory:       cleanItems.length ? inventory  : (existing ? existing.inventory : null),
      isOnline:        online,
      phase:           sessionPhase,
      parseStats:      ps || (existing && existing.parseStats) || null,
      fishPathDiscovery: fishPathDiscovery || (existing && existing.fishPathDiscovery) || null,
      trackerBuild:    sanitiseTrackerBuild(body.trackerBuild) || (existing && existing.trackerBuild) || null,
      lastPayloadType: cleanItems.length ? 'inventory_snapshot' : (type || 'inventory_snapshot'),
      lastSeenAt:      now,
      lastInventoryAt: now,
      updatedAt:       now,
      partialSnapshotDetected: partialInfo.partialSnapshotDetected || false,
      partialSnapshotReason: partialInfo.partialSnapshotReason || null,
      lastGoodFishPreserved: partialInfo.lastGoodFishPreserved || false,
      partialSnapshotMeta: partialInfo.isPartial ? {
        currentRawAccepted: partialInfo.currentRawAccepted,
        previousGoodFishCount: partialInfo.previousGoodFishCount,
        selectedPath: partialInfo.selectedPath,
        selectedFishPath: partialInfo.selectedFishPath,
      } : (existing && existing.partialSnapshotMeta) || null,
      lastGoodFishItems: existing?.lastGoodFishItems || null,
      lastGoodRawItems: existing?.lastGoodRawItems || null,
      lastGoodInventory: existing?.lastGoodInventory || null,
      lastGoodPublicFishCount: existing?.lastGoodPublicFishCount || 0,
      catchWatcherStatus: body.catchWatcherStatus || (existing && existing.catchWatcherStatus) || null,
      bagInstanceCount: Number.isFinite(Number(body.bagInstanceCount))
        ? Number(body.bagInstanceCount)
        : (ps?.acceptedInstances ?? existing?.bagInstanceCount ?? null),
      trackerClientProof: body.trackerClientProof && typeof body.trackerClientProof === 'object'
        ? {
          trackerBuild: sanitiseTrackerBuild(body.trackerClientProof.trackerBuild) || null,
          uploadedAt: body.trackerClientProof.uploadedAt || now,
          supportsBagInstanceCount: body.trackerClientProof.supportsBagInstanceCount === true,
          noHeavyScanner: body.trackerClientProof.noHeavyScanner !== false,
          replionSourceOfTruth: true,
        }
        : (existing?.trackerClientProof || null),
    };

    const enrichedForGood = enrichItemsFromCatalog(liveTrackDB[key].items);
    recordGlobalObservationsFromItems(enrichedForGood, {
      userId: cleanUserId,
      sessionKey: key,
      gameId: body.gameId || null,
      placeId: body.placeId || null,
    });
    const publicFishCount = enrichedForGood.filter(isPublicFishItem).length;
    partialSnapshot.updateLastGoodFishOnSession(
      liveTrackDB[key],
      cleanItems,
      publicFishCount,
      partialInfo,
    );
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

    const persistBase = `${req.protocol}://${req.get('host')}`;
    persistSessionState(key, persistBase).catch(() => {});

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
async function handleGetBackpack(req, res) {
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

  // Enrich from raw tracker payload when available (BLOCKER10I/10S).
  const sourceItems = partialSnapshot.itemsForSessionDisplay(data);
  const enrichedFlat = enrichItemsFromCatalog(sourceItems);
  const enrichedInventory = buildInventoryGroups(enrichedFlat);
  const rawInventory = buildInventoryGroups(sourceItems);
  const countsRaw = inventoryCountsFromGroups(rawInventory);
  const countsEnriched = inventoryCountsFromGroups(enrichedInventory);
  const baseUrl = `${req.protocol}://${req.get('host')}`;
  const publicFish = await buildPublicFishFields(enrichedFlat, baseUrl, { sessionData: data });
  const imageResolutionProof = fishImageAssets.buildImageResolutionProof(publicFish.fishItems);

  const fishCatalogStats = fishCatalog.getStats();

  const enriched = {
    ...data,
    renderBuild:     PUBLIC_RENDER_BUILD,
    publicApiBuild:  PUBLIC_API_BUILD,
    trackerBuild:    data.trackerBuild || null,
    items:           publicFish.fishItems,
    inventory:       publicFish.fishInventory,
    counts:          buildPublicLegacyCounts(publicFish.fishCounts),
    fishItems:       publicFish.fishItems,
    publicItems:     publicFish.publicItems,
    publicFishItems: publicFish.publicFishItems,
    fishInventory:   publicFish.fishInventory,
    fishCounts:      publicFish.fishCounts,
    publicCounts:    publicFish.publicCounts,
    hiddenPublicRows: publicFish.hiddenPublicRows,
    quarantinedPublicNames: publicFish.quarantinedPublicNames,
    missingExpectedFishProof: publicFish.missingExpectedFishProof,
    countParityProof: publicFish.countParityProof,
    rarityColorProof: publicFish.rarityColorProof,
    globalDbUiProof: publicFish.globalDbUiProof,
    globalCatalogProof: globalCatalogService.buildGlobalDbSummaryProof(),
    globalImageProof: globalCatalogService.buildGlobalImageProof(publicFish.fishItems),
    globalRarityProof: globalCatalogService.buildGlobalRarityProof(publicFish.fishItems),
    imageRenderProof: fishImageCache.buildImageRenderProof(publicFish.fishItems, 20),
    imageResolutionProof,
    fishCatalogTotal: fishCatalogStats.fishCatalogTotal,
    fishCatalogWithImages: fishCatalogStats.fishCatalogWithImages,
    fishCatalogWithRarity: fishCatalogStats.fishCatalogWithRarity,
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
router.get('/api/fishit-tracker/debug/:username', getLimiter, async (req, res) => {
  const cleanUser = sanitiseUsername(req.params.username);
  if (!cleanUser) return res.status(400).json({ ok: false, error: 'Invalid username.' });

  const key   = cleanUser.toLowerCase();
  const data  = liveTrackDB[key];

  if (!data) {
    // Enumerate known keys (usernames only, strip uid: aliases and limit count).
    const knownKeys = Object.keys(liveTrackDB)
      .filter((k) => !k.startsWith('uid:'))
      .slice(0, 100);
    return res.status(404).json({ ok: false, error: 'not_found', key, knownKeys, serverCommit: resolveServerCommit() });
  }

  const rawItemsArr = partialSnapshot.itemsForSessionDisplay(data);
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
  const baseUrl = `${req.protocol}://${req.get('host')}`;
  const publicFishDbg = await buildPublicFishFields(enrichedAll, baseUrl, { sessionData: data });
  const imageResolutionProof = fishImageAssets.buildImageResolutionProof(publicFishDbg.fishItems);
  const fishCatalogStats = fishCatalog.getStats();
  const imageCacheStats = fishImageCache.getImageCacheStats();
  const rarityStats = rarityEnrichment.getRarityStats(publicFishDbg.fishItems);

  const diags = Array.isArray(data.unresolvedDiagnostics) ? data.unresolvedDiagnostics : [];
  const unresolvedIds = diags.filter((d) => d && !d.found).map((d) => d.id);
  const resolvedFromDiag = diags.filter((d) => d && d.found).map((d) => ({
    id: d.id,
    name: (d.candidateKeys && d.candidateKeys[0]) || null,
    path: d.candidatePath || null,
  }));

  return res.status(200).json({
    ok:              true,
    serverCommit:    resolveServerCommit(),
    publicApiBuild:  PUBLIC_API_BUILD,
    renderBuild:     PUBLIC_RENDER_BUILD,
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
    catalogPolish: catalogPolish.getCatalogPolishStats(imageCacheStats, rarityStats),
    nameNormalizationProof: catalogPolish.getNameNormalizationProof(25),
    imageCacheProof: fishImageCache.getImageCacheProof(25),
    imageSourceProof: fishImageCache.buildImageSourceProof(publicFishDbg.fishItems),
    quizBotImageCatalog: quizBotImageCatalog.getCatalogMeta(),
    quizBotImageAudit: quizBotImageCatalog.auditNames(
      publicFishDbg.fishItems.map((f) => f.baseFishName || f.name).filter(Boolean),
    ),
    globalCatalogProof: globalCatalogService.buildGlobalDbSummaryProof(),
    globalCatalogItemProof: globalCatalogService.buildGlobalCatalogProof(publicFishDbg.fishItems),
    globalImageProof: globalCatalogService.buildGlobalImageProof(publicFishDbg.fishItems),
    imageRenderProof: fishImageCache.buildImageRenderProof(publicFishDbg.fishItems, 15),
    flickerProof: fishImageCache.FLICKER_PROOF,
    countParityProof: publicFishDbg.countParityProof,
    replionCountProof: buildReplionCountProof(publicFishDbg.countParityProof),
    catchLearningProof: buildCatchLearningProof(data, data.nameCatalogDiscovery),
    amountProof: publicFishDbg.amountProof || buildAmountProof(publicFishDbg.fishItems, enrichedAll),
    ambiguousContainerIds: [...AMBIGUOUS_CONTAINER_IDS],
    ambiguousContainerProof: buildAmbiguousContainerProof(enrichedAll, data),
    hiddenPublicRows: publicFishDbg.hiddenPublicRows,
    quarantinedPublicNames: publicFishDbg.quarantinedPublicNames,
    missingExpectedFishProof: publicFishDbg.missingExpectedFishProof,
    publicCounts: publicFishDbg.publicCounts,
    rarityColorProof: publicFishDbg.rarityColorProof,
    globalDbUiProof: publicFishDbg.globalDbUiProof,
    unmappedReviewProof: buildUnmappedReviewProof(enrichedAll),
    trackerClientProof: buildTrackerClientProof(data),
    globalRarityProof: globalCatalogService.buildGlobalRarityProof(publicFishDbg.fishItems),
    globalEvidenceProof: globalCatalogService.buildGlobalEvidenceProof(15),
    globalConflictProof: globalCatalogService.buildGlobalConflictProof(15),
    globalContributionProof: globalCatalogService.buildGlobalContributionProof(),
    quizBotSeedImportProof: globalCatalogService.buildQuizBotSeedImportProof(),
    globalDbStats: globalDb.getStats(),
    publicFilterTrace: buildPublicFilterTrace(enrichedAll),
    inventoryParityProof: buildInventoryParityProof(
      rawItemsArr,
      enrichedAll,
      publicFishDbg.fishItems,
      data,
    ),
    rarityResolutionProof: rarityEnrichment.getRarityResolutionProof(25),
    raritySourcesUsed: rarityStats.raritySourcesUsed,
    rarityCatalogCount: rarityStats.rarityCatalogCount,
    publicFishItems: publicFishDbg.fishItems.slice(0, 10),
    fishImageAssetCatalogCount: fishImageAssets.getCatalogEntryCount(),
    fishCatalogTotal: fishCatalogStats.fishCatalogTotal,
    fishCatalogWithImages: fishCatalogStats.fishCatalogWithImages,
    fishCatalogWithRarity: fishCatalogStats.fishCatalogWithRarity,
    fishCatalogSources: fishCatalogStats.fishCatalogSources,
    missingImageFishIds: fishCatalogStats.missingImageFishIds,
    missingRarityFishIds: fishCatalogStats.missingRarityFishIds,
    unresolvedDiagnostics: diags.length ? diags : null,
    unresolvedIds,
    stillUnresolvedIds: unresolvedIds,
    resolvedFromDiagnostics: resolvedFromDiag.length ? resolvedFromDiag : null,
    discoveredCatalogIngest: data.discoveredCatalogIngest || null,
    nameCatalogDiscovery: catchDelta.buildNameCatalogDiscoveryForDebug(
      data.nameCatalogDiscovery,
      learnedFishCatalog,
      data,
    ),
    learnedFishCatalogCount: learnedFishCatalog.getAllMappings().length,
    learningValidation: nameOnlyCatalog.buildLearningValidation(learnedFishCatalog, globalFishCatalog),
    globalCatalog: globalFishCatalog.getStats(),
    globalCatalogForItems: globalFishCatalog.catalogMapForItemIds(
      enrichedAll.map((i) => i && i.itemId).filter(Boolean).slice(0, 30),
    ),
    liveCatchBinding: globalFishCatalog.buildLiveCatchBinding(data.nameCatalogDiscovery),
    evidenceSourceDebug: liveCatchProof.buildEvidenceSourceDebug(
      globalFishCatalog.getStoreMeta(),
      data.nameCatalogDiscovery,
    ),
    newUnresolvedBindingProof: liveCatchProof.buildNewUnresolvedBindingProof(
      data.nameCatalogDiscovery,
      key,
      (id) => globalFishCatalog.lookupById(id),
    ),
    staticCatalogAudit: staticCatalogAudit.auditStaticCatalogSources(),
    partialSnapshotDetected: !!data.partialSnapshotDetected,
    lastGoodFishPreserved: !!data.lastGoodFishPreserved,
    partialSnapshotReason: data.partialSnapshotReason || null,
    partialSnapshotMeta: data.partialSnapshotMeta || null,
    selectedGeneralInventoryPath: data.parseStats?.selectedGeneralPath
      || data.parseStats?.selectedPath || null,
    selectedFishInventoryPath: data.parseStats?.selectedFishPath
      || data.fishPathDiscovery?.selectedFishPath || null,
    fishPathDiscovery: data.fishPathDiscovery || data.parseStats?.fishPathDiscovery || null,
    emptyPublicFishReason: publicFishDbg.publicItems.length === 0
      ? (data.partialSnapshotDetected
        ? data.partialSnapshotReason
        : (data.parseStats?.fish === 0 ? 'parse_stats_fish_zero' : 'no_public_fish_items'))
      : null,
    catchWatcherStatus: data.catchWatcherStatus || null,
    lastGoodPublicFishCount: data.lastGoodPublicFishCount || 0,
    lastCatchParsed: data.nameCatalogDiscovery?.lastCatchParsed
      || data.lastCatchParsed || null,
    publicNameContractProof: catalogPolish.buildPublicNameContractProof(publicFishDbg.fishItems, 25),
    missingFishRecoveryProof: buildMissingFishRecoveryProof(
      rawItemsArr,
      enrichedAll,
      publicFishDbg.fishItems,
    ),
    manualVerifiedCatalogCount: manualVerifiedCatalog.getCount(),
    knownRarityCount: rarityStats.knownCount,
    publicFishContainsGiantSquid: publicFishDbg.fishItems.some(
      (f) => String(f.itemId) === '156' || String(f.baseFishName || f.name).toLowerCase() === 'giant squid',
    ),
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
module.exports.isPublicFishCardVisible = isPublicFishCardVisible;
module.exports.applyPublicCosmeticCleanup = applyPublicCosmeticCleanup;
module.exports.stripHiddenPublicCosmeticPrefix = stripHiddenPublicCosmeticPrefix;
module.exports.buildHiddenPublicRows = buildHiddenPublicRows;
module.exports.HIDDEN_PUBLIC_COSMETIC_TAGS = HIDDEN_PUBLIC_COSMETIC_TAGS;
module.exports.isLikelyFishInventoryItem = isLikelyFishInventoryItem;
module.exports.buildPublicFilterTrace = buildPublicFilterTrace;
module.exports.buildInventoryParityProof = buildInventoryParityProof;
module.exports.buildCountParityProof = buildCountParityProof;
module.exports.buildUnmappedReviewProof = buildUnmappedReviewProof;
module.exports.buildRarityColorProof = buildRarityColorProof;
module.exports.buildAmountProof = buildAmountProof;
module.exports.extractReplionAmount = extractReplionAmount;
module.exports.extractReplionIdentityFields = extractReplionIdentityFields;
module.exports.annotateReplionIdentity = annotateReplionIdentity;
module.exports.isReplionUuidInstance = isReplionUuidInstance;
module.exports.hasReplionMetadataIdentity = hasReplionMetadataIdentity;
module.exports.buildReplionCountProof = buildReplionCountProof;
module.exports.buildCatchLearningProof = buildCatchLearningProof;
module.exports._itemIdLockedBaseName = _itemIdLockedBaseName;
module.exports.buildTrackerClientProof = buildTrackerClientProof;
module.exports.explainPublicExclusionReason = explainPublicExclusionReason;
module.exports.PUBLIC_API_BUILD = PUBLIC_API_BUILD;
module.exports.isAmbiguousContainerItem = isAmbiguousContainerItem;
module.exports.buildAmbiguousContainerProof = buildAmbiguousContainerProof;
module.exports.AMBIGUOUS_CONTAINER_IDS = AMBIGUOUS_CONTAINER_IDS;
module.exports.resolveAmbiguousContainerDisplay = resolveAmbiguousContainerDisplay;
module.exports.trustedCatalogMetaForMetadataId = trustedCatalogMetaForMetadataId;
module.exports.BLOCKER10Z9_BUILD = BLOCKER10Z9_BUILD;
module.exports.BLOCKER10Z8_BUILD = BLOCKER10Z9_BUILD;
module.exports.isTrustedPublicNameSource = isTrustedPublicNameSource;
module.exports.isSnapshotBackedPublicCard = isSnapshotBackedPublicCard;
module.exports.buildPublicIdentityProof = buildPublicIdentityProof;
module.exports.isContestedCatalogItemId = isContestedCatalogItemId;
module.exports.promoteTrustedAmbiguousContainerRows = promoteTrustedAmbiguousContainerRows;
module.exports.buildQuarantinedPublicNames = buildQuarantinedPublicNames;
module.exports.buildMissingExpectedFishProof = buildMissingExpectedFishProof;
module.exports.isTrustedRadiantCatfishInCatalog = isTrustedRadiantCatfishInCatalog;
module.exports.BLOCKER10Z7_BUILD = BLOCKER10Z9_BUILD;
module.exports.renderTrackerPage = renderTrackerPage;
module.exports.buildTrackerPageLocals = buildTrackerPageLocals;
module.exports.BLOCKER10V_BUILD = PUBLIC_API_BUILD;
module.exports.BLOCKER10U6_BUILD = PUBLIC_API_BUILD;
module.exports.BLOCKER10U5_BUILD = PUBLIC_API_BUILD;
module.exports.BLOCKER10U3_U4_BUILD = PUBLIC_API_BUILD;
module.exports.BLOCKER10U2_BUILD = PUBLIC_API_BUILD;
module.exports.BLOCKER10U_BUILD = PUBLIC_API_BUILD;
module.exports.BLOCKER10T_BUILD = PUBLIC_API_BUILD;
module.exports.BLOCKER10S_BUILD = PUBLIC_API_BUILD;
module.exports.BLOCKER10R_BUILD = PUBLIC_API_BUILD;
module.exports.BLOCKER10Q_BUILD = PUBLIC_API_BUILD;
module.exports.BLOCKER10P_BUILD = PUBLIC_API_BUILD;
module.exports.BLOCKER10O_BUILD = PUBLIC_API_BUILD;
module.exports.BLOCKER10N2_BUILD = PUBLIC_API_BUILD;
module.exports.BLOCKER10N_BUILD = PUBLIC_API_BUILD;
module.exports.globalCatalogService = globalCatalogService;
module.exports.globalDb = globalDb;
module.exports.persistSessionState = persistSessionState;
module.exports.sessionStore = sessionStore;
module.exports.canonicalCatalog = canonicalCatalog;
module.exports.ingestLearnedFishEntry = ingestLearnedFishEntry;
module.exports.runCatchDeltaOnUpload = runCatchDeltaOnUpload;
module.exports.catalogMetaForItemId = catalogMetaForItemId;
