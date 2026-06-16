'use strict';

/**
 * Owned /tracker top-grid asset cache.
 *
 * Resolves real DB/gameDB/manual sources, copies the exact bytes into
 * site/public/assets/tracker-top-grid/, registers them in
 * site/data/tracker_top_grid_assets.json, and serves ONLY owned public URLs
 * on the top summary cards (never hotlink unstable /api/fishit-tracker paths).
 */

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const TOP_GRID_CARD_ORDER = Object.freeze([
  'account',
  'secretFish',
  'forgottenFish',
  'rubyGemstone',
  'evolvedEnchantStone',
  'runicStone',
]);

const TOP_GRID_ASSET_KEYS = Object.freeze({
  secretFish: 'secret-fish',
  forgottenFish: 'forgotten-fish',
  rubyGemstone: 'ruby-gemstone',
  evolvedEnchantStone: 'evolved-enchant-stone',
  runicStone: 'runic-stone',
});

const CARD_LABELS = Object.freeze({
  account: 'Online / Accounts',
  secretFish: 'Secret Fish',
  forgottenFish: 'Forgotten Fish',
  rubyGemstone: 'Ruby Gemstone',
  evolvedEnchantStone: 'Evolved Enchant Stone',
  runicStone: 'Runic Stone',
});

const ROOT = path.join(__dirname, '..');
const MANIFEST_PATH = path.join(ROOT, 'data', 'tracker_top_grid_assets.json');
const OWNED_DIR = path.join(ROOT, 'public', 'assets', 'tracker-top-grid');
const ONLINE_AVATAR_URL = '/public/img/tracker/online_avatar.png';
const PUBLIC_PREFIX = '/public/assets/tracker-top-grid';

let _manifest = null;
let _iconsCache = null;

let canonicalCatalog = null;
try { canonicalCatalog = require('./fishitCanonicalCatalog'); } catch (_) { /* optional */ }
let fishImageCache = null;
try { fishImageCache = require('./fishitFishImageCache'); } catch (_) { fishImageCache = null; }
let stoneImageAssets = null;
try { stoneImageAssets = require('./fishitStoneImageAssets'); } catch (_) { stoneImageAssets = null; }
let manualInventoryImages = null;
try { manualInventoryImages = require('./fishitInventoryManualImages'); } catch (_) { manualInventoryImages = null; }

function entryRarity(entry) {
  return String((entry && (entry.rarity || entry.Rarity || entry.tierName)) || '').trim();
}

function sha256File(filePath) {
  const body = fs.readFileSync(filePath);
  return crypto.createHash('sha256').update(body).digest('hex');
}

function extFromFile(filePath, fallback = 'png') {
  const ext = path.extname(String(filePath || '')).replace('.', '').toLowerCase();
  if (ext === 'jpeg') return 'jpg';
  return ext || fallback;
}

function ownedPublicUrl(filename) {
  return `${PUBLIC_PREFIX}/${filename}`;
}

function readCachedFishFile(assetId) {
  if (!assetId || !fishImageCache) return null;
  let entry = null;
  try { entry = fishImageCache.getCachedEntry(assetId); } catch (_) { entry = null; }
  if (!entry || !entry.localFile) return null;
  const full = path.join(fishImageCache.getCacheDir(), entry.localFile);
  if (!fs.existsSync(full)) return null;
  return { full, localFile: entry.localFile, assetId: String(assetId) };
}

function representativeFishByRarity(rarity) {
  if (!canonicalCatalog) return null;
  let store = null;
  try { store = canonicalCatalog._load(); } catch (_) { store = null; }
  const byName = store && store.byName ? store.byName : {};
  const wanted = String(rarity || '').toLowerCase();
  for (const name of Object.keys(byName).sort()) {
    const entry = byName[name];
    if (!entry || entryRarity(entry).toLowerCase() !== wanted) continue;
    const cached = readCachedFishFile(entry.imageAssetId);
    if (cached) {
      return {
        name: entry.displayName || entry.baseFishName || name,
        rarity: entryRarity(entry),
        assetId: String(entry.imageAssetId),
        sourcePath: cached.full,
        sourceDbPath: cached.localFile,
        sourceUrl: fishImageCache.localUrlForFile(cached.localFile),
      };
    }
  }
  return null;
}

