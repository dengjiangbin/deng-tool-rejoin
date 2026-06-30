'use strict';
/**
 * BLOCKER10U2 — persist live tracker sessions across PM2 restarts.
 * Hot uploads use in-memory cache + debounced async flush (no full-file sync read per POST).
 */

const path = require('path');
const fs = require('fs');
const playerStatsStore = require('./fishitPlayerStats');
const snapshotCompleteness = require('./fishitSnapshotCompleteness');
const gameItemDbPublic = require('./fishitGameItemDbPublic');
const shardedStore = require('./fishitSessionStoreSharded');
const { getLagMs } = require('./trackerEventLoopMonitor');

function storePath() {
  return process.env.FISHIT_LIVE_SESSIONS_PATH
    || path.join(__dirname, '..', 'data', 'fishit_live_sessions.json');
}

const MAX_SESSIONS = Number(process.env.FISHIT_MAX_PERSISTED_SESSIONS || 2000);
// NO 500 INSTANCE CAP: owned fish/stone/totem instances must persist completely
// (a rare Ruby+Gemstone past index 500 was previously dropped here). This is a
// high memory-safety ceiling, NOT a truncation of real inventories — the real
// transport bound is the upload body limit (TRACKER_UPLOAD_BODY_LIMIT), so a
// realistic inventory of a few thousand instances persists in full.
const MAX_ITEMS_PER_SESSION = Number(process.env.FISHIT_MAX_PERSISTED_ITEMS || 100000);
const MAX_PUBLIC_FISH = Number(process.env.FISHIT_MAX_PERSISTED_PUBLIC_FISH || 5000);
const FLUSH_DEBOUNCE_MS = Number(process.env.FISHIT_SESSION_FLUSH_MS || 400);
const SYNC_SAVE = process.env.FISHIT_SESSION_SYNC_SAVE === '1'
  || process.env.NODE_ENV === 'test';

const RETRYABLE_FS_CODES = new Set(['EBUSY', 'EPERM', 'EACCES', 'ENOENT']);

let _fileCache = null;
let _pendingDirty = false;
let _flushTimer = null;
let _flushInFlight = false;
let _lastStoreMtimeMs = 0;
let _lastStoreUpdatedAt = null;
let _lastFlushMs = 0;
let _flushCount = 0;
let _flushFailCount = 0;
let _dirtySinceMs = 0;
const MAX_LAG_DEFER_MS = Number(process.env.FISHIT_SESSION_MAX_LAG_DEFER_MS || 3000);
const MAX_ERROR_TEXT = Number(process.env.FISHIT_MAX_STORED_ERROR_CHARS || 240);
const MAX_LANE_ERRORS = Number(process.env.FISHIT_MAX_LANE_ERRORS || 3);

function _defaultFile() {
  return { updatedAt: null, sessions: {}, uidAliases: {} };
}

function _trimPlayerDataStoneRows(rows) {
  if (!Array.isArray(rows) || !rows.length) return [];
  const grouped = gameItemDbPublic.groupStoneRows(rows);
  return grouped.slice(0, MAX_ITEMS_PER_SESSION).map((row) => ({
    kind: row.kind || 'stone',
    itemId: row.itemId != null ? String(row.itemId) : null,
    name: row.name || null,
    stoneType: row.stoneType || null,
    quantity: row.quantity != null ? row.quantity : row.amount,
    tier: row.tier != null ? row.tier : null,
    rarity: row.rarity || null,
    uuid: row.uuid || null,
    // Stone/gem mutation contract (Ruby Gemstone depends on this surviving).
    mutation: row.mutation || null,
    mutationName: row.mutationName || null,
    metadataMutation: row.metadataMutation || null,
    instanceMutation: row.instanceMutation || null,
    mutationSourcePath: row.mutationSourcePath || null,
    weightKg: row.weightKg != null ? row.weightKg : null,
    metadataWeightKg: row.metadataWeightKg != null ? row.metadataWeightKg : null,
    weight: row.weight != null ? row.weight : null,
    weightSourcePath: row.weightSourcePath || null,
    icon: row.icon || null,
    iconRaw: row.iconRaw || row.icon || null,
    iconAssetId: row.iconAssetId != null ? String(row.iconAssetId) : null,
    iconSource: row.iconSource || null,
    imageAssetId: row.imageAssetId != null ? String(row.imageAssetId) : null,
    imageSource: row.imageSource || null,
    imageUrl: row.imageUrl || null,
    source: row.source || null,
    identityVerified: row.identityVerified === true,
    type: row.type || null,
    category: row.category || 'stone',
  }));
}

