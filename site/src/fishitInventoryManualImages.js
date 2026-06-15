'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { trackerReadManualAssetUrl } = require('./fishitTrackerReadUrls');

const MANUAL_OVERRIDE_SOURCE = 'manual_override';
const MANUAL_IMAGE_RESOLVER = 'manual_inventory_override';

function getDataPath() {
  return process.env.FISHIT_INVENTORY_MANUAL_IMAGES_PATH
    || path.join(__dirname, '..', 'data', 'fishit_inventory_manual_images.json');
}

function getCacheDirPath() {
  return process.env.FISHIT_MANUAL_IMAGE_CACHE_DIR
    || path.join(__dirname, '..', 'data', 'manual_image_cache');
}

const DATA_PATH = getDataPath();
const CACHE_DIR = getCacheDirPath();

const ALLOWED_MIME = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/jpg']);
const EXT_BY_MIME = {
  'image/png': '.png',
  'image/jpeg': '.jpg',
  'image/jpg': '.jpg',
  'image/webp': '.webp',
};

let _catalog = null;

function normalizeCategory(category) {
  const c = String(category || 'item').trim().toLowerCase();
  if (c === 'fish' || c === 'fishes') return 'fish';
  if (c === 'totem' || c === 'totems') return 'totems';
  if (c === 'stone' || c === 'stones' || c === 'enchantstone') return 'stones';
  if (c === 'rod' || c === 'rods' || c === 'bait' || c === 'baits') return 'item';
  return c || 'item';
}