function rubySource() {
  if (!canonicalCatalog) return null;
  let entry = null;
  try { entry = canonicalCatalog.lookupByName('Ruby'); } catch (_) { entry = null; }
  if (!entry || !entry.imageAssetId) return null;
  const cached = readCachedFishFile(entry.imageAssetId);
  if (!cached) return null;
  return {
    name: entry.displayName || entry.baseFishName || 'Ruby',
    rarity: entryRarity(entry) || null,
    assetId: String(entry.imageAssetId),
    sourcePath: cached.full,
    sourceDbPath: cached.localFile,
    sourceUrl: fishImageCache.localUrlForFile(cached.localFile),
  };
}

function evolvedStoneSource() {
  if (!stoneImageAssets) return null;
  const asset = stoneImageAssets.lookupStoneAsset('558', 'Evolved');
  const filename = asset && asset.filename ? asset.filename : 'stone_558_evolved.png';
  if (!stoneImageAssets.stoneAssetFileExists(filename)) return null;
  const full = path.join(stoneImageAssets.getCacheDir(), filename);
  return {
    name: 'Evolved Enchant Stone',
    sourcePath: full,
    sourceDbPath: filename,
    sourceUrl: stoneImageAssets.getStoneAssetUrl('', filename),
  };
}

function runicManualSource() {
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
  const full = manualInventoryImages.getManualAssetFilePath('stones', override.uploadedFile);
  if (!full || !fs.existsSync(full)) return null;
  return {
    name: override.originalName || 'Runic Stone',
    sourcePath: full,
    sourceDbPath: override.uploadedFile,
    sourceUrl: override.imageUrl || `/api/fishit-tracker/assets/manual/stones/${override.uploadedFile}`,
    manualOverride: true,
  };
}

function copyOwnedAsset(cardKey, source) {
  if (!source || !source.sourcePath || !fs.existsSync(source.sourcePath)) {
    throw new Error(`top_grid_asset_missing_source:${cardKey}`);
  }
  const assetKey = TOP_GRID_ASSET_KEYS[cardKey];
  if (!assetKey) throw new Error(`top_grid_asset_unknown_key:${cardKey}`);
  const ext = extFromFile(source.sourcePath);
  const ownedFilename = `${assetKey}.${ext}`;
  const ownedPath = path.join(OWNED_DIR, ownedFilename);
  fs.mkdirSync(OWNED_DIR, { recursive: true });
  fs.copyFileSync(source.sourcePath, ownedPath);
  const sha256 = sha256File(ownedPath);
  return {
    cardKey,
    label: CARD_LABELS[cardKey] || cardKey,
    sourceName: source.name || null,
    sourceType: source.manualOverride ? 'manual_override' : 'game_db_cache',
    sourceRarity: source.rarity || null,
    sourceAssetPath: source.sourceDbPath || null,
    sourceAssetUrl: source.sourceUrl || null,
    ownedAssetPath: path.relative(ROOT, ownedPath).replace(/\\/g, '/'),
    ownedPublicUrl: ownedPublicUrl(ownedFilename),
    sha256,
    cachedAt: new Date().toISOString(),
  };
}