function _trimPlayerDataRows(rows) {
  if (!Array.isArray(rows)) return [];
  return rows.slice(0, MAX_ITEMS_PER_SESSION).map((row) => {
    if (!row || typeof row !== 'object') return row;
    return {
      kind: row.kind || null,
      itemId: row.itemId != null ? String(row.itemId) : null,
      name: row.name || null,
      cleanName: row.cleanName || null,
      baseName: row.baseName || row.baseFishName || null,
      baseFishName: row.baseFishName || row.baseName || null,
      quantity: row.quantity != null ? row.quantity : row.amount,
      tier: row.tier != null ? row.tier : null,
      rarity: row.rarity || null,
      uuid: row.uuid || null,
      // Per-instance mutation contract — keep every alias so the detail view
      // can resolve the real mutation after a save→reload cycle. (Dropping
      // these aliases is what previously made the detail card show `nil`.)
      mutation: row.mutation || null,
      mutationName: row.mutationName || null,
      metadataMutation: row.metadataMutation || null,
      instanceMutation: row.instanceMutation || null,
      mutationSourcePath: row.mutationSourcePath || null,
      // Per-instance weight contract — must survive persistence for EVERY row,
      // independent of mutation, so non-mutated fish still show their weight.
      weightKg: row.weightKg != null ? row.weightKg : null,
      metadataWeightKg: row.metadataWeightKg != null ? row.metadataWeightKg : null,
      weight: row.weight != null ? row.weight : null,
      weightSourcePath: row.weightSourcePath || null,
      icon: row.icon || null,
      iconRaw: row.iconRaw || row.icon || null,
      iconAssetId: row.iconAssetId != null ? String(row.iconAssetId) : null,
      iconSource: row.iconSource || null,
      imageAssetId: row.imageAssetId != null ? String(row.imageAssetId) : null,
      imageSource: row.imageSource || null,
      imageUrl: row.imageUrl || null,
      source: row.source || null,
      identityVerified: row.identityVerified === true,
      type: row.type || null,
      stoneType: row.stoneType || null,
      category: row.category || null,
    };
  });
}

function _trimItems(items) {
  if (!Array.isArray(items)) return [];
  return items.slice(0, MAX_ITEMS_PER_SESSION).map((it) => {
    if (!it || typeof it !== 'object') return it;
    return {
      name: it.name,
      displayName: it.displayName,
      baseFishName: it.baseFishName,
      mutation: it.mutation,
      mutationName: it.mutationName || null,
      metadataMutation: it.metadataMutation || null,
      instanceMutation: it.instanceMutation || null,
      mutationSourcePath: it.mutationSourcePath || null,
      amount: it.amount,
      category: it.category,
      itemId: it.itemId,
      rarity: it.rarity,
      tier: it.tier,
      weight: it.weight,
      weightKg: it.weightKg,
      metadataWeightKg: it.metadataWeightKg != null ? it.metadataWeightKg : null,
      weightSourcePath: it.weightSourcePath || null,
      imageUrl: it.imageUrl,
      imageAssetId: it.imageAssetId,
      imageStatus: it.imageStatus,
      imageSource: it.imageSource,
      shiny: it.shiny,
      resolved: it.resolved,
      catalogSource: it.catalogSource,
      replionUuid: it.replionUuid || null,
      replionAmountSource: it.replionAmountSource || null,
      metadataFishName: it.metadataFishName || null,
      metadataFishId: it.metadataFishId || null,
      metadataBaseFishName: it.metadataBaseFishName || null,
      containerItemId: it.containerItemId || null,
      rawProof: it.rawProof?.sourcePath
        ? { sourcePath: it.rawProof.sourcePath }
        : (it.rawProof || null),
      identityVerified: it.identityVerified === true,
      replionIdentityUnverified: it.replionIdentityUnverified === true,
    };
  });
}

