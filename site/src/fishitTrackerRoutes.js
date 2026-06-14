'use strict';
/**
 * Fish It Backpack Tracker – API routes + dashboard page.
 *
 * Public routes (no authentication required):
 *   GET  /tracker                     – serve the live tracker + inventory dashboard UI
 *   GET  /tracker.lua                   – redirect to latest cache-busted dist/tracker.lua
 *   GET  /inventory, /fishit-tracker    – 301 redirect to /tracker (legacy aliases)
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
const path      = require('path');
const fs        = require('fs');
const crypto    = require('crypto');
const { execFileSync } = require('child_process');
const { createUserRateLimit } = require('./rateLimitUtils');
const { trackerUploadCoalesceMiddleware } = require('./trackerUploadCoalesce');
const { safeTrackerUploadHandler } = require('./trackerUploadSafeHandler');
const { safeOptionalWeight, isUsableUploadRow } = require('./fishitUploadRowSafety');
const trackerConcurrencyGate = require('./trackerConcurrencyGate');
const { finishTrackerUploadResponse } = require('./trackerUploadResponse');
const { recordUploadRequest } = require('./trackerUploadRequestMetrics');
const { buildTrackerAccountSummary } = require('./trackerAccountSummary');
const {
  ACCOUNT_PRESENCE_GRACE_MS,
  deriveAccountPresenceStatus,
  resolveLastAccountSeenAt,
} = require('./trackerAccountPresence');

const catalogStore = require('./fishitCatalogStore');
const playerStatsStore = require('./fishitPlayerStats');
const leaderstatsUpload = require('./fishitLeaderstatsUpload');
const liveTrackerSerializer = require('./fishitLiveTrackerSerializer');
const fishImageAssets = require('./fishitFishImageAssets');
const learnedFishCatalog = require('./fishitLearnedFishCatalog');
const catchDelta = require('./fishitCatalogCatchDelta');
const fishCatalog = require('./fishitFishCatalog');
const robloxThumbnails = require('./fishitRobloxThumbnails');
const staticCatalogAudit = require('./fishitStaticCatalogAudit');
const nameOnlyCatalog = require('./fishitNameOnlyCatalog');
const rarityLabels = require('./fishitRarityLabels');
const protectedFishNames = require('./fishitProtectedFishNames');
const globalFishCatalog = require('./fishitGlobalFishItemCatalog');
const liveCatchProof = require('./fishitLiveCatchProof');
const partialSnapshot = require('./fishitPartialSnapshot');
const snapshotRecovery = require('./fishitSnapshotRecovery');
const { BLOCKER10ZP_RARITY_MAPPING_AND_TRANSCENDED_STONE_IMAGE_FIX_MARKER, BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, EXPECTED_CLIENT_TRACKER_BUILD, ALLOWED_TRACKER_BUILD_EXACT, isAllowedTrackerBuild, BLOCKER10ZK_BUILD, BLOCKER10ZK_UI_MARKER, BLOCKER10ZJ_BUILD, BLOCKER10ZJ_UI_MARKER, BLOCKER10ZI_BUILD, BLOCKER10ZI_UI_MARKER, BLOCKER10ZH_BUILD, BLOCKER10ZH_UI_MARKER, BLOCKER10ZG_BUILD, BLOCKER10ZG_UI_MARKER, BLOCKER10ZF_BUILD, BLOCKER10ZF_UI_MARKER, BLOCKER10ZE_BUILD, BLOCKER10ZE_UI_MARKER, BLOCKER10ZD_BUILD, BLOCKER10ZD_UI_MARKER, BLOCKER10ZA_BUILD, BLOCKER10ZA_UI_MARKER, BLOCKER10Z18_BUILD, BLOCKER10Z18_UI_MARKER, BLOCKER10Z17_BUILD, BLOCKER10Z17_UI_MARKER, BLOCKER10Z16_BUILD, BLOCKER10Z16_UI_MARKER, BLOCKER10Z15_BUILD, BLOCKER10Z15_UI_MARKER, BLOCKER10Z14_BUILD, BLOCKER10Z14_UI_MARKER, BLOCKER10Z13_BUILD, BLOCKER10Z13_UI_MARKER } = require('./fishitTrackerBuild');
const trackerRarityStyle = require('./fishitTrackerRarityStyle');
const fishitStoneDisplayMap = require('./fishitStoneDisplayMap');
const stoneImageAssets = require('./fishitStoneImageAssets');
const totemImageAssets = require('./fishitTotemImageAssets');
const manualInventoryImages = require('./fishitInventoryManualImages');
const inventorySort = require('./fishitInventorySort');
const inventoryAssets = require('./inventoryAssets');
const inventoryTrackedAccounts = require('./inventoryTrackedAccounts');
const aioDatasetCache = require('./aioDatasetCache');
const inventorySession = require('./inventorySession');
const aioSessionStore = require('./aioSessionStore');
const supabase = require('./db');
const { internalApiBaseUrl } = require('./publicDomain');
const {
  CLEAN_TRACKER_LOADSTRING,
  DEBUG_TRACKER_LOADSTRING,
  PROTECTED_TRACKER_REL_PATH,
  PROTECTED_TRACKER_RAW_URL,
  PROTECTED_TRACKER_RAW_URL_CACHE_BUST,
} = require('./fishitTrackerLoadstring');
const {
  validateTrackerClientProof,
  prepareTrackerRequestBody,
  MINIMUM_TRACKER_BUILD,
  ALLOWED_TRACKER_CHANNEL,
  ALLOWED_TRACKER_RAW_URL,
} = require('./fishitTrackerChannelEnforcement');
const itemUtilityPublic = require('./fishitItemUtilityPublic');
const gameItemDbPublic = require('./fishitGameItemDbPublic');
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
const { computeCanonicalTrackerUsers } = require('./fishitCanonicalTrackerUsers');
const uploadAccountStatus = require('./fishitTrackerUploadStatus');
const snapshotCompleteness = require('./fishitSnapshotCompleteness');
const trackerPerf = require('./fishitTrackerPerformance');
const compactUpload = require('./fishitTrackerCompactUpload');

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

function isLiveRobloxUpload(body) {
  return body && (body.clientOrigin === 'roblox_tracker' || body.evidenceSourceMode === 'live_roblox');
}

function resolvePlayerStatsForApi(raw) {
  return playerStatsStore.normalizePlayerStatsForApi(raw);
}

function isTrustedClientBuild(build) {
  return isAllowedTrackerBuild(build);
}

function applySessionOwnerMapping(key) {
  const ownerId = inventoryTrackedAccounts.resolveOwnerDiscordIdForUsernameSync(key);
  if (!liveTrackDB[key]) {
    return {
      bound: false,
      ownerId: ownerId || null,
      reason: ownerId ? 'session_missing' : 'owner_not_registered',
    };
  }
  if (!ownerId) {
    return { bound: false, ownerId: null, reason: 'owner_not_registered' };
  }
  liveTrackDB[key].discordOwnerId = ownerId;
  return { bound: true, ownerId, reason: 'registered_account_match' };
}

function buildUploadProofLogFields(body, session, ctx = {}) {
  const proof = snapshotCompleteness.extractClientSnapshotProof(body || {});
  const completeness = ctx.completenessEval || null;
  const sessionSnap = session && typeof session === 'object' ? session : {};
  const bool = (value) => value === true;
  return {
    usernameKey: ctx.usernameKey || sessionSnap.username || body?.username || null,
    discordOwnerId: sessionSnap.discordOwnerId || body?.discordOwnerId || null,
    payloadType: body?.type || body?.payloadType || ctx.payloadType || 'inventory_snapshot',
    phase: body?.phase || sessionSnap.phase || null,
    build: sanitiseTrackerBuild(body?.trackerBuild) || sessionSnap.trackerBuild || null,
    hasLeaderstatsSnapshot: bool(completeness?.hasLeaderstatsSnapshot)
      || bool(sessionSnap.hasLeaderstatsSnapshot)
      || bool(proof.leaderstatsReady),
    hasFishSnapshot: bool(completeness?.hasFishSnapshot)
      || bool(sessionSnap.hasFishSnapshot)
      || bool(proof.fishScanReady),
    hasStoneSnapshot: bool(completeness?.hasStoneSnapshot)
      || bool(sessionSnap.hasStoneSnapshot)
      || bool(proof.stoneScanReady),
    snapshotComplete: bool(completeness?.snapshotComplete) || bool(sessionSnap.snapshotComplete),
    inventoryReady: bool(completeness?.inventoryReady) || bool(sessionSnap.inventoryReady),
    accepted: ctx.accepted === true,
    rejectReason: ctx.rejectReason || sessionSnap.lastUploadRejectReason || null,
    ownerBinding: ctx.ownerBinding || null,
  };
}

function logTrackerUploadProof(stage, fields) {
  if (!fields || typeof fields !== 'object') return;
  console.log(
    '[fishit-tracker] upload_proof stage=%s usernameKey=%s discordOwnerId=%s payloadType=%s phase=%s build=%s' +
    ' hasLeaderstatsSnapshot=%s hasFishSnapshot=%s hasStoneSnapshot=%s snapshotComplete=%s inventoryReady=%s' +
    ' accepted=%s rejectReason=%s ownerBinding=%s',
    stage,
    fields.usernameKey || '?',
    fields.discordOwnerId || 'null',
    fields.payloadType || '?',
    fields.phase || 'n/a',
    fields.build || 'n/a',
    fields.hasLeaderstatsSnapshot ? 1 : 0,
    fields.hasFishSnapshot ? 1 : 0,
    fields.hasStoneSnapshot ? 1 : 0,
    fields.snapshotComplete ? 1 : 0,
    fields.inventoryReady ? 1 : 0,
    fields.accepted ? 1 : 0,
    fields.rejectReason || 'none',
    fields.ownerBinding || 'none',
  );
}

function buildPlayerStatsProof(raw, data, nowFallback) {
  const connected = deriveAccountPresenceStatus(data).accountPresenceLive === true;
  const acceptedUploadFresh = connected && !!(data && data.lastUploadAcceptedAt);
  const displayable = resolvePlayerStatsForApi(raw);
  const trusted = playerStatsStore.isTrustedPlayerStats(raw);
  const hasDebug = !!(data && data.playerStatsDebug && data.playerStatsDebug.enabled);
  const build = (displayable && displayable.build) || data?.trackerBuild || null;

  if (!connected) {
    return {
      proven: false,
      connected: false,
      acceptedUploadFresh: false,
      reason: 'tracker_disconnected',
      trackerBuild: build,
      lastUploadAt: data?.playerStatsUpdatedAt || data?.lastInventoryAt || data?.updatedAt
        || data?.lastSeenAt || nowFallback || null,
    };
  }

  if (!trusted || !playerStatsStore.hasPlayerStatValues(displayable) || displayable?.source === 'missing') {
    let reason = 'missing_real_stats_source';
    if (build && !isTrustedClientBuild(build)) reason = 'old_client_build';
    return {
      proven: false,
      connected: true,
      acceptedUploadFresh,
      reason,
      trackerBuild: build,
      playerStatsDebug: hasDebug ? data.playerStatsDebug : null,
      lastUploadAt: data?.playerStatsUpdatedAt || data?.lastInventoryAt || data?.updatedAt
        || data?.lastSeenAt || nowFallback || null,
    };
  }

  const provenSource = displayable.source === 'replion'
    ? 'real_replion'
    : (displayable.source === 'leaderstats' ? 'real_leaderstats' : null);
  if (!provenSource) {
    return {
      proven: false,
      connected: true,
      acceptedUploadFresh,
      reason: 'missing_real_stats_source',
      trackerBuild: build,
      lastUploadAt: data?.playerStatsUpdatedAt || data?.lastInventoryAt || data?.updatedAt
        || data?.lastSeenAt || nowFallback || null,
    };
  }

  return {
    proven: true,
    connected: true,
    acceptedUploadFresh,
    source: provenSource,
    coins: displayable.coins != null ? displayable.coins : null,
    level: null,
    rod: null,
    coinsText: displayable.coinsText || null,
    totalCaughtText: displayable.totalCaughtText || null,
    totalCaught: displayable.totalCaught != null ? displayable.totalCaught : null,
    rarestFishChance: displayable.rarestFishChance || null,
    statsAt: displayable.statsAt || null,
    observedAt: displayable.observedAt || null,
    trackerBuild: build,
    lastUploadAt: data?.playerStatsUpdatedAt || data?.lastInventoryAt || data?.updatedAt
      || data?.lastSeenAt || nowFallback || null,
    playerStatsDebug: hasDebug ? data.playerStatsDebug : null,
  };
}

// Indicator 2 (Stats timer): true when any tracked stat value
// (Total Caught / Coin / Rarest Fish) differs between the previous and the
// current upload. The timer resets only when one of these actually changes.
function statsValuesChanged(prevStats, nextStats) {
  if (!nextStats) return false;
  if (!prevStats) return true;
  const norm = (s) => ({
    totalCaught: s.totalCaught != null ? String(s.totalCaught) : (s.totalCaughtText || null),
    coins: s.coins != null ? String(s.coins) : (s.coinsText || null),
    rarestFishChance: s.rarestFishChance || null,
  });
  const a = norm(prevStats);
  const b = norm(nextStats);
  return a.totalCaught !== b.totalCaught
    || a.coins !== b.coins
    || a.rarestFishChance !== b.rarestFishChance;
}

function applyPlayerStatsFields(existing, body, now, opts = {}) {
  if (opts.isHeartbeat === true) {
    return leaderstatsUpload.applyLeaderstatsUploadFields(existing, body, now, { isHeartbeat: true });
  }
  return leaderstatsUpload.applyLeaderstatsUploadFields(existing, body, now);
}

// Optional Fish It DB image resolver (real fish artwork). Loaded lazily and
// defensively so the tracker keeps working even if the DB module is absent.
let fishitDb = null;
try {
  fishitDb = require('./fishitDb');
  if (fishitDb && typeof fishitDb.getDbConnectionInfo === 'function') {
    const dbBoot = fishitDb.getDbConnectionInfo();
    console.log('[fishit-tracker] catch DB startup', JSON.stringify({
      dbPath: dbBoot.dbPath,
      exists: dbBoot.exists,
      readable: dbBoot.readable,
      hasFishCache: dbBoot.hasFishCache,
      fishCacheBytes: dbBoot.fishCacheBytes,
      error: dbBoot.error,
      marker: BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
    }));
  }
} catch (err) {
  console.error('[fishit-tracker] fishitDb load failed:', err && err.message ? err.message : err);
  fishitDb = null;
}

try {
  const restoredManual = manualInventoryImages.ensureOverrideFilesFromSeed();
  if (restoredManual > 0) {
    console.log('[fishit-tracker] manual_image_seed_restored count=%d', restoredManual);
  }
} catch (err) {
  console.error('[fishit-tracker] manual image seed restore failed:', err && err.message ? err.message : err);
}

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
        metadataFishName: it.metadataFishName,
        metadataFishId: it.metadataFishId,
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
const CANONICAL_TRACKER_PATH = '/tracker';

function requireInventorySession(req, res, next) {
  if (process.env.NODE_ENV === 'test') return next();
  if (inventorySession.hasInventorySession(req)) return next();
  const returnTo = encodeURIComponent(req.originalUrl || CANONICAL_TRACKER_PATH);
  if (req.session) {
    req.session.flash = { error: 'Please login with Discord first.' };
  }
  return res.redirect(302, `/login?return=${returnTo}`);
}

async function resolveInventoryOwnerId(req) {
  const discordId = inventorySession.getInventoryDiscordUserId(req);
  if (discordId) return discordId;
  const header = req.headers.authorization || '';
  const match = /^Bearer\s+(.+)$/i.exec(header);
  if (!match) return null;
  const token = String(match[1]).trim();
  // DENG AIO APK session token (file-backed, no Supabase dependency).
  try {
    const aio = aioSessionStore.resolveSession(token);
    if (aio && aio.discordUserId) return String(aio.discordUserId);
  } catch (_) { /* fall through */ }
  // Legacy monitor pairing app-session token.
  try {
    const tokenHash = crypto.createHash('sha256').update(token).digest('hex');
    const { data: row } = await supabase
      .from('monitor_app_sessions')
      .select('owner_discord_user_id, expires_at, revoked_at')
      .eq('token_hash', tokenHash)
      .maybeSingle();
    if (row && !row.revoked_at && row.owner_discord_user_id
        && new Date(row.expires_at).getTime() > Date.now()) {
      return String(row.owner_discord_user_id);
    }
  } catch (_) { /* fall through */ }
  return null;
}

async function repairInventorySessionMiddleware(req, _res, next) {
  try {
    await inventorySession.repairInventorySession(req);
  } catch (_) { /* non-blocking */ }
  return next();
}

async function requireInventoryApiAuth(req, res, next) {
  // Accept either the website cookie session OR an APK bearer token (DENG AIO
  // session / legacy monitor app session). Bearer access is always scoped to
  // the resolved Discord user, so a token can never read another user's data.
  let ownerId = null;
  try {
    ownerId = await resolveInventoryOwnerId(req);
  } catch (_) { ownerId = null; }
  if (!ownerId) {
    inventorySession.logInventoryAccountsAction(req, 'auth_rejected', {
      reason: 'missing_discord_user_id',
      hasSessionUser: !!inventorySession.getSessionUser(req),
      sessionKeys: req.session ? Object.keys(req.session) : [],
    });
    return res.status(401).json({
      ok: false,
      error: 'auth_required',
      message: 'Login with Discord first.',
    });
  }
  req.inventoryOwnerDiscordId = ownerId;
  req.inventoryOwnerSiteUserId = inventorySession.getInventorySiteUserId(req);
  return next();
}

function requireInventoryApiCsrf(req, res, next) {
  if (process.env.NODE_ENV === 'test') return next();
  if (!inventorySession.verifyInventoryCsrf(req)) {
    inventorySession.logInventoryAccountsAction(req, 'csrf_rejected', {
      headerToken: req.headers['x-csrf-token'] ? 'present' : 'missing',
      sessionToken: inventorySession.getInventoryCsrfToken(req) ? 'present' : 'missing',
      sessionKeys: req.session ? Object.keys(req.session) : [],
      csrfMatch: false,
    });
    return res.status(403).json({
      ok: false,
      error: 'invalid_csrf',
      message: 'Invalid request token. Refresh the page and try again.',
    });
  }
  return next();
}

function inventoryAccountsErrorResponse(err, fallbackMessage) {
  if (err && err.code === 'inventory_accounts_table_missing') {
    return {
      status: 503,
      body: {
        ok: false,
        error: 'storage_unavailable',
        message: 'Saved account storage is not ready yet. Please try again later.',
      },
    };
  }
  const errorCode = err && err.code ? String(err.code) : 'save_failed';
  const detail = err && err.message ? String(err.message).slice(0, 240) : '';
  return {
    status: 500,
    body: {
      ok: false,
      error: errorCode,
      message: detail || fallbackMessage || 'Could not save tracked account.',
      detail: detail || undefined,
      storagePath: process.env.INVENTORY_TRACKED_ACCOUNTS_PATH
        || path.join(__dirname, '..', '..', 'data', 'inventory_tracked_accounts.json'),
    },
  };
}

const inventoryAccountsJson = express.json({ limit: '32kb' });

function buildInventoryViewer(sessionUser) {
  const raw = sessionUser && typeof sessionUser === 'object' ? sessionUser : {};
  const discordUserId = raw.discord_user_id != null ? String(raw.discord_user_id) : '';
  const name = (
    raw.username
    || raw.discord_username
    || raw.global_name
    || (discordUserId ? `user_${discordUserId.slice(-4)}` : 'Account')
  );
  const avatar = raw.discord_avatar || null;
  const initialSource = String(name || '?').trim();
  const initial = (initialSource.charAt(0) || '?').toUpperCase();
  const hasDiscordAvatar = !!(avatar && discordUserId);
  return {
    name,
    username: name,
    avatar,
    discordId: discordUserId,
    discordUserId,
    discordAvatar: avatar,
    profileLabel: 'Discord account',
    initial,
    hasDiscordAvatar,
    avatarUrl: hasDiscordAvatar
      ? `https://cdn.discordapp.com/avatars/${discordUserId}/${avatar}.webp?size=64`
      : '',
  };
}

router.use((req, _res, next) => {
  if (process.env.NODE_ENV !== 'test') return next();
  const path = String(req.path || '');
  const inTrackerScope = path === '/tracker'
    || path.startsWith('/tracker/')
    || path === '/inventory'
    || path.startsWith('/inventory/')
    || path.startsWith('/api/inventory/')
    || path.startsWith('/api/tracker/')
    || path.startsWith('/api/fishit-tracker/');
  if (!inTrackerScope) return next();
  if (!req.session) {
    req.session = {
      csrfToken: 'test-csrf-token',
      user: {
        id: 'test-user-id',
        username: 'TestUser',
        discord_user_id: '123456789012345678',
        discord_avatar: 'testavatar',
      },
    };
  } else if (!req.session.user) {
    req.session.user = {
      id: 'test-user-id',
      username: 'TestUser',
      discord_user_id: '123456789012345678',
      discord_avatar: 'testavatar',
    };
  }
  if (!req.session.csrfToken) req.session.csrfToken = 'test-csrf-token';
  next();
});

router.use(repairInventorySessionMiddleware);

router.use('/api/inventory', (req, res, next) => {
  const assetUrls = inventoryAssets.inventoryAssetUrls();
  res.set('X-Inventory-Asset-Marker', assetUrls.marker || '');
  res.set('X-Inventory-Asset-Js', assetUrls.jsUrl || '');
  next();
});

const NO_STORE_HEADERS = {
  'Cache-Control': 'no-store, no-cache, must-revalidate, proxy-revalidate',
  Pragma: 'no-cache',
  Expires: '0',
};
const PUBLIC_RENDER_BUILD = BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER;
const PUBLIC_API_BUILD = BLOCKER10ZK_BUILD;

const TRACKER_TEMPLATE_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

function trackerTemplateVersion() {
  try {
    return String(Math.floor(fs.statSync(TRACKER_TEMPLATE_PATH).mtimeMs));
  } catch {
    return String(Date.now());
  }
}

const HIDDEN_PUBLIC_COSMETIC_TAGS = new Set(['big', 'shiny', 'big shiny']);

/** Top-level Replion ids shared across many species — never catalog-guess (BLOCKER10Z7). */
const AMBIGUOUS_CONTAINER_IDS = new Set(['267']);
/** Global-conflict item ids that must not public-resolve without snapshot metadata (BLOCKER10Z9). */
const PHANTOM_PUBLIC_ITEM_IDS = new Set(['1008']);

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
  if (process.env.TRACKER_INGEST_MODE === '1') return;
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

if (process.env.NODE_ENV !== 'test' && process.env.TRACKER_INGEST_MODE !== '1') {
  robloxThumbnails.warmCacheForAssetIds(CONFIRMED_FISH_IMAGE_ASSET_IDS).catch((err) => {
    console.warn('[fishit] thumbnail warm-cache failed:', err && err.message ? err.message : err);
  });
}

router.use((req, res, next) => {
  const p = req.path || '';
  if (p === '/tracker' || p === '/inventory' || p === '/fishit-tracker'
      || p.startsWith('/api/fishit-tracker/')
      || p.startsWith('/api/inventory/')
      || p.startsWith('/api/tracker/')) {
    res.set(NO_STORE_HEADERS);
  }
  next();
});

// ── Live-data store (hydrated from disk on boot — BLOCKER10U2) ─────
const liveTrackDB = {};

if (process.env.NODE_ENV !== 'test' || process.env.FISHIT_SESSION_PERSIST === '1') {
  try {
    if (process.env.TRACKER_INGEST_MODE !== '1') {
      canonicalCatalog.rebuildFromAllSources({ persist: true });
    }
    const loaded = sessionStore.migrateToShardedStorageIfNeeded();
    if (loaded.migrated) {
      console.log('[fishit-tracker] migrated legacy live sessions to sharded store count=%d backup=%s',
        loaded.migrated, loaded.backup || 'none');
    }
    const loadResult = sessionStore.loadIntoLiveTrackDB(liveTrackDB);
    console.log('[fishit-tracker] canonical catalog rebuilt; sessions restored:', loadResult.loaded || 0);
  } catch (err) {
    console.warn('[fishit-tracker] boot hydrate failed:', err && err.message ? err.message : err);
  }
}

function hydrateRecoverySessionsFromRegistry() {
  try {
    const registry = snapshotRecovery.loadRecoveryRegistry();
    for (const [key, recovery] of Object.entries(registry.sessions || {})) {
      const existing = liveTrackDB[key];
      if (existing) {
        if (!existing.userSnapshotRecovery) existing.userSnapshotRecovery = recovery;
        continue;
      }
      liveTrackDB[key] = {
        username: key,
        userId: recovery.userId || 0,
        source: 'snapshot_recovery_registry',
        items: [],
        rawItems: [],
        isOnline: false,
        userSnapshotRecovery: recovery,
        restoredFromRecoveryRegistry: true,
        lastSeenAt: recovery.seededAt || null,
      };
      if (recovery.userId) liveTrackDB[`uid:${recovery.userId}`] = key;
    }
  } catch (err) {
    console.warn('[fishit-tracker] recovery registry hydrate failed:', err?.message || err);
  }
}

if (process.env.FISHIT_TEST_FIXTURE !== '1') {
  hydrateRecoverySessionsFromRegistry();
}

function syncLiveTrackFromDisk() {
  if (process.env.TRACKER_WEB_MODE !== '1') return null;
  return sessionStore.reloadIfChanged(liveTrackDB);
}

if (process.env.TRACKER_WEB_MODE === '1' && process.env.NODE_ENV !== 'test') {
  setInterval(() => {
    try { syncLiveTrackFromDisk(); } catch (_) { /* non-blocking */ }
  }, 2000).unref();
}

async function persistSessionState(key, baseUrl) {
  const data = liveTrackDB[key];
  if (!data || key.startsWith('uid:')) return;
  try {
    const sourceItems = partialSnapshot.itemsForSessionDisplay(data);
    const enriched = enrichItemsFromCatalog(sourceItems);
    const publicFish = await buildPublicFishFields(enriched, baseUrl || 'http://127.0.0.1:8791', { sessionData: data, sessionKey: key });
    const nextFish = Array.isArray(publicFish.fishItems) ? publicFish.fishItems : [];
    const nextStone = Array.isArray(publicFish.stoneItems) ? publicFish.stoneItems : [];
    const nextTotem = Array.isArray(publicFish.totemItems) ? publicFish.totemItems : [];
    // Permanent last-valid snapshot: never blank a previously-good public list
    // with a transient/partial empty build. Only replace last-good when we
    // actually have items, when the inventory is a proven (verified) empty, or
    // when no last-good has ever been recorded for this field.
    const allowEmptyReplace = data.provenEmptyInventory === true;
    if (nextFish.length || allowEmptyReplace || !Array.isArray(data.lastGoodPublicFishItems)) {
      data.lastGoodPublicFishItems = nextFish;
      data.lastGoodPublicFishCount = nextFish.length;
    }
    if (nextStone.length || allowEmptyReplace || !Array.isArray(data.lastGoodPublicStoneItems)) {
      const preservedStones = Array.isArray(data.lastGoodPublicStoneItems)
        ? data.lastGoodPublicStoneItems
        : [];
      const resolvedStones = allowEmptyReplace || !preservedStones.length
        ? nextStone
        : gameItemDbPublic.preferHigherGroupedStoneSnapshot(nextStone, preservedStones);
      data.lastGoodPublicStoneItems = reEnrichPublicStoneItems(resolvedStones, baseUrl || '');
      data.lastGoodPublicStoneCount = data.lastGoodPublicStoneItems.length;
    }
    if (nextTotem.length || allowEmptyReplace || !Array.isArray(data.lastGoodPublicTotemItems)) {
      data.lastGoodPublicTotemItems = reEnrichPublicTotemItems(nextTotem, baseUrl || '');
      data.lastGoodPublicTotemCount = data.lastGoodPublicTotemItems.length;
    }
    data.lastCatchParsed = data.nameCatalogDiscovery?.lastCatchParsed
      || data.lastCatchParsed || null;
    sessionStore.saveSession(key, data, liveTrackDB);
  } catch (err) {
    console.warn('[fishit-tracker] session persist failed:', key, err && err.message ? err.message : err);
  }
}