function syncTopGridAssets(options = {}) {
  const persist = options.persist !== false;
  const cards = {};
  const errors = [];

  cards.secretFish = copyOwnedAsset('secretFish', representativeFishByRarity('Secret'));
  cards.forgottenFish = copyOwnedAsset('forgottenFish', representativeFishByRarity('Forgotten'));
  cards.rubyGemstone = copyOwnedAsset('rubyGemstone', rubySource());
  cards.evolvedEnchantStone = copyOwnedAsset('evolvedEnchantStone', evolvedStoneSource());
  cards.runicStone = copyOwnedAsset('runicStone', runicManualSource());

  const manifest = {
    version: 1,
    updatedAt: new Date().toISOString(),
    ownedDir: path.relative(ROOT, OWNED_DIR).replace(/\\/g, '/'),
    publicPrefix: PUBLIC_PREFIX,
    account: {
      cardKey: 'account',
      label: CARD_LABELS.account,
      ownedPublicUrl: ONLINE_AVATAR_URL,
      sourceType: 'user_uploaded_asset',
    },
    cards,
    order: [...TOP_GRID_CARD_ORDER],
  };

  if (persist) {
    fs.mkdirSync(path.dirname(MANIFEST_PATH), { recursive: true });
    const tmp = `${MANIFEST_PATH}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(manifest, null, 2), 'utf8');
    fs.renameSync(tmp, MANIFEST_PATH);
  }

  _manifest = manifest;
  _iconsCache = null;
  return manifest;
}

function loadManifest() {
  if (_manifest) return _manifest;
  try {
    if (fs.existsSync(MANIFEST_PATH)) {
      _manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
      return _manifest;
    }
  } catch (err) {
    console.error('[tracker-top-grid] manifest load failed:', err && err.message ? err.message : err);
  }
  return null;
}

function ensureOwnedFiles(manifest) {
  if (!manifest || !manifest.cards) return false;
  for (const card of Object.values(manifest.cards)) {
    const rel = card && card.ownedAssetPath;
    if (!rel) return false;
    const full = path.join(ROOT, rel);
    if (!fs.existsSync(full)) return false;
  }
  return true;
}

function resolveTopGridIcons(options = {}) {
  let manifest = loadManifest();
  if (!manifest || !ensureOwnedFiles(manifest) || options.forceSync) {
    try {
      manifest = syncTopGridAssets({ persist: true });
    } catch (err) {
      console.error('[tracker-top-grid] sync failed:', err && err.message ? err.message : err);
      manifest = loadManifest();
    }
  }

  const urls = {
    online: ONLINE_AVATAR_URL,
    secret: null,
    forgotten: null,
    ruby: null,
    evolved: null,
    runic: null,
  };
  const proof = {
    online: { url: ONLINE_AVATAR_URL, source: 'user_uploaded_asset' },
    secret: { missing: true },
    forgotten: { missing: true },
    ruby: { missing: true },
    evolved: { missing: true },
    runic: { missing: true },
  };

  if (manifest && manifest.cards) {
    const map = {
      secretFish: 'secret',
      forgottenFish: 'forgotten',
      rubyGemstone: 'ruby',
      evolvedEnchantStone: 'evolved',
      runicStone: 'runic',
    };
    for (const [cardKey, urlKey] of Object.entries(map)) {
      const row = manifest.cards[cardKey];
      if (row && row.ownedPublicUrl) {
        urls[urlKey] = row.ownedPublicUrl;
        proof[urlKey] = row;
      }
    }
  }

  _iconsCache = { urls, proof, manifest, order: TOP_GRID_CARD_ORDER };
  return _iconsCache;
}

/** Back-compat wrapper used by fishitTrackerTopSummaryIcons / routes. */
function resolveTopSummaryIcons(options = {}) {
  const resolved = resolveTopGridIcons(options);
  return {
    online: resolved.urls.online,
    secret: resolved.urls.secret,
    forgotten: resolved.urls.forgotten,
    ruby: resolved.urls.ruby,
    evolved: resolved.urls.evolved,
    runic: resolved.urls.runic,
    proof: resolved.proof,
    order: resolved.order,
    manifestPath: MANIFEST_PATH,
    ownedDir: OWNED_DIR,
  };
}

module.exports = {
  TOP_GRID_CARD_ORDER,
  TOP_GRID_ASSET_KEYS,
  CARD_LABELS,
  MANIFEST_PATH,
  OWNED_DIR,
  ONLINE_AVATAR_URL,
  PUBLIC_PREFIX,
  syncTopGridAssets,
  loadManifest,
  resolveTopGridIcons,
  resolveTopSummaryIcons,
  representativeFishByRarity,
  rubySource,
  evolvedStoneSource,
  runicManualSource,
};