function sanitiseSession(key, data) {
  if (!data || typeof data !== 'object') return null;
  const pub = Array.isArray(data.lastGoodPublicFishItems)
    ? data.lastGoodPublicFishItems.slice(0, MAX_PUBLIC_FISH)
    : null;
  const pubStone = Array.isArray(data.lastGoodPublicStoneItems)
    ? data.lastGoodPublicStoneItems.slice(0, MAX_PUBLIC_FISH)
    : null;
  const base = {
    username: data.username || key,
    userId: data.userId || 0,
    discordOwnerId: data.discordOwnerId || null,
    source: data.source || 'unknown',
    items: _trimItems(data.items),
    rawItems: _trimItems(data.rawItems),
    inventory: data.inventory || null,
    isOnline: !!data.isOnline,
    phase: data.phase || null,
    parseStats: data.parseStats || null,
    fishPathDiscovery: data.fishPathDiscovery || null,
    trackerBuild: data.trackerBuild || null,
    lastPayloadType: data.lastPayloadType || null,
    lastSeenAt: data.lastSeenAt || null,
    lastAccountSeenAt: data.lastAccountSeenAt || null,
    lastHeartbeatAt: data.lastHeartbeatAt || null,
    lastSuccessfulHeartbeatAt: data.lastSuccessfulHeartbeatAt || null,
    lastSuccessfulUploadAt: data.lastSuccessfulUploadAt || null,
    lastOfflineAt: data.lastOfflineAt || null,
    lastInventoryAt: data.lastInventoryAt || null,
    lastSnapshotUploadAt: data.lastSnapshotUploadAt || null,
    lastStatsUploadAt: data.lastStatsUploadAt || null,
    // ── Source-of-truth report identity (trackerReportIdentity) ──────────────
    // These persist the identity-gated "last real Roblox report" timestamps +
    // monotonic revisions so online/offline truth survives restarts and is read
    // identically by the ingest/worker/read lanes.
    lastRealRobloxStatusAt: data.lastRealRobloxStatusAt || null,
    statusSessionId: data.statusSessionId || null,
    statusSeq: data.statusSeq != null ? data.statusSeq : null,
    statusReportId: data.statusReportId || null,
    statusCapturedAt: data.statusCapturedAt || null,
    statusSentAt: data.statusSentAt || null,
    statusRevision: data.statusRevision != null ? data.statusRevision : null,
    serverReceivedStatusAt: data.serverReceivedStatusAt || null,
    statusIdentityReason: data.statusIdentityReason || null,
    // Identity SOURCE (client_explicit vs backend_derived) must persist or the
    // read API/worker (which only ever see the on-disk row) can never report
    // client_explicit even when the Lua client sends explicit identity — the
    // exact reason both denghub2/dengjiangbin read backend_derived while online.
    reportIdentitySource: data.reportIdentitySource || null,
    lastRealLeaderstatsAt: data.lastRealLeaderstatsAt || null,
    leaderstatsSessionId: data.leaderstatsSessionId || null,
    leaderstatsSeq: data.leaderstatsSeq != null ? data.leaderstatsSeq : null,
    leaderstatsReportId: data.leaderstatsReportId || null,
    leaderstatsCapturedAt: data.leaderstatsCapturedAt || null,
    leaderstatsRevision: data.leaderstatsRevision != null ? data.leaderstatsRevision : null,
    serverReceivedLeaderstatsAt: data.serverReceivedLeaderstatsAt || null,
    leaderstatsIdentitySource: data.leaderstatsIdentitySource || null,
    lastRealInventoryAt: data.lastRealInventoryAt || null,
    inventorySessionId: data.inventorySessionId || null,
    inventorySeq: data.inventorySeq != null ? data.inventorySeq : null,
    inventoryReportId: data.inventoryReportId || null,
    inventoryCapturedAt: data.inventoryCapturedAt || null,
    inventoryHash: data.inventoryHash || null,
    inventoryRevision: data.inventoryRevision != null ? data.inventoryRevision : null,
    serverReceivedInventoryAt: data.serverReceivedInventoryAt || null,
    inventoryIdentitySource: data.inventoryIdentitySource || null,
    leaderstatsUploadOk: data.leaderstatsUploadOk === true,
    leaderstatsUploadedAt: data.leaderstatsUploadedAt || null,
    leaderstatsUploadSeq: data.leaderstatsUploadSeq != null ? data.leaderstatsUploadSeq : null,
    leaderstatsMissingReason: data.leaderstatsMissingReason || null,
    lastValidLeaderstats: data.lastValidLeaderstats || null,
    lastValidLeaderstatsAt: data.lastValidLeaderstatsAt || null,
    lastStatsChangeAt: data.lastStatsChangeAt || null,
    lastRequiredUploadAt: data.lastRequiredUploadAt || null,
    requiredOk: data.requiredOk === true,
    playerStatsChanged: data.playerStatsChanged === true,
    sameValuesFreshSync: data.sameValuesFreshSync === true,
    inventoryChanged: data.inventoryChanged === true,
    lastStatus: data.lastStatus || null,
    lastStatusAt: data.lastStatusAt || null,
    lastLoaderErrorMessage: data.lastLoaderErrorMessage
      ? String(data.lastLoaderErrorMessage).slice(0, MAX_ERROR_TEXT)
      : null,
    redSince: data.redSince || null,
    inventoryRedSince: data.inventoryRedSince || null,
    statsRedSince: data.statsRedSince || null,
    lastSyncReason: data.lastSyncReason || null,
    intervalSeconds: Number(data.intervalSeconds) > 0 ? Number(data.intervalSeconds) : null,
    uploadIntervalSeconds: Number(data.uploadIntervalSeconds) > 0 ? Number(data.uploadIntervalSeconds) : null,
    updatedAt: data.updatedAt || null,
    partialSnapshotDetected: !!data.partialSnapshotDetected,
    partialSnapshotReason: data.partialSnapshotReason || null,
    lastGoodFishPreserved: !!data.lastGoodFishPreserved,
    partialSnapshotMeta: data.partialSnapshotMeta || null,
    lastGoodFishItems: _trimItems(data.lastGoodFishItems),
    lastGoodRawItems: _trimItems(data.lastGoodRawItems),
    lastGoodInventory: data.lastGoodInventory || null,
    lastGoodPublicFishCount: data.lastGoodPublicFishCount || 0,
    lastGoodFishAt: data.lastGoodFishAt || null,
    lastGoodPublicFishItems: pub,
    lastGoodPublicStoneItems: pubStone,
    lastGoodPublicStoneCount: data.lastGoodPublicStoneCount || 0,
    lastGoodPublicTotemItems: Array.isArray(data.lastGoodPublicTotemItems)
      ? data.lastGoodPublicTotemItems.slice(0, MAX_PUBLIC_FISH)
      : null,
    lastGoodPublicTotemCount: data.lastGoodPublicTotemCount || 0,
    lastCatchParsed: data.lastCatchParsed
      || data.nameCatalogDiscovery?.lastCatchParsed
      || null,
    catchWatcherStatus: data.catchWatcherStatus || null,
    nameCatalogDiscovery: data.nameCatalogDiscovery
      ? {
        lastCatchParsed: data.nameCatalogDiscovery.lastCatchParsed || null,
        learnedMappings: (data.nameCatalogDiscovery.learnedMappings || []).slice(0, 20),
      }
      : null,
    userSnapshotRecovery: data.userSnapshotRecovery || null,
    playerStats: (() => {
      const raw = data.playerStats || null;
      if (!raw) return null;
      if (!playerStatsStore.isTrustedPlayerStats(raw)) return null;
      return playerStatsStore.displayablePlayerStats(raw);
    })(),
    playerStatsDebug: (() => {
      const raw = data.playerStatsDebug || null;
      const stats = data.playerStats || null;
      if (!raw || !playerStatsStore.isTrustedPlayerStats(stats)) return null;
      return raw;
    })(),
    playerStatsUpdatedAt: (() => {
      const raw = data.playerStats || null;
      if (!raw || !playerStatsStore.isTrustedPlayerStats(raw)) return null;
      return data.playerStatsUpdatedAt || null;
    })(),
    inventorySource: data.inventorySource || null,
    sourceTruth: data.sourceTruth || null,
    playerDataFishItems: _trimPlayerDataRows(data.playerDataFishItems),
    playerDataStoneItems: _trimPlayerDataStoneRows(data.playerDataStoneItems),
    playerDataTotemItems: _trimPlayerDataRows(data.playerDataTotemItems),
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
    firstSeenAt: data.firstSeenAt || null,
    firstFullSnapshotAt: data.firstFullSnapshotAt || null,
    lastFullSnapshotAt: data.lastFullSnapshotAt || null,
    hasLeaderstatsSnapshot: data.hasLeaderstatsSnapshot === true,
    hasFishSnapshot: data.hasFishSnapshot === true,
    hasStoneSnapshot: data.hasStoneSnapshot === true,
    snapshotComplete: data.snapshotComplete === true,
    inventoryReady: data.inventoryReady === true,
    snapshotCompletenessReason: data.snapshotCompletenessReason || null,
    blankPayloadRejected: data.blankPayloadRejected === true,
    provenEmptyInventory: data.provenEmptyInventory === true,
    fishItemCount: data.fishItemCount != null ? data.fishItemCount : null,
    stoneItemCount: data.stoneItemCount != null ? data.stoneItemCount : null,
    playerDataInventoryCount: data.playerDataInventoryCount != null
      ? data.playerDataInventoryCount
      : null,
    scanCompleted: data.scanCompleted === true,
    restoredFromDisk: false,
  };
  return _compactCurrentSessionState(
    snapshotCompleteness.applyRehydratedCompleteness(base, playerStatsStore),
  );
}

