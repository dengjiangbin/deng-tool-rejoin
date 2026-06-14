'use strict';

const crypto = require('crypto');

/** Fields stripped from normal 10s uploads — debug/audit only. */
const HEAVY_UPLOAD_KEYS = [
  'inventoryItemClassificationDebug',
  'totemPathAudit',
  'totemInventoryPathProof',
  'gameItemDbTotemAudit',
  'nonFishNonStoneItemGroups',
  'unresolvedItems',
  'totemItemRows',
  'playerStatsDebug',
  'sourceTruth',
  'unresolvedDiagnostics',
  'discoveredCatalog',
  'hiddenUnresolvedRows',
];

function isDebugUploadBody(body) {
  if (!body || typeof body !== 'object') return false;
  if (body.debugUpload === true) return true;
  if (body.uploadMode === 'debug') return true;
  return false;
}

function compactInventoryRow(row, kind) {
  if (!row || typeof row !== 'object') return null;
  const qty = Number(row.quantity) > 0 ? Math.floor(Number(row.quantity)) : 1;
  const out = {
    itemId: row.itemId != null ? String(row.itemId) : null,
    name: row.name || row.displayName || null,
    quantity: qty,
    source: row.source || 'playerdata_gameitemdb',
  };
  const imageKeys = ['icon', 'image', 'imageId', 'iconId', 'thumbnail', 'texture', 'assetId',
    'iconAssetId', 'imageAssetId', 'Icon', 'Image', 'ImageId', 'AssetId'];
  for (const key of imageKeys) {
    if (row[key] != null && row[key] !== '' && out[key] == null) out[key] = row[key];
  }
  if (kind === 'fish') {
    if (row.tier) out.tier = row.tier;
    if (row.rarity) out.rarity = row.rarity;
    if (row.icon) out.icon = row.icon;
    if (row.uuid) out.uuid = String(row.uuid);
    if (row.kind) out.kind = row.kind;
    if (row.type) out.type = row.type;
    if (row.identityVerified === true) out.identityVerified = true;
  } else if (kind === 'stone') {
    if (row.stoneType) out.stoneType = row.stoneType;
    if (row.icon) out.icon = row.icon;
    if (row.uuid) out.uuid = String(row.uuid);
    if (row.kind) out.kind = row.kind;
    if (row.identityVerified === true) out.identityVerified = true;
  } else if (kind === 'totem') {
    if (row.type) out.type = row.type;
    if (row.icon) out.icon = row.icon;
    if (row.uuid) out.uuid = String(row.uuid);
    if (row.kind) out.kind = row.kind;
    if (row.resolveSource) out.resolveSource = row.resolveSource;
    if (row.identityVerified === true) out.identityVerified = true;
  }
  return out;
}

function compactItemList(items, kind) {
  if (!Array.isArray(items)) return [];
  return items.map((row) => compactInventoryRow(row, kind)).filter(Boolean);
}

function computeCompactChecksum(body) {
  try {
    const h = crypto.createHash('sha256');
    h.update(String(body?.fishItems?.length || 0));
    h.update('|');
    h.update(String(body?.stoneItems?.length || 0));
    h.update('|');
    h.update(String(body?.totemItems?.length || 0));
    h.update('|');
    h.update(String(body?.uploadSeq || ''));
    h.update('|');
    h.update(String(body?.playerStats?.totalCaught ?? ''));
    h.update('|');
    h.update(String(body?.playerStats?.coins ?? ''));
    return h.digest('hex').slice(0, 16);
  } catch {
    return null;
  }
}

function buildCompactGameItemDbProof(body, existingProof) {
  const fishItems = Array.isArray(body?.fishItems) ? body.fishItems : [];
  const stoneItems = Array.isArray(body?.stoneItems) ? body.stoneItems : [];
  const totemItems = Array.isArray(body?.totemItems) ? body.totemItems : [];
  const totemQty = totemItems.reduce(
    (s, row) => s + (Number(row?.quantity) > 0 ? Math.floor(Number(row.quantity)) : 1),
    0,
  );
  return {
    enabled: true,
    build: body?.trackerBuild || existingProof?.build || null,
    uploadPath: 'playerdata_gameitemdb',
    inventorySource: 'playerdata_gameitemdb',
    fishCount: fishItems.length,
    stoneCount: stoneItems.length,
    totemCount: totemItems.length,
    totemEffectiveQty: totemQty,
    unresolvedCount: 0,
    compact: true,
    payloadChecksum: computeCompactChecksum(body),
  };
}

