'use strict';
/**
 * BLOCKER10S — detect partial zero-fish snapshots and preserve last good public fish.
 */

const catalogStore = require('./fishitCatalogStore');

function countFishCategory(items) {
  if (!Array.isArray(items)) return 0;
  let n = 0;
  for (const it of items) {
    if (!it) continue;
    const cat = String(it.category || '').toLowerCase();
    if (cat === 'fish') n += 1;
    else if (cat !== 'rod' && cat !== 'bait' && cat !== 'items' && it.name
        && !catalogStore.isPlaceholderItemName(it.name, it.itemId)) {
      n += 1;
    }
  }
  return n;
}

function detectPartialZeroFishSnapshot({ ps, cleanItems, existing, priorPublicFishCount }) {
  const fishCount = countFishCategory(cleanItems);
  const prevGood = existing?.lastGoodFishItems?.length
    || existing?.lastGoodPublicFishCount
    || priorPublicFishCount
    || 0;
  const accepted = ps?.accepted || cleanItems.length || 0;
  const acceptedInstances = ps?.acceptedInstances || accepted;
  const parseFish = ps?.fish != null ? ps.fish : null;
  const selectedPath = ps?.selectedPath || ps?.selectedGeneralPath || null;
  const selectedFishPath = ps?.selectedFishPath || null;
  const fishFromFishPath = ps?.fishPathAccepted || 0;

  const reasons = [];
  if (fishCount === 0 && prevGood > 0) reasons.push('zero_fish_in_upload');
  if (parseFish === 0 && prevGood > 0) reasons.push('parse_stats_fish_zero');
  if (selectedPath === 'Inventory.Items' && fishFromFishPath === 0 && !selectedFishPath && prevGood > 0) {
    reasons.push('general_items_path_only');
  }
  if (prevGood > 0 && acceptedInstances > 0 && acceptedInstances < Math.max(3, Math.floor(prevGood * 0.5))) {
    reasons.push('accepted_much_smaller_than_prior_good');
  }

  const isPartial = reasons.length > 0 && fishCount === 0 && prevGood > 0;
  return {
    isPartial,
    partialSnapshotDetected: isPartial,
    partialSnapshotReason: isPartial
      ? `zero_fish_partial_snapshot_preserved_last_good:${reasons.join('+')}`
      : null,
    lastGoodFishPreserved: false,
    currentRawAccepted: accepted,
    currentAcceptedInstances: acceptedInstances,
    currentFishCount: fishCount,
    previousGoodFishCount: prevGood,
    selectedPath: selectedPath || null,
    selectedFishPath: selectedFishPath || null,
    reasons,
  };
}

function cloneItems(items) {
  return Array.isArray(items) ? items.map((it) => ({ ...it })) : [];
}

function applyPartialSnapshotPreservation({
  cleanItems,
  rawItems,
  inventory,
  existing,
  partialInfo,
}) {
  if (!partialInfo?.isPartial || !existing) {
    return {
      cleanItems,
      rawItems,
      inventory,
      partialInfo: { ...partialInfo, lastGoodFishPreserved: false },
    };
  }

  const preservedItems = existing.lastGoodFishItems?.length
    ? cloneItems(existing.lastGoodFishItems)
    : cloneItems(existing.items || []);
  const preservedRaw = existing.lastGoodRawItems?.length
    ? cloneItems(existing.lastGoodRawItems)
    : cloneItems(existing.rawItems || existing.items || []);
  const preservedInventory = existing.lastGoodInventory || existing.inventory || inventory;

  return {
    cleanItems: preservedItems,
    rawItems: preservedRaw,
    inventory: preservedInventory,
    partialInfo: {
      ...partialInfo,
      lastGoodFishPreserved: preservedItems.length > 0,
    },
  };
}

function updateLastGoodFishOnSession(session, cleanItems, publicFishCount, partialInfo) {
  if (!session) return;
  if (partialInfo?.isPartial) {
    session.partialSnapshotDetected = true;
    session.partialSnapshotReason = partialInfo.partialSnapshotReason;
    session.lastGoodFishPreserved = partialInfo.lastGoodFishPreserved;
    session.partialSnapshotMeta = {
      currentRawAccepted: partialInfo.currentRawAccepted,
      previousGoodFishCount: partialInfo.previousGoodFishCount,
      selectedPath: partialInfo.selectedPath,
      selectedFishPath: partialInfo.selectedFishPath,
      at: new Date().toISOString(),
    };
    return;
  }

  const fishItems = cleanItems.filter((it) => {
    const cat = String(it?.category || '').toLowerCase();
    return cat === 'fish' || (cat !== 'items' && cat !== 'rod' && cat !== 'bait' && it?.name
      && !catalogStore.isPlaceholderItemName(it.name, it.itemId));
  });

  if (publicFishCount > 0 || fishItems.length > 0) {
    session.lastGoodFishItems = cloneItems(fishItems.length ? fishItems : cleanItems);
    session.lastGoodRawItems = cloneItems(session.rawItems || cleanItems);
    session.lastGoodInventory = session.inventory || null;
    session.lastGoodPublicFishCount = publicFishCount || fishItems.length;
    session.lastGoodFishAt = new Date().toISOString();
    session.partialSnapshotDetected = false;
    session.partialSnapshotReason = null;
    session.lastGoodFishPreserved = false;
  }
}

function itemsForSessionDisplay(data) {
  if (!data) return [];
  if (data.partialSnapshotDetected && data.lastGoodFishPreserved && data.lastGoodFishItems?.length) {
    return data.lastGoodFishItems;
  }
  if (Array.isArray(data.inventory?.all) && data.inventory.all.length) {
    return data.inventory.all;
  }
  const live = (data.rawItems && data.rawItems.length) ? data.rawItems : (data.items || []);
  if (live.length) return live;
  if (Array.isArray(data.lastGoodFishItems) && data.lastGoodFishItems.length) {
    return data.lastGoodFishItems;
  }
  return [];
}

function sanitiseFishPathDiscovery(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const candidates = Array.isArray(raw.candidates)
    ? raw.candidates.slice(0, 20).map((c) => ({
      path: typeof c.path === 'string' ? c.path.slice(0, 120) : null,
      rawCount: Number(c.rawCount) || 0,
      acceptedCount: Number(c.acceptedCount) || 0,
      fishLikeCount: Number(c.fishLikeCount) || 0,
      nameFieldCount: Number(c.nameFieldCount) || 0,
      weightFieldCount: Number(c.weightFieldCount) || 0,
      knownFishIdCount: Number(c.knownFishIdCount) || 0,
      score: Number(c.score) || 0,
      selected: !!c.selected,
      sample: c.sample || null,
    }))
    : [];
  return {
    candidates,
    selectedFishPath: typeof raw.selectedFishPath === 'string' ? raw.selectedFishPath.slice(0, 120) : null,
    selectedFishPathReason: typeof raw.selectedFishPathReason === 'string'
      ? raw.selectedFishPathReason.slice(0, 200) : null,
    selectedGeneralPath: typeof raw.selectedGeneralPath === 'string'
      ? raw.selectedGeneralPath.slice(0, 120) : null,
  };
}

module.exports = {
  countFishCategory,
  detectPartialZeroFishSnapshot,
  applyPartialSnapshotPreservation,
  updateLastGoodFishOnSession,
  itemsForSessionDisplay,
  sanitiseFishPathDiscovery,
};