/** Overwrite-only current state — drop legacy duplicates and unbounded debug from hot storage. */
function _compactCurrentSessionState(session) {
  if (!session || typeof session !== 'object') return session;
  const out = { ...session };
  if (Array.isArray(out.playerDataFishItems) && out.playerDataFishItems.length) {
    out.items = [];
    out.rawItems = [];
    out.inventory = null;
    out.lastGoodFishItems = null;
    out.lastGoodRawItems = null;
    out.lastGoodInventory = null;
  }
  delete out.playerStatsDebug;
  delete out.inventoryItemClassificationDebug;
  delete out.totemPathAudit;
  delete out.totemInventoryPathProof;
  delete out.gameItemDbTotemAudit;
  out.nonFishNonStoneItemGroups = [];
  delete out.unresolvedDiagnostics;
  delete out.lastInventorySnapshotDiagnostics;
  delete out.playerDataGameItemDbProof;
  delete out.playerDataUnresolvedItems;
  delete out.hiddenUnresolvedRows;
  delete out.discoveredCatalogIngest;
  if (out.lastLoaderErrorMessage) {
    out.lastLoaderErrorMessage = String(out.lastLoaderErrorMessage).slice(0, MAX_ERROR_TEXT);
  }
  if (out.lastUploadRejectReason) {
    out.lastUploadRejectReason = String(out.lastUploadRejectReason).slice(0, MAX_ERROR_TEXT);
  }
  if (Array.isArray(out.recentLaneErrors)) {
    out.recentLaneErrors = out.recentLaneErrors.slice(-MAX_LANE_ERRORS);
  }
  return out;
}