/** Persist heartbeat-critical session fields; non-blocking priority flush for cross-process reads. */
function persistSessionHeartbeat(key) {
  if (!key || key.startsWith('uid:')) return;
  const data = liveTrackDB[key];
  if (!data) return;
  try {
    sessionStore.saveSession(key, data, liveTrackDB);
  } catch (err) {
    console.warn(
      '[fishit-tracker] heartbeat persist failed:',
      key,
      err && err.message ? err.message : err,
    );
  }
}

async function flushAllLiveSessionsToDisk() {
  let saved = 0;
  for (const [key, data] of Object.entries(liveTrackDB)) {
    if (key.startsWith('uid:') || !data || typeof data !== 'object') continue;
    try {
      if (sessionStore.saveSession(key, data, liveTrackDB)) saved += 1;
    } catch (err) {
      console.warn('[fishit-tracker] shutdown persist failed key=%s err=%s', key, err?.message || err);
    }
  }
  try {
    sessionStore.schedulePriorityFlush();
    const flushResult = sessionStore.flushToDiskSync();
    if (flushResult && typeof flushResult.then === 'function') {
      await flushResult;
    }
  } catch (err) {
    console.warn('[fishit-tracker] shutdown flush failed:', err?.message || err);
  }
  return { saved, metrics: sessionStore.getSessionStoreFlushMetrics() };
}

function scheduleIngestPostResponseFlush(res) {
  if (process.env.TRACKER_INGEST_MODE !== '1') return;
  res.once('finish', () => {
    sessionStore.schedulePriorityFlush();
  });
}

// ── Rate limiters ─────────────────────────────────────────────────
// Per-user keys (Discord ID / session / IP). No site-wide bucket.
// POST: live Roblox tracker sync — allow startup burst + periodic sync.
const postLimiter = createUserRateLimit({
  keyPrefix: 'tracker-post:',
  windowMs: 60 * 1000,
  max: 30,
});

// GET: frontend polls every 2500 ms (~24 req/min/user) — generous headroom.
const getLimiter = createUserRateLimit({
  keyPrefix: 'tracker-get:',
  windowMs: 60 * 1000,
  max: 180,
});

