'use strict';
/**
 * BLOCKER10Z17 — Admin/user inventory snapshot recovery for FishTracker sessions.
 */

const path = require('path');
const fs = require('fs');
const catchNameParser = require('./fishitCatchNameParser');
const globalDb = require('./fishitGlobalDb');
const globalCatalogService = require('./fishitGlobalCatalogService');
const quizBotImageCatalog = require('./fishitQuizBotImageCatalog');
const dengFishItBotCatalog = require('./fishitDengFishItBotCatalog');
const protectedFishNames = require('./fishitProtectedFishNames');

const RECOVERY_REGISTRY_PATH = process.env.FISHIT_SNAPSHOT_RECOVERY_PATH
  || path.join(__dirname, '..', 'data', 'fishit_snapshot_recovery.json');

/** Trusted user-provided inventory snapshots (admin recovery evidence). */
const SNAPSHOT_SOURCES = {
  user_snapshot_2026_06_09: {
    sessionKey: 'denghub2',
    userId: 11033782953,
    description: 'User in-game inventory screenshots 2026-06-09 (denghub2)',
    expectedInventoryCounts: {
      'Elshark Gran Maja': 1,
      'Giant Squid': 1,
      'Mosasaur Shark': 1,
      'Panther Eel': 1,
      'Skeleton Angler Fish': 2,
      'Sparkly Eel': 2,
      'Viperangler Fish': 1,
      'Freshwater Piranha': 2,
      'Parrot Fish': 3,
      'Mossy Fishlet': 2,
      'Zebra Snakehead': 7,
      'Red Goatfish': 4,
    },
    /** Raw screenshot labels → canonical base name (mutation stacking proof). */
    rawScreenshotEntries: [
      { raw: 'Elshark Gran Maja', amount: 1 },
      { raw: 'Giant Squid', amount: 1 },
      { raw: 'Mosasaur Shark', amount: 1 },
      { raw: 'Panther Eel', amount: 1 },
      { raw: 'Big Skeleton Angler Fish', amount: 1 },
      { raw: 'Skeleton Angler Fish', amount: 1 },
      { raw: 'Sparkly Eel', amount: 2 },
      { raw: 'Big Viperangler Fish', amount: 1 },
      { raw: 'Freshwater Piranha', amount: 2 },
      { raw: 'Parrot Fish Albino', amount: 1 },
      { raw: 'Parrot Fish', amount: 2 },
      { raw: 'Mossy Fishlet', amount: 2 },
      { raw: 'Zebra Snakehead', amount: 7 },
      { raw: 'Red Goatfish Sandy', amount: 1 },
      { raw: 'Red Goatfish', amount: 3 },
    ],
    recoveredSpecies: ['Elshark Gran Maja', 'Mosasaur Shark', 'Sparkly Eel'],
  },
};

function stripMutationSuffixes(text) {
  let s = String(text || '').trim();
  const suffixes = catchNameParser.MUTATION_LABELS_ORDERED || [
    'fairy dust', 'radioactive shiny', 'radiant', 'big', 'shiny', 'baby', 'giant',
    'mutated', 'albino', 'darkened', 'glossy', 'mosaic', 'silver', 'golden', 'gold',
    'mythical', 'frozen', 'electric', 'sandy', 'corrupt', 'ghost', 'midnight',
    'radioactive', 'galaxy', 'holographic',
  ];
  const mutations = [];
  let changed = true;
  while (changed) {
    changed = false;
    for (const label of suffixes) {
      const re = new RegExp(`\\s+${label.replace(/\s+/g, '\\s+')}$`, 'i');
      if (re.test(s)) {
        mutations.unshift(label);
        s = s.replace(re, '').trim();
        changed = true;
        break;
      }
    }
  }
  return { baseFishName: s || null, mutationSuffix: mutations.length ? mutations.join(' ') : null };
}

function normalizeSnapshotFishName(rawName) {
  const s = String(rawName || '').trim();
  if (!s) return { baseFishName: null, mutation: null, displayName: null };
  if (protectedFishNames.isProtectedBaseName(s)) {
    return {
      baseFishName: protectedFishNames.normalizeProtected(s),
      mutation: null,
      displayName: s,
    };
  }
  const parsed = catchNameParser.parseCatchInput({ fishName: s, rawText: s });
  let base = parsed.baseFishName || parsed.fishNameCandidate || s;
  let mutation = parsed.mutation || null;
  const suffix = stripMutationSuffixes(base);
  if (suffix.mutationSuffix) {
    base = suffix.baseFishName || base;
    mutation = mutation ? `${mutation} ${suffix.mutationSuffix}` : suffix.mutationSuffix;
  }
  return {
    baseFishName: base,
    mutation,
    displayName: parsed.displayName || base,
  };
}