function _readFileFromDisk() {
  if (!fs.existsSync(storePath())) return _defaultFile();
  const raw = JSON.parse(fs.readFileSync(storePath(), 'utf8'));
  return {
    updatedAt: raw.updatedAt || null,
    sessions: raw.sessions && typeof raw.sessions === 'object' ? raw.sessions : {},
    uidAliases: raw.uidAliases && typeof raw.uidAliases === 'object' ? raw.uidAliases : {},
  };
}

function _syncStoreMetaFromDisk() {
  try {
    if (!fs.existsSync(storePath())) {
      _lastStoreMtimeMs = 0;
      _lastStoreUpdatedAt = null;
      return;
    }
    const st = fs.statSync(storePath());
    _lastStoreMtimeMs = st.mtimeMs;
    if (_fileCache) {
      _lastStoreUpdatedAt = _fileCache.updatedAt || null;
    }
  } catch (_) { /* ignore */ }
}

function ensureFileCache(forceReload = false) {
  if (_fileCache && !forceReload) return _fileCache;
  _fileCache = _readFileFromDisk();
  _syncStoreMetaFromDisk();
  if (!_lastStoreUpdatedAt) _lastStoreUpdatedAt = _fileCache.updatedAt || null;
  return _fileCache;
}

function _applyUidAliases(file, liveTrackDB) {
  if (!liveTrackDB) return;
  file.uidAliases = {};
  for (const [k, v] of Object.entries(liveTrackDB)) {
    if (k.startsWith('uid:') && typeof v === 'string') file.uidAliases[k] = v;
  }
}

function _trimSessionMap(sessions) {
  const keys = Object.keys(sessions).filter((k) => !k.startsWith('uid:'));
  if (keys.length <= MAX_SESSIONS) return;
  const sorted = keys.sort((a, b) => {
    const ta = Date.parse(sessions[a]?.lastSeenAt || 0);
    const tb = Date.parse(sessions[b]?.lastSeenAt || 0);
    return tb - ta;
  });
  for (const drop of sorted.slice(MAX_SESSIONS)) delete sessions[drop];
}

function _prepareFlushPayload() {
  const file = ensureFileCache();
  return {
    updatedAt: file.updatedAt,
    sessions: file.sessions,
    uidAliases: file.uidAliases,
  };
}

async function renameAsyncWithRetry(tmp, target, maxAttempts = 4) {
  let lastErr;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      await fs.promises.rename(tmp, target);
      return;
    } catch (err) {
      lastErr = err;
      if (!RETRYABLE_FS_CODES.has(err.code) || attempt >= maxAttempts - 1) throw err;
      await new Promise((resolve) => setTimeout(resolve, 20 + attempt * 30));
    }
  }
  throw lastErr;
}

