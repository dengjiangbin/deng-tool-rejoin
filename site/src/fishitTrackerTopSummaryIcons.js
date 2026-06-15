'use strict';

/**
 * Resolves the /tracker top-summary card icons from REAL DB/gameDB assets only.
 *
 * Strict policy (per product requirement): never fabricate, never fall back to a
 * placeholder/emoji/generated icon. Each resolver returns a real, already-cached
 * local asset URL (HTTP 200) or `null` when the DB entry/asset is genuinely
 * missing — callers must then surface the missing entry rather than fake it.
 *
 *  - online   : user-provided avatar copied into /public/img/tracker.
 *  - evolved  : Evolved Enchant Stone manual stone asset (data/stone_image_cache).
 *  - secret   : a real catalog fish whose rarity === 'Secret' with a cached image.
 *  - forgotten: a real catalog fish whose rarity === 'Forgotten' with a cached image.
 *  - ruby     : the canonical 'Ruby' gemstone entry's cached image.
 */

const fs = require('fs');
const path = require('path');

let canonicalCatalog = null;
try { canonicalCatalog = require('./fishitCanonicalCatalog'); } catch (_) { canonicalCatalog = null; }
let fishImageCache = null;
try { fishImageCache = require('./fishitFishImageCache'); } catch (_) { fishImageCache = null; }
let stoneImageAssets = null;
try { stoneImageAssets = require('./fishitStoneImageAssets'); } catch (_) { stoneImageAssets = null; }
let manualInventoryImages = null;
try { manualInventoryImages = require('./fishitInventoryManualImages'); } catch (_) { manualInventoryImages = null; }

const ONLINE_AVATAR_URL = '/public/img/tracker/online_avatar.png';

function entryRarity(entry) {
  return String((entry && (entry.rarity || entry.Rarity || entry.tierName)) || '').trim();
}

// Cached local URL for a catalog entry's imageAssetId, only when the file
// actually exists on disk (verified 200). Returns null otherwise.
function cachedUrlForAssetId(assetId) {
  if (!assetId || !fishImageCache) return null;
  let entry = null;
  try { entry = fishImageCache.getCachedEntry(assetId); } catch (_) { entry = null; }
  if (!entry || !entry.localFile) return null;
  let exists = false;
  try { exists = fs.existsSync(path.join(fishImageCache.getCacheDir(), entry.localFile)); } catch (_) { exists = false; }
  if (!exists) return null;
  return fishImageCache.localUrlForFile(entry.localFile);
}

// First catalog fish (deterministic, name-sorted) of the given rarity whose
// image is really cached on disk. Returns { name, assetId, url } or null.
function representativeFishByRarity(rarity) {
  if (!canonicalCatalog) return null;
  let store = null;
  try { store = canonicalCatalog._load(); } catch (_) { store = null; }
  const byName = store && store.byName ? store.byName : {};
  const wanted = String(rarity || '').toLowerCase();
  const names = Object.keys(byName).sort();
  for (const n of names) {
    const entry = byName[n];
    if (!entry || entryRarity(entry).toLowerCase() !== wanted) continue;
    const url = cachedUrlForAssetId(entry.imageAssetId);
    if (url) {
      return { name: entry.displayName || entry.baseFishName || n, assetId: String(entry.imageAssetId), url };
    }
  }
  return null;
}

function rubyIcon() {
  if (!canonicalCatalog) return null;
  let entry = null;
  try { entry = canonicalCatalog.lookupByName('Ruby'); } catch (_) { entry = null; }
  if (!entry) return null;
  const url = cachedUrlForAssetId(entry.imageAssetId);
  if (!url) return null;
  return { name: entry.displayName || entry.baseFishName || 'Ruby', assetId: String(entry.imageAssetId), url };
}

function evolvedStoneIcon() {
  if (!stoneImageAssets) return null;
  const asset = stoneImageAssets.lookupStoneAsset('558', 'Evolved');
  const filename = asset && asset.filename ? asset.filename : 'stone_558_evolved.png';
  if (!stoneImageAssets.stoneAssetFileExists(filename)) return null;
  const version = stoneImageAssets.getStoneAssetVersion(filename);
  return {
    name: 'Evolved Enchant Stone',
    filename,
    url: `/api/fishit-tracker/assets/stones/${filename}${version && version !== '0' ? `?v=${version}` : ''}`,
  };
}

// Runic Stone must use the user's existing manual override image only — never
// gameDB/fallback/generated. Returns null if the override or file is missing.
function runicStoneIcon() {
  if (!manualInventoryImages) return null;
  let override = null;
  try {
    override = manualInventoryImages.lookupManualOverride(
      { name: 'Runic Stone', category: 'stones', stoneType: 'Runic' },
      'stones',
    );
  } catch (_) { override = null; }
  if (!override || !override.uploadedFile) return null;
  if (!manualInventoryImages.manualFileExists('stones', override.uploadedFile)) return null;
  const url = override.imageUrl || `/api/fishit-tracker/assets/manual/stones/${override.uploadedFile}`;
  return {
    name: override.originalName || 'Runic Stone',
    source: 'manual_override',
    file: override.uploadedFile,
    url,
  };
}

// Full resolution + a machine-readable proof block (chosen names/paths) so the
// selections can be logged/verified without faking anything.
function resolveTopSummaryIcons() {
  const secret = representativeFishByRarity('Secret');
  const forgotten = representativeFishByRarity('Forgotten');
  const evolved = evolvedStoneIcon();
  const runic = runicStoneIcon();
  return {
    online: ONLINE_AVATAR_URL,
    secret: secret ? secret.url : null,
    forgotten: forgotten ? forgotten.url : null,
    evolved: evolved ? evolved.url : null,
    runic: runic ? runic.url : null,
    proof: {
      online: { url: ONLINE_AVATAR_URL, source: 'user_uploaded_asset' },
      secret: secret || { missing: true, rarity: 'Secret' },
      forgotten: forgotten || { missing: true, rarity: 'Forgotten' },
      evolved: evolved || { missing: true },
      runic: runic || { missing: true, name: 'Runic Stone' },
    },
  };
}

module.exports = {
  ONLINE_AVATAR_URL,
  cachedUrlForAssetId,
  representativeFishByRarity,
  rubyIcon,
  evolvedStoneIcon,
  runicStoneIcon,
  resolveTopSummaryIcons,
};