function foldKey(name) {
  return String(name || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

function getSnapshotSource(sourceId) {
  return SNAPSHOT_SOURCES[sourceId] || null;
}

function listSnapshotSources() {
  return Object.keys(SNAPSHOT_SOURCES);
}

function loadRecoveryRegistry() {
  try {
    if (!fs.existsSync(RECOVERY_REGISTRY_PATH)) return { sessions: {} };
    return JSON.parse(fs.readFileSync(RECOVERY_REGISTRY_PATH, 'utf8'));
  } catch (_) {
    return { sessions: {} };
  }
}

function saveRecoveryRegistry(registry) {
  const dir = path.dirname(RECOVERY_REGISTRY_PATH);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  const tmp = `${RECOVERY_REGISTRY_PATH}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(registry, null, 2), 'utf8');
  fs.renameSync(tmp, RECOVERY_REGISTRY_PATH);
}

function backupFilesBeforeWrite() {
  const ts = new Date().toISOString().replace(/[:.]/g, '-');
  const backupDir = path.join(__dirname, '..', 'data', 'backups', `snapshot_recovery_${ts}`);
  fs.mkdirSync(backupDir, { recursive: true });
  const copies = [];
  const candidates = [
    RECOVERY_REGISTRY_PATH,
    path.join(__dirname, '..', 'data', 'fishit_live_sessions.json'),
    globalDb.dbPath(),
  ];
  for (const src of candidates) {
    if (!fs.existsSync(src)) continue;
    const dest = path.join(backupDir, path.basename(src));
    fs.copyFileSync(src, dest);
    copies.push(dest);
  }
  return { backupDir, copies };
}

function resolveSpeciesImageAndRarity(speciesName) {
  const searchedAliases = quizBotImageCatalog.searchAliasesForName(speciesName);
  let quiz = null;
  let matchedAlias = null;
  for (const alias of searchedAliases) {
    const hit = quizBotImageCatalog.lookupByFishName(alias);
    if (hit && (hit.localPath || hit.assetId)) {
      quiz = hit;
      matchedAlias = alias;
      break;
    }
  }
  const bot = dengFishItBotCatalog.lookupEntry(speciesName);
  let fishit = null;
  try {
    const fishitDb = require('./fishitDb');
    fishit = fishitDb.resolveSpeciesImageSource(speciesName, null);
  } catch (_) { /* optional */ }

  const assetId = quiz?.assetId || fishit?.assetId || null;
  const imageUrl = null;
  const rarity = bot?.rarity || null;
  const searchedSources = ['quiz_bot_fishit_bank', 'deng_fish_it_bot', 'fishit_db_fallback'];
  const imageMissingProof = !quiz?.localPath && !assetId && !fishit?.url
    ? {
      speciesName,
      reason: 'noTrustedImageFound',
      placeholder: true,
      searchedAliases,
      searchedSources,
    }
    : null;

  return {
    speciesName,
    imageAssetId: assetId,
    imageUrl,
    imageSource: quiz ? 'quiz_bot_fishit_bank' : (fishit?.url ? 'fishit_db' : 'missing'),
    rarity,
    raritySource: bot ? 'deng_fish_it_bot' : null,
    imageMissingProof,
    quizBotMatched: !!quiz,
    dengBotMatched: !!bot,
    quizBankId: quiz?.bankId || null,
    sourceFile: quiz?.localFile || null,
    sourceDb: quiz?.sourceDb || null,
    localSeedPath: quiz?.localPath || null,
    matchedAlias,
    searchedAliases,
    searchedSources,
  };
}

function seedGlobalSpeciesEvidence(speciesName, { source, sessionKey, dryRun }) {
  const resolved = resolveSpeciesImageAndRarity(speciesName);
  const proof = {
    speciesName,
    action: dryRun ? 'would_upsert_species' : 'upserted_species',
    itemIdMappingStatus: 'pending',
    imageResolutionProof: resolved,
    rarityResolutionProof: resolved.rarity
      ? { rarity: resolved.rarity, source: resolved.raritySource }
      : { rarity: null, source: null },
  };

  if (dryRun) return proof;

  const speciesId = globalDb.upsertSpecies({
    canonical_name: speciesName,
    display_name: speciesName,
    rarity: resolved.rarity,
    rarity_source: resolved.raritySource,
    image_url: resolved.imageUrl,
    cached_image_url: resolved.imageUrl,
    image_source: resolved.imageSource,
    quiz_bot_asset_id: resolved.imageAssetId,
    source: `user_snapshot_recovery:${source}`,
    verification_status: globalDb.VERIFICATION.LIVE_OBSERVED,
  });

  const sessionHash = sessionKey
    ? globalDb.hashContributor(sessionKey, 'fishit_session_v1') : null;
  const observationId = globalDb.insertObservation({
    anonymized_user_hash: globalDb.hashContributor('snapshot_recovery'),
    session_key_hash: sessionHash,
    item_id: null,
    raw_name: speciesName,
    parsed_base_name: speciesName,
    mutation: null,
    source_payload_type: 'user_snapshot_recovery',
    observed_at: new Date().toISOString(),
  });

  proof.speciesId = speciesId;
  proof.observationId = observationId;
  return proof;
}

function applySnapshotRecovery({ sessionKey, sourceId, dryRun = false, confirm = false } = {}) {
  const source = getSnapshotSource(sourceId);
  if (!source) {
    return { ok: false, error: 'unknown_source', sourceId };
  }
  if (source.sessionKey !== sessionKey) {
    return { ok: false, error: 'session_mismatch', expected: source.sessionKey, got: sessionKey };
  }

  const isDryRun = !confirm;
  const proof = {
    ok: true,
    mode: isDryRun ? 'dry-run' : 'confirm',
    sessionKey,
    source: sourceId,
    expectedInventoryCounts: { ...source.expectedInventoryCounts },
    recoveredSpecies: [...source.recoveredSpecies],
    recoveredAmounts: {},
    speciesEvidenceRows: [],
    itemIdMappingStatus: 'pending',
    mutationNormalizationProof: [],
    backup: null,
    filesModified: [],
  };

  for (const entry of source.rawScreenshotEntries || []) {
    const norm = normalizeSnapshotFishName(entry.raw);
    proof.mutationNormalizationProof.push({
      raw: entry.raw,
      baseFishName: norm.baseFishName,
      mutation: norm.mutation,
      amount: entry.amount,
    });
  }

  for (const [name, amount] of Object.entries(source.expectedInventoryCounts)) {
    if (source.recoveredSpecies.includes(name)) {
      proof.recoveredAmounts[name] = amount;
    }
  }

  if (isDryRun) {
    for (const speciesName of source.recoveredSpecies) {
      proof.speciesEvidenceRows.push(seedGlobalSpeciesEvidence(speciesName, {
        source: sourceId,
        sessionKey,
        dryRun: true,
      }));
    }
    proof.totalExpectedFish = Object.values(source.expectedInventoryCounts)
      .reduce((s, n) => s + n, 0);
    proof.totalExpectedTypes = Object.keys(source.expectedInventoryCounts).length;
    return proof;
  }

  proof.backup = backupFilesBeforeWrite();

  for (const speciesName of source.recoveredSpecies) {
    proof.speciesEvidenceRows.push(seedGlobalSpeciesEvidence(speciesName, {
      source: sourceId,
      sessionKey,
      dryRun: false,
    }));
  }

  const registry = loadRecoveryRegistry();
  registry.sessions = registry.sessions || {};
  registry.sessions[sessionKey] = {
    source: sourceId,
    seededAt: new Date().toISOString(),
    userId: source.userId,
    expectedInventoryCounts: { ...source.expectedInventoryCounts },
    recoveredSpecies: [...source.recoveredSpecies],
    recoveredAmounts: { ...proof.recoveredAmounts },
    itemIdMappingStatus: 'pending',
    description: source.description,
  };
  registry.updatedAt = new Date().toISOString();
  saveRecoveryRegistry(registry);
  proof.filesModified.push(RECOVERY_REGISTRY_PATH);

  const sessionsPath = path.join(__dirname, '..', 'data', 'fishit_live_sessions.json');
  if (fs.existsSync(sessionsPath)) {
    try {
      const sessionsFile = JSON.parse(fs.readFileSync(sessionsPath, 'utf8'));
      sessionsFile.sessions = sessionsFile.sessions || {};
      const existing = sessionsFile.sessions[sessionKey] || { username: sessionKey };
      existing.userSnapshotRecovery = registry.sessions[sessionKey];
      sessionsFile.sessions[sessionKey] = existing;
      sessionsFile.updatedAt = new Date().toISOString();
      const tmp = `${sessionsPath}.tmp`;
      fs.writeFileSync(tmp, JSON.stringify(sessionsFile, null, 2), 'utf8');
      fs.renameSync(tmp, sessionsPath);
      proof.filesModified.push(sessionsPath);
    } catch (err) {
      proof.sessionFileWarning = err.message;
    }
  }

  proof.totalExpectedFish = Object.values(source.expectedInventoryCounts)
    .reduce((s, n) => s + n, 0);
  proof.totalExpectedTypes = Object.keys(source.expectedInventoryCounts).length;
  return proof;
}

function getSessionRecoveryMeta(sessionKey, sessionData = null) {
  if (sessionData?.userSnapshotRecovery) return sessionData.userSnapshotRecovery;
  const registry = loadRecoveryRegistry();
  return registry.sessions?.[sessionKey] || null;
}

function countPublicByBaseName(publicItems) {
  const counts = {};
  for (const item of publicItems || []) {
    const base = item.baseFishName || item.name || item.cardName;
    if (!base) continue;
    const key = foldKey(base);
    counts[key] = (counts[key] || 0) + (Number(item.amount) > 0 ? Math.floor(Number(item.amount)) : 1);
  }
  return counts;
}

function buildRecoveryCard(speciesName, amount, baseUrl, meta) {
  const resolved = resolveSpeciesImageAndRarity(speciesName);
  return {
    speciesId: null,
    globalSpeciesId: null,
    canonicalName: speciesName,
    displayName: speciesName,
    name: speciesName,
    cardName: speciesName,
    publicCardName: speciesName,
    baseFishName: speciesName,
    amount: Math.max(1, Math.floor(Number(amount) || 1)),
    replionAmountSource: 'user_snapshot_recovery',
    replionUuid: null,
    metadataFishId: null,
    metadataFishName: speciesName,
    containerItemId: null,
    containerIdCollision: false,
    replionIdentityUnverified: false,
    identityVerified: true,
    catalogLockedBaseName: speciesName,
    dataAmountSource: 'user_snapshot_recovery',
    rarity: null,
    raritySource: null,
    rarityAccentColor: null,
    imageUrl: null,
    imageUrlPresent: false,
    imageResolved: false,
    imageAssetId: resolved.imageAssetId,
    verifiedProxy: false,
    imageSource: resolved.imageSource,
    imageStatus: 'pending_species_cache',
    mutationTags: [],
    mutation: null,
    sourcePriority: 'user_snapshot_recovery',
    confidence: 'user_snapshot_recovery',
    groupedInstanceCount: 1,
    itemId: null,
    category: 'fish',
    publicWeightHidden: true,
    userSnapshotRecovery: true,
    snapshotPromotion: 'user_inventory_snapshot',
    publicIdentityProof: {
      currentSnapshot: true,
      nameTrusted: true,
      notFromCatchDeltaOnly: true,
      identitySource: 'user_snapshot_recovery',
      catalogSource: 'user_snapshot_recovery',
      recoverySource: meta?.source || null,
    },
    imageMissingProof: resolved.imageMissingProof,
    speciesImageSeed: {
      quizBankId: resolved.quizBankId,
      sourceFile: resolved.sourceFile,
      localSeedPath: resolved.localSeedPath,
      matchedAlias: resolved.matchedAlias,
      searchedAliases: resolved.searchedAliases,
    },
  };
}

/**
 * Merge snapshot-recovery cards for species missing from snapshot-backed public cards.
 */
function mergeRecoveryIntoPublicFish(publicFishResult, sessionKey, sessionData) {
  const meta = getSessionRecoveryMeta(sessionKey, sessionData);
  if (!meta?.expectedInventoryCounts) {
    return { ...publicFishResult, userSnapshotRecoveryApplied: false };
  }

  const existing = publicFishResult.fishItems || publicFishResult.publicFishItems || [];
  const existingCounts = countPublicByBaseName(existing);
  const recoveryCards = [];
  const mergeProof = [];

  for (const [speciesName, expectedAmount] of Object.entries(meta.expectedInventoryCounts)) {
    const key = foldKey(speciesName);
    const currentAmount = existingCounts[key] || 0;
    const deficit = expectedAmount - currentAmount;
    if (deficit <= 0) {
      mergeProof.push({
        speciesName,
        expectedAmount,
        currentAmount,
        action: 'snapshot_sufficient',
      });
      continue;
    }
    const card = buildRecoveryCard(speciesName, deficit, null, meta);
    recoveryCards.push(card);
    mergeProof.push({
      speciesName,
      expectedAmount,
      currentAmount,
      recoveredAmount: deficit,
      action: 'recovery_card_added',
      reason: meta.recoveredSpecies.includes(speciesName)
        ? 'user_snapshot_recovery_missing_species'
        : 'user_snapshot_count_deficit',
    });
  }

  const merged = [...existing, ...recoveryCards];
  const visibleFishInstances = merged.reduce(
    (s, f) => s + (Number(f.amount) > 0 ? Math.floor(Number(f.amount)) : 1),
    0,
  );

  return {
    ...publicFishResult,
    fishItems: merged,
    publicItems: merged,
    publicFishItems: merged,
    fishInventory: publicFishResult.fishInventory,
    fishCounts: {
      ...(publicFishResult.fishCounts || {}),
      fishTypes: merged.length,
      fishInstances: visibleFishInstances,
    },
    publicCounts: {
      ...(publicFishResult.publicCounts || {}),
      visibleFishInstances,
      visibleFishTypes: merged.length,
    },
    userSnapshotRecoveryApplied: recoveryCards.length > 0,
    userSnapshotRecoveryMergeProof: mergeProof,
  };
}

function buildUserSnapshotRecoveryProof(sessionKey, sessionData, publicFishItems) {
  const meta = getSessionRecoveryMeta(sessionKey, sessionData);
  if (!meta) {
    return {
      active: false,
      reason: 'no_snapshot_recovery_registered',
    };
  }

  const existingCounts = countPublicByBaseName(publicFishItems);
  const speciesEvidenceRows = (meta.recoveredSpecies || []).map((name) => {
    const evidence = globalCatalogService.buildGlobalSpeciesEvidenceProof(name);
    const image = resolveSpeciesImageAndRarity(name);
    return {
      speciesName: name,
      expectedAmount: meta.expectedInventoryCounts[name] || 0,
      currentPublicAmount: existingCounts[foldKey(name)] || 0,
      speciesEvidence: evidence,
      itemIdMappingStatus: evidence?.hasItemIdMapping ? 'bound' : 'pending',
      imageResolutionProof: image,
      rarityResolutionProof: image.rarity
        ? { rarity: image.rarity, source: image.raritySource }
        : null,
    };
  });

  const totalExpectedFish = Object.values(meta.expectedInventoryCounts || {})
    .reduce((s, n) => s + n, 0);
  const totalExpectedTypes = Object.keys(meta.expectedInventoryCounts || {}).length;
  const totalPublicFish = (publicFishItems || []).reduce(
    (s, f) => s + (Number(f.amount) > 0 ? Math.floor(Number(f.amount)) : 1),
    0,
  );

  return {
    active: true,
    userSnapshotRecoveryProof: true,
    source: meta.source,
    seededAt: meta.seededAt,
    description: meta.description,
    expectedInventoryCounts: meta.expectedInventoryCounts,
    recoveredSpecies: meta.recoveredSpecies,
    recoveredAmounts: meta.recoveredAmounts,
    speciesEvidenceRows,
    itemIdMappingStatus: meta.itemIdMappingStatus || 'pending',
    publicCountExplanation: {
      expectedTrackedFish: totalExpectedFish,
      expectedTypes: totalExpectedTypes,
      currentPublicTrackedFish: totalPublicFish,
      currentPublicTypes: (publicFishItems || []).length,
    },
    liveCatchEvidenceProof: sessionData?.nameCatalogDiscovery?.globalEvidence || null,
    catchToSnapshotBindingProof: sessionData?.nameCatalogDiscovery?.catchToSnapshotBindingProof || null,
    ignoredNonFishDeltaProof: sessionData?.nameCatalogDiscovery?.ignoredDeltaProof || null,
    recoveredSpeciesImageResolutionProof: buildRecoveredSpeciesImageResolutionProof(publicFishItems),
  };
}

function buildRecoveredSpeciesImageResolutionProof(fishItems, options = {}) {
  const probeNames = options.probeNames || ['Elshark Gran Maja', 'Mosasaur Shark', 'Sparkly Eel'];
  let fishImageCache = null;
  try { fishImageCache = require('./fishitFishImageCache'); } catch (_) { fishImageCache = null; }

  return probeNames.map((baseFishName) => {
    const item = (fishItems || []).find(
      (f) => foldKey(f.baseFishName || f.name) === foldKey(baseFishName),
    );
    const lookupItem = item || { baseFishName, name: baseFishName, cardName: baseFishName };
    const meta = fishImageCache
      ? fishImageCache.resolveImageMetaForItem(lookupItem)
      : { searchedSources: [], triedAliases: [] };
    const searchedAliases = quizBotImageCatalog.searchAliasesForName(baseFishName);
    const quizAudit = quizBotImageCatalog.auditNames(searchedAliases);
    const quizHit = quizAudit.find((row) => row.matched) || quizAudit[0] || null;
    const imageUrl = item?.imageUrl || null;
    const localCached = imageUrl && String(imageUrl).startsWith('/api/fishit-tracker/assets/fish/');
    const cacheFile = localCached && fishImageCache
      ? fishImageCache.filenameFromCachedUrl(imageUrl)
      : null;
    const cachePath = cacheFile && fishImageCache
      ? path.join(fishImageCache.getCacheDir(), cacheFile)
      : (meta.localFilePath || quizHit?.localPath || null);
    const fileExists = cachePath ? fs.existsSync(cachePath) : false;
    const imageResolved = item?.imageResolved === true
      && item?.imageStatus === 'cached'
      && localCached
      && fileExists;
    const searchedSources = [
      ...(meta.searchedSources || []),
      'quiz_bot_fishit_bank',
      'global_db',
      'fishit_db_fallback',
    ].filter((v, i, a) => a.indexOf(v) === i);

    return {
      baseFishName,
      imageResolved,
      imageUrl,
      imageSource: item?.imageSource || meta.imageSource || null,
      sourceFile: meta.sourceFile || quizHit?.localFile || null,
      sourceDb: meta.sourceDb || quizHit?.sourceDb || null,
      matchedAlias: meta.matchedAlias || quizHit?.matchedAlias || null,
      quizBankId: meta.quizBankId || quizHit?.bankId || null,
      localCachePath: cachePath,
      publicAssetStatus: fileExists ? 200 : (localCached ? 404 : null),
      searchedAliases,
      searchedSources,
      missingReason: imageResolved
        ? null
        : (quizHit?.matched || meta.assetId || meta.localFilePath
          ? 'species_cache_pending'
          : 'noTrustedImageFound'),
    };
  });
}

function registerLiveCatchSpeciesEvidence(sessionKey, baseFishName, observationId) {
  const registry = loadRecoveryRegistry();
  if (!registry.sessions?.[sessionKey]) return null;
  registry.sessions[sessionKey].liveCatchSpeciesEvidence = registry.sessions[sessionKey].liveCatchSpeciesEvidence || [];
  registry.sessions[sessionKey].liveCatchSpeciesEvidence.push({
    baseFishName,
    observationId,
    recordedAt: new Date().toISOString(),
  });
  registry.updatedAt = new Date().toISOString();
  saveRecoveryRegistry(registry);
  return registry.sessions[sessionKey].liveCatchSpeciesEvidence;
}

module.exports = {
  SNAPSHOT_SOURCES,
  RECOVERY_REGISTRY_PATH,
  normalizeSnapshotFishName,
  getSnapshotSource,
  listSnapshotSources,
  loadRecoveryRegistry,
  applySnapshotRecovery,
  getSessionRecoveryMeta,
  mergeRecoveryIntoPublicFish,
  buildUserSnapshotRecoveryProof,
  buildRecoveryCard,
  resolveSpeciesImageAndRarity,
  buildRecoveredSpeciesImageResolutionProof,
  seedGlobalSpeciesEvidence,
  registerLiveCatchSpeciesEvidence,
  countPublicByBaseName,
  _resetForTests() {
    try {
      if (fs.existsSync(RECOVERY_REGISTRY_PATH)) fs.unlinkSync(RECOVERY_REGISTRY_PATH);
    } catch (_) { /* ignore */ }
  },
};