async function flushToDiskAsync(options = {}) {
  const priority = options.priority === true;
  if (shardedStore.useShardedStorage()) {
    return shardedStore.flushDirtyAccountsAsync({ priority });
  }
  if (!_pendingDirty || !_fileCache) return { flushed: false };
  if (_flushInFlight) {
    if (priority) scheduleFlushDelay(0);
    return { flushed: false, inFlight: true };
  }
  const lagMs = getLagMs();
  const dirtyAgeMs = _dirtySinceMs > 0 ? Date.now() - _dirtySinceMs : 0;
  if (!SYNC_SAVE && !priority && lagMs > 400 && dirtyAgeMs < MAX_LAG_DEFER_MS) {
    scheduleFlushDelay(Math.min(10_000, FLUSH_DEBOUNCE_MS + 2000));
    return { flushed: false, deferred: true, lagMs, dirtyAgeMs };
  }
  _flushInFlight = true;
  const started = Date.now();
  try {
    const payload = _prepareFlushPayload();
    const dir = path.dirname(storePath());
    await fs.promises.mkdir(dir, { recursive: true });
    const tmp = `${storePath()}.tmp`;
    await fs.promises.writeFile(tmp, JSON.stringify(payload), 'utf8');
    await renameAsyncWithRetry(tmp, storePath());
    _pendingDirty = false;
    _dirtySinceMs = 0;
    _flushCount += 1;
    _lastFlushMs = Date.now() - started;
    try {
      const st = await fs.promises.stat(storePath());
      _lastStoreMtimeMs = st.mtimeMs;
    } catch (_) {
      _lastStoreMtimeMs = Date.now();
    }
    _lastStoreUpdatedAt = payload.updatedAt || null;
    return { flushed: true, durationMs: _lastFlushMs, priority };
  } catch (err) {
    _flushFailCount += 1;
    console.warn('[fishit] session async flush failed:', err && err.message ? err.message : err);
    return { flushed: false, error: err.message };
  } finally {
    _flushInFlight = false;
    if (_pendingDirty) scheduleFlush();
  }
}