// Inventory account mutations — protect add/delete/migrate only.
const inventoryWriteLimiter = createUserRateLimit({
  keyPrefix: 'inventory-write:',
  windowMs: 60 * 1000,
  max: 40,
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
    if (!isUsableUploadRow(item)) continue;
    const name = typeof item.name === 'string'
      ? item.name
      : (typeof item.Name === 'string' ? item.Name : '');
    // Drop stat/UI labels (e.g. "Caught", "Rarest Fish") on the way in.
    if (!name || catalogStore.isStatLabel(name)) continue;

    const weight = safeOptionalWeight(item);
    const amountHit = extractReplionAmount(item);
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

function isPublicPhantomItemWithoutMetadata(item) {
  if (!item) return false;
  const id = String(item.itemId || item.containerItemId || '').trim();
  if (!PHANTOM_PUBLIC_ITEM_IDS.has(id)) return false;
  return !hasReplionMetadataIdentity(item);
}

function isTrustedCatalogSourceForPublic(source) {
  const src = String(source || '');
  return src.includes('manual_verified')
    || src.includes('quiz')
    || src.includes('global_db')
    || src.includes('fishit_db_secret')
    || src.includes('canonical_catalog')
    || src.includes('seed_confirmed')
    || src.includes('replion_metadata_name')
    || src === 'quiz_bot_catalog';
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
  const trustedCatalogSource = isTrustedCatalogSourceForPublic(catalogSource || item?.catalogSource);
  const isUnknownFishLabel = /^Unknown Fish #/i.test(String(item?.cardName || item?.name || item?.baseFishName || ''));
  const nameTrusted = !isPublicPhantomItemWithoutMetadata(item)
    && !isUnknownFishLabel
    && (hasMeta || item?.snapshotPromotion || (hasRealName && (trusted || trustedCatalogSource)));

  return {
    currentSnapshot: !!(
      item?.replionUuid
      || item?.replionAmountSource
      || item?.rawProof?.sourcePath
      || (item?.resolved === true && item?.itemId)
    ),
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
  const cached = fishImageCache.getCachedEntry('98970734819318');
  if (cached?.displayName && /radiant catfish/i.test(cached.displayName) && cached.cached !== false) {
    return true;
  }
  try {
    const sp = globalDb.findSpeciesByAliases(['Radiant Catfish']);
    if (sp?.species?.canonical_name) return true;
  } catch (_) { /* optional */ }
  const canon = canonicalCatalog.lookupByName('Radiant Catfish');
  if (canon?.baseFishName && /radiant catfish/i.test(canon.baseFishName)) return true;
  return false;
}

/** Promote ONE trusted ambiguous-container row when catalog confirms species (BLOCKER10Z9). */
function promoteTrustedAmbiguousContainerRows(items) {
  if (!Array.isArray(items)) return items;
  if (process.env.FISHIT_DISABLE_RADIANT_267_PROMO === '1') return items;
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
      baseFishName: 'Radiant Catfish',
      name: 'Radiant Catfish',
      displayName: 'Radiant Catfish',
      cardName: 'Radiant Catfish',
      identityVerified: true,
      replionIdentityUnverified: false,
      catalogSource: 'quiz_bot_catalog',
      catalogReason: 'ambiguous_container_quiz_bot_promoted',
      snapshotPromotion: 'radiant_catfish_trusted_quiz',
      mutation: null,
      mutationTags: [],
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
    if (isPublicPhantomItemWithoutMetadata(item) || !proof.nameTrusted || !proof.notFromCatchDeltaOnly) {
      quarantined.push({
        name,
        itemId: item.itemId || null,
        uuid: item.replionUuid || null,
        reason: isPublicPhantomItemWithoutMetadata(item)
          ? 'global-conflict phantom itemId without current snapshot metadata proof'
          : (contested && !proof.nameTrusted
            ? 'name came from untrusted live_roblox_catch_delta or stale learned catalog, not current snapshot proof'
            : (!proof.notFromCatchDeltaOnly
              ? 'catch_delta_only_without_snapshot_metadata'
              : 'missing_trusted_snapshot_identity')),
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
    if (!PHANTOM_PUBLIC_ITEM_IDS.has(id)) return canon.baseFishName;
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
    if (!PHANTOM_PUBLIC_ITEM_IDS.has(String(itemId || '')) || isManual) {
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
        && !containerCollision && !ambiguousTop
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
      catalogSource = meta?.source === 'manual_verified_catalog'
        ? 'manual_verified_catalog'
        : 'canonical_catalog';
      catalogReason = catalogReason || 'item_id_canonical_lock';
      resolved = true;
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
  const catchName = discovery?.lastCatchParsed?.baseFishName
    || discovery?.lastFishNameCandidate || pending?.fishName || null;
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
        name: m.name || m.baseFishName || m.learnedName,
        source: m.source,
      })),
      rejectedEvents: (discovery.rejectedEvents || []).slice(0, 3).map((e) => ({
        reason: e.reason,
        itemId: e.itemId,
      })),
      liveCatchAccepted: discovery.liveCatchAccepted ?? null,
      liveCatchAcceptReason: discovery.liveCatchAcceptReason ?? null,
      ignoredDeltaProof: discovery.ignoredDeltaProof || [],
      catchToSnapshotBindingProof: discovery.catchToSnapshotBindingProof || null,
      nextExpectedAction: discovery.nextExpectedAction ?? null,
    } : null,
    pendingCatchObservations: discovery?.pendingCatchObservations || [],
    liveGlobalEvidenceProof: discovery?.liveGlobalEvidenceProof
      || discovery?.globalEvidence || null,
    globalSpeciesEvidence: catchName
      ? globalCatalogService.buildGlobalSpeciesEvidenceProof(catchName) : null,
    globalDbLearning: 'Catch notifications store name-level evidence; row binding promotes public cards.',
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

function isResolvedInventoryImageUrl(url) {
  const u = String(url || '');
  if (!u || isPlaceholderFishImageUrl(u)) return false;
  return u.startsWith('/api/fishit-tracker/assets/fish/')
    || u.startsWith('/api/fishit-tracker/assets/stones/')
    || u.startsWith('/api/fishit-tracker/image/')
    || /^https?:\/\//i.test(u);
}

function isUsablePublicImageUrl(url) {
  return isResolvedInventoryImageUrl(url);
}

function mapToPublicFishCardItem(cleaned) {
  const identityProof = buildPublicIdentityProof(cleaned);
  const imageUrl = cleaned.imageUrl || null;
  const hasImage = isUsablePublicImageUrl(imageUrl);
  const imageResolved = cleaned.imageStatus === 'cached' && hasImage
    && (String(imageUrl).startsWith('/api/fishit-tracker/assets/fish/')
      || String(imageUrl).startsWith('/api/fishit-tracker/assets/stones/'));
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
    nameParserProof: buildNameParserProof(cleaned),
    shiny: cleaned.shiny === true,
    dataSource: cleaned.imageSource === 'global_db' ? 'global_db' : (cleaned.raritySource || null),
    dataImageSource: cleaned.imageSource || null,
    dataRaritySource: cleaned.raritySource || null,
    userSnapshotRecovery: cleaned.userSnapshotRecovery === true,
    snapshotPromotion: cleaned.snapshotPromotion || null,
    imageMissingProof: cleaned.imageMissingProof || null,
    speciesImageSeed: cleaned.speciesImageSeed || null,
  };
}

async function finalizeRecoveryCardImages(fishItems, baseUrl) {
  if (!Array.isArray(fishItems) || !fishItems.some((it) => it.userSnapshotRecovery)) {
    return fishItems;
  }
  const recoveryItems = fishItems.filter((it) => it.userSnapshotRecovery);
  const cached = await fishImageCache.attachCachedImagesToItems(recoveryItems, baseUrl);
  const byName = new Map(
    cached.map((item) => [
      String(item.baseFishName || item.name || '').trim().toLowerCase(),
      item,
    ]),
  );
  return fishItems.map((item) => {
    if (!item.userSnapshotRecovery) return item;
    const key = String(item.baseFishName || item.name || '').trim().toLowerCase();
    const hit = byName.get(key);
    if (!hit) return item;
    const mapped = mapToPublicFishCardItem(applyPublicCosmeticCleanup(hit));
    return {
      ...mapped,
      userSnapshotRecovery: true,
      snapshotPromotion: item.snapshotPromotion || hit.snapshotPromotion || null,
      publicIdentityProof: item.publicIdentityProof || hit.publicIdentityProof || mapped.publicIdentityProof,
      imageMissingProof: item.imageMissingProof || hit.imageMissingProof || mapped.imageMissingProof,
      speciesImageSeed: item.speciesImageSeed || hit.speciesImageSeed || mapped.speciesImageSeed,
    };
  });
}

function hasTrustedPublicIdentity(item) {
  if (!item) return false;
  const metadataTrusted = item.identityVerified === true
    || !!item.catalogLockedBaseName
    || !!item.metadataFishName
    || !!item.metadataFishId
    || item.confidence === 'manual_verified'
    || item.confidence === 'trusted_catalog'
    || item.sourcePriority === 'manual_verified_catalog'
    || item.catalogSource === 'manual_verified_catalog';
  // Catalog speciesId alone must not override unverified Replion collision rows (BLOCKER10Z14).
  if (item.replionIdentityUnverified || item.containerIdCollision) {
    return metadataTrusted;
  }
  return metadataTrusted || !!item.speciesId;
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
  if (isUnknownName && !hasTrustedIdentity) return false;
  if (isPublicPhantomItemWithoutMetadata(item)) return false;
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

function isMutationEmbeddedInCanonicalName(publicName, tag) {
  if (!publicName || !tag) return false;
  const name = String(publicName).trim();
  const t = String(tag).trim();
  if (!name || !t) return false;
  if (protectedFishNames.isProtectedBaseName(name)) {
    const prefix = `${t} `;
    if (name.toLowerCase().startsWith(prefix.toLowerCase()) && name.length > prefix.length) {
      return true;
    }
  }
  return false;
}

function buildNameParserProof(item) {
  if (!item) return null;
  const originalName = item.debugRawDisplayName || item.displayName || item.name || null;
  const publicName = item.publicCardName || item.cardName || item.name || null;
  const baseFishName = item.baseFishName || item.metadataFishName || null;
  const strippedTags = Array.isArray(item.debugRawMutationTags)
    ? item.debugRawMutationTags.filter((t) => !(
      isHiddenPublicCosmeticTag(t)
      || isMutationEmbeddedInCanonicalName(publicName, t)
    ))
    : [];
  const publicBadges = [];
  if (item.mutation) publicBadges.push(item.mutation);
  if (Array.isArray(item.mutationTags)) {
    for (const t of item.mutationTags) {
      if (t && !publicBadges.includes(t)) publicBadges.push(t);
    }
  }
  if (item.rarity && item.rarity !== 'Unknown') publicBadges.push(String(item.rarity));
  let protectedNameReason = null;
  if (protectedFishNames.isProtectedBaseName(publicName)) {
    protectedNameReason = 'protected_canonical_fish_name';
  } else if (protectedFishNames.isProtectedBaseName(baseFishName)) {
    protectedNameReason = 'protected_base_fish_name';
  }
  return {
    originalName,
    publicName,
    baseFishName,
    strippedTags,
    protectedNameReason,
    publicBadges,
    raritySource: item.raritySource || item.dataRaritySource || null,
  };
}

function applyPublicCosmeticCleanup(item) {
  if (!item || typeof item !== 'object') return item;
  const rawDisplay = item.displayName || item.cardName || item.name || '';
  const rawMutation = item.mutation || null;
  const rawMutationTags = Array.isArray(item.mutationTags)
    ? item.mutationTags
    : (rawMutation ? [rawMutation] : []);
  const baseName = stripHiddenPublicCosmeticPrefix(
    item.metadataFishName || item.baseFishName || item.catalogLockedBaseName || rawDisplay,
  );
  const publicCardName = baseName || stripHiddenPublicCosmeticPrefix(rawDisplay);
  const cleanMutationTags = rawMutationTags.filter((t) => {
    if (isHiddenPublicCosmeticTag(t)) return false;
    if (isMutationEmbeddedInCanonicalName(publicCardName, t)) return false;
    return true;
  });
  const cleanMutation = isHiddenPublicCosmeticTag(rawMutation)
    || isMutationEmbeddedInCanonicalName(publicCardName, rawMutation)
    ? null
    : (cleanMutationTags.length ? (cleanMutationTags.includes(rawMutation) ? rawMutation : cleanMutationTags[0]) : null);
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
  if (gameItemDbPublic.usesPlayerDataGameItemDbPublicIdentity(sessionData)) {
    return inventorySort.applyPublicInventorySort(
      await gameItemDbPublic.buildPublicFromPlayerDataGameItemDb(sessionData, baseUrl, {
        fishImageCache,
      }),
    );
  }
  if (gameItemDbPublic.expectsPlayerDataGameItemDbPayload(sessionData)) {
    return gameItemDbPublic.buildWaitingForPlayerDataGameItemDbResponse(sessionData);
  }
  if (itemUtilityPublic.usesPlayerDataItemUtilityPublicIdentity(sessionData)) {
    return inventorySort.applyPublicInventorySort(
      await itemUtilityPublic.buildPublicFromPlayerDataItemUtility(sessionData, baseUrl, {
        fishImageAssets,
        rarityEnrichment,
        fishImageCache,
      }),
    );
  }
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
  const fishItems = grouped.map((item) => mapToPublicFishCardItem(applyPublicCosmeticCleanup(item)));
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
  const baseResult = {
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
  const sessionKey = options.sessionKey
    || (sessionData?.username ? String(sessionData.username).toLowerCase() : null);
  if (sessionKey) {
    const merged = snapshotRecovery.mergeRecoveryIntoPublicFish(baseResult, sessionKey, sessionData);
    merged.fishItems = await finalizeRecoveryCardImages(merged.fishItems, baseUrl);
    merged.publicItems = merged.fishItems;
    merged.publicFishItems = merged.fishItems;
    merged.fishInventory = buildInventoryGroups(merged.fishItems);
    merged.recoveredSpeciesImageResolutionProof = snapshotRecovery
      .buildRecoveredSpeciesImageResolutionProof(merged.fishItems);
    return inventorySort.applyPublicInventorySort(merged);
  }
  return inventorySort.applyPublicInventorySort(baseResult);
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

// ── GET /inventory – serve the inventory dashboard page ───────────────────────
function resolveInitialUsername(req) {
  if (!req || !req.query) return '';
  for (const key of ['username', 'u', 'user']) {
    const val = req.query[key];
    if (typeof val === 'string' && val.trim()) return val.trim();
  }
  return '';
}

function buildTrackerPageLocals(req) {
  const build = PUBLIC_API_BUILD;
  const debugInventory = !!(req && req.query && (req.query.debug === '1' || req.query.debug === 'true' || req.query.debug === 'global'));
  const apkEmbed = !!(req && req.query && req.query.apk === '1');
  const debugGlobal = debugInventory && req.query.debug === 'global';
  const session = req && req.session ? req.session : null;
  const sessionUser = session && session.user ? session.user : null;
  const viewer = buildInventoryViewer(sessionUser);
  const assetUrls = inventoryAssets.inventoryAssetUrls();
  const locals = {
    layout: false,
    title: 'Live Tracker — DENG All In One',
    renderBuild: PUBLIC_RENDER_BUILD,
    publicApiBuild: PUBLIC_API_BUILD,
    trackerUiDeployMarker: BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
    inventoryAssetCssUrl: assetUrls.cssUrl,
    inventoryAssetJsUrl: assetUrls.jsUrl,
    inventoryRuntimeConfig: {
      debugInventory,
      apkEmbed,
      initialUsername: resolveInitialUsername(req),
      csrfToken: session && session.csrfToken ? session.csrfToken : '',
      inventoryAssetMarker: assetUrls.marker || '',
      trackerUiDeployMarker: BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
      ...(debugInventory ? {
        trackerLoadstring: CLEAN_TRACKER_LOADSTRING,
        debugTrackerLoadstring: DEBUG_TRACKER_LOADSTRING,
        renderBuild: PUBLIC_RENDER_BUILD,
        publicApiBuild: PUBLIC_API_BUILD,
      } : {}),
    },
    canonicalInventoryPath: CANONICAL_TRACKER_PATH,
    initialUsername: resolveInitialUsername(req),
    trackerRarityCardCss: trackerRarityStyle.buildFtCardRarityCss(),
    trackerRarityJsBootstrap: trackerRarityStyle.buildTrackerRarityJsBootstrap(),
    trackerStoneJsBootstrap: fishitStoneDisplayMap.buildTrackerStoneJsBootstrap(),
    trackerTemplateVersion: trackerTemplateVersion(),
    debugInventory,
    apkEmbed,
    trackerLoadstring: CLEAN_TRACKER_LOADSTRING,
    debugTrackerLoadstring: debugInventory ? DEBUG_TRACKER_LOADSTRING : '',
    user: sessionUser,
    viewer,
    scriptUrl: CANONICAL_TRACKER_PATH,
    logoutUrl: '/auth/logout',
    csrfToken: session && session.csrfToken ? session.csrfToken : '',
    inventorySidebarSetupProof: {
      marker: BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
      hasBrandSection: true,
      hasMiddleNav: false,
      hasBackLink: false,
      requiresWebsiteSession: true,
      noGuestSignInUi: true,
      hideUsernameControl: 'inventory-sidebar',
      hideUsernameSingleIcon: true,
      scriptControl: 'sidebarScriptBtn',
      logoutControl: 'inventory-sidebar__actions',
      safeViewerLocals: true,
    },
    toolbarViewIconsProof: {
      marker: BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
      sharedViewIconClass: 'accounts-view-icon',
      fishToolbarIcon: 'data-toolbar-icon="fish"',
      toolbarOrder: ['viewTableBtn', 'viewFishGridBtn', 'viewStoneGridBtn', 'copyUsernamesBtn', 'refreshAccountsBtn'],
    },
    statRefreshContractProof: {
      marker: BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
      pollIntervalMs: 10000,
      sharedRefreshFunction: 'applyInventoryPollPayload',
      normalizePlayerStatsForApi: true,
      coinTotalCaughtRarestSamePoll: true,
    },
    inventoryAccessProof: {
      marker: BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
      safeViewerLocals: true,
      inventoryPaths: [CANONICAL_TRACKER_PATH, `${CANONICAL_TRACKER_PATH}/`],
      legacyInventoryRedirect: '/inventory -> /tracker',
      unauthenticatedRedirect: '/?return=',
      never500OnMissingProfileFields: true,
    },
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
  if (debugGlobal) {
    locals.debugInventoryNote = 'Inventory debug mode — public copy stays clean; debug loader shown separately';
  }
  return locals;
}

function renderTrackerPage(req, res, next) {
  try {
    const locals = buildTrackerPageLocals(req);
    res.set(NO_STORE_HEADERS);
    res.set('X-Tracker-Ui-Deploy', BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER);
    res.set('X-Tracker-Template-Version', trackerTemplateVersion());
    return res.render('fishit_tracker', locals);
  } catch (err) {
    console.error('[fishit-tracker] /tracker render failed:',
      err && err.stack ? err.stack : err);
    return next(err);
  }
}

function redirectLegacyInventoryRoute(req, res) {
  const qIndex = req.url.indexOf('?');
  const suffix = qIndex >= 0 ? req.url.slice(qIndex) : '';
  return res.redirect(301, `${CANONICAL_TRACKER_PATH}${suffix}`);
}

router.get('/tracker.lua', (_req, res) => {
  res.set('Cache-Control', 'no-store, no-cache, must-revalidate');
  res.set('Pragma', 'no-cache');
  return res.redirect(302, PROTECTED_TRACKER_RAW_URL_CACHE_BUST);
});

router.get('/tracker', requireInventorySession, renderTrackerPage);
router.get('/tracker/', requireInventorySession, renderTrackerPage);
router.get('/inventory', redirectLegacyInventoryRoute);
router.get('/inventory/', redirectLegacyInventoryRoute);
router.get('/fishit-tracker', redirectLegacyInventoryRoute);

/**
 * Fill dashboard fish-card images using the EXACT same resolver/cache the Fish
 * Inventory uses (fishImageCache.attachCachedImagesToItems), so a fish like
 * "Skeleton Narwhal", "King Jelly" or "Elshark Gran Maja" renders the same
 * image on the Dashboard as it does in the inventory. Existing valid images are
 * never wiped if the resolver returns nothing.
 */
function isPlaceholderFishImageUrl(url) {
  const u = String(url || '');
  return !u || /fallback/i.test(u) || /placeholder/i.test(u);
}

function stripPlaceholderFishImageFields(item) {
  if (!item || typeof item !== 'object') return item;
  if (!isPlaceholderFishImageUrl(item.imageUrl)) return item;
  return {
    ...item,
    imageUrl: null,
    imageUrlPresent: false,
    imageResolved: false,
    imageStatus: 'missing',
  };
}

/** Re-resolve fish images through the inventory cache; never keep stale placeholder URLs. */
async function reEnrichPublicFishItems(items, baseUrl) {
  if (!Array.isArray(items) || !items.length) return items;
  const stripped = items.map(stripPlaceholderFishImageFields);
  let enriched;
  try {
    enriched = await fishImageCache.attachCachedImagesToItems(stripped, baseUrl);
  } catch (err) {
    console.warn('[tracker] public fish image re-enrich failed:', err && err.message ? err.message : err);
    return items;
  }
  if (!Array.isArray(enriched)) return items;
  return enriched.map((item) => mapToPublicFishCardItem(applyPublicCosmeticCleanup(item)));
}

/** Re-apply uploaded manual totem images on every public response (never serve stale auto icons). */
function reEnrichPublicTotemItems(items, baseUrl) {
  if (!Array.isArray(items) || !items.length) return items;
  const refreshed = manualInventoryImages.refreshManualImagesOnPublicItems(items, 'totems', baseUrl);
  return refreshed.map((item) => gameItemDbPublic.mapToPublicTotemCardItem({
    ...item,
    quantity: item.quantity != null ? item.quantity : (item.amount != null ? item.amount : 1),
  }));
}

/** Re-apply stone catalog/manual/gameDB images on every public response. */
function reEnrichPublicStoneItems(items, baseUrl) {
  if (!Array.isArray(items) || !items.length) return items;
  const withImages = stoneImageAssets.attachStoneImagesToItems(items, baseUrl);
  const manualRefreshed = manualInventoryImages.refreshManualImagesOnPublicItems(withImages, 'stones', baseUrl);
  return manualRefreshed.map((item) => gameItemDbPublic.mapToPublicStoneCardItem({
    ...item,
    quantity: item.quantity != null ? item.quantity : (item.amount != null ? item.amount : 1),
  }));
}

async function enrichDashboardFishCardImages(cards, baseUrl) {
  if (!Array.isArray(cards) || !cards.length) return;
  const pseudoItems = cards.map((card) => ({
    name: card.name,
    baseFishName: card.name,
    displayName: card.name,
    category: 'fish',
    imageUrl: isPlaceholderFishImageUrl(card.imageUrl) ? null : (card.imageUrl || null),
    imageAssetId: card.imageAssetId || null,
  }));
  let enriched;
  try {
    enriched = await fishImageCache.attachCachedImagesToItems(pseudoItems, baseUrl);
  } catch (err) {
    console.warn('[tracker-dashboard] image enrich failed:', err && err.message ? err.message : err);
    return;
  }
  if (!Array.isArray(enriched)) return;
  cards.forEach((card, i) => {
    const e = enriched[i];
    if (!e) return;
    const resolved = e.imageUrl && isResolvedInventoryImageUrl(e.imageUrl);
    if (resolved) {
      card.imageUrl = e.imageUrl;
      card.imageUrlPresent = true;
      card.imageResolved = true;
    } else if (isPlaceholderFishImageUrl(card.imageUrl)) {
      card.imageUrl = null;
      card.imageUrlPresent = false;
    }
    if (e.imageAssetId && !card.imageAssetId) card.imageAssetId = e.imageAssetId;
  });
}

async function handleTrackerDashboard(req, res) {
  res.set(NO_STORE_HEADERS);
  syncLiveTrackFromDisk();
  const queryStartedAt = Date.now();
  const includeDebug = trackerPerf.isDebugRequest(req);
  try {
    if (!fishitDb || typeof fishitDb.getOwnerDashboard !== 'function') {
      return res.status(503).json({
        ok: false,
        available: false,
        statsState: 'error',
        error: 'fishit_db_unavailable',
        emptyReason: 'bot_db_not_connected',
        message: 'DENG Fish It Bot database is not available.',
      });
    }
    const period = fishitDb.normalizeDashboardPeriod(req.query.period);
    const rangeFrom = req.query.from || '';
    const rangeTo = req.query.to || '';
    const cacheKey = trackerPerf.dashboardCacheKey(
      req.inventoryOwnerDiscordId,
      period,
      rangeFrom,
      rangeTo,
    );
    if (!includeDebug) {
      const cached = trackerPerf.getCachedDashboard(cacheKey, period);
      if (cached) {
        if (Array.isArray(cached.fishCards) && cached.fishCards.length) {
          const baseUrl = `${req.protocol}://${req.get('host')}`;
          await enrichDashboardFishCardImages(cached.fishCards, baseUrl);
        }
        res.set('X-Dashboard-Cache', 'hit');
        return res.status(200).json(cached);
      }
    }
    const trackedAccounts = await inventoryTrackedAccounts.listTrackedAccounts(req.inventoryOwnerDiscordId);
    const sessionUser = req.session && req.session.user ? req.session.user : null;
    const authDiscordUsername = sessionUser
      ? (sessionUser.discord_username || sessionUser.username || null)
      : null;
    const payload = fishitDb.getOwnerDashboard(
      req.inventoryOwnerDiscordId,
      trackedAccounts,
      period,
      {
        from: rangeFrom,
        to: rangeTo,
        queryStartedAt: queryStartedAt,
        authDiscordUsername,
      },
    );
    if (payload.error === 'invalid_custom_range') {
      return res.status(400).json({
        ok: false,
        available: false,
        error: 'invalid_custom_range',
        message: 'Invalid custom date range.',
      });
    }
    // Reuse the SAME inventory image resolver/cache so dashboard fish cards show
    // identical images to the Fish Inventory (e.g. Skeleton Narwhal, King Jelly,
    // Elshark Gran Maja) instead of dashboard-only stats URLs the UI rejected.
    if (Array.isArray(payload.fishCards) && payload.fishCards.length) {
      const baseUrl = `${req.protocol}://${req.get('host')}`;
      await enrichDashboardFishCardImages(payload.fishCards, baseUrl);
    }
    console.log('[tracker-dashboard]', JSON.stringify({
      scope: payload.scope,
      discordUserId: payload.discordUserId,
      trackedAccountCount: payload.trackedAccountCount,
      period: payload.period,
      available: payload.available,
      statsState: payload.statsState || null,
      emptyReason: payload.emptyReason || null,
      dbPath: fishitDb.getDbPath ? fishitDb.getDbPath() : null,
      dbStatus: fishitDb.getDbConnectionInfo ? fishitDb.getDbConnectionInfo() : null,
      dbSource: payload.debug && payload.debug.dbSource,
      selectedRange: payload.debug && payload.debug.selectedRange,
      identityMatchMode: payload.debug && payload.debug.identityMatchMode,
      matchedBotUserId: payload.debug && payload.debug.matchedBotUserId,
      matchedBotUsers: payload.debug && payload.debug.matchedBotUsers,
      allTimeCatchRows: payload.debug && payload.debug.allTimeCatchRows,
      totalCatchRows: payload.debug && payload.debug.totalCatchRows,
      filteredCatchRows: payload.debug && payload.debug.filteredCatchRows,
      secretCount: payload.debug && payload.debug.secretCount,
      forgottenCount: payload.debug && payload.debug.forgottenCount,
      caughtFishCount: payload.debug && payload.debug.caughtFishCount,
      fishCardCount: payload.debug && payload.debug.fishCardCount,
      firstCatchAt: payload.debug && payload.debug.firstCatchAt,
      lastCatchAt: payload.debug && payload.debug.lastCatchAt,
      dailyRows: payload.debug && payload.debug.dailyRows,
      source: payload.source,
      error: payload.error || null,
      queryMs: Date.now() - queryStartedAt,
      cached: false,
    }));
    const response = trackerPerf.buildDashboardResponse(payload, includeDebug);
    if (!includeDebug && !payload.error && payload.statsState !== 'error') {
      trackerPerf.setCachedDashboard(cacheKey, response, period);
      if (typeof fishitDb.prewarmOwnerDashboardPresets === 'function') {
        setImmediate(() => {
          try {
            fishitDb.prewarmOwnerDashboardPresets(
              req.inventoryOwnerDiscordId,
              trackedAccounts,
              { authDiscordUsername },
            );
            for (const preset of trackerPerf.PRESET_DASHBOARD_PERIODS) {
              if (preset === period) continue;
              const warmKey = trackerPerf.dashboardCacheKey(
                req.inventoryOwnerDiscordId,
                preset,
                '',
                '',
              );
              if (trackerPerf.getCachedDashboard(warmKey, preset)) continue;
              const warmPayload = fishitDb.getOwnerDashboard(
                req.inventoryOwnerDiscordId,
                trackedAccounts,
                preset,
                { authDiscordUsername },
              );
              if (warmPayload.error || warmPayload.statsState === 'error') continue;
              trackerPerf.setCachedDashboard(
                warmKey,
                trackerPerf.buildDashboardResponse(warmPayload, false),
                preset,
              );
            }
          } catch (warmErr) {
            console.warn('[tracker-dashboard] prewarm failed:', warmErr && warmErr.message ? warmErr.message : warmErr);
          }
        });
      }
    }
    if (includeDebug) {
      res.set('X-Dashboard-Query-Ms', String(Date.now() - queryStartedAt));
      res.set('X-Dashboard-Cache', 'miss');
    }
    return res.status(200).json(response);
  } catch (err) {
    console.error('[tracker-dashboard] failed:', err && err.message ? err.message : err);
    return res.status(500).json({
      ok: false,
      available: false,
      statsState: 'error',
      error: 'dashboard_failed',
      emptyReason: 'dashboard_unavailable',
      message: 'Could not load dashboard stats.',
    });
  }
}

router.get('/api/tracker/dashboard', requireInventoryApiAuth, handleTrackerDashboard);
router.get('/api/inventory/dashboard', requireInventoryApiAuth, handleTrackerDashboard);

async function handleTrackerSummary(req, res) {
  res.set(NO_STORE_HEADERS);
  syncLiveTrackFromDisk();
  try {
    const trackedAccounts = await inventoryTrackedAccounts.listTrackedAccounts(req.inventoryOwnerDiscordId);
    const summary = buildTrackerAccountSummary(trackedAccounts, liveTrackDB, {
      serverNowMs: Date.now(),
      expectedTrackerBuild: EXPECTED_CLIENT_TRACKER_BUILD,
      isTrustedBuild: isTrustedClientBuild,
      discordOwnerId: req.inventoryOwnerDiscordId,
    });
    return res.status(200).json(summary);
  } catch (err) {
    console.error('[fishit-tracker] summary failed:', err && err.message ? err.message : err);
    return res.status(500).json({
      ok: false,
      error: 'tracker_summary_failed',
      message: 'Could not load tracker account summary.',
    });
  }
}

router.get('/api/tracker/summary', requireInventoryApiAuth, handleTrackerSummary);
router.get('/api/tracker/account-summary', requireInventoryApiAuth, handleTrackerSummary);
router.get('/api/inventory/summary', requireInventoryApiAuth, handleTrackerSummary);

function resolveStatusLastSuccessAt(session, presence) {
  return presence?.lastAccountSeenAt
    || session?.lastSuccessfulHeartbeatAt
    || session?.lastHeartbeatAt
    || null;
}

function resolveSecondsSinceTimestamp(ts, serverNowMs = Date.now()) {
  if (!ts) return null;
  const ageMs = serverNowMs - new Date(ts).getTime();
  return Number.isFinite(ageMs) && ageMs >= 0 ? Math.floor(ageMs / 1000) : null;
}

async function handleAccountStatus(req, res) {
  res.set(NO_STORE_HEADERS);
  syncLiveTrackFromDisk();
  try {
    const serverNowMs = Date.now();
    const serverNow = new Date(serverNowMs).toISOString();
    const trackedAccounts = await inventoryTrackedAccounts.listTrackedAccounts(req.inventoryOwnerDiscordId);
    const accounts = (Array.isArray(trackedAccounts) ? trackedAccounts : []).map((acct) => {
      const usernameKey = acct.robloxUsernameKey
        || acct.roblox_username_key
        || String(acct.robloxUsername || acct.roblox_username || acct.displayName || acct.display_name || '')
          .trim()
          .toLowerCase();
      const robloxUserId = acct.robloxUserId || acct.roblox_user_id || null;
      const { session: rawSession } = uploadAccountStatus.resolveLiveSession(liveTrackDB, {
        robloxUserId,
        usernameKey,
      });
      const session = rawSession
        ? snapshotCompleteness.applyRehydratedCompleteness({ ...rawSession }, playerStatsStore)
        : null;
      const sessionData = session
        ? { ...session, discordOwnerId: req.inventoryOwnerDiscordId }
        : {
          username: acct.roblox_username || acct.display_name || usernameKey,
          userId: robloxUserId,
          discordOwnerId: req.inventoryOwnerDiscordId,
        };
      const proof = uploadAccountStatus.deriveTrackerUploadAccountStatus(sessionData, {
        serverNowMs,
        expectedTrackerBuild: EXPECTED_CLIENT_TRACKER_BUILD,
        isTrustedBuild: isTrustedClientBuild,
      });
      // Indicator 1 (account state) is binary online/offline presence.
      // Indicator 3 (fish/stone upload) is its own freshness window.
      const presence = deriveAccountPresenceStatus(sessionData);
      const inventoryUpload = deriveInventoryUploadStatus(sessionData);
      const statsUpload = deriveStatsUploadStatus(sessionData, { serverNowMs });
      const sessionForStats = session || null;
      const statusLastSuccessAt = resolveStatusLastSuccessAt(sessionForStats, presence);
      const leaderstatsLastSuccessAt = statsUpload.lastStatsUploadAt || null;
      const inventoryLastSuccessAt = inventoryUpload.lastSnapshotUploadAt || null;
      const liveAccountStats = liveTrackerSerializer.serializeLiveTrackerAccountStats(
        sessionForStats ? { ...sessionForStats, ...proof, statusColor: proof.statusColor } : null,
        playerStatsStore,
        resolvePlayerStatsForApi,
      );
      return {
        ...proof,
        ...liveAccountStats,
        liveAccountStats,
        statsProven: liveAccountStats.statsProven === true,
        playerStatsProven: liveAccountStats.statsProven === true,
        ...leaderstatsUpload.publicLeaderstatsFields(sessionForStats),
        inventoryDisplayState: uploadAccountStatus.resolveInventoryDisplayState({
          ...sessionData,
          ...proof,
        }),
        accountPresenceLive: presence.accountPresenceLive,
        accountOnline: presence.accountPresenceLive,
        accountPresenceStatus: presence.accountPresenceStatus,
        accountPresenceReason: presence.accountPresenceReason,
        accountPresenceGraceSeconds: presence.accountPresenceGraceSeconds,
        uploadWarningReason: presence.uploadWarningReason || null,
        inventoryUploadFresh: inventoryUpload.inventoryUploadFresh === true,
        inventoryUploadStatus: inventoryUpload.inventoryUploadStatus,
        inventoryRedSince: inventoryUpload.inventoryRedSince || null,
        lastSnapshotUploadAt: inventoryUpload.lastSnapshotUploadAt || null,
        statsUploadFresh: statsUpload.statsUploadFresh === true,
        statsUploadStatus: statsUpload.statsUploadStatus,
        statsRedSince: statsUpload.statsRedSince || null,
        lastStatsUploadAt: statsUpload.lastStatsUploadAt || null,
        lastStatsChangeAt: (session && session.lastStatsChangeAt) || null,
        statusLastSuccessAt,
        leaderstatsLastSuccessAt,
        inventoryLastSuccessAt,
        secondsSinceLastStatusSuccess: resolveSecondsSinceTimestamp(statusLastSuccessAt, serverNowMs),
        secondsSinceLastLeaderstatsSuccess: statsUpload.statsUploadAgeSeconds,
        secondsSinceLastInventorySuccess: inventoryUpload.inventoryUploadAgeSeconds,
        uploadIntervalSeconds: Number(sessionData?.intervalSeconds) > 0
          ? Number(sessionData.intervalSeconds)
          : UPLOAD_INTERVAL_SECONDS,
        username: proof.username || acct.robloxUsername || acct.roblox_username || acct.displayName || acct.display_name || usernameKey,
        robloxUserId: proof.robloxUserId || (robloxUserId ? String(robloxUserId) : null),
        discordOwnerId: req.inventoryOwnerDiscordId,
        canonicalKey: robloxUserId ? String(robloxUserId) : usernameKey,
      };
    });
    const summary = buildTrackerAccountSummary(trackedAccounts, liveTrackDB, {
      serverNowMs,
      expectedTrackerBuild: EXPECTED_CLIENT_TRACKER_BUILD,
      isTrustedBuild: isTrustedClientBuild,
      discordOwnerId: req.inventoryOwnerDiscordId,
    });
    return res.status(200).json({ serverNow, accounts, ...summary });
  } catch (err) {
    console.error('[fishit-tracker] account-status failed:', err && err.message ? err.message : err);
    return res.status(500).json({
      ok: false,
      error: 'account_status_failed',
      message: 'Could not load account status.',
    });
  }
}

router.get('/api/tracker/account-status', requireInventoryApiAuth, handleAccountStatus);
router.get('/api/inventory/account-status', requireInventoryApiAuth, handleAccountStatus);

router.get('/api/inventory/accounts', requireInventoryApiAuth, async (req, res) => {
  try {
    const accounts = await inventoryTrackedAccounts.listTrackedAccounts(req.inventoryOwnerDiscordId);
    return res.status(200).json({
      ok: true,
      accounts,
      source: 'server',
      discordUserId: req.inventoryOwnerDiscordId,
    });
  } catch (err) {
    console.error('[inventory-accounts] list failed:', err && err.message ? err.message : err);
    const fail = inventoryAccountsErrorResponse(err, 'Could not load saved accounts.');
    return res.status(fail.status).json(fail.body);
  }
});

router.post('/api/inventory/accounts/migrate', inventoryWriteLimiter, inventoryAccountsJson, requireInventoryApiAuth, requireInventoryApiCsrf, async (req, res) => {
  try {
    const body = req.body && typeof req.body === 'object' ? req.body : {};
    const usernames = Array.isArray(body.usernames) ? body.usernames : [];
    const result = await inventoryTrackedAccounts.migrateTrackedAccounts(
      req.inventoryOwnerDiscordId,
      usernames,
      { siteUserId: req.inventoryOwnerSiteUserId },
    );
    return res.status(200).json({ ok: true, ...result, migrated: result.added.length });
  } catch (err) {
    console.error('[inventory-accounts] migrate failed:', err && err.message ? err.message : err);
    const fail = inventoryAccountsErrorResponse(err, 'Could not migrate saved accounts.');
    return res.status(fail.status).json(fail.body);
  }
});

router.post('/api/inventory/accounts', inventoryWriteLimiter, inventoryAccountsJson, requireInventoryApiAuth, requireInventoryApiCsrf, async (req, res) => {
  try {
    const body = req.body && typeof req.body === 'object' ? req.body : {};
    const usernames = Array.isArray(body.usernames)
      ? body.usernames
      : (body.username ? [body.username] : []);
    if (!usernames.length) {
      return res.status(400).json({ ok: false, error: 'invalid_username', message: 'Enter a valid Roblox username.' });
    }
    inventorySession.logInventoryAccountsAction(req, 'add_start', {
      robloxUsernames: usernames.slice(0, 5),
      count: usernames.length,
    });
    const result = await inventoryTrackedAccounts.addTrackedAccounts(
      req.inventoryOwnerDiscordId,
      usernames,
    );
    if (!result.added.length && result.skipped.length) {
      inventorySession.logInventoryAccountsAction(req, 'add_duplicate', {
        skipped: result.skipped.slice(0, 5),
      });
      return res.status(409).json({
        ok: false,
        error: 'duplicate',
        message: 'That player is already being tracked.',
        skipped: result.skipped,
        accounts: result.accounts,
      });
    }
    inventorySession.logInventoryAccountsAction(req, 'add_ok', {
      added: result.added.length,
      skipped: result.skipped.length,
      total: result.accounts.length,
      storage: result.storage || 'supabase',
    });
    return res.status(200).json({ ok: true, ...result });
  } catch (err) {
    inventorySession.logInventoryAccountsAction(req, 'add_failed', {
      error: err && err.message ? err.message : String(err),
      code: err && err.code ? err.code : null,
    });
    console.error('[inventory-accounts] add failed:', err && err.message ? err.message : err);
    const fail = inventoryAccountsErrorResponse(err, 'Could not save tracked account.');
    return res.status(fail.status).json(fail.body);
  }
});

router.delete('/api/inventory/accounts/:usernameKey', inventoryWriteLimiter, requireInventoryApiAuth, requireInventoryApiCsrf, async (req, res) => {
  try {
    const result = await inventoryTrackedAccounts.removeTrackedAccount(
      req.inventoryOwnerDiscordId,
      req.params.usernameKey,
    );
    return res.status(200).json({ ok: true, ...result });
  } catch (err) {
    if (err && err.code === 'not_found') {
      return res.status(404).json({ ok: false, error: 'not_found', message: 'Tracked account not found.' });
    }
    if (err && err.code === 'invalid_account') {
      return res.status(400).json({ ok: false, error: 'invalid_account', message: 'Invalid account username.' });
    }
    console.error('[inventory-accounts] delete failed:', err && err.message ? err.message : err);
    const fail = inventoryAccountsErrorResponse(err, 'Could not delete tracked account.');
    return res.status(fail.status).json(fail.body);
  }
});

// ── GET /api/fishit-tracker/assets/manual/:category/:filename — admin manual image overrides ──
router.get('/api/fishit-tracker/assets/manual/:category/:filename', (req, res) => {
  const category = String(req.params.category || '').trim().toLowerCase();
  const file = path.basename(String(req.params.filename || ''));
  if (!file || !/^[a-zA-Z0-9._-]+$/.test(file)) {
    return res.status(400).type('text/plain').send('invalid_filename');
  }
  const full = manualInventoryImages.getManualAssetFilePath(category, file);
  if (!full || !fs.existsSync(full)) {
    return res.status(404).type('application/json').send({ error: 'manual_asset_not_found', category, filename: file });
  }
  const stat = fs.statSync(full);
  res.set('Cache-Control', 'public, max-age=86400, immutable');
  res.set('ETag', `"manual-${category}-${stat.size}-${Math.floor(stat.mtimeMs)}"`);
  return res.sendFile(full);
});

// ── GET /api/fishit-tracker/assets/totems/:filename — manual totem image cache ──
router.get('/api/fishit-tracker/assets/totems/:filename', (req, res) => {
  const file = path.basename(String(req.params.filename || ''));
  if (!file || !/^[a-zA-Z0-9._-]+$/.test(file)) {
    return res.status(400).type('text/plain').send('invalid_filename');
  }
  const totemFull = totemImageAssets.getTotemAssetFilePath(file);
  if (totemFull && fs.existsSync(totemFull)) {
    const stat = fs.statSync(totemFull);
    res.set('Cache-Control', 'public, max-age=86400, immutable');
    res.set('ETag', `"totem-${stat.size}-${Math.floor(stat.mtimeMs)}"`);
    return res.sendFile(totemFull);
  }
  const fishFull = path.join(fishImageCache.getCacheDir(), file);
  if (fs.existsSync(fishFull)) {
    const stat = fs.statSync(fishFull);
    res.set('Cache-Control', 'public, max-age=86400, immutable');
    res.set('ETag', `"totem-fishcache-${stat.size}-${Math.floor(stat.mtimeMs)}"`);
    return res.sendFile(fishFull);
  }
  return res.status(404).type('application/json').send({ error: 'totem_asset_not_found', filename: file });
});

// ── GET /api/fishit-tracker/assets/stones/:filename — manual stone image cache (BLOCKER10ZD) ──
router.get('/api/fishit-tracker/assets/stones/:filename', (req, res) => {
  const file = path.basename(String(req.params.filename || ''));
  if (!file || !/^[a-zA-Z0-9._-]+$/.test(file)) {
    return res.status(400).type('text/plain').send('invalid_filename');
  }
  const stoneFull = stoneImageAssets.getStoneAssetFilePath(file);
  if (stoneFull && fs.existsSync(stoneFull)) {
    const stat = fs.statSync(stoneFull);
    res.set('Cache-Control', 'public, max-age=86400, immutable');
    res.set('ETag', `"stone-${stat.size}-${Math.floor(stat.mtimeMs)}"`);
    return res.sendFile(stoneFull);
  }
  const fishFull = path.join(fishImageCache.getCacheDir(), file);
  if (fs.existsSync(fishFull)) {
    const stat = fs.statSync(fishFull);
    res.set('Cache-Control', 'public, max-age=86400, immutable');
    res.set('ETag', `"stone-fishcache-${stat.size}-${Math.floor(stat.mtimeMs)}"`);
    return res.sendFile(fishFull);
  }
  return res.status(404).type('application/json').send({ error: 'stone_asset_not_found', filename: file });
});

// ── GET /api/fishit-tracker/assets/item/:filename — category-neutral inventory asset cache ──
router.get('/api/fishit-tracker/assets/item/:filename', async (req, res) => {
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
    } catch (_) { /* fall through */ }
    return res.status(404).type('application/json').send({ error: 'inventory_asset_not_found', filename: file });
  }
  const stat = fs.statSync(full);
  res.set('Cache-Control', 'public, max-age=86400, immutable');
  res.set('ETag', `"item-${stat.size}-${Math.floor(stat.mtimeMs)}"`);
  return res.sendFile(full);
});

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

function buildTotemScanProof(rows, extra = {}) {
  const names = [];
  const seen = new Set();
  for (const row of (Array.isArray(rows) ? rows : [])) {
    const name = String(row?.name || row?.displayName || '').trim();
    if (!name || seen.has(name)) continue;
    seen.add(name);
    names.push(name);
  }
  const quantity = (Array.isArray(rows) ? rows : []).reduce(
    (sum, row) => sum + (Number(row?.quantity || row?.amount) > 0
      ? Math.floor(Number(row.quantity || row.amount))
      : 1),
    0,
  );
  return {
    count: names.length,
    names,
    quantity,
    source: extra.source || 'playerdata_gameitemdb',
    uploadedTotemCount: extra.uploadedTotemCount != null ? extra.uploadedTotemCount : names.length,
    uploadedTotemQuantity: extra.uploadedTotemQuantity != null ? extra.uploadedTotemQuantity : quantity,
    evidenceMatch: extra.evidenceMatch || null,
  };
}

function applyLitePublicSnapshotFields(session, usesGameItemDb, usesItemUtility, playerDataFishItems, playerDataStoneItems, playerDataTotemItems) {
  if (!session) return session;
  if ((usesGameItemDb || usesItemUtility) && Array.isArray(playerDataFishItems) && playerDataFishItems.length) {
    const grouped = gameItemDbPublic.groupFishRows(playerDataFishItems);
    const cards = grouped.map((row) => gameItemDbPublic.mapToPublicFishCardItem(row));
    session.lastGoodPublicFishItems = cards;
    session.lastGoodPublicFishCount = cards.length;
    session.publicFishItems = cards;
  }
  if ((usesGameItemDb || usesItemUtility) && Array.isArray(playerDataStoneItems) && playerDataStoneItems.length) {
    const grouped = gameItemDbPublic.groupStoneRows(playerDataStoneItems);
    const cards = grouped.map((row) => gameItemDbPublic.mapToPublicStoneCardItem(row));
    session.lastGoodPublicStoneItems = cards;
    session.lastGoodPublicStoneCount = cards.length;
  }
  if ((usesGameItemDb || usesItemUtility) && Array.isArray(playerDataTotemItems) && playerDataTotemItems.length) {
    const grouped = manualInventoryImages.attachManualImagesToItems(
      gameItemDbPublic.groupTotemRows(playerDataTotemItems),
      'totems',
      '',
    );
    const cards = grouped.map((row) => gameItemDbPublic.mapToPublicTotemCardItem(row));
    session.lastGoodPublicTotemItems = cards;
    session.lastGoodPublicTotemCount = cards.length;
  }
  return session;
}

function clearStaleAuditProofFields(session) {
  if (!session || typeof session !== 'object') return session;
  return {
    ...session,
    inventoryItemClassificationDebug: null,
    totemPathAudit: null,
    totemInventoryPathProof: null,
    gameItemDbTotemAudit: null,
    nonFishNonStoneItemGroups: [],
    lastInventorySnapshotDiagnostics: null,
  };
}

function markOutdatedBuildSession(session, gate, now) {
  const base = clearStaleAuditProofFields(session || {});
  const rejected = uploadAccountStatus.markTrackerSyncMissed({
    ...base,
    loaderOutdated: true,
    expectedLoaderBuild: EXPECTED_CLIENT_TRACKER_BUILD,
    lastUploadRejectReason: 'OUTDATED_TRACKER_BUILD',
    lastUploadError: 'OUTDATED_TRACKER_BUILD',
    lastUploadRejectedAt: now,
    lastUploadReceivedAt: now,
    trackerBuild: gate?.proof?.trackerBuild || base.trackerBuild || null,
    lastUploadTrackerBuild: gate?.proof?.trackerBuild || base.lastUploadTrackerBuild || null,
  }, now);
  rejected.latestPayloadAccepted = false;
  return rejected;
}

function ensureSessionBuildCurrent(session) {
  if (!session) return session;
  const build = session.trackerBuild || session.lastUploadTrackerBuild;
  if (!build || isTrustedClientBuild(build)) return session;
  const now = new Date().toISOString();
  return markOutdatedBuildSession(session, { proof: { trackerBuild: build } }, now);
}

// ── POST update-backpack (canonical + legacy alias) ───────────────
// Accepts both:
//   • inventory_snapshot  – the Replion source-of-truth inventory. The items
//     array REPLACES the previous snapshot (counts never accumulate).
//   • tracker_status      – a lightweight online/offline + source ping with no
//     items; keeps the last known inventory and only flips flags.
function handleUpdateBackpack(req, res) {
    const uploadArrivalAt = Date.now();
    const requestBytes = Number(req.headers['content-length'])
      || Buffer.byteLength(JSON.stringify(req.body || {}), 'utf8');
    let responseStatusCode = 200;
    let responseAccepted = null;
    let responseRejectReason = null;
    let responsePayloadType = null;
    let responseUsernameKey = null;
    const origStatus = res.status.bind(res);
    const origJson = res.json.bind(res);
    res.status = (code) => {
      responseStatusCode = code;
      return origStatus(code);
    };
    res.json = (body) => {
      recordUploadRequest({
        route: req.path,
        payloadType: responsePayloadType || (req.body && req.body.type) || 'unknown',
        usernameKey: responseUsernameKey || (req.body?.username ? String(req.body.username).toLowerCase() : '?'),
        contentLength: requestBytes,
        durationMs: Date.now() - uploadArrivalAt,
        statusCode: responseStatusCode,
        accepted: responseAccepted != null ? responseAccepted : body?.accepted !== false,
        rejectReason: responseRejectReason || body?.rejectReason || body?.error || null,
        errorClass: responseStatusCode >= 500 ? 'server' : (responseStatusCode >= 400 ? 'client' : '-'),
      });
      return origJson(body);
    };
    const rawIncomingBody = prepareTrackerRequestBody(req.body || {}, {
      testMode: process.env.NODE_ENV === 'test',
    });
    const isDebugUpload = compactUpload.isDebugUploadBody(rawIncomingBody);
    const parseStart = Date.now();
    const body = isDebugUpload
      ? rawIncomingBody
      : compactUpload.stripHeavyUploadFields(rawIncomingBody);
    const parseMs = Date.now() - parseStart;
    const arrivalType = body.type || 'inventory_snapshot';
    responsePayloadType = arrivalType;
    responseUsernameKey = body.username ? String(body.username).toLowerCase() : null;
    if (arrivalType !== 'tracker_status'
      && !leaderstatsUpload.isLeaderstatsOnlyBody(body)) {
      const auditArrival = extractTotemAuditArrivalMeta(rawIncomingBody);
      console.log(
        '[fishit-tracker] upload_arrival route=%s type=%s user=%s sessionKey=%s build=%s' +
        ' debugUpload=%s compact=%s hasTotemPathAudit=%s hasTotemInventoryPathProof=%s hasClassificationDebug=%s hasGameItemDbTotemAudit=%s' +
        ' nonFishGroups=%d totemCount=%d unresolvedCount=%s requestBytes=%d gate=%j',
        req.path,
        arrivalType,
        body.username || '?',
        body.username ? String(body.username).toLowerCase() : '?',
        body.trackerBuild || body.trackerClientProof?.trackerBuild || 'n/a',
        isDebugUpload,
        !isDebugUpload,
        auditArrival.hasTotemPathAudit,
        auditArrival.hasTotemInventoryPathProof,
        auditArrival.hasClassificationDebug,
        auditArrival.hasGameItemDbTotemAudit,
        auditArrival.nonFishGroups,
        auditArrival.totemCount,
        auditArrival.unresolvedCount == null ? 'n/a' : String(auditArrival.unresolvedCount),
        requestBytes,
        trackerConcurrencyGate.stats(),
      );
    }
    const clientGate = validateTrackerClientProof(body);
    if (!clientGate.ok) {
      const rejectKey = body.username ? String(body.username).toLowerCase() : null;
      responseAccepted = false;
      responseRejectReason = (clientGate.reasons || [])[0] || clientGate.error || 'client_proof_rejected';
      logTrackerUploadProof('rejected', buildUploadProofLogFields(body, rejectKey ? liveTrackDB[rejectKey] : null, {
        usernameKey: rejectKey,
        payloadType: arrivalType,
        accepted: false,
        rejectReason: (clientGate.reasons || [])[0] || clientGate.error || 'client_proof_rejected',
        ownerBinding: rejectKey
          ? (inventoryTrackedAccounts.resolveOwnerDiscordIdForUsernameSync(rejectKey)
            ? 'registered_account_match'
            : 'owner_not_registered')
          : 'no_session_key',
      }));
      console.warn(
        '[fishit-tracker] tracker client rejected route=%s reasons=%s build=%s channel=%s source=%s',
        req.path,
        (clientGate.reasons || []).join(','),
        clientGate.proof?.trackerBuild || 'n/a',
        clientGate.proof?.trackerChannel || 'n/a',
        clientGate.proof?.scriptSource || 'n/a',
      );
      const nowRejected = new Date().toISOString();
      if (rejectKey) {
        const existingRejected = liveTrackDB[rejectKey] || {
          username: sanitiseUsername(body.username),
          userId: Number.isFinite(Number(body.userId)) ? Number(body.userId) : 0,
          items: [],
          inventory: null,
        };
        liveTrackDB[rejectKey] = clientGate.error === 'OUTDATED_TRACKER_BUILD'
          ? markOutdatedBuildSession(existingRejected, clientGate, nowRejected)
          : {
            ...existingRejected,
            lastUploadError: (clientGate.reasons || []).join(',') || clientGate.error,
            lastUploadReceivedAt: nowRejected,
            lastUploadStatusCodeReturned: clientGate.status || 403,
            lastUploadRejectReason: (clientGate.reasons || [])[0] || clientGate.error,
            lastUploadRejectedAt: nowRejected,
          };
      }
      return res.status(clientGate.status || 403).json({
        error: clientGate.error,
        reasons: clientGate.reasons,
        required: clientGate.required,
        allowedTrackerChannel: ALLOWED_TRACKER_CHANNEL,
        allowedTrackerRawUrl: ALLOWED_TRACKER_RAW_URL,
        minimumTrackerBuild: MINIMUM_TRACKER_BUILD,
      });
    }

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
    const nextUploadRequestCount = (Number(existing?.uploadRequestCount) || 0) + 1;
    const withUploadCount = (session) => ({ ...(session || {}), uploadRequestCount: nextUploadRequestCount });
    const isStatusOnly = type === 'tracker_status';
    const cleanUserId = Number.isFinite(Number(userId)) ? Number(userId) : 0;
    const incomingBuild = sanitiseTrackerBuild(body.trackerBuild) || existing?.trackerBuild || null;
    const hadPlayerStats = !!(body.playerStats || body.playerStatsDebug);
    const uploadDebugBase = {
      endpoint: req.path,
      payloadType,
      username: cleanUser,
      sessionKey: key,
      trackerBuild: incomingBuild,
      hadPlayerStats,
    };

    // ── tracker_status heartbeat ──────────────────────────────────
    // Proves the script is running. Creates the session when it does not yet
    // exist, and flips online/phase. NEVER clears inventory or parseStats.
    if (isStatusOnly) {
      const base = existing || { username: cleanUser, userId: cleanUserId, items: [], inventory: null };
      const ps = sanitiseParseStats(body.parseStats) || base.parseStats || null;
      const phaseOut = effectivePhase(phase || base.phase, ps, (base.items || []).length > 0);
      const loaderErr = body.loaderError && typeof body.loaderError === 'object' ? body.loaderError : null;
      liveTrackDB[key] = withUploadCount({
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
        expectedLoaderBuild: EXPECTED_CLIENT_TRACKER_BUILD,
        lastPayloadType: 'tracker_status',
        lastHeartbeatAt: now,
        lastSeenAt:      now,
        lastAccountSeenAt: now,
        ...(online ? {} : { lastOfflineAt: now }),
        lastInventoryAt: base.lastInventoryAt || base.updatedAt || null,
        updatedAt:       now,
        ...applyPlayerStatsFields(base.playerStats ? { playerStats: base.playerStats, ...base } : base, body, now, { isHeartbeat: true }),
      });
      if (loaderErr) {
        liveTrackDB[key].lastLoaderErrorAt = now;
        liveTrackDB[key].lastLoaderErrorMessage = String(loaderErr.errorMessage || '').slice(0, 500);
        liveTrackDB[key].lastLoaderErrorPhase = loaderErr.phase || null;
      }
      if (Array.isArray(body.unresolvedDiagnostics) && body.unresolvedDiagnostics.length) {
        liveTrackDB[key].unresolvedDiagnostics = body.unresolvedDiagnostics.slice(0, 30);
      }
      if (Array.isArray(body.discoveredCatalog) && body.discoveredCatalog.length) {
        liveTrackDB[key].discoveredCatalogIngest = ingestDiscoveredCatalog(body.discoveredCatalog);
      }
      // Store userId→key alias so GET can resolve by userId if needed.
      if (cleanUserId) liveTrackDB['uid:' + cleanUserId] = key;
      const ownerBinding = applySessionOwnerMapping(key);
      const uploadRejected = loaderErr
        || body.uploadFailed === true
        || body.syncFailed === true;
      const rejectReason = loaderErr
        ? (loaderErr.errorMessage || 'loader_error')
        : (body.failureReason || body.failReason || body.lastFailureReason || 'upload_failed');
      const rejectStatusCode = Number(body.lastUploadStatusCode || body.statusCode || body.httpStatus || 0);
      const transientUploadFailure = !loaderErr
        && uploadAccountStatus.isTransientServerUploadFailure(rejectReason, rejectStatusCode);
      if (uploadRejected) {
        if (transientUploadFailure) {
          liveTrackDB[key] = applyTransientUploadFailure(
            liveTrackDB[key],
            now,
            rejectReason,
            rejectStatusCode,
          );
          liveTrackDB[key] = uploadAccountStatus.applyAcceptedUploadMeta(liveTrackDB[key], body, now, {
            heartbeatOnly: true,
          });
          liveTrackDB[key] = applyUploadDebugFields(liveTrackDB[key], {
            ...uploadDebugBase,
            rejected: true,
            rejectReason: liveTrackDB[key].lastFailureReason || rejectReason,
            statusCode: Number.isFinite(rejectStatusCode) && rejectStatusCode > 0 ? rejectStatusCode : 502,
          });
        } else {
          liveTrackDB[key] = uploadAccountStatus.applyRejectedUploadMeta(
            liveTrackDB[key],
            body,
            now,
            rejectReason,
          );
          liveTrackDB[key] = applyUploadSyncFailure(liveTrackDB[key], now, rejectReason);
          liveTrackDB[key] = applyUploadDebugFields(liveTrackDB[key], {
            ...uploadDebugBase,
            rejected: true,
            rejectReason,
            statusCode: 200,
          });
        }
      } else {
        liveTrackDB[key] = uploadAccountStatus.applyAcceptedUploadMeta(liveTrackDB[key], body, now, {
          heartbeatOnly: true,
        });
        liveTrackDB[key] = snapshotCompleteness.applyHeartbeatUpdate(liveTrackDB[key], body, now);
        liveTrackDB[key] = applyUploadDebugFields(liveTrackDB[key], {
          ...uploadDebugBase,
          accepted: true,
          statusCode: 200,
        });
        liveTrackDB[key].lastHeartbeatDiagnostics = buildHeartbeatDiagnostics(body, liveTrackDB[key], now);
      }
      logTrackerUploadProof('heartbeat', buildUploadProofLogFields(body, liveTrackDB[key], {
        usernameKey: key,
        payloadType: 'tracker_status',
        accepted: !uploadRejected,
        rejectReason: uploadRejected ? rejectReason : null,
        ownerBinding: ownerBinding.reason,
      }));
      // Server-side log.
      console.log(
        `[fishit-tracker] POST hit route=${req.path} user=${cleanUser} sessionKey=${key}` +
        ` userId=${cleanUserId} payloadType=tracker_status accepted=${uploadRejected ? 0 : 1} ok=true` +
        ` lastSeenAt=${now} lastInventoryAt=${liveTrackDB[key].lastInventoryAt || 'n/a'} online=${online}`
      );
      const uploadStatus = uploadAccountStatus.deriveTrackerUploadAccountStatus(liveTrackDB[key], {
        serverNowMs: Date.now(),
        expectedTrackerBuild: EXPECTED_CLIENT_TRACKER_BUILD,
        isTrustedBuild: isTrustedClientBuild,
      });
      const conn = deriveConnectionStatus(liveTrackDB[key]);
      scheduleAioTrackerCacheRefresh(key);
      persistSessionHeartbeat(key);
      scheduleIngestPostResponseFlush(res);
      return res.status(200).json({
        ok: true,
        status: 'success',
        accepted: true,
        note: 'status_only',
        phase: liveTrackDB[key].phase,
        lastSeenAt: now,
        lastInventoryAt: liveTrackDB[key].lastInventoryAt || null,
        online: uploadStatus.statusColor !== 'red',
        currentStatus: uploadStatus.statusColor,
        accountOnlineStatus: uploadStatus.status,
        inGameStatus: uploadStatus.statusColor !== 'red',
        uploadSyncFresh: conn.currentStatus === 'green',
        uploadSyncStatus: conn.connectionStatusReason || null,
        connectionStatus: uploadStatus.status,
        serverTime: now,
        heartbeatAccepted: true,
        ...uploadStatus,
      });
    }

    // ── required_leaderstats only (tiny fast path — no inventory work) ──
    if (leaderstatsUpload.isLeaderstatsOnlyBody(body)) {
      const base = existing || { username: cleanUser, userId: cleanUserId, items: [], inventory: null };
      const leaderFields = leaderstatsUpload.applyLeaderstatsUploadFields(base, body, now);
      liveTrackDB[key] = withUploadCount({
        ...base,
        username: cleanUser,
        userId: cleanUserId || base.userId || 0,
        source: source !== 'unknown' ? source : (base.source || source),
        items: base.items || [],
        inventory: base.inventory || null,
        isOnline: online,
        phase: effectivePhase(phase || base.phase, base.parseStats, (base.items || []).length > 0),
        trackerBuild: incomingBuild || base.trackerBuild || null,
        lastPayloadType: 'required_leaderstats',
        lastSeenAt: now,
        lastAccountSeenAt: now,
        updatedAt: now,
        lastInventoryAt: base.lastInventoryAt || base.updatedAt || null,
        ...leaderFields,
      });
      liveTrackDB[key] = applyUploadDebugFields(liveTrackDB[key], {
        ...uploadDebugBase,
        accepted: liveTrackDB[key].leaderstatsUploadOk === true,
        statusCode: 202,
      });
      if (cleanUserId) liveTrackDB['uid:' + cleanUserId] = key;
      applySessionOwnerMapping(key);
      scheduleAioTrackerCacheRefresh(key);
      persistSessionHeartbeat(key);
      scheduleIngestPostResponseFlush(res);
      responseAccepted = liveTrackDB[key].leaderstatsUploadOk === true;
      const coalesced = req.trackerUploadCoalesced === true;
      console.log(
        '[fishit-tracker] leaderstats_fast_path user=%s sessionKey=%s accepted=%d coalesced=%s responseMs=%d',
        cleanUser,
        key,
        responseAccepted ? 1 : 0,
        coalesced ? '1' : '0',
        Date.now() - uploadArrivalAt,
      );
      return res.status(202).json({
        ok: true,
        accepted: true,
        coalesced,
        lane: 'required_leaderstats',
        minNextUploadSeconds: leaderstatsUpload.MIN_NEXT_UPLOAD_SECONDS,
        leaderstatsUploadOk: liveTrackDB[key].leaderstatsUploadOk === true,
        lastValidLeaderstatsAt: liveTrackDB[key].lastValidLeaderstatsAt || null,
        lastSeenAt: now,
        serverTime: now,
        note: 'leaderstats_only_fast_path',
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
      existing.lastSeenAt = now;
      existing.lastAccountSeenAt = now;
      existing.lastOfflineAt = now;
      liveTrackDB[key] = withUploadCount(applyUploadDebugFields(existing, {
        ...uploadDebugBase,
        accepted: true,
        statusCode: 200,
      }));
      scheduleAioTrackerCacheRefresh(key);
      persistSessionHeartbeat(key);
      scheduleIngestPostResponseFlush(res);
      return res.status(200).json({ status: 'success', note: 'offline_keep' });
    }

    // ── Inventory snapshot ────────────────────────────────────────
    const expectsPlayerDataGameItemDb = /BLOCKER10ZL_|BLOCKER10Z[ABC]|PLAYERDATA_GAMEITEMDB|TOTEM_|UPLOAD_COMPACT/i.test(incomingBuild);
    const isPlayerDataPayload = gameItemDbPublic.detectGameItemDbUpload(body);
    if (expectsPlayerDataGameItemDb && payloadType === 'inventory_snapshot' && !isPlayerDataPayload) {
      liveTrackDB[key] = withUploadCount({
        ...(existing || { username: cleanUser, userId: cleanUserId, items: [], inventory: null }),
        username: cleanUser,
        userId: cleanUserId || existing?.userId || 0,
        source: source !== 'unknown' ? source : (existing?.source || source),
        items: existing?.items || [],
        inventory: existing?.inventory || null,
        isOnline: online,
        phase: existing?.phase || phase || 'live',
        trackerBuild: incomingBuild || null,
        lastPayloadType: 'inventory_snapshot',
        lastSeenAt: now,
        updatedAt: now,
        lastInventoryAt: existing?.lastInventoryAt || existing?.updatedAt || null,
        legacySnapshotIgnored: true,
        legacySnapshotIgnoreReason: 'missing_playerdata_gameitemdb_inventorySource',
        inventorySource: existing?.inventorySource || null,
        playerDataFishItems: existing?.playerDataFishItems || null,
        playerDataStoneItems: existing?.playerDataStoneItems || null,
        sourceTruth: existing?.sourceTruth || null,
        playerDataGameItemDbProof: existing?.playerDataGameItemDbProof || null,
      });
      if (cleanUserId) liveTrackDB['uid:' + cleanUserId] = key;
      liveTrackDB[key] = applyUploadDebugFields(liveTrackDB[key], {
        ...uploadDebugBase,
        accepted: true,
        statusCode: 200,
      });
      console.log(
        `[fishit-tracker] legacy snapshot ignored user=${cleanUser} sessionKey=${key}` +
        ' reason=missing_playerdata_gameitemdb_inventorySource',
      );
      return res.status(200).json({
        ok: true,
        status: 'success',
        legacySnapshotIgnored: true,
        ignoreReason: 'missing_playerdata_gameitemdb_inventorySource',
        lastSeenAt: now,
        online: isSessionLive(liveTrackDB[key]),
      });
    }

    let rawItems = normaliseInventoryItems(body);
    const gateWaitMs = 0;
    const rawPersistStart = Date.now();
    const ps         = sanitiseParseStats(body.parseStats);
    const fishPathDiscovery = partialSnapshot.sanitiseFishPathDiscovery(body.fishPathDiscovery
      || body.parseStats?.fishPathDiscovery);
    let cleanItems = mergeItemsNoDowngradeFromCatalog(rawItems);
    if (existing && existing.items && cleanItems.length) {
      cleanItems = mergeItemsNoDowngrade(cleanItems, existing.items);
    }
    let inventory  = buildInventoryGroups(cleanItems);

    const usesGameItemDb = gameItemDbPublic.detectGameItemDbUpload(body)
      && (Array.isArray(body.fishItems) || Array.isArray(body.stoneItems) || Array.isArray(body.totemItems));
    const earlyPlayerDataFishCount = usesGameItemDb && Array.isArray(body.fishItems)
      ? body.fishItems.length
      : 0;
    const earlyPlayerDataStoneCount = usesGameItemDb && Array.isArray(body.stoneItems)
      ? body.stoneItems.length
      : 0;

    const priorPublicFishCount = existing?.lastGoodPublicFishCount || 0;
    let partialInfo = partialSnapshot.detectPartialZeroFishSnapshot({
      ps,
      cleanItems,
      existing,
      priorPublicFishCount,
      playerDataFishCount: earlyPlayerDataFishCount,
      playerDataStoneCount: earlyPlayerDataStoneCount,
      usesPlayerDataGameItemDb: usesGameItemDb,
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

    const usesItemUtility = !usesGameItemDb
      && body.inventorySource === itemUtilityPublic.PLAYERDATA_ITEMUTILITY_SOURCE
      && Array.isArray(body.fishItems);
    const playerDataFishItems = usesGameItemDb
      ? gameItemDbPublic.normaliseUploadRows(body.fishItems || [])
      : (usesItemUtility ? body.fishItems.filter(itemUtilityPublic.isPlayerDataItemUtilityRow) : null);
    const playerDataStoneItemsRaw = usesGameItemDb
      ? gameItemDbPublic.normaliseUploadRows(body.stoneItems || []).filter((row) => row && row.kind === 'stone')
      : ((usesItemUtility && Array.isArray(body.stoneItems))
        ? body.stoneItems.filter(itemUtilityPublic.isPlayerDataItemUtilityRow)
        : []);
    const playerDataStoneItems = playerDataStoneItemsRaw.length
      ? gameItemDbPublic.groupStoneRows(playerDataStoneItemsRaw)
      : playerDataStoneItemsRaw;
    const playerDataTotemItems = usesGameItemDb
      ? gameItemDbPublic.normaliseUploadRows(body.totemItems || []).filter((row) => row && row.kind === 'totem')
      : ((usesItemUtility && Array.isArray(body.totemItems))
        ? body.totemItems.filter((row) => gameItemDbPublic.isTotemRow(row))
        : []);

    const hasDisplayItems = cleanItems.length > 0
      || (partialInfo.lastGoodFishPreserved && existing && existing.items?.length)
      || (usesGameItemDb && (
        (playerDataFishItems && playerDataFishItems.length > 0)
        || (playerDataStoneItems && playerDataStoneItems.length > 0)
        || (playerDataTotemItems && playerDataTotemItems.length > 0)
      ))
      || ((usesItemUtility) && playerDataFishItems && playerDataFishItems.length > 0);
    const sessionPhase = partialInfo.lastGoodFishPreserved
      ? 'live'
      : effectivePhase(phase, ps, hasDisplayItems);
    const completenessEval = snapshotCompleteness.evaluateSnapshotCompleteness({
      body,
      existing,
      cleanItems,
      playerDataFishItems,
      playerDataStoneItems,
      playerDataTotemItems,
      parseStats: ps,
      partialInfo,
      isHeartbeat: false,
      now,
    });

    let nextFishItems = (usesGameItemDb || usesItemUtility) ? playerDataFishItems : null;
    let nextStoneItems = (usesGameItemDb || usesItemUtility) ? playerDataStoneItems : null;
    let nextTotemItems = (usesGameItemDb || usesItemUtility) ? playerDataTotemItems : null;
    let nextItems = cleanItems.length ? cleanItems : (existing ? existing.items : []);
    let nextRawItems = rawItems.length ? rawItems : (existing ? existing.rawItems : []);
    let nextInventory = cleanItems.length ? inventory : (existing ? existing.inventory : null);
    let nextPlayerStatsFields = applyPlayerStatsFields(existing, body, now);
    const leaderstatsEval = leaderstatsUpload.evaluateIncomingLeaderstats(body, existing, now);

    if (completenessEval.preserveExistingInventory && existing) {
      if (!nextItems.length && existing.items?.length) nextItems = existing.items;
      if (!nextRawItems.length && existing.rawItems?.length) nextRawItems = existing.rawItems;
      if (!nextInventory && existing.inventory) nextInventory = existing.inventory;
      if ((!nextFishItems || !nextFishItems.length) && existing.playerDataFishItems?.length) {
        nextFishItems = existing.playerDataFishItems;
      }
      if ((!nextStoneItems || !nextStoneItems.length) && existing.playerDataStoneItems?.length) {
        nextStoneItems = existing.playerDataStoneItems;
      }
      if ((!nextTotemItems || !nextTotemItems.length) && existing.playerDataTotemItems?.length) {
        nextTotemItems = existing.playerDataTotemItems;
      }
    }

    const syncEval = uploadAccountStatus.evaluateAcceptedSnapshotSync({
      completenessEval,
      acceptedCount,
      body,
      playerDataFishItems,
      playerDataStoneItems,
      playerDataTotemItems,
      nextPlayerStatsFields,
      leaderstatsEval,
      uploadRejected: false,
      now,
    });
    const inventoryTimestamps = syncEval.hasInventory
      ? {
        lastInventoryAt: now,
        lastSnapshotUploadAt: now,
      }
      : {
        lastInventoryAt: existing?.lastInventoryAt || existing?.updatedAt || null,
        lastSnapshotUploadAt: existing?.lastSnapshotUploadAt || existing?.lastInventoryAt || null,
      };
    const leaderstatsTimestamps = syncEval.accepted
      ? { lastSuccessfulUploadAt: now }
      : { lastSuccessfulUploadAt: existing?.lastSuccessfulUploadAt || null };

    const shouldProcessAudit = isDebugUpload
      && (usesGameItemDb || isTotemAuditTrackerBuild(incomingBuild));
    const totemAuditFields = shouldProcessAudit
      ? resolveTotemAuditFields(rawIncomingBody, existing, incomingBuild)
      : {
        inventoryItemClassificationDebug: existing?.inventoryItemClassificationDebug || null,
        totemPathAudit: existing?.totemPathAudit || null,
        totemInventoryPathProof: existing?.totemInventoryPathProof || null,
        gameItemDbTotemAudit: existing?.gameItemDbTotemAudit || null,
        nonFishNonStoneItemGroups: existing?.nonFishNonStoneItemGroups || [],
      };
    const snapshotDiagnosticsBody = shouldProcessAudit
      ? {
        ...rawIncomingBody,
        inventoryItemClassificationDebug: totemAuditFields.inventoryItemClassificationDebug,
        totemPathAudit: totemAuditFields.totemPathAudit,
        totemInventoryPathProof: totemAuditFields.totemInventoryPathProof,
        gameItemDbTotemAudit: totemAuditFields.gameItemDbTotemAudit,
        nonFishNonStoneItemGroups: totemAuditFields.nonFishNonStoneItemGroups,
      }
      : null;
    const mergedGameItemDbProof = usesGameItemDb
      ? (shouldProcessAudit
        ? {
          ...(rawIncomingBody.playerDataGameItemDbProof || existing?.playerDataGameItemDbProof || {}),
          inventoryItemClassificationDebug: totemAuditFields.inventoryItemClassificationDebug,
          totemPathAudit: totemAuditFields.totemPathAudit,
          totemInventoryPathProof: totemAuditFields.totemInventoryPathProof,
          gameItemDbTotemAudit: totemAuditFields.gameItemDbTotemAudit,
          nonFishNonStoneItemGroups: totemAuditFields.nonFishNonStoneItemGroups,
        }
        : compactUpload.buildCompactGameItemDbProof(body, existing?.playerDataGameItemDbProof))
      : (existing?.playerDataGameItemDbProof || null);

    // Store under username key + userId alias.
    liveTrackDB[key] = withUploadCount(snapshotCompleteness.applyCompletenessFields({
      username:        cleanUser,
      userId:          cleanUserId,
      source,
      rawItems:        nextRawItems,
      items:           nextItems,
      inventory:       nextInventory,
      isOnline:        online,
      phase:           completenessEval.snapshotComplete ? sessionPhase : (existing?.phase || sessionPhase),
      parseStats:      ps || (existing && existing.parseStats) || null,
      fishPathDiscovery: fishPathDiscovery || (existing && existing.fishPathDiscovery) || null,
      trackerBuild:    sanitiseTrackerBuild(body.trackerBuild) || (existing && existing.trackerBuild) || null,
      expectedLoaderBuild: EXPECTED_CLIENT_TRACKER_BUILD,
      lastPayloadType: completenessEval.payloadType || (cleanItems.length ? 'inventory_snapshot' : (type || 'inventory_snapshot')),
      lastSeenAt:      now,
      lastAccountSeenAt: now,
      ...inventoryTimestamps,
      ...leaderstatsTimestamps,
      ...(online ? { lastHeartbeatAt: now, lastSuccessfulHeartbeatAt: now } : { lastOfflineAt: now }),
      lastStatsChangeAt: existing?.lastStatsChangeAt || null,
      updatedAt:       now,
      partialSnapshotDetected: partialInfo.partialSnapshotDetected || completenessEval.quarantineBlankInventory || false,
      partialSnapshotReason: partialInfo.partialSnapshotReason
        || (completenessEval.quarantineBlankInventory ? completenessEval.snapshotCompletenessReason : null),
      lastGoodFishPreserved: partialInfo.lastGoodFishPreserved || completenessEval.preserveExistingInventory || false,
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
      inventorySource: usesGameItemDb
        ? gameItemDbPublic.PLAYERDATA_GAMEITEMDB_SOURCE
        : (usesItemUtility
          ? itemUtilityPublic.PLAYERDATA_ITEMUTILITY_SOURCE
          : (existing?.inventorySource || null)),
      playerDataFishItems: nextFishItems,
      playerDataStoneItems: nextStoneItems,
      playerDataTotemItems: nextTotemItems,
      totemScanProof: buildTotemScanProof(nextTotemItems, {
        uploadedTotemCount: Array.isArray(body.totemItems) ? body.totemItems.length : 0,
        uploadedTotemQuantity: (Array.isArray(body.totemItems) ? body.totemItems : []).reduce(
          (s, row) => s + (Number(row?.quantity) > 0 ? Math.floor(Number(row.quantity)) : 1),
          0,
        ),
      }),
      inventoryItemClassificationDebug: shouldProcessAudit
        ? totemAuditFields.inventoryItemClassificationDebug
        : (existing?.inventoryItemClassificationDebug || null),
      totemPathAudit: shouldProcessAudit
        ? totemAuditFields.totemPathAudit
        : (existing?.totemPathAudit || null),
      totemInventoryPathProof: shouldProcessAudit
        ? totemAuditFields.totemInventoryPathProof
        : (existing?.totemInventoryPathProof || null),
      gameItemDbTotemAudit: shouldProcessAudit
        ? totemAuditFields.gameItemDbTotemAudit
        : (existing?.gameItemDbTotemAudit || null),
      nonFishNonStoneItemGroups: shouldProcessAudit
        ? totemAuditFields.nonFishNonStoneItemGroups
        : (existing?.nonFishNonStoneItemGroups || []),
      lastInventorySnapshotDiagnostics: shouldProcessAudit
        ? buildInventorySnapshotDiagnostics(snapshotDiagnosticsBody, existing)
        : (existing?.lastInventorySnapshotDiagnostics || null),
      lastInventorySnapshotAt: shouldProcessAudit
        ? now
        : (existing?.lastInventorySnapshotAt || null),
      lastInventorySnapshotBuild: shouldProcessAudit
        ? (incomingBuild || existing?.lastInventorySnapshotBuild || null)
        : (existing?.lastInventorySnapshotBuild || null),
      lastInventorySnapshotPayloadType: shouldProcessAudit
        ? (payloadType || 'inventory_snapshot')
        : (existing?.lastInventorySnapshotPayloadType || null),
      playerDataGameItemDbProof: mergedGameItemDbProof,
      playerDataHiddenUnresolved: usesItemUtility
        ? (Array.isArray(body.hiddenUnresolvedRows) ? body.hiddenUnresolvedRows.slice(0, 50) : [])
        : (existing?.playerDataHiddenUnresolved || []),
      playerDataItemUtilityProof: usesItemUtility && body.playerDataItemUtilityProof
        ? body.playerDataItemUtilityProof
        : (existing?.playerDataItemUtilityProof || null),
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
      ...nextPlayerStatsFields,
    }, completenessEval, now));

    applyLitePublicSnapshotFields(
      liveTrackDB[key],
      usesGameItemDb,
      usesItemUtility,
      playerDataFishItems,
      playerDataStoneItems,
      playerDataTotemItems,
    );
    if (Array.isArray(body.unresolvedDiagnostics) && body.unresolvedDiagnostics.length) {
      liveTrackDB[key].unresolvedDiagnostics = body.unresolvedDiagnostics.slice(0, 20);
    }
    liveTrackDB[key].lastItemCounts = catchDelta.buildItemCountsFromItems(rawItems);
    if (cleanUserId) liveTrackDB['uid:' + cleanUserId] = key;
    const ownerBinding = applySessionOwnerMapping(key);
    liveTrackDB[key] = uploadAccountStatus.applyAcceptedUploadMeta(liveTrackDB[key], body, now);
    liveTrackDB[key] = applyUploadDebugFields(liveTrackDB[key], {
      ...uploadDebugBase,
      accepted: syncEval.accepted,
      statusCode: 200,
      requestBytes,
      responseMs: Date.now() - uploadArrivalAt,
      compact: !isDebugUpload,
      debugUpload: isDebugUpload,
    });
    if (syncEval.accepted) {
      const payloadHash = computeUploadPayloadHash(body, acceptedCount);
      liveTrackDB[key].inventoryChanged = payloadHash !== (existing?.lastPayloadHash || null);
      liveTrackDB[key] = uploadAccountStatus.markTrackerSyncSuccess(liveTrackDB[key], now, {
        syncReason: syncEval.reason,
        lastStatsUpdatedAt: liveTrackDB[key].lastStatsUploadAt
          || liveTrackDB[key].playerStatsUpdatedAt
          || now,
        payloadHash,
        intervalSeconds: body.intervalSeconds || body.syncIntervalSeconds,
        loaderOutdated: !!(incomingBuild && incomingBuild !== EXPECTED_CLIENT_TRACKER_BUILD),
        lastInventoryAt: inventoryTimestamps.lastInventoryAt || now,
        lastSnapshotUploadAt: inventoryTimestamps.lastSnapshotUploadAt || now,
        expectedLoaderBuild: EXPECTED_CLIENT_TRACKER_BUILD,
      });
      leaderstatsUpload.logLeaderstatsUploadProof(cleanUser, leaderstatsEval, now);
    } else if (
      syncEval.reason === 'inventory_without_leaderstats'
      || completenessEval.rejectBlankInventory
      || completenessEval.blankPayloadRejected
    ) {
      liveTrackDB[key] = uploadAccountStatus.markTrackerSyncMissed(liveTrackDB[key], now);
      leaderstatsUpload.logLeaderstatsUploadProof(cleanUser, leaderstatsEval, now);
    } else if (online) {
      liveTrackDB[key] = uploadAccountStatus.markTrackerHeartbeatSuccess(liveTrackDB[key], now, {
        syncReason: 'upload_received_pending_enrichment',
        intervalSeconds: body.intervalSeconds || body.syncIntervalSeconds,
        expectedLoaderBuild: EXPECTED_CLIENT_TRACKER_BUILD,
        loaderOutdated: !!(incomingBuild && incomingBuild !== EXPECTED_CLIENT_TRACKER_BUILD),
      });
    }

    const rawPersistMs = Date.now() - rawPersistStart;
    const persistBase = `${req.protocol}://${req.get('host')}`;
    const cacheRefreshStart = Date.now();
    scheduleAioTrackerCacheRefresh(key);
    const cacheRefreshMs = Date.now() - cacheRefreshStart;
    const deferredSnapshotSeq = (Number(liveTrackDB[key].deferredSnapshotSeq) || 0) + 1;
    liveTrackDB[key].deferredSnapshotSeq = deferredSnapshotSeq;

    console.log(
      '[fishit-tracker] upload_persist user=%s sessionKey=%s accepted=%d snapshotComplete=%s' +
      ' fishCount=%d stoneCount=%d totemCount=%d totemQuantity=%d' +
      ' persistedTotemPathAudit=%s persistedClassificationDebug=%s persistedNonFishGroups=%d' +
      ' serverReceivedAt=%s cacheRefresh=scheduled gate=%j rawPersistMs=%d',
      cleanUser,
      key,
      acceptedCount,
      completenessEval.snapshotComplete === true,
      Array.isArray(playerDataFishItems) ? playerDataFishItems.length : 0,
      Array.isArray(playerDataStoneItems) ? playerDataStoneItems.length : 0,
      Array.isArray(playerDataTotemItems) ? playerDataTotemItems.length : 0,
      (Array.isArray(playerDataTotemItems) ? playerDataTotemItems : []).reduce(
        (s, row) => s + (Number(row?.quantity) > 0 ? Math.floor(Number(row.quantity)) : 1),
        0,
      ),
      !!liveTrackDB[key]?.totemPathAudit,
      !!liveTrackDB[key]?.inventoryItemClassificationDebug,
      Array.isArray(liveTrackDB[key]?.nonFishNonStoneItemGroups)
        ? liveTrackDB[key].nonFishNonStoneItemGroups.length
        : 0,
      now,
      trackerConcurrencyGate.stats(),
      rawPersistMs,
    );
    logTrackerUploadProof('inventory_snapshot', buildUploadProofLogFields(body, liveTrackDB[key], {
      usernameKey: key,
      payloadType: payloadType,
      completenessEval,
      accepted: syncEval.accepted === true,
      rejectReason: syncEval.accepted ? null : syncEval.reason,
      ownerBinding: ownerBinding.reason,
    }));
    const responsePayload = {
      ok: true,
      status: 'success',
      accepted: completenessEval.snapshotComplete,
      acceptedCount,
      snapshotComplete: completenessEval.snapshotComplete === true,
      inventoryReady: completenessEval.inventoryReady === true,
      snapshotCompletenessReason: completenessEval.snapshotCompletenessReason,
      blankPayloadRejected: completenessEval.blankPayloadRejected === true,
      lastInventoryAt: liveTrackDB[key].lastInventoryAt || null,
      lastSeenAt: now,
      online: true,
      serverTime: now,
      heartbeatAccepted: true,
    };
    const totalResponseMs = Date.now() - uploadArrivalAt;
    trackerConcurrencyGate.logUploadTiming({
      upload_arrival_at: new Date(uploadArrivalAt).toISOString(),
      username: cleanUser,
      sessionKey: key,
      userId: cleanUserId,
      gate_wait_ms: gateWaitMs,
      raw_persist_ms: rawPersistMs,
      cache_refresh_ms: cacheRefreshMs,
      enrichment_queue_ms: 0,
      enrichment_ms: 0,
      total_response_ms: totalResponseMs,
      pending_queue: trackerConcurrencyGate.stats().queued,
      per_account_pending: trackerConcurrencyGate.perAccountPendingCount(key),
    });
    console.log(
      '[fishit-tracker] upload_fast_path username=%s requestBytes=%d parseMs=%d persistMs=%d responseMs=%d debugUpload=%s',
      cleanUser,
      requestBytes,
      parseMs,
      rawPersistMs,
      totalResponseMs,
      isDebugUpload,
    );
    trackerConcurrencyGate.logQueueStatus();

    const deferredBody = body;
    const deferredRawItems = rawItems;
    const deferredCleanItems = cleanItems;
    const deferredPartialInfo = partialInfo;
    const deferredAcceptedCount = acceptedCount;
    trackerConcurrencyGate.scheduleDeferredUploadWork(key, (queueMeta = {}) => {
      const enrichStart = Date.now();
      const learnedIngest = ingestLearnedFishCatalogFromBody(deferredBody);
      const evidenceSourceMode = liveCatchProof.resolveEvidenceSourceMode(deferredBody);
      const globalContext = {
        enabled: true,
        userId: cleanUserId,
        userIdHash: globalFishCatalog.hashContributorId(cleanUserId),
        gameId: deferredBody.gameId || deferredBody.game_id || null,
        placeId: deferredBody.placeId || deferredBody.place_id || null,
        gameVersion: deferredBody.gameVersion || deferredBody.game_version || null,
        evidenceSourceMode,
        sessionKey: key,
      };
      if (deferredSnapshotSeq !== liveTrackDB[key]?.deferredSnapshotSeq) {
        return { skipped: true, reason: 'superseded_before_start' };
      }
      catalogStore.learnFromTrackerItems(deferredRawItems);
      let nameCatalogDiscovery = runCatchDeltaOnUpload(
        deferredBody,
        deferredRawItems,
        liveTrackDB[key],
        key,
      );
      const pendingCatchPayload = deferredBody.pendingCatchName || deferredBody.pendingCatch;
      if (pendingCatchPayload) {
        const bindingProof = catchDelta.attemptCatchSnapshotBinding({
          pendingCatch: pendingCatchPayload,
          previousItems: existing?.items || [],
          currentItems: deferredRawItems,
          mainCatalogLookup: (id) => catalogStore.lookupById(id),
          ingestLearned: ingestLearnedFishEntry,
          globalContext,
          existingDiscovery: nameCatalogDiscovery,
        });
        if (nameCatalogDiscovery) {
          if (!nameCatalogDiscovery.catchToSnapshotBindingProof) {
            nameCatalogDiscovery.catchToSnapshotBindingProof = bindingProof;
          }
          if (!nameCatalogDiscovery.nextExpectedAction && bindingProof.nextExpectedAction) {
            nameCatalogDiscovery.nextExpectedAction = bindingProof.nextExpectedAction;
          }
        } else if (bindingProof.attempted) {
          nameCatalogDiscovery = {
            catchToSnapshotBindingProof: bindingProof,
            lastCatchParsed: catchDelta.sanitisePendingCatch(pendingCatchPayload),
            nextExpectedAction: bindingProof.nextExpectedAction,
          };
        }
      }
      if (deferredSnapshotSeq !== liveTrackDB[key]?.deferredSnapshotSeq) {
        return { skipped: true, reason: 'superseded_mid_enrichment' };
      }
      const session = liveTrackDB[key];
      if (!session) return { skipped: true, reason: 'session_missing' };
      const enrichedForGood = enrichItemsFromCatalog(session.items || deferredCleanItems);
      let publicFishCount = enrichedForGood.filter(isPublicFishItem).length;
      if (usesGameItemDb && playerDataFishItems) {
        publicFishCount = gameItemDbPublic.groupFishRows(playerDataFishItems).length;
      } else if (usesItemUtility && playerDataFishItems) {
        publicFishCount = itemUtilityPublic.groupFishRows(playerDataFishItems).length;
      }
      partialSnapshot.updateLastGoodFishOnSession(
        session,
        deferredCleanItems,
        publicFishCount,
        deferredPartialInfo,
      );
      const recoveryMeta = snapshotRecovery.getSessionRecoveryMeta(key, existing);
      if (recoveryMeta) session.userSnapshotRecovery = recoveryMeta;
      try {
        recordGlobalObservationsFromItems(enrichedForGood, {
          userId: cleanUserId,
          sessionKey: key,
          gameId: deferredBody.gameId || null,
          placeId: deferredBody.placeId || null,
        });
        if (Array.isArray(deferredBody.discoveredCatalog) && deferredBody.discoveredCatalog.length) {
          session.discoveredCatalogIngest = ingestDiscoveredCatalog(deferredBody.discoveredCatalog);
        }
        if (learnedIngest.length) {
          session.learnedFishCatalogIngest = learnedIngest;
        }
        if (nameCatalogDiscovery) {
          session.nameCatalogDiscovery = nameCatalogDiscovery;
          if (nameCatalogDiscovery.globalEvidence?.accepted
              && nameCatalogDiscovery.lastCatchParsed?.baseFishName) {
            snapshotRecovery.registerLiveCatchSpeciesEvidence(
              key,
              nameCatalogDiscovery.lastCatchParsed.baseFishName,
              nameCatalogDiscovery.globalEvidence.observationId
                || nameCatalogDiscovery.liveCatchPendingObservationId,
            );
          }
        }
      } catch (bgErr) {
        console.warn('[fishit-tracker] deferred upload work failed:', bgErr?.message || bgErr);
      }
      if (isDebugUpload && deferredSnapshotSeq === liveTrackDB[key]?.deferredSnapshotSeq) {
        const auditFields = resolveTotemAuditFields(deferredBody, session, incomingBuild);
        session.inventoryItemClassificationDebug = auditFields.inventoryItemClassificationDebug;
        session.totemPathAudit = auditFields.totemPathAudit;
        session.totemInventoryPathProof = auditFields.totemInventoryPathProof;
        session.gameItemDbTotemAudit = auditFields.gameItemDbTotemAudit;
        session.nonFishNonStoneItemGroups = auditFields.nonFishNonStoneItemGroups;
        session.lastInventorySnapshotDiagnostics = buildInventorySnapshotDiagnostics({
          ...deferredBody,
          ...auditFields,
        }, session);
        session.lastInventorySnapshotAt = new Date().toISOString();
        session.lastInventorySnapshotBuild = incomingBuild || session.trackerBuild || null;
        session.lastInventorySnapshotPayloadType = payloadType || 'inventory_snapshot';
      }
      const enrichmentMs = Date.now() - enrichStart;
      trackerConcurrencyGate.logUploadTiming({
        upload_arrival_at: new Date(uploadArrivalAt).toISOString(),
        username: cleanUser,
        sessionKey: key,
        userId: cleanUserId,
        gate_wait_ms: gateWaitMs,
        raw_persist_ms: rawPersistMs,
        cache_refresh_ms: cacheRefreshMs,
        enrichment_queue_ms: queueMeta.enrichmentQueueMs || 0,
        enrichment_ms: enrichmentMs,
        total_response_ms: totalResponseMs,
        pending_queue: trackerConcurrencyGate.stats().queued,
        per_account_pending: trackerConcurrencyGate.perAccountPendingCount(key),
      });
      return persistSessionState(key, persistBase).catch(() => {});
    });

    console.log(
      `[fishit-tracker] POST ok=true user=${cleanUser} sessionKey=${key}` +
      ` accepted=${deferredAcceptedCount} snapshotComplete=${completenessEval.snapshotComplete === true}` +
      ` lastSeenAt=${now} lastInventoryAt=${liveTrackDB[key].lastInventoryAt || 'n/a'} online=true` +
      ` fastPathMs=${totalResponseMs}`
    );
    persistSessionHeartbeat(key);
    return finishTrackerUploadResponse(req, res, responsePayload, key);
}

const updateBackpackMiddleware = [
  express.json({ limit: process.env.TRACKER_UPLOAD_BODY_LIMIT || '512kb' }),
  trackerUploadCoalesceMiddleware,
  safeTrackerUploadHandler(
    'update-backpack',
    trackerConcurrencyGate.wrapTrackerUpload('update-backpack', handleUpdateBackpack),
  ),
];

function handleUpdateCatalog(req, res) {
  const body = req.body || {};
  const { type } = body;

  if (type === 'catalog_summary') {
    const stats = body.catalogStats || {};
    console.log(
      `[fishit-tracker] recv catalog_summary user=${body.playerName || '?'}` +
      ` fish=${stats.fish || 0} rods=${stats.rods || 0} items=${stats.items || 0}` +
      ` images=${stats.images || 0} metadataByIdKeys=${stats.metadataByIdKeys || 0}`
    );
    return res.status(200).json({ status: 'success', type: 'catalog_summary', stats });
  }

  if (type !== 'fish_catalog_snapshot' || !body.catalog || typeof body.catalog !== 'object') {
    return res.status(400).json({ error: 'Invalid catalog payload. Expected type=catalog_summary or fish_catalog_snapshot.' });
  }
  const summary = catalogStore.ingestSnapshot(body);
  return res.status(200).json({ status: 'success', ...summary });
}

const updateCatalogMiddleware = [
  express.json({ limit: process.env.TRACKER_UPLOAD_BODY_LIMIT || '512kb' }),
  trackerUploadCoalesceMiddleware,
  safeTrackerUploadHandler('update-catalog', handleUpdateCatalog),
];

const uploadRouter = express.Router();

function registerTrackerUploadRoutes(target) {
  target.post('/api/fishit-tracker/update-backpack', updateBackpackMiddleware);
  target.post('/api/fish-it-tracker/update-backpack', updateBackpackMiddleware);
  target.post('/api/tracker/update-backpack', updateBackpackMiddleware);
  target.post('/api/tracker/update-catalog', updateCatalogMiddleware);
}

const skipUploadOnWeb = process.env.SKIP_TRACKER_UPLOAD_ROUTES === '1'
  || process.env.TRACKER_WEB_MODE === '1';

if (!skipUploadOnWeb) {
  registerTrackerUploadRoutes(router);
}
registerTrackerUploadRoutes(uploadRouter);

/** Use the freshest sync signal — inventory/heartbeat/upload timestamps compete by age. */
function freshestSessionTimestamp(data) {
  if (!data) return null;
  const fields = [
    data.lastSeenAt,
    data.lastInventoryAt,
    data.updatedAt,
    data.playerStatsUpdatedAt,
    data.lastUploadAcceptedAt,
  ];
  let best = null;
  let bestMs = -1;
  for (const ts of fields) {
    if (!ts) continue;
    const ms = new Date(ts).getTime();
    if (Number.isFinite(ms) && ms > bestMs) {
      bestMs = ms;
      best = ts;
    }
  }
  return best;
}

function statusTimestampForSession(data) {
  return freshestSessionTimestamp(data);
}

function buildStatsPollingProof() {
  return {
    publicPollIntervalMs: 10000,
    syncTickMs: 1000,
    sharedRefreshFunction: 'applyInventoryPollPayload',
    statsFromSamePayload: true,
    coinRefreshesOnInterval: true,
    totalCaughtRefreshesOnInterval: true,
    rarestFishRefreshesOnInterval: true,
    fishCardsRefreshesOnInterval: true,
    statusDurationUpdatesEverySecond: true,
    statusFormat: '[circle] <duration>',
  };
}

function buildUnifiedPollPipelineProof(data) {
  return {
    sharedRefreshFunction: 'applyInventoryPollPayload',
    liveSnapshotField: 'entry.liveSnapshot',
    samePayloadForFishStonesStats: true,
    pollIntervalMs: 10000,
    lastPollPayloadAt: data?.lastInventoryAt || data?.playerStatsUpdatedAt || null,
  };
}

function buildStatsHarmonyProof(data) {
  const stats = resolvePlayerStatsForApi(data?.playerStats);
  return {
    coinTotalCaughtRarestFromSamePlayerStatsObject: true,
    playerStatsUpdatedAt: data?.playerStatsUpdatedAt || null,
    coinsText: stats?.coinsText || null,
    totalCaughtText: stats?.totalCaughtText || null,
    rarestFishChance: stats?.rarestFishChance || null,
    numericTotalCaughtPreferred: stats?.totalCaught != null,
  };
}

function buildTotalCaughtIntervalProof(data) {
  const stats = resolvePlayerStatsForApi(data?.playerStats);
  return {
    totalCaughtSubtextUsesUploadSyncTimerOnly: true,
    totalCaughtSubtextNeverUsesStatDelta: true,
    totalCaughtSubtextSource: 'lastSuccessfulUploadAt',
    totalCaughtRefreshesOnInterval: true,
    totalCaught: stats?.totalCaught ?? null,
    totalCaughtText: stats?.totalCaughtText ?? null,
    lastRequiredUploadAt: data?.lastRequiredUploadAt || data?.leaderstatsUploadedAt || null,
    lastSuccessfulUploadAt: data?.lastSuccessfulUploadAt || null,
  };
}

function buildCoinIntervalProof(data) {
  const stats = resolvePlayerStatsForApi(data?.playerStats);
  return {
    coinRefreshesOnEveryPoll: true,
    coins: stats?.coins ?? null,
    coinsText: stats?.coinsText ?? null,
  };
}

function buildRarestFishIntervalProof(data) {
  const stats = resolvePlayerStatsForApi(data?.playerStats);
  return {
    rarestFishRefreshesOnEveryPoll: true,
    rarestFishChance: stats?.rarestFishChance ?? null,
  };
}

function buildFishStoneIntervalProof(data) {
  return {
    fishStoneFromSamePollPayload: true,
    lastInventoryAt: data?.lastInventoryAt || data?.updatedAt || null,
    playerStatsUpdatedAt: data?.playerStatsUpdatedAt || null,
  };
}

function buildStatusFormatProof() {
  return {
    tablePublicFormat: '[circle] <duration>',
    cardPublicFormat: '[circle] <duration> <username>',
    publicFormat: '[circle] <duration>',
    literalLastSyncLabelPresent: false,
    durationTextUsesNormalColor: true,
    indicatorColorOnDotOnly: true,
    durationUpdatesEverySecond: true,
    durationResetsAfterSuccessfulPoll: true,
    noLiveLabel: true,
    noClockTime: true,
    noDuplicateUsernameInTableStatus: true,
  };
}

function buildRouteInventoryOnlyProof() {
  return {
    publicInventoryPath: CANONICAL_TRACKER_PATH,
    legacyInventoryRedirect: '/inventory -> /tracker',
    legacyFishitTrackerRedirect: '/fishit-tracker -> /tracker',
    publicUiUsesLiveTrackerLabel: true,
  };
}

function buildTrackerLuaTouchProof() {
  return {
    touched: false,
    reason: 'frontend_backend_route_ui_only',
    liveDistRelativePath: PROTECTED_TRACKER_REL_PATH,
    liveDistFetchUrl: PROTECTED_TRACKER_RAW_URL_CACHE_BUST,
    localDistPath: path.join(__dirname, '..', '..', 'dist', PROTECTED_TRACKER_REL_PATH),
  };
}

function buildStatusNoZeroProof() {
  return {
    minimumDurationSeconds: 1,
    neverRendersZeroSeconds: true,
    tableStatusFormat: '[circle] <duration>',
    noDuplicateUsernameInTableStatus: true,
    indicatorColorOnDotOnly: true,
  };
}

function buildGridModeProof() {
  return {
    fishGridShowsAllAccountsFish: true,
    stoneGridShowsAllAccountsStones: true,
    eachAccountAllAccountsTabsRemoved: true,
    individualInventoryViaBackpackOnly: true,
    bulkTabsRemoved: true,
  };
}

function buildToolbarActionProof() {
  return {
    order: ['table', 'fishGrid', 'stoneGrid', 'copyUsernames', 'refresh'],
    copyUsernamesOnly: true,
    copyIncludesTableData: false,
  };
}

function buildUploadIntervalProof(data) {
  return {
    trackerUploadIntervalSeconds: UPLOAD_INTERVAL_SECONDS,
    lastUploadAcceptedAt: data?.lastUploadAcceptedAt || null,
    lastInventoryAt: data?.lastInventoryAt || data?.updatedAt || null,
    playerStatsUpdatedAt: data?.playerStatsUpdatedAt || null,
  };
}

function buildResponsiveLayoutProof() {
  return {
    desktopTableMinWidthPx: 769,
    mobileStatsHorizontalMaxWidthPx: 768,
    desktopTableForced: true,
    mobileStatsFlexRow: true,
    desktopLayoutReverted: true,
    tableHasNoExtraPublicText: true,
  };
}

function buildConnectionIndicatorProof(data, maxAgeMs = HEARTBEAT_FRESH_MAX_MS) {
  const presence = deriveAccountPresenceStatus(data, maxAgeMs);
  const inventoryTs = data?.lastSnapshotUploadAt || data?.lastInventoryAt || data?.updatedAt || null;
  return {
    connected: presence.accountPresenceLive === true,
    indicatorColor: presence.accountPresenceLive ? 'green' : 'red',
    timestampUsed: presence.lastHeartbeatAt,
    heartbeatAgeSeconds: presence.heartbeatAgeSeconds,
    inventoryAgeSeconds: syncAgeSecondsFromTimestamp(inventoryTs),
    chosenReason: presence.accountPresenceReason,
    accountOnlineStatus: presence.accountPresenceStatus,
    inGameStatus: presence.accountPresenceLive,
  };
}

function syncAgeSecondsFromTimestamp(ts) {
  if (!ts) return null;
  const age = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  return Number.isFinite(age) && age >= 0 ? age : null;
}

function buildSyncProof(data, maxAgeMs = STATS_FRESH_MAX_MS) {
  const uploadSync = deriveConnectionStatus(data);
  const presence = deriveAccountPresenceStatus(data);
  const statsUpload = deriveStatsUploadStatus(data);
  const inventoryUpload = deriveInventoryUploadStatus(data);
  const statusTimestampUsed = statsUpload.lastStatsUploadAt || statusTimestampForSession(data);
  const ageSeconds = syncAgeSecondsFromTimestamp(statusTimestampUsed);
  const accountOnline = presence.accountPresenceLive === true;
  return {
    isReceivingLua: !!(data && data.lastUploadReceivedAt),
    statusTimestampUsed,
    statsUploadTimestampUsed: statsUpload.lastStatsUploadAt,
    lastSeenAt: data?.lastSeenAt || null,
    lastHeartbeatAt: data?.lastHeartbeatAt || null,
    updatedAt: data?.updatedAt || null,
    inventoryUpdatedAt: data?.lastInventoryAt || null,
    lastSnapshotUploadAt: inventoryUpload.lastSnapshotUploadAt || null,
    lastStatsUploadAt: statsUpload.lastStatsUploadAt || null,
    playerStatsUpdatedAt: data?.playerStatsUpdatedAt || null,
    lastSuccessfulUploadAt: data?.lastSuccessfulUploadAt || null,
    lastUploadAttemptAt: data?.lastUploadAttemptAt || null,
    lastUploadFailedAt: data?.lastUploadFailedAt || null,
    lastFailureReason: data?.lastFailureReason || null,
    uploadSyncRedSince: uploadSync.redSince || null,
    uploadSyncRedDurationSeconds: uploadSync.redDurationSeconds != null ? uploadSync.redDurationSeconds : null,
    uploadSyncFresh: uploadSync.currentStatus === 'green',
    uploadSyncStatus: uploadSync.connectionStatusReason || null,
    currentStatus: accountOnline ? 'green' : 'red',
    ageSeconds,
    isOnline: accountOnline,
    accountPresenceLive: presence.accountPresenceLive,
    accountPresenceStatus: presence.accountPresenceStatus,
    accountPresenceReason: presence.accountPresenceReason,
    accountOnlineStatus: presence.accountPresenceStatus,
    inGameStatus: presence.accountPresenceLive,
    statsUploadFresh: statsUpload.statsUploadFresh,
    statsUploadStatus: statsUpload.statsUploadStatus,
    statsRedSince: statsUpload.statsRedSince || null,
    inventoryUploadFresh: inventoryUpload.inventoryUploadFresh,
    inventoryUploadStatus: inventoryUpload.inventoryUploadStatus,
    inventorySyncStatus: inventoryUpload.inventoryUploadStatus,
    fishStoneSyncStatus: inventoryUpload.inventoryUploadStatus,
    inventoryRedSince: inventoryUpload.inventoryRedSince || null,
    inventoryStaleAfterSeconds: inventoryUpload.inventoryStaleAfterSeconds,
    statusColor: accountOnline ? 'green' : 'red',
    connectionStatus: presence.accountPresenceStatus,
    connectionStatusReason: presence.accountPresenceReason,
    statsFreshMaxMs: maxAgeMs,
    intervalSeconds: uploadSync.intervalSeconds,
    graceSeconds: uploadSync.graceSeconds,
  };
}

function buildClientBuildProof(data) {
  const latestClientBuild = data?.trackerBuild || data?.lastUploadTrackerBuild || null;
  const expectedClientBuild = EXPECTED_CLIENT_TRACKER_BUILD;
  const buildMismatch = !!(latestClientBuild && !isTrustedClientBuild(latestClientBuild));
  return {
    latestClientBuild,
    expectedClientBuild,
    buildMismatch,
    mismatchReason: buildMismatch ? 'client_loader_still_executing_old_build' : null,
  };
}

function applyUploadDebugFields(session, opts = {}) {
  const now = new Date().toISOString();
  const base = session && typeof session === 'object' ? session : {};
  const isHeartbeat = opts.payloadType === 'tracker_status';
  const patch = {
    lastUploadReceivedAt: now,
    lastUploadEndpoint: opts.endpoint || null,
    lastUploadUsername: opts.username || base.username || null,
    lastUploadSessionKey: opts.sessionKey || null,
    lastUploadTrackerBuild: opts.trackerBuild || base.trackerBuild || null,
    lastUploadHadPlayerStats: !!opts.hadPlayerStats,
    lastUploadStatusCodeReturned: opts.statusCode != null ? opts.statusCode : null,
  };
  if (!isHeartbeat) {
    patch.lastUploadPayloadType = opts.payloadType || null;
  } else {
    patch.lastHeartbeatPayloadType = 'tracker_status';
    patch.lastHeartbeatAt = now;
  }
  if (opts.accepted) {
    patch.lastUploadAcceptedAt = now;
    patch.lastUploadRejectedAt = null;
    patch.lastUploadRejectReason = null;
    patch.lastUploadError = null;
  }
  if (opts.rejected) {
    patch.lastUploadRejectedAt = now;
    patch.lastUploadRejectReason = opts.rejectReason || 'rejected';
    patch.lastUploadError = opts.rejectReason || 'rejected';
  }
  if (opts.requestBytes != null) patch.lastRequestBytes = opts.requestBytes;
  if (opts.responseMs != null) patch.lastUploadResponseMs = opts.responseMs;
  if (opts.compact != null) patch.lastUploadCompact = opts.compact === true;
  if (opts.debugUpload != null) patch.lastUploadDebug = opts.debugUpload === true;
  return { ...base, ...patch };
}

function buildInventorySnapshotDiagnostics(body, session) {
  const proof = body?.playerDataGameItemDbProof && typeof body.playerDataGameItemDbProof === 'object'
    ? body.playerDataGameItemDbProof
    : {};
  const classification = body?.inventoryItemClassificationDebug
    || proof.inventoryItemClassificationDebug
    || session?.inventoryItemClassificationDebug
    || null;
  const uploadedUnresolvedCount = Array.isArray(body?.unresolvedItems)
    ? body.unresolvedItems.length
    : null;
  const persistedUnresolvedCount = Number.isFinite(Number(proof.unresolvedCount))
    ? Number(proof.unresolvedCount)
    : (Array.isArray(proof.unresolvedItems) ? proof.unresolvedItems.length : null);
  const classificationUnresolvedCount = Number.isFinite(Number(classification?.unresolvedRows))
    ? Number(classification.unresolvedRows)
    : null;
  const publicHiddenUnresolvedCount = Array.isArray(session?.playerDataHiddenUnresolved)
    ? session.playerDataHiddenUnresolved.length
    : null;
  const alignedUnresolvedCount = persistedUnresolvedCount ?? uploadedUnresolvedCount
    ?? classificationUnresolvedCount ?? null;
  return {
    payloadType: body?.type || 'inventory_snapshot',
    trackerBuild: sanitiseTrackerBuild(body?.trackerBuild) || session?.trackerBuild || null,
    at: new Date().toISOString(),
    fishCount: Array.isArray(body?.fishItems) ? body.fishItems.length : (proof.fishCount ?? null),
    stoneCount: Array.isArray(body?.stoneItems) ? body.stoneItems.length : (proof.stoneCount ?? null),
    totemCount: Array.isArray(body?.totemItems) ? body.totemItems.length : (proof.totemCount ?? null),
    totemQuantity: (Array.isArray(body?.totemItems) ? body.totemItems : []).reduce(
      (s, row) => s + (Number(row?.quantity) > 0 ? Math.floor(Number(row.quantity)) : 1),
      0,
    ),
    unresolvedCount: alignedUnresolvedCount,
    uploadedUnresolvedCount,
    persistedUnresolvedCount,
    publicHiddenUnresolvedCount,
    classificationUnresolvedCount,
    playerDataInventoryCount: proof.playerDataInventoryCount ?? classification?.totalRows ?? null,
    scannedRows: proof.scannedRows ?? classification?.scannedRows ?? null,
    skippedNoIdRows: proof.skippedNoIdRows ?? classification?.skippedNoIdRows ?? null,
    inventoryItemClassificationDebug: classification,
    totemPathAudit: body?.totemPathAudit || proof.totemPathAudit || session?.totemPathAudit || null,
    totemInventoryPathProof: body?.totemInventoryPathProof
      || proof.totemInventoryPathProof
      || session?.totemInventoryPathProof
      || null,
    gameItemDbTotemAudit: body?.gameItemDbTotemAudit || proof.gameItemDbTotemAudit || session?.gameItemDbTotemAudit || null,
    nonFishNonStoneItemGroups: body?.nonFishNonStoneItemGroups
      || proof.nonFishNonStoneItemGroups
      || classification?.nonFishNonStoneItemGroups
      || session?.nonFishNonStoneItemGroups
      || [],
    totemScanProof: buildTotemScanProof(
      Array.isArray(body?.totemItems) ? body.totemItems : (session?.playerDataTotemItems || []),
      {
        uploadedTotemCount: Array.isArray(body?.totemItems) ? body.totemItems.length : 0,
        uploadedTotemQuantity: (Array.isArray(body?.totemItems) ? body.totemItems : []).reduce(
          (s, row) => s + (Number(row?.quantity) > 0 ? Math.floor(Number(row.quantity)) : 1),
          0,
        ),
      },
    ),
  };
}

function isTotemAuditTrackerBuild(build) {
  return /^TOTEM_/i.test(String(build || ''));
}

function emptyTotemAuditSkeleton(build, reason = 'audit_missing_from_upload') {
  return {
    inventoryItemClassificationDebug: {
      enabled: true,
      build: build || null,
      scannedRows: null,
      scannedPaths: [],
      totemNotFoundReason: reason,
      unresolvedGroups: [],
      ignoredGroups: [],
      totemCandidateGroups: [],
    },
    totemPathAudit: {
      searchedTerms: ['Totem', 'Mutation', 'Artifact', 'Gear', 'Trophy'],
      inventoryPathCounts: {},
      matches: [],
    },
    gameItemDbTotemAudit: {
      totemNameMatches: [],
      gearSamples: [],
      trophySamples: [],
      artifactMatches: [],
      mutationMatches: [],
    },
    nonFishNonStoneItemGroups: [],
  };
}

function resolveTotemAuditFields(body, existing, incomingBuild) {
  const proof = body?.playerDataGameItemDbProof && typeof body.playerDataGameItemDbProof === 'object'
    ? body.playerDataGameItemDbProof
    : {};
  const pick = (top, nested, prev) => top || nested || prev || null;
  let inventoryItemClassificationDebug = pick(
    body.inventoryItemClassificationDebug,
    proof.inventoryItemClassificationDebug,
    existing?.inventoryItemClassificationDebug,
  );
  let totemPathAudit = pick(body.totemPathAudit, proof.totemPathAudit, existing?.totemPathAudit);
  let totemInventoryPathProof = pick(
    body.totemInventoryPathProof,
    proof.totemInventoryPathProof,
    existing?.totemInventoryPathProof,
  );
  let gameItemDbTotemAudit = pick(
    body.gameItemDbTotemAudit,
    proof.gameItemDbTotemAudit,
    existing?.gameItemDbTotemAudit,
  );
  let nonFishNonStoneItemGroups = Array.isArray(body.nonFishNonStoneItemGroups)
    ? body.nonFishNonStoneItemGroups.slice(0, 80)
    : (Array.isArray(proof.nonFishNonStoneItemGroups)
      ? proof.nonFishNonStoneItemGroups.slice(0, 80)
      : (existing?.nonFishNonStoneItemGroups || []));

  if (isTotemAuditTrackerBuild(incomingBuild)) {
    const skeleton = emptyTotemAuditSkeleton(incomingBuild);
    if (!inventoryItemClassificationDebug) {
      inventoryItemClassificationDebug = skeleton.inventoryItemClassificationDebug;
    }
    totemPathAudit = totemPathAudit || skeleton.totemPathAudit;
    gameItemDbTotemAudit = gameItemDbTotemAudit || skeleton.gameItemDbTotemAudit;
    if (!nonFishNonStoneItemGroups.length) {
      nonFishNonStoneItemGroups = skeleton.nonFishNonStoneItemGroups;
    }
  }

  return {
    inventoryItemClassificationDebug,
    totemPathAudit,
    totemInventoryPathProof,
    gameItemDbTotemAudit,
    nonFishNonStoneItemGroups,
  };
}

function extractTotemAuditArrivalMeta(body) {
  const proof = body?.playerDataGameItemDbProof && typeof body.playerDataGameItemDbProof === 'object'
    ? body.playerDataGameItemDbProof
    : {};
  const hasTotemPathAudit = !!(body.totemPathAudit || proof.totemPathAudit);
  const hasTotemInventoryPathProof = !!(body.totemInventoryPathProof || proof.totemInventoryPathProof);
  const hasClassificationDebug = !!(body.inventoryItemClassificationDebug || proof.inventoryItemClassificationDebug);
  const hasGameItemDbTotemAudit = !!(body.gameItemDbTotemAudit || proof.gameItemDbTotemAudit);
  const nonFishGroups = Array.isArray(body.nonFishNonStoneItemGroups)
    ? body.nonFishNonStoneItemGroups.length
    : (Array.isArray(proof.nonFishNonStoneItemGroups) ? proof.nonFishNonStoneItemGroups.length : 0);
  const totemCount = Array.isArray(body.totemItems) ? body.totemItems.length : 0;
  const unresolvedCount = Array.isArray(body.unresolvedItems)
    ? body.unresolvedItems.length
    : (Number.isFinite(Number(proof.unresolvedCount)) ? Number(proof.unresolvedCount) : null);
  return {
    hasTotemPathAudit,
    hasTotemInventoryPathProof,
    hasClassificationDebug,
    hasGameItemDbTotemAudit,
    nonFishGroups,
    totemCount,
    unresolvedCount,
  };
}

function buildHeartbeatDiagnostics(body, session, now) {
  return {
    payloadType: 'tracker_status',
    trackerBuild: sanitiseTrackerBuild(body?.trackerBuild) || session?.trackerBuild || null,
    at: now || new Date().toISOString(),
    phase: body?.phase || session?.phase || null,
    hadPlayerStats: !!(body?.playerStats || body?.playerStatsDebug),
    preservedInventorySnapshotAt: session?.lastInventorySnapshotAt || session?.lastInventoryAt || null,
    preservedFishCount: Array.isArray(session?.playerDataFishItems) ? session.playerDataFishItems.length : null,
    preservedStoneCount: Array.isArray(session?.playerDataStoneItems) ? session.playerDataStoneItems.length : null,
    preservedTotemCount: Array.isArray(session?.playerDataTotemItems) ? session.playerDataTotemItems.length : null,
    preservedClassificationDebugPresent: !!session?.inventoryItemClassificationDebug,
  };
}

function statsUploadTimestamp(data) {
  return leaderstatsUpload.leaderstatsUploadTimestamp(data);
}

function deriveStatsUploadStatus(data) {
  return leaderstatsUpload.deriveLeaderstatsUploadStatus(data);
}

const UPLOAD_INTERVAL_SECONDS = 60;
const UPLOAD_GRACE_SECONDS = 15;
const PUBLIC_STATUS_GRACE_SECONDS = uploadAccountStatus.PUBLIC_STATUS_GRACE_SECONDS || 600;
const LOADER_ERROR_FRESH_MAX_MS = 120000;

function inventoryUploadGraceSeconds(intervalSeconds) {
  return PUBLIC_STATUS_GRACE_SECONDS;
}

function inventoryUploadStaleAfterSeconds(intervalSeconds) {
  return PUBLIC_STATUS_GRACE_SECONDS;
}

function computeUploadPayloadHash(body, itemCount) {
  try {
    const digest = crypto.createHash('sha256');
    digest.update(String(itemCount || 0));
    digest.update('|');
    digest.update(String(body?.playerStats?.totalCaught ?? ''));
    digest.update('|');
    digest.update(String(body?.playerStats?.coins ?? ''));
    digest.update('|');
    digest.update(String(body?.trackerBuild ?? ''));
    return digest.digest('hex').slice(0, 16);
  } catch {
    return null;
  }
}

function applyUploadSyncSuccess(session, now, opts = {}) {
  const graceSeconds = Number(opts.graceSeconds) >= 0
    ? Number(opts.graceSeconds)
    : (Number(session?.graceSeconds) >= 0 ? Number(session.graceSeconds) : UPLOAD_GRACE_SECONDS);
  return uploadAccountStatus.markTrackerSyncSuccess(session, now, {
    syncReason: opts.syncReason || 'accepted_snapshot',
    lastStatsUpdatedAt: opts.lastStatsUpdatedAt,
    payloadHash: opts.payloadHash,
    intervalSeconds: opts.intervalSeconds,
    graceSeconds,
    loaderOutdated: opts.loaderOutdated,
    expectedLoaderBuild: EXPECTED_CLIENT_TRACKER_BUILD,
  });
}

function applyUploadSyncFailure(session, now, reason) {
  return {
    ...uploadAccountStatus.markTrackerSyncMissed(session, now),
    lastUploadAttemptAt: now,
    lastFailureReason: String(reason || 'upload_failed').slice(0, 240),
    lastUploadFailureIsTransient: false,
  };
}

function applyTransientUploadFailure(session, now, reason, statusCode) {
  return uploadAccountStatus.applyTransientUploadFailure(session, now, reason, statusCode);
}

const STATS_FRESH_MAX_MS = (UPLOAD_INTERVAL_SECONDS + UPLOAD_GRACE_SECONDS) * 1000;
const HEARTBEAT_FRESH_MAX_MS = ACCOUNT_PRESENCE_GRACE_MS;

function deriveConnectionStatus(data) {
  /** Upload/stats sync freshness only — never use for main account online/offline indicator. */
  const intervalSeconds = Number(data?.intervalSeconds) > 0
    ? Number(data.intervalSeconds)
    : UPLOAD_INTERVAL_SECONDS;
  const graceSeconds = Number(data?.graceSeconds) >= 0
    ? Number(data.graceSeconds)
    : UPLOAD_GRACE_SECONDS;
  const expectedLoaderBuild = EXPECTED_CLIENT_TRACKER_BUILD;
  const loaderBuild = data?.trackerBuild || data?.lastUploadTrackerBuild || null;
  const lastSuccessfulUploadAt = data?.leaderstatsUploadOk === true
    ? (data?.leaderstatsUploadedAt || data?.lastSuccessfulUploadAt || null)
    : null;
  const redSinceStored = data?.lastStatus === 'green' ? null : (data?.redSince || null);

  const base = {
    expectedLoaderBuild,
    loaderBuild,
    lastLoaderBuild: loaderBuild,
    intervalSeconds,
    graceSeconds,
    lastSuccessfulUploadAt,
    lastUploadAttemptAt: data?.lastUploadAttemptAt || null,
    lastUploadFailedAt: data?.lastUploadFailedAt || null,
    lastFailureReason: data?.lastFailureReason || null,
    lastStatusChangeAt: data?.lastStatusChangeAt || null,
    lastStatsUpdatedAt: data?.lastStatsUpdatedAt || data?.playerStatsUpdatedAt || null,
    lastPayloadHash: data?.lastPayloadHash || null,
    redSince: redSinceStored,
    currentStatus: 'red',
    loaderOutdated: !!(loaderBuild && loaderBuild !== expectedLoaderBuild),
    lastHeartbeatAt: data?.lastHeartbeatAt || data?.lastSeenAt || null,
    lastSnapshotUploadAt: data?.lastSnapshotUploadAt || data?.lastInventoryAt || null,
    lastStatsUploadAt: data?.lastStatsUploadAt || null,
    lastLoaderErrorAt: data?.lastLoaderErrorAt || null,
    lastLoaderErrorMessage: data?.lastLoaderErrorMessage || null,
    lastLoaderErrorPhase: data?.lastLoaderErrorPhase || null,
  };

  const finishRed = (reason, redStart, extra = {}) => {
    const redSince = redStart || redSinceStored || data?.lastUploadFailedAt || null;
    return {
      ...base,
      ...extra,
      currentStatus: 'red',
      connectionStatus: extra.connectionStatus || 'offline',
      connectionStatusColor: 'red',
      connectionStatusReason: reason,
      redSince,
      redDurationSeconds: syncAgeSecondsFromTimestamp(redSince),
      statsFresh: false,
    };
  };

  if (!data) {
    return finishRed('no_session', null, { connectionStatus: 'offline' });
  }

  if (loaderBuild && !isTrustedClientBuild(loaderBuild)) {
    return finishRed('outdated_loader', redSinceStored || data?.lastUploadFailedAt || data?.updatedAt, {
      connectionStatus: 'error',
      loaderOutdated: true,
    });
  }

  if (base.lastLoaderErrorAt && base.lastLoaderErrorMessage) {
    const errAge = Date.now() - new Date(base.lastLoaderErrorAt).getTime();
    if (Number.isFinite(errAge) && errAge >= 0 && errAge < LOADER_ERROR_FRESH_MAX_MS) {
      const registerFail = /register|Exceeded limit 200/i.test(String(base.lastLoaderErrorMessage));
      return finishRed(
        registerFail ? 'loader_register_limit' : 'loader_runtime_error',
        redSinceStored || base.lastLoaderErrorAt,
        { connectionStatus: 'error' },
      );
    }
  }

  if (!lastSuccessfulUploadAt) {
    return finishRed('no_successful_upload', redSinceStored || data?.lastUploadAttemptAt);
  }

  const successMs = new Date(lastSuccessfulUploadAt).getTime();
  const deadlineMs = successMs + (intervalSeconds + graceSeconds) * 1000;
  const nowMs = Date.now();

  if (nowMs <= deadlineMs) {
    return {
      ...base,
      currentStatus: 'green',
      connectionStatus: 'live',
      connectionStatusColor: 'green',
      connectionStatusReason: 'fresh_upload',
      redSince: null,
      redDurationSeconds: 0,
      statsFresh: true,
    };
  }

  return finishRed(
    'upload_interval_missed',
    redSinceStored || new Date(deadlineMs).toISOString(),
  );
}

/** Account is live when the server has a recent accepted tracker upload (green or yellow). */
function isSessionLive(data) {
  const uploadStatus = uploadAccountStatus.deriveTrackerUploadAccountStatus(data, {
    expectedTrackerBuild: EXPECTED_CLIENT_TRACKER_BUILD,
    isTrustedBuild: isTrustedClientBuild,
  });
  return uploadStatus.statusColor === 'green' || uploadStatus.statusColor === 'yellow';
}

function deriveUploadAccountStatus(data, serverNowMs) {
  return uploadAccountStatus.deriveTrackerUploadAccountStatus(data, {
    serverNowMs: serverNowMs != null ? serverNowMs : Date.now(),
    expectedTrackerBuild: EXPECTED_CLIENT_TRACKER_BUILD,
    isTrustedBuild: isTrustedClientBuild,
  });
}

function deriveUploadSyncStatus(data) {
  return deriveConnectionStatus(data);
}

function deriveInventoryUploadStatus(data) {
  const intervalSeconds = Number(data?.intervalSeconds) > 0
    ? Number(data.intervalSeconds)
    : UPLOAD_INTERVAL_SECONDS;
  const graceSeconds = inventoryUploadGraceSeconds(intervalSeconds);
  const staleAfterSeconds = inventoryUploadStaleAfterSeconds(intervalSeconds);
  // Indicator 3 reflects ONLY the fish/stone inventory upload for this interval.
  // It must not borrow from account online/upload status, stats freshness, or
  // heartbeat — only the timestamps written when an inventory snapshot is
  // accepted (lastSnapshotUploadAt / lastInventoryAt).
  const ts = data?.lastSnapshotUploadAt
    || data?.lastInventoryAt
    || null;
  const ageSeconds = syncAgeSecondsFromTimestamp(ts);
  const deadlineMs = ts ? new Date(ts).getTime() + staleAfterSeconds * 1000 : null;
  const fresh = !!(ts && Date.now() <= deadlineMs);
  return {
    lastSnapshotUploadAt: ts,
    inventoryUploadAgeSeconds: ageSeconds,
    inventoryUploadFresh: fresh,
    inventoryUploadStatus: fresh ? 'fresh' : (ts ? 'stale' : 'never'),
    inventoryUploadReason: fresh ? 'fresh' : (ts ? 'upload_stale' : 'never_uploaded'),
    inventoryRedSince: fresh ? null : (data?.inventoryRedSince || data?.redSince || (deadlineMs ? new Date(deadlineMs).toISOString() : null)),
    intervalSeconds,
    graceSeconds,
    inventoryStaleAfterSeconds: staleAfterSeconds,
  };
}

function isSessionHeartbeatRecent(data, maxAgeMs = ACCOUNT_PRESENCE_GRACE_MS) {
  if (!data) return false;
  const ts = resolveLastAccountSeenAt(data);
  if (!ts) return false;
  const age = Date.now() - new Date(ts).getTime();
  return Number.isFinite(age) && age >= 0 && age < maxAgeMs;
}

// Catalog upload routes are registered via registerTrackerUploadRoutes().

function hasSyncedInventory(data) {
  return Number(data.lastGoodPublicFishCount) > 0
    || Number(data.visibleFishInstances) > 0
    || Boolean(data.lastSyncAt)
    || Boolean(data.lastPollOkAt);
}

function publicFishCountForSession(data) {
  if (!hasSyncedInventory(data)) return 0;
  const good = Number(data.lastGoodPublicFishCount);
  if (Number.isFinite(good) && good > 0) return Math.floor(good);
  const visible = Number(data.visibleFishInstances);
  if (Number.isFinite(visible) && visible > 0) return Math.floor(visible);
  return 0;
}

function collectPublicFishItTrackerStats() {
  let trackedFishers = 0;
  let onlineFishers = 0;
  let inventoriesSynced = 0;
  let fishTracked = 0;
  for (const [key, data] of Object.entries(liveTrackDB)) {
    if (key.startsWith('uid:')) continue;
    if (!data || typeof data !== 'object') continue;
    trackedFishers += 1;
    if (isSessionHeartbeatRecent(data)) onlineFishers += 1;
    if (hasSyncedInventory(data)) {
      inventoriesSynced += 1;
      fishTracked += publicFishCountForSession(data);
    }
  }
  return {
    available: trackedFishers > 0 || onlineFishers > 0 || inventoriesSynced > 0 || fishTracked > 0,
    trackedFishers,
    onlineFishers,
    inventoriesSynced,
    fishTracked,
    updatedAt: new Date().toISOString(),
    sources: {
      trackedFishers: {
        service: 'fishit-tracker',
        store: 'liveTrackDB',
        method: 'COUNT DISTINCT username keys excluding uid:* aliases',
      },
      onlineFishers: {
        service: 'fishit-tracker',
        store: 'liveTrackDB',
        method: 'isSessionHeartbeatRecent(session) within 45s heartbeat window',
      },
      inventoriesSynced: {
        service: 'fishit-tracker',
        store: 'liveTrackDB',
        method: 'sessions with lastGoodPublicFishCount|visibleFishInstances|lastSyncAt|lastPollOkAt',
      },
      fishTracked: {
        service: 'fishit-tracker',
        store: 'liveTrackDB',
        method: 'SUM(lastGoodPublicFishCount OR visibleFishInstances) on synced fish-only public snapshots',
      },
    },
    rejectedSources: ['fishitDb', 'deng_fish_it_bot', 'quiz_bot', '!d'],
  };
}

function collectPublicTrackerNetworkStats() {
  syncLiveTrackFromDisk();
  const canonical = computeCanonicalTrackerUsers(liveTrackDB);
  const registeredTrackedCount = inventoryTrackedAccounts.countRegisteredTrackedUsernamesSync();
  const trackedUsernames = Math.max(registeredTrackedCount, canonical.currentBuildUniqueUsers);
  let inventoriesSynced = 0;
  for (const [key, data] of Object.entries(liveTrackDB)) {
    if (key.startsWith('uid:')) continue;
    if (!data || typeof data !== 'object') continue;
    if (Number(data.lastGoodPublicFishCount) > 0
      || Number(data.visibleFishInstances) > 0
      || data.lastSyncAt
      || data.lastPollOkAt) {
      inventoriesSynced += 1;
    }
  }
  return {
    available: canonical.available,
    rawUploadRows: canonical.rawUploadRows,
    rawSessionRows: canonical.rawSessionRows,
    uniqueKeysSeen: canonical.uniqueKeysSeen,
    duplicatesRemoved: canonical.duplicatesRemoved,
    currentBuildUniqueUsers: canonical.currentBuildUniqueUsers,
    onlineUniqueUsers: canonical.onlineUniqueUsers,
    oldBuildIgnored: canonical.oldBuildIgnored,
    invalidPayloadIgnored: canonical.invalidPayloadIgnored,
    staleIgnored: canonical.staleIgnored,
    expectedBuild: canonical.expectedBuild,
    summary: canonical.summary,
    trackedUsernames,
    onlineUsernames: canonical.onlineUniqueUsers,
    registeredTrackedCount,
    inventoriesSynced,
    updatedAt: canonical.updatedAt,
  };
}

function collectPublicTrackerNetworkProof() {
  const canonical = computeCanonicalTrackerUsers(liveTrackDB);
  return {
    pass: true,
    marker: 'CANONICAL_TRACKER_USER_COUNT_2026_06_12',
    ...canonical,
    summary: {
      ...canonical.summary,
      note: 'Unique tracker users dedupe by robloxUserId, else lower(username). Online uses same presence grace as inventory.',
    },
  };
}

function buildPublicTrackerStatsPayload() {
  const stats = collectPublicTrackerNetworkStats();
  return {
    ok: true,
    trackedCount: stats.trackedUsernames,
    onlineCount: stats.onlineUsernames,
    registeredTrackedCount: stats.registeredTrackedCount || 0,
    serverTime: stats.updatedAt || new Date().toISOString(),
    source: 'canonical_tracker_summary',
    cache: 'no-store',
    rawSessionRows: stats.rawSessionRows,
    currentBuildUniqueUsers: stats.currentBuildUniqueUsers,
  };
}

// ── GET /api/fishit-tracker/public-network ───────────────────────
router.get('/api/fishit-tracker/public-network', getLimiter, (_req, res) => {
  res.set(NO_STORE_HEADERS);
  return res.status(200).json(collectPublicTrackerNetworkStats());
});

router.get('/api/public/tracker-stats', getLimiter, (_req, res) => {
  res.set(NO_STORE_HEADERS);
  return res.status(200).json(buildPublicTrackerStatsPayload());
});

router.get('/api/home/network-stats', getLimiter, (_req, res) => {
  res.set(NO_STORE_HEADERS);
  return res.status(200).json(buildPublicTrackerStatsPayload());
});

// ── GET /api/fishit-tracker/public-network-proof ─────────────────
router.get('/api/fishit-tracker/public-network-proof', getLimiter, (_req, res) => {
  res.set(NO_STORE_HEADERS);
  return res.status(200).json(collectPublicTrackerNetworkProof());
});

// ── GET /api/fishit-tracker/catalog ──────────────────────────────
// Expose the stored catalog (debugging / verification).
router.get('/api/fishit-tracker/catalog', getLimiter, (_req, res) => {
  return res.status(200).json(catalogStore.getCatalog());
});

// ── GET get-backpack (canonical + legacy alias) ───────────────────
// Also resolves userId aliases (uid:<number> keys created on POST).
async function handleGetBackpack(req, res) {
  syncLiveTrackFromDisk();
  const queryStartedAt = Date.now();
  const wantLite = trackerPerf.isLiteBackpackRequest(req);
  const includeDebug = trackerPerf.isDebugRequest(req);
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
  data = ensureSessionBuildCurrent(data);
  data = snapshotCompleteness.applyRehydratedCompleteness(data, playerStatsStore);
  liveTrackDB[key] = data;

  // Enrich from raw tracker payload when available (BLOCKER10I/10S).
  const sourceItems = partialSnapshot.itemsForSessionDisplay(data);
  const enrichedFlat = enrichItemsFromCatalog(sourceItems);
  const enrichedInventory = buildInventoryGroups(enrichedFlat);
  const rawInventory = buildInventoryGroups(sourceItems);
  const countsRaw = inventoryCountsFromGroups(rawInventory);
  const countsEnriched = inventoryCountsFromGroups(enrichedInventory);
  const baseUrl = `${req.protocol}://${req.get('host')}`;
  let publicFish = await buildPublicFishFields(enrichedFlat, baseUrl, { sessionData: data, sessionKey: key });
  const liveFishCount = Array.isArray(publicFish.fishItems) ? publicFish.fishItems.length : 0;
  if (liveFishCount === 0 && Array.isArray(data.lastGoodPublicFishItems) && data.lastGoodPublicFishItems.length) {
    const preserved = await reEnrichPublicFishItems(data.lastGoodPublicFishItems, baseUrl);
    publicFish = {
      ...publicFish,
      fishItems: preserved,
      publicItems: preserved,
      publicFishItems: preserved,
      fishInventory: buildInventoryGroups(preserved),
      dataStale: true,
      lastGoodFishPreserved: true,
    };
  } else if (Array.isArray(publicFish.fishItems) && publicFish.fishItems.length) {
    const refreshed = await reEnrichPublicFishItems(publicFish.fishItems, baseUrl);
    publicFish = {
      ...publicFish,
      fishItems: refreshed,
      publicItems: refreshed,
      publicFishItems: refreshed,
      fishInventory: buildInventoryGroups(refreshed),
    };
  }
  // Symmetric to the fish fallback above: when the live build yields no stones
  // (offline / waiting / partial / post-restart), keep showing the last valid
  // stone inventory instead of an empty grid.
  const liveStoneCount = Array.isArray(publicFish.stoneItems) ? publicFish.stoneItems.length : 0;
  if (Array.isArray(data.lastGoodPublicStoneItems) && data.lastGoodPublicStoneItems.length) {
    const resolvedStones = gameItemDbPublic.preferHigherGroupedStoneSnapshot(
      publicFish.stoneItems || [],
      data.lastGoodPublicStoneItems,
    );
    if (resolvedStones !== publicFish.stoneItems) {
      publicFish = {
        ...publicFish,
        stoneItems: reEnrichPublicStoneItems(resolvedStones, baseUrl),
        stoneInventory: reEnrichPublicStoneItems(resolvedStones, baseUrl),
        stoneDataStale: true,
        lastGoodStonePreserved: true,
      };
    } else if (liveStoneCount === 0) {
      publicFish = {
        ...publicFish,
        stoneItems: reEnrichPublicStoneItems(data.lastGoodPublicStoneItems, baseUrl),
        stoneInventory: reEnrichPublicStoneItems(data.lastGoodPublicStoneItems, baseUrl),
        stoneDataStale: true,
        lastGoodStonePreserved: true,
      };
    } else if (Array.isArray(publicFish.stoneItems) && publicFish.stoneItems.length) {
      const refreshedStones = reEnrichPublicStoneItems(publicFish.stoneItems, baseUrl);
      publicFish = {
        ...publicFish,
        stoneItems: refreshedStones,
        stoneInventory: refreshedStones,
      };
    }
  } else if (Array.isArray(publicFish.stoneItems) && publicFish.stoneItems.length) {
    const refreshedStones = reEnrichPublicStoneItems(publicFish.stoneItems, baseUrl);
    publicFish = {
      ...publicFish,
      stoneItems: refreshedStones,
      stoneInventory: refreshedStones,
    };
  }
  const liveTotemCount = Array.isArray(publicFish.totemItems) ? publicFish.totemItems.length : 0;
  if (liveTotemCount === 0 && Array.isArray(data.lastGoodPublicTotemItems) && data.lastGoodPublicTotemItems.length) {
    const preservedTotems = reEnrichPublicTotemItems(data.lastGoodPublicTotemItems, baseUrl);
    publicFish = {
      ...publicFish,
      totemItems: preservedTotems,
      totemInventory: preservedTotems,
      totemDataStale: true,
      lastGoodTotemPreserved: true,
    };
  } else if (Array.isArray(publicFish.totemItems) && publicFish.totemItems.length) {
    const refreshedTotems = reEnrichPublicTotemItems(publicFish.totemItems, baseUrl);
    publicFish = {
      ...publicFish,
      totemItems: refreshedTotems,
      totemInventory: refreshedTotems,
    };
  }
  const imageResolutionProof = wantLite
    ? null
    : fishImageAssets.buildImageResolutionProof(publicFish.fishItems);
  if (!wantLite && !publicFish.recoveredSpeciesImageResolutionProof) {
    snapshotRecovery.buildRecoveredSpeciesImageResolutionProof(publicFish.fishItems);
  }

  const fishCatalogStats = wantLite ? null : fishCatalog.getStats();

  const connection = deriveConnectionStatus(data);
  const presence = deriveAccountPresenceStatus(data);
  const uploadStatus = deriveUploadAccountStatus(data);
  const statsUpload = deriveStatsUploadStatus(data);
  const inventoryUpload = deriveInventoryUploadStatus(data);
  const statusLastSuccessAt = resolveStatusLastSuccessAt(data, presence);
  const leaderstatsLastSuccessAt = statsUpload.lastStatsUploadAt || null;
  const inventoryLastSuccessAt = inventoryUpload.lastSnapshotUploadAt || null;
  const serverNowMs = Date.now();
  const liveAccountStats = liveTrackerSerializer.serializeLiveTrackerAccountStats(
    { ...data, ...uploadStatus, statusColor: uploadStatus.statusColor },
    playerStatsStore,
    resolvePlayerStatsForApi,
  );
  const enrichedBase = {
    username: data.username || cleanUser,
    userId: data.userId || null,
    renderBuild:     PUBLIC_RENDER_BUILD,
    publicApiBuild:  PUBLIC_API_BUILD,
    trackerBuild:    data.trackerBuild || null,
    inventorySource: data.inventorySource || null,
    sourceTruth:     data.sourceTruth || null,
    items:           publicFish.fishItems,
    inventory:       publicFish.fishInventory,
    counts:          buildPublicLegacyCounts(publicFish.fishCounts),
    fishItems:       publicFish.fishItems,
    stoneItems:      publicFish.stoneItems || [],
    totemItems:      publicFish.totemItems || [],
    activationState: publicFish.activationState || null,
    publicItems:     publicFish.publicItems,
    publicFishItems: publicFish.publicFishItems,
    fishInventory:   publicFish.fishInventory,
    stoneInventory:  publicFish.stoneInventory || publicFish.stoneItems || [],
    totemInventory:  publicFish.totemInventory || publicFish.totemItems || [],
    fishCounts:      publicFish.fishCounts,
    publicCounts:    publicFish.publicCounts,
    lastInventoryAt: data.lastInventoryAt || data.updatedAt || null,
    lastSeenAt:      data.lastSeenAt || null,
    lastHeartbeatAt: data.lastHeartbeatAt || data.lastSeenAt || null,
    lastAccountSeenAt: resolveLastAccountSeenAt(data),
    lastSnapshotUploadAt: data.lastSnapshotUploadAt || data.lastInventoryAt || null,
    lastStatsUploadAt: data.lastStatsUploadAt || data.playerStatsUpdatedAt || null,
    lastSuccessfulUploadAt: data.lastSuccessfulUploadAt || null,
    lastLoaderErrorAt: data.lastLoaderErrorAt || null,
    lastLoaderErrorMessage: data.lastLoaderErrorMessage || null,
    expectedLoaderBuild: EXPECTED_CLIENT_TRACKER_BUILD,
    lastLoaderBuild: data.trackerBuild || data.lastUploadTrackerBuild || null,
    isOnline:        data.isOnline === true,
    loaderOnline:    data.isOnline === true,
    connectionLive:  presence.accountPresenceLive,
    accountPresenceLive: presence.accountPresenceLive,
    accountPresenceStatus: presence.accountPresenceStatus,
    accountPresenceReason: presence.accountPresenceReason,
    accountOnline: presence.accountPresenceLive,
    accountOnlineStatus: presence.accountPresenceLive ? uploadStatus.status : 'offline',
    accountStatusReason: presence.accountStatusReason || uploadStatus.statusDecisionReason,
    accountPresenceGraceSeconds: presence.accountPresenceGraceSeconds,
    uploadWarningReason: presence.uploadWarningReason || null,
    inGameStatus: presence.accountPresenceLive,
    currentStatus: presence.accountPresenceLive
      ? (uploadStatus.statusColor === 'yellow' ? 'yellow' : 'green')
      : 'red',
    status: uploadStatus.status,
    statusColor: uploadStatus.statusColor,
    lastStatus: uploadStatus.lastStatus || data.lastStatus || null,
    lastStatusAt: uploadStatus.lastStatusAt || data.lastStatusAt || null,
    statusDecisionReason: uploadStatus.statusDecisionReason,
    secondsSinceLastSuccess: uploadStatus.secondsSinceLastSuccess,
    onlineThresholdSeconds: uploadStatus.onlineThresholdSeconds,
    offlineThresholdSeconds: uploadStatus.offlineThresholdSeconds,
    uploadIntervalSeconds: uploadStatus.uploadIntervalSeconds,
    runId: uploadStatus.runId,
    uploadSeq: uploadStatus.uploadSeq,
    loaderBuild: uploadStatus.loaderBuild,
    serverReceivedAt: uploadStatus.serverReceivedAt,
    latestPayloadAccepted: uploadStatus.latestPayloadAccepted,
    rejectReason: uploadStatus.rejectReason,
    isCurrentBuild: uploadStatus.isCurrentBuild,
    isOldBuild: uploadStatus.isOldBuild,
    provenEmptyInventory: uploadStatus.provenEmptyInventory,
    snapshotComplete: uploadStatus.snapshotComplete === true,
    inventoryReady: uploadStatus.inventoryReady === true,
    snapshotCompletenessReason: uploadStatus.snapshotCompletenessReason || data?.snapshotCompletenessReason || null,
    hasLeaderstatsSnapshot: uploadStatus.hasLeaderstatsSnapshot === true,
    hasFishSnapshot: uploadStatus.hasFishSnapshot === true,
    hasStoneSnapshot: uploadStatus.hasStoneSnapshot === true,
    firstFullSnapshotAt: uploadStatus.firstFullSnapshotAt || data?.firstFullSnapshotAt || null,
    lastFullSnapshotAt: uploadStatus.lastFullSnapshotAt || data?.lastFullSnapshotAt || null,
    blankPayloadRejected: uploadStatus.blankPayloadRejected === true,
    payloadType: uploadStatus.payloadType,
    inventoryDisplayState: uploadAccountStatus.resolveInventoryDisplayState({
      ...data,
      ...uploadStatus,
    }),
    connectionStatus: uploadStatus.status,
    connectionStatusColor: uploadStatus.statusColor,
    connectionStatusReason: uploadStatus.statusDecisionReason,
    uploadSyncFresh: connection.currentStatus === 'green',
    uploadSyncStatus: connection.connectionStatusReason || null,
    uploadSyncRedSince: connection.redSince || null,
    uploadSyncRedDurationSeconds: connection.redDurationSeconds != null ? connection.redDurationSeconds : null,
    lastStatsUpdatedAt: statsUpload.lastStatsUploadAt || connection.lastStatsUpdatedAt || null,
    lastStatsChangeAt: data.lastStatsChangeAt || null,
    statusLastSuccessAt,
    leaderstatsLastSuccessAt,
    inventoryLastSuccessAt,
    secondsSinceLastStatusSuccess: resolveSecondsSinceTimestamp(statusLastSuccessAt, serverNowMs),
    secondsSinceLastLeaderstatsSuccess: statsUpload.statsUploadAgeSeconds,
    secondsSinceLastInventorySuccess: inventoryUpload.inventoryUploadAgeSeconds,
    statsUploadFresh: statsUpload.statsUploadFresh === true,
    statsUploadStatus: statsUpload.statsUploadStatus,
    statsRedSince: statsUpload.statsRedSince || null,
    ...leaderstatsUpload.publicLeaderstatsFields(data),
    inventoryUploadFresh: inventoryUpload.inventoryUploadFresh === true,
    inventoryUploadStatus: inventoryUpload.inventoryUploadStatus,
    inventorySyncStatus: inventoryUpload.inventoryUploadStatus,
    inventorySyncReason: inventoryUpload.inventoryUploadReason || null,
    lastInventorySyncAt: inventoryUpload.lastSnapshotUploadAt || null,
    lastFishStoneSyncAt: inventoryUpload.lastSnapshotUploadAt || null,
    fishStoneSyncStatus: inventoryUpload.inventoryUploadStatus,
    inventoryRedSince: inventoryUpload.inventoryRedSince || null,
    inventoryStaleAfterSeconds: inventoryUpload.inventoryStaleAfterSeconds,
    statsFresh: statsUpload.statsUploadFresh === true,
    redSince: uploadStatus.statusColor === 'green'
      ? null
      : (uploadStatus.redSince || inventoryUpload.inventoryRedSince || connection.redSince || null),
    redDurationSeconds: connection.redDurationSeconds != null ? connection.redDurationSeconds : null,
    intervalSeconds: connection.intervalSeconds,
    graceSeconds: connection.graceSeconds,
    lastUploadAttemptAt: data.lastUploadAttemptAt || null,
    lastUploadFailedAt: data.lastUploadFailedAt || null,
    lastFailureReason: data.lastFailureReason || null,
    lastStatusChangeAt: data.lastStatusChangeAt || null,
    lastPayloadHash: data.lastPayloadHash || null,
    dataStale:       (!statsUpload.statsUploadFresh || !inventoryUpload.inventoryUploadFresh)
      || !!(publicFish.dataStale || publicFish.lastGoodFishPreserved),
    lastGoodFishPreserved: !!(publicFish.lastGoodFishPreserved || data.lastGoodFishPreserved),
    lastGoodPublicFishCount: data.lastGoodPublicFishCount || publicFish.fishItems.length || 0,
    playerStats:     leaderstatsUpload.resolvePlayerStatsForLiveDisplay(data, resolvePlayerStatsForApi),
    playerStatsProven: !!(leaderstatsUpload.resolvePlayerStatsForLiveDisplay(data, resolvePlayerStatsForApi)
      && (data.leaderstatsUploadOk === true || data.lastValidLeaderstats)
      && buildPlayerStatsProof(data.playerStats || data.lastValidLeaderstats, data).proven),
    playerStatsUpdatedAt: data.playerStatsUpdatedAt || null,
    liveAccountStats,
    statsSource: liveAccountStats.statsSource,
    statsEmptyReason: liveAccountStats.emptyReason,
    updatedAt: data.updatedAt || null,
  };
  const enrichedFullOnly = wantLite ? {} : {
    ...data,
    playerDataGameItemDbProof: publicFish.playerDataGameItemDbProof || null,
    playerDataItemUtilityProof: publicFish.playerDataItemUtilityProof || null,
    hiddenPublicRows: publicFish.hiddenPublicRows,
    quarantinedPublicNames: publicFish.quarantinedPublicNames,
    missingExpectedFishProof: publicFish.missingExpectedFishProof,
    countParityProof: publicFish.countParityProof,
    rarityColorProof: publicFish.rarityColorProof,
    globalDbUiProof: publicFish.globalDbUiProof,
    missingPublicRarityCount: publicFish.missingPublicRarityCount != null
      ? publicFish.missingPublicRarityCount
      : 0,
    manualRarityProof: publicFish.manualRarityProof || null,
    stoneAssetProof: publicFish.stoneAssetProof || null,
    inventorySortProof: publicFish.inventorySortProof || null,
    globalCatalogProof: (gameItemDbPublic.usesPlayerDataGameItemDbPublicIdentity(data)
      || itemUtilityPublic.usesPlayerDataItemUtilityPublicIdentity(data))
      ? null
      : globalCatalogService.buildGlobalDbSummaryProof(),
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
    snapshotCompleteness: snapshotCompleteness.buildSnapshotCompletenessProof(data),
    syncProof:       buildSyncProof(data),
  };
  const enriched = { ...enrichedBase, ...enrichedFullOnly };

  const body = wantLite
    ? trackerPerf.buildLiteBackpackResponse(enriched)
    : { ...enriched, lite: false, responseMode: 'full' };
  if (includeDebug) {
    res.set('X-Backpack-Mode', body.responseMode || 'full');
    res.set('X-Tracker-Query-Ms', String(Date.now() - queryStartedAt));
  }
  return res.status(200).json(body);
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
  let data  = liveTrackDB[key];
  const refreshAudit = req.query.refreshAudit === '1' || req.query.refreshAudit === 'true';

  if (!data) {
    // Enumerate known keys (usernames only, strip uid: aliases and limit count).
    const knownKeys = Object.keys(liveTrackDB)
      .filter((k) => !k.startsWith('uid:'))
      .slice(0, 100);
    return res.status(404).json({ ok: false, error: 'not_found', key, knownKeys, serverCommit: resolveServerCommit() });
  }
  data = ensureSessionBuildCurrent(data);
  data = snapshotCompleteness.applyRehydratedCompleteness(data, playerStatsStore);
  liveTrackDB[key] = data;

  const wantFullDebug = trackerPerf.isFullDebugRequest(req);
  if (!wantFullDebug) {
    return res.status(200).json(trackerPerf.buildLiteTrackerDebugResponse(data, key, {
      serverCommit: resolveServerCommit(),
      expectedClientBuild: EXPECTED_CLIENT_TRACKER_BUILD,
      uploadGate: trackerConcurrencyGate.stats(),
      isTrustedBuild: isTrustedClientBuild,
    }));
  }
  if (!trackerPerf.isAdminDebugRequest(req)) {
    return res.status(401).json({
      ok: false,
      error: 'unauthorized',
      message: 'Admin token required for full debug (?full=1).',
    });
  }

  if (refreshAudit) {
    data.pendingAuditRefresh = true;
    const persistBase = `${req.protocol}://${req.get('host')}`;
    trackerConcurrencyGate.scheduleDeferredUploadWork(key, () => {
      const session = liveTrackDB[key];
      if (!session) return { skipped: true, reason: 'session_missing' };
      const auditBody = {
        type: 'inventory_snapshot',
        trackerBuild: session.trackerBuild,
        fishItems: session.playerDataFishItems,
        stoneItems: session.playerDataStoneItems,
        totemItems: session.playerDataTotemItems,
        inventoryItemClassificationDebug: session.inventoryItemClassificationDebug,
        totemPathAudit: session.totemPathAudit,
        totemInventoryPathProof: session.totemInventoryPathProof,
        gameItemDbTotemAudit: session.gameItemDbTotemAudit,
        nonFishNonStoneItemGroups: session.nonFishNonStoneItemGroups,
      };
      session.lastInventorySnapshotDiagnostics = buildInventorySnapshotDiagnostics(auditBody, session);
      session.lastInventorySnapshotAt = new Date().toISOString();
      session.pendingAuditRefresh = false;
      return persistSessionState(key, persistBase).catch(() => {});
    });
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
  const publicFishDbg = await buildPublicFishFields(enrichedAll, baseUrl, { sessionData: data, sessionKey: key });
  const imageResolutionProof = fishImageAssets.buildImageResolutionProof(publicFishDbg.fishItems);
  const recoveredSpeciesImageResolutionProof = publicFishDbg.recoveredSpeciesImageResolutionProof
    || snapshotRecovery.buildRecoveredSpeciesImageResolutionProof(publicFishDbg.fishItems);
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
    responseMode:    'full',
    serverCommit:    resolveServerCommit(),
    publicApiBuild:  PUBLIC_API_BUILD,
    renderBuild:     PUBLIC_RENDER_BUILD,
    trackerBuild:    data.trackerBuild || null,
    expectedClientTrackerBuild: EXPECTED_CLIENT_TRACKER_BUILD,
    minimumTrackerBuild: MINIMUM_TRACKER_BUILD,
    allowedTrackerBuilds: ALLOWED_TRACKER_BUILD_EXACT,
    playerStats:     resolvePlayerStatsForApi(data.playerStats),
    playerStatsDebug: playerStatsStore.isTrustedPlayerStats(data.playerStats) ? (data.playerStatsDebug || null) : null,
    playerStatsUpdatedAt: data.playerStatsUpdatedAt || null,
    playerStatsProof: buildPlayerStatsProof(data.playerStats, data),
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
    lastUploadReceivedAt: data.lastUploadReceivedAt || null,
    lastUploadAcceptedAt: data.lastUploadAcceptedAt || null,
    lastUploadRejectedAt: data.lastUploadRejectedAt || null,
    lastUploadRejectReason: data.lastUploadRejectReason || null,
    lastUploadEndpoint: data.lastUploadEndpoint || null,
    lastUploadPayloadType: data.lastUploadPayloadType || null,
    lastUploadUsername: data.lastUploadUsername || null,
    lastUploadSessionKey: data.lastUploadSessionKey || null,
    lastUploadTrackerBuild: data.lastUploadTrackerBuild || null,
    lastUploadHadPlayerStats: data.lastUploadHadPlayerStats === true,
    lastUploadStatusCodeReturned: data.lastUploadStatusCodeReturned != null
      ? data.lastUploadStatusCodeReturned
      : null,
    lastUploadError: data.lastUploadError || data.lastUploadRejectReason || data.lastFailureReason || null,
    latestSuccessfulUploadAt: data.lastSuccessfulUploadAt || data.lastInventoryAt || null,
    uploadPipelineDiagnostics: (() => {
      const snapDiag = data.lastInventorySnapshotDiagnostics || {};
      const fishItems = Array.isArray(publicFishDbg.fishItems) && publicFishDbg.fishItems.length
        ? publicFishDbg.fishItems
        : (Array.isArray(data.playerDataFishItems) ? data.playerDataFishItems : []);
      const stoneItems = Array.isArray(publicFishDbg.stoneItems) && publicFishDbg.stoneItems.length
        ? publicFishDbg.stoneItems
        : (Array.isArray(data.playerDataStoneItems) ? data.playerDataStoneItems : []);
      const totemItems = Array.isArray(publicFishDbg.totemItems) && publicFishDbg.totemItems.length
        ? publicFishDbg.totemItems
        : (Array.isArray(data.playerDataTotemItems) ? data.playerDataTotemItems : []);
      return {
        lastUploadError: data.lastUploadError || data.lastUploadRejectReason || data.lastFailureReason || null,
        lastUploadReceivedAt: data.lastUploadReceivedAt || null,
        latestSuccessfulUploadAt: data.lastSuccessfulUploadAt || data.lastInventoryAt || null,
        lastUploadStatusCodeReturned: data.lastUploadStatusCodeReturned != null
          ? data.lastUploadStatusCodeReturned
          : null,
        lastUploadPayloadType: data.lastUploadPayloadType || null,
        lastHeartbeatPayloadType: data.lastHeartbeatPayloadType || null,
        lastInventorySnapshotAt: data.lastInventorySnapshotAt || data.lastInventoryAt || null,
        lastInventorySnapshotPayloadType: data.lastInventorySnapshotPayloadType || null,
        fishCount: fishItems.length || snapDiag.fishCount || 0,
        stoneCount: stoneItems.length || snapDiag.stoneCount || 0,
        totemCount: totemItems.length || snapDiag.totemCount || 0,
        totemQuantity: totemItems.reduce(
          (s, row) => s + (Number(row?.amount || row?.quantity) > 0 ? Math.floor(Number(row.amount || row.quantity)) : 1),
          0,
        ) || snapDiag.totemQuantity || 0,
        totemScanProof: data.totemScanProof
          || snapDiag.totemScanProof
          || buildTotemScanProof(totemItems),
        rawUploadTotemCount: totemItems.length || snapDiag.totemCount || 0,
        hasInventoryClassificationDebug: !!(
          data.inventoryItemClassificationDebug
          || snapDiag.inventoryItemClassificationDebug
        ),
        uploadGate: trackerConcurrencyGate.stats(),
        aioCacheRefresh: 'scheduled_on_accept',
      };
    })(),
    syncProof: buildSyncProof(data),
    connectionIndicatorProof: buildConnectionIndicatorProof(data),
    statusFormatProof: buildStatusFormatProof(),
    statusNoZeroProof: buildStatusNoZeroProof(),
    routeInventoryOnlyProof: buildRouteInventoryOnlyProof(),
    trackerLuaTouchProof: buildTrackerLuaTouchProof(),
    statsPollingProof: buildStatsPollingProof(),
    unifiedPollPipelineProof: buildUnifiedPollPipelineProof(data),
    statsHarmonyProof: buildStatsHarmonyProof(data),
    totalCaughtIntervalProof: buildTotalCaughtIntervalProof(data),
    coinIntervalProof: buildCoinIntervalProof(data),
    rarestFishIntervalProof: buildRarestFishIntervalProof(data),
    fishStoneIntervalProof: buildFishStoneIntervalProof(data),
    uploadIntervalProof: buildUploadIntervalProof(data),
    toolbarActionProof: buildToolbarActionProof(),
    gridModeProof: buildGridModeProof(),
    responsiveLayoutProof: buildResponsiveLayoutProof(),
    ...buildClientBuildProof(data),
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
    recoveredSpeciesImageResolutionProof,
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
    nameParserProof: publicFishDbg.fishItems.map((f) => f.nameParserProof).filter(Boolean),
    publicCounts: publicFishDbg.publicCounts,
    rarityColorProof: publicFishDbg.rarityColorProof,
    globalDbUiProof: publicFishDbg.globalDbUiProof,
    missingPublicRarityCount: publicFishDbg.missingPublicRarityCount != null
      ? publicFishDbg.missingPublicRarityCount
      : 0,
    manualRarityProof: publicFishDbg.manualRarityProof || null,
    stoneAssetProof: publicFishDbg.stoneAssetProof || null,
    unmappedReviewProof: buildUnmappedReviewProof(enrichedAll),
    trackerClientProof: buildTrackerClientProof(data),
    globalRarityProof: globalCatalogService.buildGlobalRarityProof(publicFishDbg.fishItems),
    globalEvidenceProof: globalCatalogService.buildGlobalEvidenceProof(15),
    globalConflictProof: globalCatalogService.buildGlobalConflictProof(15),
    globalContributionProof: globalCatalogService.buildGlobalContributionProof(),
    quizBotSeedImportProof: globalCatalogService.buildQuizBotSeedImportProof(),
    dengFishItBotCatalogProof: globalCatalogService.buildDengFishItBotCatalogProof(
      publicFishDbg.fishItems.map((f) => f.baseFishName || f.name).filter(Boolean),
    ),
    globalLearningProof: globalCatalogService.buildGlobalLearningProof(25),
    resetSeedProof: globalCatalogService.getLastResetSeedProof(),
    globalDbStats: globalDb.getStats(),
    playerDataGameItemDbProof: publicFishDbg.playerDataGameItemDbProof || null,
    playerDataItemUtilityProof: publicFishDbg.playerDataItemUtilityProof || null,
    activationState: publicFishDbg.activationState || null,
    inventorySource: publicFishDbg.inventorySource || data.inventorySource || null,
    sourceTruth: publicFishDbg.sourceTruth || data.sourceTruth || null,
    stoneItems: publicFishDbg.stoneItems || [],
    totemItems: publicFishDbg.totemItems || [],
    totemScanProof: data.totemScanProof
      || buildTotemScanProof(publicFishDbg.totemItems || data.playerDataTotemItems),
    inventoryItemClassificationDebug: data.inventoryItemClassificationDebug
      || data.lastInventorySnapshotDiagnostics?.inventoryItemClassificationDebug
      || null,
    totemPathAudit: data.totemPathAudit
      || data.lastInventorySnapshotDiagnostics?.totemPathAudit
      || null,
    totemInventoryPathProof: data.totemInventoryPathProof
      || data.lastInventorySnapshotDiagnostics?.totemInventoryPathProof
      || null,
    gameItemDbTotemAudit: data.gameItemDbTotemAudit
      || data.lastInventorySnapshotDiagnostics?.gameItemDbTotemAudit
      || null,
    nonFishNonStoneItemGroups: Array.isArray(data.nonFishNonStoneItemGroups) && data.nonFishNonStoneItemGroups.length
      ? data.nonFishNonStoneItemGroups
      : (data.lastInventorySnapshotDiagnostics?.nonFishNonStoneItemGroups || []),
    lastHeartbeatDiagnostics: data.lastHeartbeatDiagnostics || null,
    lastInventorySnapshotDiagnostics: data.lastInventorySnapshotDiagnostics || null,
    lastInventorySnapshotAt: data.lastInventorySnapshotAt || data.lastInventoryAt || null,
    lastInventorySnapshotBuild: data.lastInventorySnapshotBuild || null,
    lastInventorySnapshotPayloadType: data.lastInventorySnapshotPayloadType || null,
    lastHeartbeatPayloadType: data.lastHeartbeatPayloadType || null,
    fishItems: publicFishDbg.fishItems || [],
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
    snapshotCompleteness: snapshotCompleteness.buildSnapshotCompletenessProof(data),
    snapshotComplete: data.snapshotComplete === true,
    inventoryReady: data.inventoryReady === true,
    inventoryDisplayState: uploadAccountStatus.resolveInventoryDisplayState(data),
    snapshotCompletenessReason: data.snapshotCompletenessReason || null,
    blankPayloadRejected: data.blankPayloadRejected === true,
    uploadAccountStatus: uploadAccountStatus.deriveTrackerUploadAccountStatus(data, {
      expectedTrackerBuild: EXPECTED_CLIENT_TRACKER_BUILD,
      isTrustedBuild: isTrustedClientBuild,
    }),
    selectedGeneralInventoryPath: data.parseStats?.selectedGeneralPath
      || data.parseStats?.selectedPath || null,
    selectedFishInventoryPath: data.parseStats?.selectedFishPath
      || data.fishPathDiscovery?.selectedFishPath || null,
    fishPathDiscovery: data.fishPathDiscovery || data.parseStats?.fishPathDiscovery || null,
    emptyPublicFishReason: publicFishDbg.publicItems.length === 0
      ? (data.snapshotComplete === false
        ? (data.snapshotCompletenessReason || 'awaiting_full_snapshot')
        : (data.partialSnapshotDetected
          ? data.partialSnapshotReason
          : (data.provenEmptyInventory ? 'verified_empty_inventory' : (data.parseStats?.fish === 0 ? 'parse_stats_fish_zero' : 'no_public_fish_items'))))
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
    userSnapshotRecoveryProof: snapshotRecovery.buildUserSnapshotRecoveryProof(
      key,
      data,
      publicFishDbg.fishItems,
    ),
    manualVerifiedCatalogCount: manualVerifiedCatalog.getCount(),
    knownRarityCount: rarityStats.knownCount,
    publicFishContainsGiantSquid: publicFishDbg.fishItems.some(
      (f) => String(f.itemId) === '156' || String(f.baseFishName || f.name).toLowerCase() === 'giant squid',
    ),
  });
});

router.get('/api/tracker/debug/:username', getLimiter, (req, res, next) => {
  req.url = `/api/fishit-tracker/debug/${encodeURIComponent(req.params.username)}`;
  router.handle(req, res, next);
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

function scheduleAioTrackerCacheRefresh(usernameKey) {
  const key = String(usernameKey || '').trim().toLowerCase();
  if (!key) return;
  const listOwners = inventoryTrackedAccounts.listDiscordOwnersForUsernameKey;
  if (typeof listOwners !== 'function') return;
  const baseUrl = internalApiBaseUrl();
  listOwners(key)
    .then((owners) => {
      if (!owners || !owners.length) return;
      for (const ownerId of owners) {
        aioDatasetCache.refreshOwnersAfterUpload(ownerId, {
          tracker: () => buildAioTrackerDataset(ownerId, { baseUrl, fast: true }),
          dashboard: async () => {
            const accounts = await inventoryTrackedAccounts.listTrackedAccounts(ownerId);
            const data = fishitDb.getOwnerDashboard(ownerId, accounts, 'all', {
              queryStartedAt: Date.now(),
            });
            if (data && data.debug) delete data.debug;
            return data;
          },
        });
      }
    })
    .catch(() => {});
}

function buildAioLiteAccountSnapshotFast(session) {
  if (!session) return null;
  let fishSource = Array.isArray(session.lastGoodPublicFishItems) && session.lastGoodPublicFishItems.length
    ? session.lastGoodPublicFishItems
    : [];
  if (!fishSource.length && Array.isArray(session.publicFishItems)) fishSource = session.publicFishItems;
  const fishItems = fishSource.map(stripLiteFishItem).filter(Boolean).slice(0, 48);
  let stoneSource = Array.isArray(session.lastGoodPublicStoneItems) && session.lastGoodPublicStoneItems.length
    ? session.lastGoodPublicStoneItems
    : [];
  if (!stoneSource.length && Array.isArray(session.stoneItems)) stoneSource = session.stoneItems;
  const stoneItems = stoneSource.map(stripLiteStoneItem).filter(Boolean).slice(0, 24);
  let totemSource = Array.isArray(session.lastGoodPublicTotemItems) && session.lastGoodPublicTotemItems.length
    ? session.lastGoodPublicTotemItems
    : [];
  if (!totemSource.length && Array.isArray(session.totemItems)) totemSource = session.totemItems;
  const totemItems = totemSource.map(stripLiteTotemItem).filter(Boolean).slice(0, 24);
  return {
    fishItems,
    stoneItems,
    totemItems,
    hasFish: fishItems.length > 0,
    hasStone: stoneItems.length > 0,
    hasTotem: totemItems.length > 0,
  };
}

function stripLiteFishItem(item) {
  if (!item || typeof item !== 'object') return null;
  const name = item.name || item.displayName || item.speciesName || null;
  if (!name) return null;
  return {
    name,
    rarity: item.rarity || item.rarityLabel || null,
    imageUrl: item.imageUrl || item.resolvedImageUrl || item.cardImageUrl || item.thumb || null,
    count: Number(item.count || item.amount || 1) || 1,
  };
}

function stripLiteTotemItem(item) {
  if (!item || typeof item !== 'object') return null;
  const name = item.name || item.displayName || item.totemName || null;
  if (!name) return null;
  return {
    name,
    imageUrl: item.imageUrl || item.resolvedImageUrl || item.cardImageUrl || null,
    count: Number(item.count || item.amount || 1) || 1,
    uuid: item.uuid || null,
    itemId: item.itemId || null,
  };
}

function stripLiteStoneItem(item) {
  if (!item || typeof item !== 'object') return null;
  const name = item.name || item.displayName || item.stoneName || null;
  if (!name) return null;
  return {
    name,
    imageUrl: item.imageUrl || item.resolvedImageUrl || item.cardImageUrl || null,
    count: Number(item.count || item.amount || 1) || 1,
  };
}

async function buildAioLiteAccountSnapshot(session, sessionKey, baseUrl) {
  if (!session) return null;
  const sourceItems = partialSnapshot.itemsForSessionDisplay(session);
  const enrichedFlat = enrichItemsFromCatalog(sourceItems);
  let publicFish = await buildPublicFishFields(enrichedFlat, baseUrl, {
    sessionData: session,
    sessionKey,
  });
  const liveFishCount = Array.isArray(publicFish.fishItems) ? publicFish.fishItems.length : 0;
  if (liveFishCount === 0 && Array.isArray(session.lastGoodPublicFishItems) && session.lastGoodPublicFishItems.length) {
    const preserved = await reEnrichPublicFishItems(session.lastGoodPublicFishItems, baseUrl);
    publicFish = {
      ...publicFish,
      fishItems: preserved,
      publicFishItems: preserved,
      dataStale: true,
      lastGoodFishPreserved: true,
    };
  } else if (Array.isArray(publicFish.fishItems) && publicFish.fishItems.length) {
    const refreshed = await reEnrichPublicFishItems(publicFish.fishItems, baseUrl);
    publicFish = {
      ...publicFish,
      fishItems: refreshed,
      publicFishItems: refreshed,
    };
  }
  const liveStoneCount = Array.isArray(publicFish.stoneItems) ? publicFish.stoneItems.length : 0;
  if (liveStoneCount === 0 && Array.isArray(session.lastGoodPublicStoneItems) && session.lastGoodPublicStoneItems.length) {
    publicFish = {
      ...publicFish,
      stoneItems: session.lastGoodPublicStoneItems,
      stoneDataStale: true,
      lastGoodStonePreserved: true,
    };
  }
  const liveTotemCount = Array.isArray(publicFish.totemItems) ? publicFish.totemItems.length : 0;
  if (liveTotemCount === 0 && Array.isArray(session.lastGoodPublicTotemItems) && session.lastGoodPublicTotemItems.length) {
    publicFish = {
      ...publicFish,
      totemItems: session.lastGoodPublicTotemItems,
      totemDataStale: true,
      lastGoodTotemPreserved: true,
    };
  }
  const fishItems = (publicFish.fishItems || [])
    .map(stripLiteFishItem)
    .filter(Boolean)
    .slice(0, 48);
  const stoneItems = (publicFish.stoneItems || [])
    .map(stripLiteStoneItem)
    .filter(Boolean)
    .slice(0, 24);
  const totemItems = (publicFish.totemItems || [])
    .map(stripLiteTotemItem)
    .filter(Boolean)
    .slice(0, 24);
  return {
    fishItems,
    stoneItems,
    totemItems,
    hasFish: fishItems.length > 0,
    hasStone: stoneItems.length > 0,
    hasTotem: totemItems.length > 0,
  };
}

/**
 * AIO sync dataset: live tracker account rows + lite fish/stone/stats snapshots.
 * Mirrors /api/tracker/account-status indicator fields for native APK rendering.
 */
async function buildAioTrackerDataset(discordOwnerId, opts = {}) {
  const serverNowMs = Date.now();
  const serverNow = new Date(serverNowMs).toISOString();
  const baseUrl = (opts.baseUrl || internalApiBaseUrl()).replace(/\/+$/, '');
  const trackedAccounts = await inventoryTrackedAccounts.listTrackedAccounts(discordOwnerId);
  const accounts = [];
  for (const acct of (Array.isArray(trackedAccounts) ? trackedAccounts : [])) {
    const usernameKey = acct.robloxUsernameKey
      || acct.roblox_username_key
      || String(acct.robloxUsername || acct.roblox_username || acct.displayName || acct.display_name || '')
        .trim()
        .toLowerCase();
    const robloxUserId = acct.robloxUserId || acct.roblox_user_id || null;
    const { key: sessionKey, session } = uploadAccountStatus.resolveLiveSession(liveTrackDB, {
      robloxUserId,
      usernameKey,
    });
    const sessionData = session
      ? { ...session, discordOwnerId }
      : {
        username: acct.roblox_username || acct.display_name || usernameKey,
        userId: robloxUserId,
        discordOwnerId,
      };
    const proof = uploadAccountStatus.deriveTrackerUploadAccountStatus(sessionData, {
      serverNowMs,
      expectedTrackerBuild: EXPECTED_CLIENT_TRACKER_BUILD,
      isTrustedBuild: isTrustedClientBuild,
    });
    const presence = deriveAccountPresenceStatus(sessionData);
    const statsUpload = deriveStatsUploadStatus(sessionData);
    const inventoryUpload = deriveInventoryUploadStatus(sessionData);
    const liveAccountStats = liveTrackerSerializer.serializeLiveTrackerAccountStats(
      session ? { ...session, ...proof, statusColor: proof.statusColor } : null,
      playerStatsStore,
      resolvePlayerStatsForApi,
    );
    const statsSnapshot = {
      coinsText: liveAccountStats.coinsText,
      totalCaughtText: liveAccountStats.totalCaughtText,
      rarestFishChance: liveAccountStats.rarestFishChance,
      statsProven: liveAccountStats.statsProven === true,
      emptyReason: liveAccountStats.emptyReason || null,
    };
    let snapshot = null;
    if (session && sessionKey) {
      const lite = opts.fast
        ? buildAioLiteAccountSnapshotFast(session)
        : await buildAioLiteAccountSnapshot(session, sessionKey, baseUrl);
      if (lite && (lite.hasFish || lite.hasStone || lite.hasTotem || statsSnapshot.statsProven)) {
        snapshot = {
          stats: statsSnapshot,
          fishItems: lite.fishItems,
          stoneItems: lite.stoneItems,
          totemItems: lite.totemItems,
        };
      }
    } else if (statsSnapshot.statsProven) {
      snapshot = {
        stats: statsSnapshot,
        fishItems: [],
        stoneItems: [],
        totemItems: [],
      };
    }
    accounts.push({
      ...proof,
      ...liveAccountStats,
      accountPresenceLive: presence.accountPresenceLive === true,
      accountOnline: presence.accountPresenceLive === true,
      accountPresenceStatus: presence.accountPresenceStatus,
      accountPresenceReason: presence.accountPresenceReason,
      accountPresenceGraceSeconds: presence.accountPresenceGraceSeconds,
      statsUploadFresh: statsUpload.statsUploadFresh === true,
      statsUploadStatus: statsUpload.statsUploadStatus,
      statsRedSince: statsUpload.statsRedSince || null,
      statsUploadAgeSeconds: statsUpload.statsUploadAgeSeconds,
      inventoryUploadFresh: inventoryUpload.inventoryUploadFresh === true,
      inventoryUploadStatus: inventoryUpload.inventoryUploadStatus,
      inventoryRedSince: inventoryUpload.inventoryRedSince || null,
      lastSnapshotUploadAt: inventoryUpload.lastSnapshotUploadAt || null,
      lastStatsChangeAt: (session && session.lastStatsChangeAt) || null,
      intervalSeconds: statsUpload.intervalSeconds,
      graceSeconds: statsUpload.graceSeconds,
      username: proof.username
        || acct.robloxUsername
        || acct.roblox_username
        || acct.displayName
        || acct.display_name
        || usernameKey,
      robloxUserId: proof.robloxUserId || (robloxUserId ? String(robloxUserId) : null),
      discordOwnerId,
      canonicalKey: robloxUserId ? String(robloxUserId) : usernameKey,
      snapshot,
    });
  }
  return { serverNow, accounts };
}

// Mirror GET /api/fishit-tracker/* read routes onto /api/tracker/* so aio.deng.my.id
// can route POST uploads to ingest (8792) while browser polls stay on web (8791).
(function registerTrackerWebReadAliases() {
  const aliasPrefix = '/api/tracker/';
  const sourcePrefix = '/api/fishit-tracker/';
  const existingPaths = new Set(
    router.stack.filter((layer) => layer.route).map((layer) => layer.route.path),
  );
  for (const layer of router.stack) {
    if (!layer.route || !layer.route.methods.get) continue;
    const path = layer.route.path;
    if (!path.startsWith(sourcePrefix)) continue;
    const aliasPath = aliasPrefix + path.slice(sourcePrefix.length);
    if (existingPaths.has(aliasPath)) continue;
    const handlers = layer.route.stack.map((s) => s.handle);
    router.get(aliasPath, ...handlers);
    existingPaths.add(aliasPath);
  }
})();

module.exports = router;
module.exports.uploadRouter = uploadRouter;
module.exports.liveTrackDB = liveTrackDB;
module.exports.syncLiveTrackFromDisk = syncLiveTrackFromDisk;
module.exports.collectPublicTrackerNetworkStats = collectPublicTrackerNetworkStats;
module.exports.collectPublicTrackerNetworkProof = collectPublicTrackerNetworkProof;
module.exports.buildPublicTrackerStatsPayload = buildPublicTrackerStatsPayload;
module.exports.collectPublicFishItTrackerStats = collectPublicFishItTrackerStats;
module.exports.mergeItemsNoDowngradeFromCatalog = mergeItemsNoDowngradeFromCatalog;
module.exports.enrichItemsFromCatalog = enrichItemsFromCatalog;
module.exports.inventoryCountsFromGroups = inventoryCountsFromGroups;
module.exports.catalogMapForItems = catalogMapForItems;
module.exports.debugItemSlice = debugItemSlice;
module.exports.resolveServerCommit = resolveServerCommit;
module.exports.isSessionLive = isSessionLive;
module.exports.isSessionHeartbeatRecent = isSessionHeartbeatRecent;
module.exports.deriveConnectionStatus = deriveConnectionStatus;
module.exports.deriveUploadSyncStatus = deriveUploadSyncStatus;
module.exports.deriveAccountPresenceStatus = deriveAccountPresenceStatus;
module.exports.resolveLastAccountSeenAt = resolveLastAccountSeenAt;
module.exports.ACCOUNT_PRESENCE_GRACE_MS = ACCOUNT_PRESENCE_GRACE_MS;
module.exports.deriveStatsUploadStatus = deriveStatsUploadStatus;
module.exports.deriveInventoryUploadStatus = deriveInventoryUploadStatus;
module.exports.applyUploadSyncSuccess = applyUploadSyncSuccess;
module.exports.applyUploadSyncFailure = applyUploadSyncFailure;
module.exports.applyTransientUploadFailure = applyTransientUploadFailure;
module.exports.deriveUploadAccountStatus = deriveUploadAccountStatus;
module.exports.uploadAccountStatus = uploadAccountStatus;
module.exports.snapshotCompleteness = snapshotCompleteness;
module.exports.UPLOAD_INTERVAL_SECONDS = UPLOAD_INTERVAL_SECONDS;
module.exports.UPLOAD_GRACE_SECONDS = UPLOAD_GRACE_SECONDS;
module.exports.inventoryUploadGraceSeconds = inventoryUploadGraceSeconds;
module.exports.inventoryUploadStaleAfterSeconds = inventoryUploadStaleAfterSeconds;
module.exports.buildPlayerStatsProof = buildPlayerStatsProof;
module.exports.buildPublicFishFields = buildPublicFishFields;
module.exports.buildAioTrackerDataset = buildAioTrackerDataset;
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
module.exports.BLOCKER10ZA_BUILD = BLOCKER10ZA_BUILD;
module.exports.itemUtilityPublic = itemUtilityPublic;
module.exports.BLOCKER10Z17_BUILD = BLOCKER10Z17_BUILD;
module.exports.BLOCKER10Z16_BUILD = BLOCKER10Z16_BUILD;
module.exports.BLOCKER10Z15_BUILD = BLOCKER10Z15_BUILD;
module.exports.BLOCKER10Z14_BUILD = BLOCKER10Z14_BUILD;
module.exports.BLOCKER10Z13_BUILD = BLOCKER10Z13_BUILD;
module.exports.BLOCKER10Z12_BUILD = BLOCKER10Z13_BUILD;
module.exports.BLOCKER10Z11_BUILD = BLOCKER10Z13_BUILD;
module.exports.BLOCKER10Z10_BUILD = BLOCKER10Z13_BUILD;
module.exports.BLOCKER10Z9_BUILD = BLOCKER10Z13_BUILD;
module.exports.BLOCKER10Z8_BUILD = BLOCKER10Z13_BUILD;
module.exports.isMutationEmbeddedInCanonicalName = isMutationEmbeddedInCanonicalName;
module.exports.buildNameParserProof = buildNameParserProof;
module.exports.isTrustedPublicNameSource = isTrustedPublicNameSource;
module.exports.isSnapshotBackedPublicCard = isSnapshotBackedPublicCard;
module.exports.buildPublicIdentityProof = buildPublicIdentityProof;
module.exports.isContestedCatalogItemId = isContestedCatalogItemId;
module.exports.promoteTrustedAmbiguousContainerRows = promoteTrustedAmbiguousContainerRows;
module.exports.buildQuarantinedPublicNames = buildQuarantinedPublicNames;
module.exports.buildMissingExpectedFishProof = buildMissingExpectedFishProof;
module.exports.isTrustedRadiantCatfishInCatalog = isTrustedRadiantCatfishInCatalog;
module.exports.BLOCKER10Z7_BUILD = BLOCKER10Z13_BUILD;
module.exports.renderTrackerPage = renderTrackerPage;
module.exports.buildTrackerPageLocals = buildTrackerPageLocals;
module.exports.buildInventoryViewer = buildInventoryViewer;
module.exports.requireInventorySession = requireInventorySession;
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
module.exports.persistSessionHeartbeat = persistSessionHeartbeat;
module.exports.flushAllLiveSessionsToDisk = flushAllLiveSessionsToDisk;
module.exports.sessionStore = sessionStore;
module.exports.canonicalCatalog = canonicalCatalog;
module.exports.ingestLearnedFishEntry = ingestLearnedFishEntry;
module.exports.runCatchDeltaOnUpload = runCatchDeltaOnUpload;
module.exports.catalogMetaForItemId = catalogMetaForItemId;