function compactPlayerStatsDebug(dbg) {
  if (!dbg || typeof dbg !== 'object') return null;
  const out = {
    enabled: dbg.enabled === true,
    source: dbg.source || null,
    rawCoinsValue: dbg.rawCoinsValue,
    rawTotalCaughtValue: dbg.rawTotalCaughtValue,
    rawRarestFishValue: dbg.rawRarestFishValue,
    matchedPath: dbg.matchedPath,
  };
  if (Array.isArray(dbg.leaderstatKeys)) {
    out.leaderstatKeys = dbg.leaderstatKeys.slice(0, 20);
  }
  if (dbg.coinProbe && typeof dbg.coinProbe === 'object') {
    out.coinProbe = {
      matchedPath: dbg.coinProbe.matchedPath,
      parsedValue: dbg.coinProbe.parsedValue,
    };
  }
  return out;
}

function stripHeavyUploadFields(body, { isDebug = false } = {}) {
  if (!body || typeof body !== 'object') return body;
  if (isDebug) return body;
  const out = { ...body };
  for (const key of HEAVY_UPLOAD_KEYS) {
    delete out[key];
  }
  const compactDbg = compactPlayerStatsDebug(body.playerStatsDebug);
  if (compactDbg) {
    out.leaderstatsProofCompact = compactDbg;
  }
  if (Array.isArray(out.fishItems)) {
    out.fishItems = compactItemList(out.fishItems, 'fish');
  }
  if (Array.isArray(out.stoneItems)) {
    out.stoneItems = compactItemList(out.stoneItems, 'stone');
  }
  if (Array.isArray(out.totemItems)) {
    out.totemItems = compactItemList(out.totemItems, 'totem');
  }
  if (out.playerDataGameItemDbProof) {
    out.playerDataGameItemDbProof = buildCompactGameItemDbProof(out, body.playerDataGameItemDbProof);
  }
  return out;
}

function extractAuditFieldsFromBody(body) {
  const proof = body?.playerDataGameItemDbProof && typeof body.playerDataGameItemDbProof === 'object'
    ? body.playerDataGameItemDbProof
    : {};
  return {
    inventoryItemClassificationDebug: body?.inventoryItemClassificationDebug
      || proof.inventoryItemClassificationDebug || null,
    totemPathAudit: body?.totemPathAudit || proof.totemPathAudit || null,
    totemInventoryPathProof: body?.totemInventoryPathProof || proof.totemInventoryPathProof || null,
    gameItemDbTotemAudit: body?.gameItemDbTotemAudit || proof.gameItemDbTotemAudit || null,
    nonFishNonStoneItemGroups: Array.isArray(body?.nonFishNonStoneItemGroups)
      ? body.nonFishNonStoneItemGroups.slice(0, 80)
      : (Array.isArray(proof.nonFishNonStoneItemGroups)
        ? proof.nonFishNonStoneItemGroups.slice(0, 80)
        : []),
  };
}

function shouldLogUnresolvedDebug(opts = {}) {
  if (opts.debugAuditUpload === true) return true;
  if (process.env.DEBUG_AUDIT_UPLOAD === 'true' || process.env.DEBUG_AUDIT_UPLOAD === '1') return true;
  if (process.env.FISHIT_DEBUG_UNRESOLVED === '1' || process.env.FISHIT_DEBUG_UNRESOLVED === 'true') {
    return true;
  }
  if (opts.adminDebug === true) return true;
  return false;
}

module.exports = {
  HEAVY_UPLOAD_KEYS,
  isDebugUploadBody,
  stripHeavyUploadFields,
  compactPlayerStatsDebug,
  compactInventoryRow,
  compactItemList,
  buildCompactGameItemDbProof,
  computeCompactChecksum,
  extractAuditFieldsFromBody,
  shouldLogUnresolvedDebug,
};