function flushToDiskSync() {
  if (shardedStore.useShardedStorage()) {
    return shardedStore.flushDirtyAccountsAsync({ priority: true });
  }
  if (!_fileCache) return { flushed: false };
  const started = Date.now();
  try {
    const payload = _prepareFlushPayload();
    const dir = path.dirname(storePath());
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    const tmp = `${storePath()}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(payload), 'utf8');
    fs.renameSync(tmp, storePath());
    _pendingDirty = false;
    _dirtySinceMs = 0;
    _flushCount += 1;
    _lastFlushMs = Date.now() - started;
    _lastStoreMtimeMs = fs.statSync(storePath()).mtimeMs;
    _lastStoreUpdatedAt = payload.updatedAt || null;
    return { flushed: true, durationMs: _lastFlushMs };
  } catch (err) {
    _flushFailCount += 1;
    throw err;
  }
}

function schedulePriorityFlush() {
  if (shardedStore.useShardedStorage()) {
    shardedStore.flushDirtyAccountsAsync({ priority: true }).catch(() => {});
    return;
  }
  if (SYNC_SAVE) {
    flushToDiskSync();
    return;
  }
  if (_flushTimer) {
    clearTimeout(_flushTimer);
    _flushTimer = null;
  }
  setImmediate(() => {
    flushToDiskAsync({ priority: true }).catch(() => {});
  });
}

function scheduleFlushDelay(ms) {
  if (_flushTimer) clearTimeout(_flushTimer);
  _flushTimer = setTimeout(() => {
    _flushTimer = null;
    flushToDiskAsync().catch(() => {});
  }, ms);
  if (typeof _flushTimer.unref === 'function') _flushTimer.unref();
}

function scheduleFlush() {
  if (SYNC_SAVE) {
    flushToDiskSync();
    return;
  }
  if (_flushTimer) return;
  scheduleFlushDelay(FLUSH_DEBOUNCE_MS);
}

function saveSession(key, data, liveTrackDB) {
  if (!key || !data) return false;
  const row = sanitiseSession(key, data);
  if (!row) return false;
  if (shardedStore.useShardedStorage()) {
    return shardedStore.saveAccount(key, row, liveTrackDB);
  }
  const file = ensureFileCache();
  file.sessions = file.sessions || {};
  file.sessions[key] = row;
  file.updatedAt = new Date().toISOString();
  _applyUidAliases(file, liveTrackDB);
  _trimSessionMap(file.sessions);
  _pendingDirty = true;
  if (!_dirtySinceMs) _dirtySinceMs = Date.now();
  scheduleFlush();
  return true;
}

/** Immediate disk write for heartbeat/status so worker + read see fresh presence. */
function flushSessionImmediate(key, data, options = {}) {
  if (!key || !data) return { flushed: false };
  const row = sanitiseSession(key, data);
  if (!row) return { flushed: false };
  if (shardedStore.useShardedStorage()) {
    if (options.full === true) {
      return shardedStore.flushAccountSync(key, row);
    }
    return shardedStore.flushPresenceHeartbeatSync(key, row);
  }
  return flushToDiskSync();
}

function ensureAccountLoaded(liveTrackDB, key) {
  if (!liveTrackDB || typeof liveTrackDB !== 'object') return { loaded: false };
  const normalizedKey = String(key || '').trim().toLowerCase();
  if (!normalizedKey) return { loaded: false };
  if (shardedStore.useShardedStorage()) {
    return shardedStore.reloadAccountShard(normalizedKey, liveTrackDB, sanitiseSession);
  }
  reloadIfChanged(liveTrackDB);
  return { loaded: !!liveTrackDB[normalizedKey], key: normalizedKey, mode: 'legacy' };
}

function reloadIfChanged(liveTrackDB) {
  if (!liveTrackDB || typeof liveTrackDB !== 'object') return { reloaded: false };
  if (shardedStore.useShardedStorage()) {
    return shardedStore.reloadChangedAccounts(liveTrackDB, sanitiseSession);
  }
  try {
    if (!fs.existsSync(storePath())) return { reloaded: false, path: storePath() };
    const st = fs.statSync(storePath());
    if (st.mtimeMs <= _lastStoreMtimeMs) return { reloaded: false };
    const raw = JSON.parse(fs.readFileSync(storePath(), 'utf8'));
    if (raw.updatedAt === _lastStoreUpdatedAt && st.mtimeMs <= _lastStoreMtimeMs) {
      return { reloaded: false };
    }
    _fileCache = {
      updatedAt: raw.updatedAt || null,
      sessions: raw.sessions && typeof raw.sessions === 'object' ? raw.sessions : {},
      uidAliases: raw.uidAliases && typeof raw.uidAliases === 'object' ? raw.uidAliases : {},
    };
    const sessions = _fileCache.sessions;
    const uidAliases = _fileCache.uidAliases;
    let merged = 0;
    for (const [key, rowData] of Object.entries(sessions)) {
      if (key.startsWith('uid:')) continue;
      const row = sanitiseSession(key, rowData);
      if (!row) continue;
      row.restoredFromDisk = true;
      liveTrackDB[key] = { ...(liveTrackDB[key] || {}), ...row };
      merged += 1;
    }
    for (const [alias, usernameKey] of Object.entries(uidAliases)) {
      liveTrackDB[alias] = usernameKey;
    }
    _lastStoreMtimeMs = st.mtimeMs;
    _lastStoreUpdatedAt = raw.updatedAt || null;
    return { reloaded: true, merged, path: storePath(), updatedAt: raw.updatedAt || null };
  } catch (err) {
    return { reloaded: false, error: err.message };
  }
}

function getSessionFileMetrics() {
  if (shardedStore.useShardedStorage()) {
    return shardedStore.getShardedMetrics();
  }
  try {
    if (_fileCache) {
      const keys = Object.keys(_fileCache.sessions || {}).filter((k) => !k.startsWith('uid:'));
      return {
        path: storePath(),
        exists: true,
        sessionCount: keys.length,
        updatedAt: _fileCache.updatedAt || null,
        mtimeMs: _lastStoreMtimeMs || null,
        pendingDirty: _pendingDirty,
        flushCount: _flushCount,
        flushFailCount: _flushFailCount,
        lastFlushMs: _lastFlushMs,
      };
    }
    if (!fs.existsSync(storePath())) {
      return { path: storePath(), exists: false, sessionCount: 0 };
    }
    const st = fs.statSync(storePath());
    const raw = JSON.parse(fs.readFileSync(storePath(), 'utf8'));
    const keys = Object.keys(raw.sessions || {}).filter((k) => !k.startsWith('uid:'));
    return {
      path: storePath(),
      exists: true,
      sessionCount: keys.length,
      updatedAt: raw.updatedAt || null,
      mtimeMs: st.mtimeMs,
      oldestMtimeMs: st.mtimeMs,
    };
  } catch (err) {
    return { path: storePath(), exists: false, error: err.message };
  }
}

function loadIntoLiveTrackDB(liveTrackDB) {
  if (!liveTrackDB || typeof liveTrackDB !== 'object') return { loaded: 0 };
  if (shardedStore.useShardedStorage()) {
    return shardedStore.loadAllIntoLiveTrackDB(liveTrackDB, sanitiseSession);
  }
  let loaded = 0;
  try {
    _fileCache = _readFileFromDisk();
    const sessions = _fileCache.sessions;
    const uidAliases = _fileCache.uidAliases;
    for (const [key, data] of Object.entries(sessions)) {
      if (key.startsWith('uid:')) continue;
      const row = sanitiseSession(key, data);
      if (!row) continue;
      row.restoredFromDisk = true;
      liveTrackDB[key] = row;
      loaded += 1;
    }
    for (const [alias, usernameKey] of Object.entries(uidAliases)) {
      liveTrackDB[alias] = usernameKey;
    }
    _syncStoreMetaFromDisk();
    _lastStoreUpdatedAt = _fileCache.updatedAt || null;
    return { loaded, path: storePath(), updatedAt: _fileCache.updatedAt || null };
  } catch (err) {
    console.warn('[fishit] session store load failed:', err && err.message ? err.message : err);
    return { loaded: 0, error: err.message };
  }
}

function getStoreMeta() {
  if (shardedStore.useShardedStorage()) {
    return shardedStore.getShardedMetrics();
  }
  try {
    if (_fileCache) {
      const keys = Object.keys(_fileCache.sessions || {}).filter((k) => !k.startsWith('uid:'));
      return {
        exists: true,
        path: storePath(),
        updatedAt: _fileCache.updatedAt,
        sessionCount: keys.length,
        usernames: keys.slice(0, 20),
        pendingDirty: _pendingDirty,
      };
    }
    if (!fs.existsSync(storePath())) return { exists: false, path: storePath() };
    const raw = JSON.parse(fs.readFileSync(storePath(), 'utf8'));
    const keys = Object.keys(raw.sessions || {}).filter((k) => !k.startsWith('uid:'));
    return {
      exists: true,
      path: storePath(),
      updatedAt: raw.updatedAt,
      sessionCount: keys.length,
      usernames: keys.slice(0, 20),
    };
  } catch (err) {
    return { exists: false, path: storePath(), error: err.message };
  }
}

function getSessionStoreFlushMetrics() {
  if (shardedStore.useShardedStorage()) {
    const m = shardedStore.getShardedMetrics();
    return {
      mode: 'sharded',
      pendingDirty: m.pendingDirtyAccounts > 0,
      pendingAccountCount: m.pendingDirtyAccounts,
      flushCount: m.flushCount,
      flushFailCount: m.flushFailCount,
      lastFlushMs: m.lastFlushMs,
      totalBytes: m.totalBytes,
      accountCount: m.accountCount,
    };
  }
  return {
    mode: 'legacy',
    pendingDirty: _pendingDirty,
    dirtyAgeMs: _dirtySinceMs > 0 ? Date.now() - _dirtySinceMs : 0,
    flushCount: _flushCount,
    flushFailCount: _flushFailCount,
    lastFlushMs: _lastFlushMs,
    debounceMs: FLUSH_DEBOUNCE_MS,
    maxLagDeferMs: MAX_LAG_DEFER_MS,
    syncSave: SYNC_SAVE,
  };
}

function _reset() {
  shardedStore.resetShardedForTests();
  if (_flushTimer) {
    clearTimeout(_flushTimer);
    _flushTimer = null;
  }
  _fileCache = null;
  _pendingDirty = false;
  _flushInFlight = false;
  _flushCount = 0;
  _flushFailCount = 0;
  _lastFlushMs = 0;
  _dirtySinceMs = 0;
  try {
    if (fs.existsSync(storePath())) fs.unlinkSync(storePath());
  } catch (_) { /* test seam */ }
  _lastStoreMtimeMs = 0;
  _lastStoreUpdatedAt = null;
}

function _invalidateReloadCursorForTests() {
  _lastStoreMtimeMs = 0;
  _lastStoreUpdatedAt = null;
  shardedStore.invalidateReloadCursorForTests();
}

function migrateToShardedStorageIfNeeded() {
  if (!shardedStore.useShardedStorage()) return { migrated: 0, mode: 'legacy' };
  return shardedStore.migrateLegacyMonolithIfNeeded(sanitiseSession);
}

function buildPublicStatsSessionSnapshot(opts = {}) {
  if (shardedStore.useShardedStorage()) {
    return shardedStore.buildPublicStatsSessionSnapshot(sanitiseSession, opts);
  }
  try {
    _fileCache = _readFileFromDisk();
    const out = {};
    for (const [key, data] of Object.entries(_fileCache.sessions || {})) {
      if (key.startsWith('uid:')) continue;
      const row = sanitiseSession(key, data);
      if (row) out[key] = row;
    }
    return out;
  } catch (_) {
    return {};
  }
}

module.exports = {
  storePath,
  get STORE_PATH() { return storePath(); },
  loadIntoLiveTrackDB,
  ensureAccountLoaded,
  reloadIfChanged,
  buildPublicStatsSessionSnapshot,
  saveSession,
  flushSessionImmediate,
  flushToDiskSync,
  flushToDiskAsync,
  schedulePriorityFlush,
  sanitiseSession,
  migrateToShardedStorageIfNeeded,
  getStoreMeta,
  getSessionFileMetrics,
  getSessionStoreFlushMetrics,
  _reset,
  _invalidateReloadCursorForTests,
  _compactCurrentSessionState,
};