function normalizeItemName(name) {
  return String(name || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

function makeLookupKey(category, itemId, name) {
  const cat = normalizeCategory(category);
  const id = itemId != null ? String(itemId).trim() : '';
  const nm = normalizeItemName(name);
  return `${cat}|${id}|${nm}`;
}

function loadCatalog(force = false) {
  if (_catalog && !force) return _catalog;
  const dataPath = getDataPath();
  try {
    const raw = JSON.parse(fs.readFileSync(dataPath, 'utf8'));
    _catalog = {
      version: raw.version || 1,
      updatedAt: raw.updatedAt || null,
      overrides: raw.overrides && typeof raw.overrides === 'object' ? raw.overrides : {},
    };
  } catch {
    _catalog = { version: 1, updatedAt: null, overrides: {} };
  }
  return _catalog;
}

function saveCatalog(catalog) {
  const dataPath = getDataPath();
  const next = {
    version: catalog.version || 1,
    updatedAt: new Date().toISOString(),
    overrides: catalog.overrides || {},
  };
  fs.mkdirSync(path.dirname(dataPath), { recursive: true });
  const tmp = `${dataPath}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(next, null, 2), 'utf8');
  fs.renameSync(tmp, dataPath);
  _catalog = next;
  return next;
}

function getCacheDir() {
  const cacheDir = getCacheDirPath();
  fs.mkdirSync(cacheDir, { recursive: true });
  return cacheDir;
}

function getManualAssetFilePath(category, filename) {
  const cat = normalizeCategory(category);
  const file = path.basename(String(filename || ''));
  if (!file) return null;
  return path.join(getCacheDir(), cat, file);
}

function buildManualImageUrl(baseUrl, category, filename) {
  return trackerReadManualAssetUrl(baseUrl, category, filename);
}

function lookupManualOverride(item, category) {
  const catalog = loadCatalog();
  const cat = normalizeCategory(category || item?.category || item?.kind || item?.type);
  const itemId = item?.itemId != null ? String(item.itemId).trim() : '';
  const name = item?.name || item?.displayName || item?.baseFishName || '';
  const normalizedName = normalizeItemName(name);

  const keys = [
    makeLookupKey(cat, itemId, normalizedName),
    makeLookupKey(cat, itemId, ''),
    makeLookupKey(cat, '', normalizedName),
  ];
  for (const key of keys) {
    if (catalog.overrides[key]) return catalog.overrides[key];
  }

  for (const entry of Object.values(catalog.overrides)) {
    if (!entry || normalizeCategory(entry.category) !== cat) continue;
    if (itemId && entry.itemId != null && String(entry.itemId).trim() === itemId) return entry;
    if (normalizedName && entry.normalizedName === normalizedName) return entry;
  }
  return null;
}

function manualFileExists(category, filename) {
  const full = getManualAssetFilePath(category, filename);
  return Boolean(full && fs.existsSync(full));
}

function attachManualImagesToItems(items, category, baseUrl) {
  if (!Array.isArray(items)) return [];
  const cat = normalizeCategory(category);
  return items.map((item) => {
    const override = lookupManualOverride(item, cat);
    if (!override?.uploadedFile) return item;
    if (!manualFileExists(cat, override.uploadedFile)) return item;
    const imageUrl = buildManualImageUrl(baseUrl, cat, override.uploadedFile);
    logImageOverrideMatch(override.originalName, override.normalizedName);
    return {
      ...item,
      name: override.originalName || item.name || item.displayName,
      displayName: override.originalName || item.displayName || item.name,
      imageUrl,
      imageUrlPresent: true,
      imageResolved: true,
      imageSource: MANUAL_OVERRIDE_SOURCE,
      imageResolver: MANUAL_IMAGE_RESOLVER,
      manualImageOverride: true,
      manualImageProof: {
        category: cat,
        itemId: override.itemId || item.itemId || null,
        normalizedName: override.normalizedName || normalizeItemName(item.name),
        originalName: override.originalName || null,
        uploadedFile: override.uploadedFile,
        sha256: override.sha256 || null,
        imageSource: MANUAL_OVERRIDE_SOURCE,
      },
      assetImageResolveProof: {
        imageResolver: MANUAL_IMAGE_RESOLVER,
        imageSource: MANUAL_OVERRIDE_SOURCE,
        imageResolved: true,
        resolvedAssetId: null,
        imageFieldUsed: 'manual_override',
        quantity: item.quantity != null ? item.quantity : null,
        source: item.source || 'playerdata_gameitemdb',
      },
    };
  });
}

function hasManualOverride(item, category) {
  return item?.imageSource === MANUAL_OVERRIDE_SOURCE && item?.imageResolved === true && Boolean(item?.imageUrl);
}

/** Re-apply catalog manual overrides onto already-public card rows (poll/cache safe). */
function refreshManualImagesOnPublicItems(items, category, baseUrl) {
  if (!Array.isArray(items)) return [];
  const cat = normalizeCategory(category);
  return items.map((item) => {
    const override = lookupManualOverride(item, cat);
    if (!override?.uploadedFile || !manualFileExists(cat, override.uploadedFile)) return item;
    const imageUrl = buildManualImageUrl(baseUrl, cat, override.uploadedFile);
    return {
      ...item,
      name: override.originalName || item.name || item.displayName,
      displayName: override.originalName || item.displayName || item.name,
      imageUrl,
      imageUrlPresent: true,
      imageResolved: true,
      imageSource: MANUAL_OVERRIDE_SOURCE,
      imageResolver: MANUAL_IMAGE_RESOLVER,
      manualImageOverride: true,
      manualImageProof: {
        category: cat,
        itemId: override.itemId || item.itemId || null,
        normalizedName: override.normalizedName || normalizeItemName(item.name),
        originalName: override.originalName || null,
        uploadedFile: override.uploadedFile,
        sha256: override.sha256 || null,
        imageSource: MANUAL_OVERRIDE_SOURCE,
      },
    };
  });
}

function decodeImagePayload(body = {}) {
  if (body.imageBuffer && Buffer.isBuffer(body.imageBuffer)) {
    return { buffer: body.imageBuffer, mime: body.mimeType || body.contentType || 'image/png' };
  }
  const b64 = body.imageBase64 || body.imageData || body.image;
  if (!b64 || typeof b64 !== 'string') return null;
  const match = b64.match(/^data:(image\/[a-z+]+);base64,(.+)$/i);
  const mime = match ? match[1].toLowerCase() : (body.mimeType || body.contentType || 'image/png').toLowerCase();
  const data = match ? match[2] : b64;
  try {
    const buffer = Buffer.from(data, 'base64');
    if (!buffer.length) return null;
    return { buffer, mime };
  } catch {
    return null;
  }
}

function sniffImageExt(buffer, mime) {
  const m = String(mime || '').toLowerCase();
  if (EXT_BY_MIME[m]) return EXT_BY_MIME[m];
  if (buffer[0] === 0x89 && buffer[1] === 0x50) return '.png';
  if (buffer[0] === 0xff && buffer[1] === 0xd8) return '.jpg';
  if (buffer.slice(0, 4).toString('ascii') === 'RIFF' && buffer.slice(8, 12).toString('ascii') === 'WEBP') return '.webp';
  return '.png';
}

function upsertManualOverride(input = {}) {
  const category = normalizeCategory(input.category);
  const itemId = input.itemId != null ? String(input.itemId).trim() : '';
  const originalName = String(input.name || input.originalName || '').trim();
  const normalizedName = normalizeItemName(originalName || input.normalizedName);
  if (!category) throw new Error('category_required');
  if (!itemId && !normalizedName) throw new Error('item_id_or_name_required');

  const decoded = decodeImagePayload(input);
  if (!decoded?.buffer?.length) throw new Error('image_required');

  const mime = String(decoded.mime || '').toLowerCase();
  if (!ALLOWED_MIME.has(mime) && !mime.startsWith('image/')) {
    throw new Error('unsupported_image_type');
  }

  const sha256 = crypto.createHash('sha256').update(decoded.buffer).digest('hex');
  const ext = sniffImageExt(decoded.buffer, mime);
  const uploadedFile = `${sha256.slice(0, 32)}${ext}`;
  const cacheDir = path.join(getCacheDir(), category);
  fs.mkdirSync(cacheDir, { recursive: true });
  const fullPath = path.join(cacheDir, uploadedFile);
  if (!fs.existsSync(fullPath)) {
    fs.writeFileSync(fullPath, decoded.buffer);
  }

  const now = new Date().toISOString();
  const key = makeLookupKey(category, itemId, normalizedName);
  const imageUrl = `/api/fishit-tracker/assets/manual/${category}/${uploadedFile}`;
  const entry = {
    category,
    itemId: itemId || null,
    normalizedName,
    originalName: originalName || normalizedName,
    uploadedFile,
    imageUrl,
    sha256,
    uploadedAt: input.uploadedAt || now,
    updatedAt: now,
  };

  const catalog = loadCatalog();
  const prev = catalog.overrides[key];
  if (prev?.uploadedAt) entry.uploadedAt = prev.uploadedAt;
  catalog.overrides[key] = entry;
  saveCatalog(catalog);
  return entry;
}

function listManualOverrides(category) {
  const catalog = loadCatalog();
  const cat = category ? normalizeCategory(category) : null;
  return Object.entries(catalog.overrides)
    .map(([key, row]) => ({ key, ...row }))
    .filter((row) => !cat || normalizeCategory(row.category) === cat)
    .sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')));
}

function buildMissingImageDebugList(items = [], category) {
  const cat = normalizeCategory(category);
  return (Array.isArray(items) ? items : []).map((item) => ({
    category: cat,
    itemId: item.itemId || null,
    name: item.name || item.displayName || null,
    quantity: item.quantity != null ? item.quantity : item.amount,
    imageResolved: item.imageResolved === true,
    imageUrlPresent: Boolean(item.imageUrl),
    imageSource: item.imageSource || null,
    imageResolver: item.imageResolver || null,
    hasManualOverride: lookupManualOverride(item, cat) != null,
  })).filter((row) => row.imageResolved !== true);
}

function buildManualImageProof(items = []) {
  const catalog = loadCatalog();
  return {
    catalogPath: getDataPath(),
    cacheDir: getCacheDirPath(),
    overrideCount: Object.keys(catalog.overrides || {}).length,
    manualResolvedCount: items.filter((i) => i.imageSource === MANUAL_OVERRIDE_SOURCE && i.imageResolved).length,
    rows: items.map((item) => ({
      itemId: item.itemId || null,
      name: item.name || null,
      imageSource: item.imageSource || null,
      imageResolved: item.imageResolved === true,
      imageUrl: item.imageUrl || null,
      manualImageProof: item.manualImageProof || null,
    })),
  };
}

function _resetCatalogForTests() {
  _catalog = null;
}

const SEED_FILE_BY_KEY = {
  'totems|2|mutation totem': 'mutation_totem.png',
  'totems|1|luck totem': 'luck_totem.png',
  // Manual overrides for broken Runic Stone / Love Totem / Shiny Totem art
  // (2026-06-15). Keyed by name only so they match regardless of itemId.
  'totems||love totem': 'love_totem_2026_06_15.png',
  'totems||shiny totem': 'shiny_totem_2026_06_15.png',
  // v2: the original runic_stone_2026_06_15.png seed was a Roblox log
  // screenshot. v2 is the correct uploaded Runic Stone art; the new filename
  // forces a fresh copy into the served cache dir and busts client caches.
  'stones||runic stone': 'runic_stone_2026_06_15_v2.png',
};

// Emit IMAGE_OVERRIDE_MATCH once per item-name per process so the override is
// provable from server logs (and WebView console via the APK forwarder) without
// flooding the log on every poll.
const _loggedOverrideMatches = new Set();
function logImageOverrideMatch(originalName, normalizedName) {
  const label = String(originalName || normalizedName || '').trim();
  if (!label) return;
  const key = String(normalizedName || label).toLowerCase();
  if (_loggedOverrideMatches.has(key)) return;
  _loggedOverrideMatches.add(key);
  // eslint-disable-next-line no-console
  console.log('[DengTrackerImages] IMAGE_OVERRIDE_MATCH %s', label);
}

function ensureOverrideFilesFromSeed() {
  const catalog = loadCatalog();
  const seedDir = path.join(__dirname, '..', 'data', 'manual_image_seed');
  let restored = 0;
  for (const [key, entry] of Object.entries(catalog.overrides || {})) {
    if (!entry?.uploadedFile) continue;
    const cat = normalizeCategory(entry.category);
    if (manualFileExists(cat, entry.uploadedFile)) continue;
    const seedName = SEED_FILE_BY_KEY[key]
      || SEED_FILE_BY_KEY[makeLookupKey(cat, entry.itemId, entry.normalizedName)];
    if (!seedName) continue;
    const seedPath = path.join(seedDir, seedName);
    if (!fs.existsSync(seedPath)) continue;
    const dest = getManualAssetFilePath(cat, entry.uploadedFile);
    if (!dest) continue;
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.copyFileSync(seedPath, dest);
    restored += 1;
  }
  return restored;
}

module.exports = {
  MANUAL_OVERRIDE_SOURCE,
  MANUAL_IMAGE_RESOLVER,
  getDataPath,
  getCacheDirPath,
  DATA_PATH: getDataPath(),
  CACHE_DIR: getCacheDirPath(),
  normalizeCategory,
  normalizeItemName,
  makeLookupKey,
  loadCatalog,
  saveCatalog,
  getCacheDir,
  getManualAssetFilePath,
  buildManualImageUrl,
  lookupManualOverride,
  manualFileExists,
  attachManualImagesToItems,
  refreshManualImagesOnPublicItems,
  hasManualOverride,
  upsertManualOverride,
  listManualOverrides,
  buildMissingImageDebugList,
  buildManualImageProof,
  decodeImagePayload,
  ensureOverrideFilesFromSeed,
  logImageOverrideMatch,
  _resetCatalogForTests,
};
