'use strict';
/**
 * BLOCKER10U2 — persist live tracker sessions across PM2 restarts.
 */

const path = require('path');
const fs = require('fs');
const playerStatsStore = require('./fishitPlayerStats');
const snapshotCompleteness = require('./fishitSnapshotCompleteness');

const STORE_PATH = process.env.FISHIT_LIVE_SESSIONS_PATH
  || path.join(__dirname, '..', 'data', 'fishit_live_sessions.json');

const MAX_SESSIONS = Number(process.env.FISHIT_MAX_PERSISTED_SESSIONS || 200);
const MAX_ITEMS_PER_SESSION = Number(process.env.FISHIT_MAX_PERSISTED_ITEMS || 500);
const MAX_PUBLIC_FISH = Number(process.env.FISHIT_MAX_PERSISTED_PUBLIC_FISH || 100);

function _defaultFile() {
  return { updatedAt: null, sessions: {}, uidAliases: {} };
}

function _trimPlayerDataRows(rows) {
  if (!Array.isArray(rows)) return [];
  return rows.slice(0, MAX_ITEMS_PER_SESSION).map((row) => {
    if (!row || typeof row !== 'object') return row;
    return {
      kind: row.kind || null,
      itemId: row.itemId != null ? String(row.itemId) : null,
      name: row.name || null,
      baseName: row.baseName || row.baseFishName || null,
      baseFishName: row.baseFishName || row.baseName || null,
      quantity: row.quantity != null ? row.quantity : row.amount,
      tier: row.tier != null ? row.tier : null,
      rarity: row.rarity || null,
      uuid: row.uuid || null,
      mutation: row.mutation || null,
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
      amount: it.amount,
      category: it.category,
      itemId: it.itemId,
      rarity: it.rarity,
      tier: it.tier,
      weight: it.weight,
      weightKg: it.weightKg,
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
    lastStatus: data.lastStatus || null,
    lastStatusAt: data.lastStatusAt || null,
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
    playerDataStoneItems: _trimPlayerDataRows(data.playerDataStoneItems),
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
  return snapshotCompleteness.applyRehydratedCompleteness(base, playerStatsStore);
}

let _lastStoreMtimeMs = 0;
let _lastStoreUpdatedAt = null;

function reloadIfChanged(liveTrackDB) {
  if (!liveTrackDB || typeof liveTrackDB !== 'object') return { reloaded: false };
  try {
    if (!fs.existsSync(STORE_PATH)) return { reloaded: false, path: STORE_PATH };
    const st = fs.statSync(STORE_PATH);
    if (st.mtimeMs <= _lastStoreMtimeMs) return { reloaded: false };
    const raw = JSON.parse(fs.readFileSync(STORE_PATH, 'utf8'));
    if (raw.updatedAt === _lastStoreUpdatedAt && st.mtimeMs <= _lastStoreMtimeMs) {
      return { reloaded: false };
    }
    const sessions = raw.sessions && typeof raw.sessions === 'object' ? raw.sessions : {};
    const uidAliases = raw.uidAliases && typeof raw.uidAliases === 'object' ? raw.uidAliases : {};
    let merged = 0;
    for (const [key, data] of Object.entries(sessions)) {
      if (key.startsWith('uid:')) continue;
      const row = sanitiseSession(key, data);
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
    return { reloaded: true, merged, path: STORE_PATH, updatedAt: raw.updatedAt || null };
  } catch (err) {
    return { reloaded: false, error: err.message };
  }
}

function getSessionFileMetrics() {
  try {
    if (!fs.existsSync(STORE_PATH)) {
      return { path: STORE_PATH, exists: false, sessionCount: 0 };
    }
    const st = fs.statSync(STORE_PATH);
    const raw = JSON.parse(fs.readFileSync(STORE_PATH, 'utf8'));
    const keys = Object.keys(raw.sessions || {}).filter((k) => !k.startsWith('uid:'));
    return {
      path: STORE_PATH,
      exists: true,
      sessionCount: keys.length,
      updatedAt: raw.updatedAt || null,
      mtimeMs: st.mtimeMs,
      oldestMtimeMs: st.mtimeMs,
    };
  } catch (err) {
    return { path: STORE_PATH, exists: false, error: err.message };
  }
}

function loadIntoLiveTrackDB(liveTrackDB) {
  if (!liveTrackDB || typeof liveTrackDB !== 'object') return { loaded: 0 };
  let loaded = 0;
  try {
    if (!fs.existsSync(STORE_PATH)) return { loaded: 0, path: STORE_PATH };
    const raw = JSON.parse(fs.readFileSync(STORE_PATH, 'utf8'));
    const sessions = raw.sessions && typeof raw.sessions === 'object' ? raw.sessions : {};
    const uidAliases = raw.uidAliases && typeof raw.uidAliases === 'object' ? raw.uidAliases : {};
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
    _lastStoreMtimeMs = fs.existsSync(STORE_PATH) ? fs.statSync(STORE_PATH).mtimeMs : 0;
    _lastStoreUpdatedAt = raw.updatedAt || null;
    return { loaded, path: STORE_PATH, updatedAt: raw.updatedAt || null };
  } catch (err) {
    console.warn('[fishit] session store load failed:', err && err.message ? err.message : err);
    return { loaded: 0, error: err.message };
  }
}

const RETRYABLE_FS_CODES = new Set(['EBUSY', 'EPERM', 'EACCES', 'ENOENT']);

function sleepMs(ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) { /* sync backoff for hot upload path */ }
}

function renameWithRetry(tmp, target, maxAttempts = 6) {
  let lastErr;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      fs.renameSync(tmp, target);
      return;
    } catch (err) {
      lastErr = err;
      if (!RETRYABLE_FS_CODES.has(err.code) || attempt >= maxAttempts - 1) throw err;
      sleepMs(15 + attempt * 25);
    }
  }
  throw lastErr;
}

function saveSession(key, data, liveTrackDB) {
  if (!key || !data) return false;
  let file = _defaultFile();
  try {
    if (fs.existsSync(STORE_PATH)) {
      file = JSON.parse(fs.readFileSync(STORE_PATH, 'utf8'));
    }
  } catch (_) { /* fresh file */ }

  file.sessions = file.sessions || {};
  file.sessions[key] = sanitiseSession(key, data);
  file.updatedAt = new Date().toISOString();

  if (liveTrackDB) {
    file.uidAliases = {};
    for (const [k, v] of Object.entries(liveTrackDB)) {
      if (k.startsWith('uid:') && typeof v === 'string') file.uidAliases[k] = v;
    }
  }

  const keys = Object.keys(file.sessions).filter((k) => !k.startsWith('uid:'));
  if (keys.length > MAX_SESSIONS) {
    const sorted = keys.sort((a, b) => {
      const ta = Date.parse(file.sessions[a]?.lastSeenAt || 0);
      const tb = Date.parse(file.sessions[b]?.lastSeenAt || 0);
      return tb - ta;
    });
    for (const drop of sorted.slice(MAX_SESSIONS)) delete file.sessions[drop];
  }

  const dir = path.dirname(STORE_PATH);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  const tmp = `${STORE_PATH}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(file, null, 2), 'utf8');
  renameWithRetry(tmp, STORE_PATH);
  return true;
}

function getStoreMeta() {
  try {
    if (!fs.existsSync(STORE_PATH)) return { exists: false, path: STORE_PATH };
    const raw = JSON.parse(fs.readFileSync(STORE_PATH, 'utf8'));
    const keys = Object.keys(raw.sessions || {}).filter((k) => !k.startsWith('uid:'));
    return {
      exists: true,
      path: STORE_PATH,
      updatedAt: raw.updatedAt,
      sessionCount: keys.length,
      usernames: keys.slice(0, 20),
    };
  } catch (err) {
    return { exists: false, path: STORE_PATH, error: err.message };
  }
}

function _reset() {
  try {
    if (fs.existsSync(STORE_PATH)) fs.unlinkSync(STORE_PATH);
  } catch (_) { /* test seam */ }
  _lastStoreMtimeMs = 0;
  _lastStoreUpdatedAt = null;
}

module.exports = {
  STORE_PATH,
  loadIntoLiveTrackDB,
  reloadIfChanged,
  saveSession,
  sanitiseSession,
  getStoreMeta,
  getSessionFileMetrics,
  _reset,
};
