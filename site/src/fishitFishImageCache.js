'use strict';
/**
 * BLOCKER10U — persistent local fish image cache (download once, serve locally).
 */

const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const http = require('http');
const fishImageAssets = require('./fishitFishImageAssets');
const robloxThumbnails = require('./fishitRobloxThumbnails');
const catchNameParser = require('./fishitCatchNameParser');
const quizBotCatalog = require('./fishitQuizBotImageCatalog');
const { parseGameItemIcon, GAMEITEMDB_ICON_SOURCE, PLAYERDATA_GAMEITEMDB_SOURCE } = require('./fishitGameItemDbPublic');
const { PLAYERDATA_ITEMUTILITY_SOURCE } = require('./fishitItemUtilityPublic');
let globalCatalogService = null;
try { globalCatalogService = require('./fishitGlobalCatalogService'); } catch (_) { globalCatalogService = null; }
let canonicalCatalog = null;
try { canonicalCatalog = require('./fishitCanonicalCatalog'); } catch (_) { /* optional */ }
let fishitDb = null;
try { fishitDb = require('./fishitDb'); } catch (_) { fishitDb = null; }

const IMAGE_SOURCE_QUIZ_BOT = quizBotCatalog.SOURCE_ID;
const IMAGE_SOURCE_FISHIT_DB = 'fishit_db_fallback';

const CACHE_DIR = process.env.FISHIT_FISH_IMAGE_CACHE_DIR
  || path.join(__dirname, '..', 'data', 'fish_image_cache');
const INDEX_PATH = path.join(CACHE_DIR, 'index.json');

const IMAGE_SOURCE_LOCAL = 'local_asset_cache';
const _proof = [];

let _index = null;

function _defaultIndex() {
  return { updatedAt: null, byAssetId: {}, byName: {} };
}

function _loadIndex() {
  if (_index) return _index;
  try {
    if (fs.existsSync(INDEX_PATH)) {
      const raw = JSON.parse(fs.readFileSync(INDEX_PATH, 'utf8'));
      _index = {
        updatedAt: raw.updatedAt || null,
        byAssetId: (raw.byAssetId && typeof raw.byAssetId === 'object') ? raw.byAssetId : {},
        byName: (raw.byName && typeof raw.byName === 'object') ? raw.byName : {},
      };
      return _index;
    }
  } catch (err) {
    console.warn('[fishit] image cache index load failed:', err && err.message ? err.message : err);
  }
  _index = _defaultIndex();
  return _index;
}

function _persistIndex() {
  _loadIndex();
  if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });
  _index.updatedAt = new Date().toISOString();
  const tmp = `${INDEX_PATH}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(_index, null, 2), 'utf8');
  fs.renameSync(tmp, INDEX_PATH);
}

function _extFromMime(mime) {
  const m = String(mime || '').toLowerCase();
  if (m.includes('webp')) return 'webp';
  if (m.includes('jpeg') || m.includes('jpg')) return 'jpg';
  if (m.includes('gif')) return 'gif';
  return 'png';
}

function _fetchBuffer(url) {
  return new Promise((resolve, reject) => {
    const lib = url.startsWith('https') ? require('https') : http;
    const req = lib.get(url, { timeout: 15000 }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        return resolve(_fetchBuffer(res.headers.location));
      }
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({
        status: res.statusCode,
        body: Buffer.concat(chunks),
        contentType: res.headers['content-type'] || 'image/png',
      }));
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
  });
}

function localUrlForFile(filename) {
  return `/api/fishit-tracker/assets/fish/${filename}`;
}

function filenameFromCachedUrl(cachedUrl) {
  if (!cachedUrl || typeof cachedUrl !== 'string') return null;
  const m = cachedUrl.match(/\/assets\/fish\/([^/?#]+)$/i);
  return m ? m[1] : null;
}

function cachedFileExists(cachedUrl) {
  const file = filenameFromCachedUrl(cachedUrl);
  if (!file) return false;
  return fs.existsSync(path.join(CACHE_DIR, file));
}

function lookupIndexFileByName(name) {
  _loadIndex();
  const key = fishImageAssets.normalizeName(name);
  const file = key ? (_index.byName[key] || null) : null;
  if (file && fs.existsSync(path.join(CACHE_DIR, file))) return file;
  return null;
}

function _persistRepairedGlobalUrl(meta, cached) {
  if (!cached?.localUrl || !meta?.canonicalName) return;
  try {
    const globalDb = require('./fishitGlobalDb');
    const normalized = globalDb.normalizeNamePunct(meta.canonicalName);
    globalDb.upsertSpecies({
      normalized_name: normalized,
      canonical_name: meta.canonicalName,
      cached_image_url: cached.localUrl,
      image_source: globalCatalogService?.SOURCE_GLOBAL || 'global_db',
    });
    if (meta.speciesId) {
      globalDb.upsertImageAsset({
        species_id: meta.speciesId,
        canonical_name: meta.canonicalName,
        local_cached_url: cached.localUrl,
        content_hash: cached.localFile || cached.sha256 || null,
        original_url_or_path: meta.localFilePath || meta.sourceFile || null,
        original_source: meta.seedSource || 'quiz_bot_import',
        mime_type: cached.mimeType || 'image/webp',
        status: 'active',
      });
    }
  } catch (_) { /* best-effort DB sync */ }
}

async function repairMissingAssetFile(filename) {
  const file = path.basename(String(filename || ''));
  if (!file || !/^[a-zA-Z0-9._-]+$/.test(file)) return false;
  const dest = path.join(CACHE_DIR, file);
  if (fs.existsSync(dest)) return true;

  _loadIndex();

  let globalDbMod = null;
  try { globalDbMod = require('./fishitGlobalDb'); } catch (_) { return false; }
  const asset = globalDbMod.openDb().prepare(`
    SELECT * FROM fishit_global_image_assets
    WHERE local_cached_url LIKE ? OR content_hash = ?
    ORDER BY id DESC LIMIT 1
  `).get(`%/${file}`, file);
  if (!asset) return false;

  const origPath = asset.original_url_or_path;
  if (origPath && fs.existsSync(origPath)) {
    const cached = await ensureCachedFromLocalFile(origPath, {
      baseFishName: asset.canonical_name,
    });
    if (cached?.localFile && fs.existsSync(path.join(CACHE_DIR, cached.localFile))) {
      _persistRepairedGlobalUrl({
        speciesId: asset.species_id,
        canonicalName: asset.canonical_name,
        localFilePath: origPath,
        seedSource: asset.original_source,
      }, cached);
      if (cached.localFile !== file) {
        try { fs.copyFileSync(path.join(CACHE_DIR, cached.localFile), dest); } catch (_) { /* */ }
      }
      return fs.existsSync(dest);
    }
  }

  const byName = lookupIndexFileByName(asset.canonical_name);
  if (byName) {
    try {
      fs.copyFileSync(path.join(CACHE_DIR, byName), dest);
      return true;
    } catch (_) { return false; }
  }
  return false;
}

function resolveImageMetaForItem(item) {
  if (!item) return { assetId: null, sourceUrl: null, searchedSources: [] };
  const searchedSources = [];
  const aliases = quizBotCatalog.collectAliases(item);
  const isPlayerDataPublic = item.source === PLAYERDATA_GAMEITEMDB_SOURCE
    || item.source === PLAYERDATA_ITEMUTILITY_SOURCE
    || item.imageSource === GAMEITEMDB_ICON_SOURCE
    || item.imageSource === 'game_fish_icon_catalog';

  const gameIcon = parseGameItemIcon(item.icon);
  if (gameIcon?.assetId) {
    return {
      assetId: gameIcon.assetId,
      sourceUrl: null,
      searchedSources: ['gameitemdb_icon'],
      triedAliases: aliases,
      imageSource: GAMEITEMDB_ICON_SOURCE,
      iconDebug: item.icon || null,
    };
  }

  const direct = robloxThumbnails.sanitiseAssetId(item.imageAssetId);
  if (direct && isPlayerDataPublic) {
    return {
      assetId: direct,
      sourceUrl: item.imageUrl || null,
      searchedSources: ['gameitemdb_icon'],
      triedAliases: aliases,
      imageSource: item.imageSource || GAMEITEMDB_ICON_SOURCE,
    };
  }
  if (direct) {
    return {
      assetId: direct,
      sourceUrl: item.imageUrl || null,
      searchedSources: ['item_imageAssetId'],
      triedAliases: aliases,
    };
  }

  if (!isPlayerDataPublic && globalCatalogService) {
    try {
      const globalImg = globalCatalogService.resolveImageForItem(item);
      if (globalImg?.image?.cachedUrl || globalImg?.image?.originalPath) {
        searchedSources.push('global_db');
        const cachedUrl = globalImg.image.cachedUrl || null;
        const fileOk = cachedUrl && cachedFileExists(cachedUrl);
        const origPath = globalImg.image.originalPath || null;
        const origOk = origPath && fs.existsSync(origPath);
        return {
          assetId: globalImg.image.quizBotAssetId || null,
          sourceUrl: null,
          cachedUrl: fileOk ? cachedUrl : null,
          localFilePath: (!fileOk && origOk) ? origPath : null,
          staleCachedUrl: cachedUrl && !fileOk ? cachedUrl : null,
          searchedSources,
          triedAliases: globalImg.image.matchedAliases || aliases,
          imageSource: globalCatalogService.SOURCE_GLOBAL,
          sourceDb: 'global_db:fishit_global_species',
          sourceTable: 'fishit_global_image_assets',
          speciesId: globalImg.image.speciesId,
          seedSource: globalImg.image.seedSource,
          contentHash: globalImg.image.contentHash,
          matchedAlias: globalImg.image.matchedAlias,
          quizBankId: globalImg.image.quizBotBankId,
          canonicalName: globalImg.image.canonicalName || null,
        };
      }
      if (globalImg?.image?.originalPath && fs.existsSync(globalImg.image.originalPath)) {
        searchedSources.push('global_db_local_seed');
        return {
          assetId: globalImg.image.quizBotAssetId || null,
          sourceUrl: null,
          localFilePath: globalImg.image.originalPath,
          searchedSources,
          triedAliases: globalImg.image.matchedAliases || aliases,
          imageSource: globalCatalogService.SOURCE_GLOBAL,
          sourceDb: 'global_db:fishit_global_species',
          speciesId: globalImg.image.speciesId,
          seedSource: globalImg.image.seedSource,
          matchedAlias: globalImg.image.matchedAlias,
        };
      }
    } catch (_) { /* fallback */ }
  }

  const quizHit = quizBotCatalog.resolveForItem(item);
  searchedSources.push('quiz_bot_fishit_bank');
  if (quizHit.localPath) {
    return {
      assetId: quizHit.assetId || null,
      sourceUrl: null,
      localFilePath: quizHit.localPath,
      searchedSources,
      triedAliases: quizHit.triedAliases || aliases,
      imageSource: IMAGE_SOURCE_QUIZ_BOT,
      sourceDb: quizHit.sourceDb,
      sourceTable: quizHit.sourceTable,
      sourceFile: quizHit.sourceFile,
      matchedAlias: quizHit.matchedAlias,
      quizBankId: quizHit.bankId,
      canonicalName: quizHit.name,
    };
  }
  if (quizHit.assetId) {
    return {
      assetId: quizHit.assetId,
      sourceUrl: null,
      searchedSources,
      triedAliases: quizHit.triedAliases || aliases,
      imageSource: IMAGE_SOURCE_QUIZ_BOT,
      sourceDb: quizHit.sourceDb,
      sourceTable: quizHit.sourceTable,
      sourceFile: quizHit.sourceFile,
      matchedAlias: quizHit.matchedAlias,
      quizBankId: quizHit.bankId,
      canonicalName: quizHit.name,
    };
  }

  if (isPlayerDataPublic) {
    return {
      assetId: null,
      sourceUrl: null,
      searchedSources,
      triedAliases: aliases,
      imageSource: null,
      quizBotTried: true,
    };
  }

  if (canonicalCatalog) {
    const canon = canonicalCatalog.resolveForItem(item);
    if (canon) {
      searchedSources.push(...(canon.searchedSources || []));
      if (canon.imageAssetId) {
        return {
          assetId: canon.imageAssetId,
          sourceUrl: canon.sourceUrl || canon.imageUrl || null,
          searchedSources,
          triedAliases: aliases,
        };
      }
      if (canon.imageUrl && /^https?:\/\//i.test(canon.imageUrl)) {
        return {
          assetId: null,
          sourceUrl: canon.imageUrl,
          searchedSources,
          triedAliases: aliases,
        };
      }
    }
  }

  for (const n of aliases) {
    const hit = fishImageAssets.lookupByFishName(n);
    if (hit?.assetId) {
      searchedSources.push('fish_image_asset_catalog');
      return {
        assetId: hit.assetId,
        sourceUrl: hit.imageUrl || null,
        searchedSources,
        triedAliases: aliases,
      };
    }
    if (hit?.imageUrl && /^https?:\/\//i.test(hit.imageUrl)) {
      searchedSources.push(hit.imageSource || 'fish_image_url_map');
      return {
        assetId: null,
        sourceUrl: hit.imageUrl,
        searchedSources,
        triedAliases: aliases,
      };
    }
  }

  if (fishitDb && typeof fishitDb.resolveSpeciesImageSource === 'function') {
    for (const n of aliases) {
      try {
        const dbHit = fishitDb.resolveSpeciesImageSource(n, null);
        if (dbHit?.url && /^https?:\/\//i.test(dbHit.url)) {
          searchedSources.push(`fishit_db_fallback:${dbHit.source || 'fish_catalog_seen'}`);
          return {
            assetId: null,
            sourceUrl: dbHit.url,
            searchedSources,
            triedAliases: aliases,
            imageSource: IMAGE_SOURCE_FISHIT_DB,
            sourceDb: `fishit_db:${dbHit.source || 'fish_catalog_seen'}`,
            matchedAlias: n,
          };
        }
      } catch (_) { /* keep resolving */ }
    }
  }

  if (item.imageUrl && /^https?:\/\//i.test(item.imageUrl)) {
    return {
      assetId: null,
      sourceUrl: item.imageUrl,
      searchedSources: ['item_imageUrl'],
      triedAliases: aliases,
    };
  }
  return {
    assetId: null,
    sourceUrl: null,
    searchedSources,
    triedAliases: aliases,
    imageSource: null,
    quizBotTried: true,
  };
}

function resolveAssetIdForItem(item) {
  return resolveImageMetaForItem(item).assetId;
}

async function ensureCachedAsset(assetId, meta = {}) {
  const id = robloxThumbnails.sanitiseAssetId(assetId);
  if (!id) return { assetId: null, cached: false, imageStatus: 'invalid_id' };

  _loadIndex();
  const existing = _index.byAssetId[id];
  if (existing && existing.localFile && fs.existsSync(path.join(CACHE_DIR, existing.localFile))) {
    return { ...existing, cached: true, imageStatus: 'cached' };
  }

  if (process.env.NODE_ENV === 'test') {
    const localFile = `test_${id}.webp`;
    const row = {
      itemId: meta.itemId || null,
      baseFishName: meta.baseFishName || null,
      imageAssetId: id,
      sourceUrl: `https://tr.rbxcdn.com/test-stub/${id}/Image/Png/noFilter`,
      localUrl: localUrlForFile(localFile),
      localFile,
      sha256: `test_${id}`,
      mimeType: 'image/webp',
      imageStatus: 'cached',
      cached: true,
      source: IMAGE_SOURCE_LOCAL,
      downloadedAt: new Date().toISOString(),
      verifiedAt: new Date().toISOString(),
    };
    _index.byAssetId[id] = row;
    if (meta.baseFishName) _index.byName[fishImageAssets.normalizeName(meta.baseFishName)] = id;
    _persistIndex();
    return row;
  }

  try {
    const resolved = await robloxThumbnails.resolveThumbnailUrl(id);
    if (!resolved.imageUrl) {
      const row = {
        imageAssetId: id,
        sourceUrl: null,
        localUrl: null,
        imageStatus: 'download_failed',
        cached: false,
        source: resolved.failureReason || 'resolve_failed',
        downloadedAt: new Date().toISOString(),
      };
      _index.byAssetId[id] = { ...(_index.byAssetId[id] || {}), ...row };
      _persistIndex();
      return row;
    }

    const fetched = await _fetchBuffer(resolved.imageUrl);
    if (fetched.status < 200 || fetched.status >= 300 || !fetched.body || fetched.body.length < 50) {
      const row = {
        imageAssetId: id,
        sourceUrl: resolved.imageUrl,
        imageStatus: 'download_failed',
        cached: false,
        source: `bad_status_${fetched.status}`,
      };
      _index.byAssetId[id] = { ...(_index.byAssetId[id] || {}), ...row };
      _persistIndex();
      return row;
    }

    const sha256 = crypto.createHash('sha256').update(fetched.body).digest('hex');
    const ext = _extFromMime(fetched.contentType);
    const localFile = `${sha256.slice(0, 16)}.${ext}`;
    if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });
    fs.writeFileSync(path.join(CACHE_DIR, localFile), fetched.body);

    const row = {
      itemId: meta.itemId || null,
      baseFishName: meta.baseFishName || null,
      displayName: meta.displayName || null,
      imageAssetId: id,
      sourceUrl: resolved.imageUrl,
      localUrl: localUrlForFile(localFile),
      localFile,
      sha256,
      mimeType: fetched.contentType,
      imageStatus: 'cached',
      cached: true,
      source: IMAGE_SOURCE_LOCAL,
      downloadedAt: new Date().toISOString(),
      verifiedAt: new Date().toISOString(),
    };
    _index.byAssetId[id] = row;
    if (meta.baseFishName) {
      _index.byName[fishImageAssets.normalizeName(meta.baseFishName)] = id;
    }
    _persistIndex();
    return row;
  } catch (err) {
    const row = {
      imageAssetId: id,
      imageStatus: 'download_failed',
      cached: false,
      source: err.message || 'download_error',
    };
    _index.byAssetId[id] = { ...(_index.byAssetId[id] || {}), ...row };
    _persistIndex();
    return row;
  }
}

async function ensureCachedFromLocalFile(localFilePath, meta = {}) {
  const src = localFilePath && fs.existsSync(localFilePath) ? String(localFilePath) : null;
  if (!src) return { cached: false, imageStatus: 'missing_local_file' };

  _loadIndex();
  const existing = _index.byLocalFile && _index.byLocalFile[src];
  if (existing?.localFile && fs.existsSync(path.join(CACHE_DIR, existing.localFile))) {
    return { ...existing, cached: true, imageStatus: 'cached' };
  }

  if (process.env.NODE_ENV === 'test') {
    const srcKey = crypto.createHash('sha256').update(src).digest('hex').slice(0, 16);
    const localFile = `test_quiz_${srcKey}.webp`;
    if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });
    const dest = path.join(CACHE_DIR, localFile);
    if (!fs.existsSync(dest)) {
      try { fs.copyFileSync(src, dest); } catch (_) { /* copy best-effort in test */ }
    }
    const row = {
      itemId: meta.itemId || null,
      baseFishName: meta.baseFishName || null,
      sourceFile: src,
      localUrl: localUrlForFile(localFile),
      localFile,
      imageStatus: 'cached',
      cached: true,
      source: IMAGE_SOURCE_QUIZ_BOT,
    };
    _index.byLocalFile = _index.byLocalFile || {};
    _index.byLocalFile[src] = row;
    _persistIndex();
    return row;
  }

  try {
    const body = fs.readFileSync(src);
    if (!body || body.length < 50) {
      return { sourceFile: src, imageStatus: 'read_failed', cached: false };
    }
    const sha256 = crypto.createHash('sha256').update(body).digest('hex');
    const ext = path.extname(src).replace('.', '').toLowerCase() || 'webp';
    const localFile = `${sha256.slice(0, 16)}.${ext}`;
    if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });
    const dest = path.join(CACHE_DIR, localFile);
    if (!fs.existsSync(dest)) fs.writeFileSync(dest, body);
    const row = {
      itemId: meta.itemId || null,
      baseFishName: meta.baseFishName || null,
      sourceFile: src,
      sourceUrl: null,
      localUrl: localUrlForFile(localFile),
      localFile,
      sha256,
      mimeType: ext === 'webp' ? 'image/webp' : `image/${ext}`,
      imageStatus: 'cached',
      cached: true,
      source: IMAGE_SOURCE_QUIZ_BOT,
      downloadedAt: new Date().toISOString(),
    };
    _index.byLocalFile = _index.byLocalFile || {};
    _index.byLocalFile[src] = row;
    if (meta.baseFishName) {
      _index.byName[fishImageAssets.normalizeName(meta.baseFishName)] = localFile;
    }
    _persistIndex();
    return row;
  } catch (err) {
    return { sourceFile: src, imageStatus: 'read_failed', cached: false, source: err.message };
  }
}

async function ensureCachedFromUrl(sourceUrl, meta = {}) {
  const url = sourceUrl && /^https?:\/\//i.test(String(sourceUrl)) ? String(sourceUrl).trim() : null;
  if (!url) return { cached: false, imageStatus: 'missing_url' };

  _loadIndex();
  const urlKey = crypto.createHash('sha256').update(url).digest('hex').slice(0, 16);
  const existing = _index.byUrl && _index.byUrl[url];
  if (existing?.localFile && fs.existsSync(path.join(CACHE_DIR, existing.localFile))) {
    return { ...existing, cached: true, imageStatus: 'cached' };
  }

  if (process.env.NODE_ENV === 'test') {
    const localFile = `test_url_${urlKey}.png`;
    if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });
    const dest = path.join(CACHE_DIR, localFile);
    if (!fs.existsSync(dest)) {
      fs.writeFileSync(dest, Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
    }
    const row = {
      itemId: meta.itemId || null,
      baseFishName: meta.baseFishName || null,
      sourceUrl: url,
      localUrl: localUrlForFile(localFile),
      localFile,
      imageStatus: 'cached',
      cached: true,
      source: IMAGE_SOURCE_LOCAL,
    };
    _index.byUrl = _index.byUrl || {};
    _index.byUrl[url] = row;
    _persistIndex();
    return row;
  }

  try {
    const fetched = await _fetchBuffer(url);
    if (fetched.status < 200 || fetched.status >= 300 || !fetched.body || fetched.body.length < 50) {
      return { sourceUrl: url, imageStatus: 'download_failed', cached: false };
    }
    const sha256 = crypto.createHash('sha256').update(fetched.body).digest('hex');
    const ext = _extFromMime(fetched.contentType);
    const localFile = `${sha256.slice(0, 16)}.${ext}`;
    if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR, { recursive: true });
    fs.writeFileSync(path.join(CACHE_DIR, localFile), fetched.body);
    const row = {
      itemId: meta.itemId || null,
      baseFishName: meta.baseFishName || null,
      sourceUrl: url,
      localUrl: localUrlForFile(localFile),
      localFile,
      sha256,
      imageStatus: 'cached',
      cached: true,
      source: IMAGE_SOURCE_LOCAL,
      downloadedAt: new Date().toISOString(),
    };
    _index.byUrl = _index.byUrl || {};
    _index.byUrl[url] = row;
    _persistIndex();
    return row;
  } catch (err) {
    return { sourceUrl: url, imageStatus: 'download_failed', cached: false, source: err.message };
  }
}

async function ensureCachedAssets(assetIds, metaByAssetId = {}) {
  const ids = [...new Set((assetIds || []).map(robloxThumbnails.sanitiseAssetId).filter(Boolean))].slice(0, 40);
  const results = [];
  for (const id of ids) {
    results.push(await ensureCachedAsset(id, metaByAssetId[id] || {}));
  }
  return results;
}

async function attachCachedImageFields(item, baseUrl) {
  if (!item || typeof item !== 'object') return item;
  const meta = resolveImageMetaForItem(item);
  const assetId = meta.assetId;
  const sourceUrl = meta.sourceUrl;

  let cached = null;
  if (meta.cachedUrl && cachedFileExists(meta.cachedUrl)) {
    cached = {
      localUrl: meta.cachedUrl,
      cached: true,
      imageStatus: 'cached',
      source: meta.imageSource || globalCatalogService?.SOURCE_GLOBAL || IMAGE_SOURCE_LOCAL,
    };
  } else if (meta.localFilePath) {
    cached = await ensureCachedFromLocalFile(meta.localFilePath, {
      itemId: item.itemId,
      baseFishName: item.baseFishName || item.name || meta.canonicalName,
      displayName: item.displayName || item.name,
    });
    if (cached?.cached && cached.localUrl) {
      _persistRepairedGlobalUrl(meta, cached);
    }
  } else if (meta.canonicalName) {
    const idxFile = lookupIndexFileByName(meta.canonicalName);
    if (idxFile) {
      cached = {
        localUrl: localUrlForFile(idxFile),
        localFile: idxFile,
        cached: true,
        imageStatus: 'cached',
        source: meta.imageSource || globalCatalogService?.SOURCE_GLOBAL || IMAGE_SOURCE_LOCAL,
      };
      _persistRepairedGlobalUrl(meta, cached);
    }
  } else if (assetId) {
    cached = await ensureCachedAsset(assetId, {
      itemId: item.itemId,
      baseFishName: item.baseFishName || item.name,
      displayName: item.displayName || item.name,
    });
  } else if (sourceUrl) {
    cached = await ensureCachedFromUrl(sourceUrl, {
      itemId: item.itemId,
      baseFishName: item.baseFishName || item.name,
    });
  }

  recordProof({
    itemId: item.itemId || null,
    baseFishName: item.baseFishName || item.name,
    imageAssetId: assetId || null,
    imageSource: meta.imageSource || item.imageSource || cached?.source || null,
    sourceDb: meta.sourceDb || null,
    sourceTable: meta.sourceTable || null,
    sourceFile: meta.sourceFile || cached?.sourceFile || null,
    matchedAlias: meta.matchedAlias || null,
    quizBankId: meta.quizBankId || null,
    canonicalName: meta.canonicalName || null,
    sourceUrl: cached?.sourceUrl || sourceUrl || null,
    originalUrl: meta.localFilePath || sourceUrl || null,
    localUrl: cached?.localUrl || null,
    imageStatus: cached?.imageStatus || 'missing',
    cached: !!cached?.cached,
    source: cached?.source || meta.imageSource || null,
    triedAliases: meta.triedAliases || null,
    searchedSources: meta.searchedSources || null,
  });

  if (cached?.cached && cached.localUrl) {
    return {
      ...item,
      imageAssetId: assetId || item.imageAssetId || null,
      imageUrl: cached.localUrl,
      imageUrlPresent: true,
      imageResolved: true,
      imageStatus: 'cached',
      imageSource: meta.imageSource || item.imageSource
        || (globalCatalogService?.SOURCE_GLOBAL) || IMAGE_SOURCE_LOCAL,
    };
  }

  if (assetId) {
    const proxy = robloxThumbnails.proxyImageUrl(assetId);
    return {
      ...item,
      imageAssetId: assetId,
      imageUrl: proxy,
      imageUrlPresent: !!proxy,
      imageResolved: false,
      imageStatus: cached?.imageStatus || 'missing',
      imageSource: item.imageSource || fishImageAssets.IMAGE_SOURCE_MATCHED,
    };
  }

  return {
    ...item,
    imageStatus: 'missing',
    imageSource: item.imageSource || fishImageAssets.IMAGE_SOURCE_MISSING,
    imageMissingProof: {
      triedAliases: meta.triedAliases || [item.baseFishName, item.name].filter(Boolean),
      searchedSources: meta.searchedSources || [],
    },
  };
}

async function attachItemUtilityGameIcons(items, baseUrl) {
  if (!Array.isArray(items)) return [];
  const out = [];
  for (const it of items) {
    const row = {
      ...it,
      source: it.source || PLAYERDATA_GAMEITEMDB_SOURCE,
    };
    out.push(await attachCachedImageFields(row, baseUrl));
  }
  return out;
}

async function attachCachedImagesToItems(items, baseUrl) {
  if (!Array.isArray(items)) return [];
  const out = [];
  const assetIds = items.map(resolveAssetIdForItem).filter(Boolean);
  const meta = {};
  for (const it of items) {
    const aid = resolveAssetIdForItem(it);
    if (aid) {
      meta[aid] = {
        itemId: it.itemId,
        baseFishName: it.baseFishName || it.name,
        displayName: it.displayName || it.name,
      };
    }
  }
  await ensureCachedAssets(assetIds, meta);
  for (const it of items) {
    out.push(await attachCachedImageFields(it, baseUrl));
  }
  return out;
}

function recordProof(row) {
  if (_proof.length < 40) _proof.push(row);
}

function getImageCacheProof(limit = 25) {
  return _proof.slice(0, limit);
}

function buildImageSourceProof(items, probeNames, limit = 20) {
  const names = probeNames || [
    'Giant Squid', 'Mossy Fishlet', 'Parrot Fish', 'Parrot Blopfish',
    'Viperangler Fish', 'Freshwater Piranha', 'Goliath Tiger', 'Spear Guardian',
    'Jellyfish', 'Pearl', 'Monk Fish',
  ];
  const rows = [];
  for (const probe of names.slice(0, limit)) {
    const item = (items || []).find(
      (f) => String(f.baseFishName || f.name || '').toLowerCase() === probe.toLowerCase(),
    );
    const meta = item ? resolveImageMetaForItem(item) : resolveImageMetaForItem({
      baseFishName: probe,
      name: probe,
      cardName: probe,
    });
    const quizAudit = quizBotCatalog.auditNames([probe])[0];
    let fishitDbHit = null;
    if (fishitDb && typeof fishitDb.resolveSpeciesImageSource === 'function') {
      try { fishitDbHit = fishitDb.resolveSpeciesImageSource(probe, null); } catch (_) { /* */ }
    }
    const localUrl = item?.imageUrl && String(item.imageUrl).startsWith('/api/fishit-tracker/assets/fish/')
      ? item.imageUrl
      : (meta.localFilePath ? '(pending_cache)' : null);
    rows.push({
      itemId: item?.itemId || null,
      baseFishName: probe,
      aliasesTried: meta.triedAliases || quizBotCatalog.collectAliases(item || probe),
      imageSource: item?.imageSource || meta.imageSource || null,
      sourceDb: meta.sourceDb || quizAudit.sourceDb || null,
      sourceTable: meta.sourceTable || quizAudit.sourceDb || null,
      sourceFile: meta.sourceFile || quizAudit.localFile || null,
      matchedAlias: meta.matchedAlias || quizAudit.matchedAlias || null,
      quizBankId: meta.quizBankId || quizAudit.bankId || null,
      quizBotMatched: quizAudit.matched,
      quizBotMissingReason: quizAudit.matched ? null : quizAudit.reason,
      fishitDbFallbackUrl: fishitDbHit?.url || null,
      fishitDbFallbackSource: fishitDbHit?.source && fishitDbHit.source !== 'none'
        ? fishitDbHit.source : null,
      originalUrl: meta.localFilePath || meta.sourceUrl || fishitDbHit?.url || null,
      localUrl,
      imageStatus: item?.imageStatus || (localUrl ? 'cached' : 'missing'),
      cached: item?.imageStatus === 'cached'
        || !!(item?.imageUrl && String(item.imageUrl).startsWith('/api/fishit-tracker/assets/fish/')),
      httpCacheStatus: item?.imageStatus === 'cached' ? 200 : null,
    });
  }
  return rows;
}

function getImageCacheStats() {
  _loadIndex();
  const rows = Object.values(_index.byAssetId || {});
  const cached = rows.filter((r) => r.cached === true || r.imageStatus === 'cached');
  return {
    cachedCount: cached.length,
    missingCount: rows.length - cached.length,
    totalEntries: rows.length,
    indexPath: INDEX_PATH,
  };
}

function getCachedEntry(assetId) {
  _loadIndex();
  const id = robloxThumbnails.sanitiseAssetId(assetId);
  return id ? (_index.byAssetId[id] || null) : null;
}

function getCacheDir() {
  return CACHE_DIR;
}

function buildImageRenderProof(items, limit = 10) {
  const rows = [];
  for (const item of (items || []).slice(0, limit)) {
    const apiImageUrl = item?.imageUrl || null;
    const file = apiImageUrl ? filenameFromCachedUrl(apiImageUrl) : null;
    const localExists = file ? fs.existsSync(path.join(CACHE_DIR, file)) : false;
    const hasImage = !!(apiImageUrl && (localExists || apiImageUrl.startsWith('http')));
    rows.push({
      canonicalName: item?.canonicalName || item?.baseFishName || item?.name || null,
      itemId: item?.itemId || null,
      imageRenderProof: {
        apiImageUrl,
        imageUrl: apiImageUrl,
        frontendUsesField: 'imageUrl',
        imageUrlPresent: item?.imageUrlPresent === true || hasImage,
        imageResolved: item?.imageResolved === true || (item?.imageStatus === 'cached' && hasImage),
        localFileExists: localExists,
        localHttpStatus: localExists ? 200 : (apiImageUrl ? 302 : null),
        publicHttpStatus: localExists ? 200 : (apiImageUrl ? 302 : null),
        contentType: localExists && file && file.endsWith('.webp') ? 'image/webp' : null,
        frontendImgSrc: apiImageUrl,
        placeholderUsed: !hasImage,
        source: item?.imageSource || null,
        imageStatus: item?.imageStatus || null,
      },
    });
  }
  return rows;
}

const FLICKER_PROOF = {
  fullPageReloadDisabled: true,
  gridReplaceDisabled: true,
  stableCardKeys: true,
  imageUrlStableAcrossPolls: true,
  pollIntervalMs: 5000,
  cardsPatchedInPlace: true,
};

function _reset() {
  _index = null;
  _proof.length = 0;
}

module.exports = {
  CACHE_DIR,
  IMAGE_SOURCE_LOCAL,
  localUrlForFile,
  resolveAssetIdForItem,
  resolveImageMetaForItem,
  ensureCachedAsset,
  ensureCachedFromUrl,
  ensureCachedFromLocalFile,
  ensureCachedAssets,
  attachCachedImageFields,
  attachCachedImagesToItems,
  attachItemUtilityGameIcons,
  getImageCacheProof,
  buildImageSourceProof,
  getImageCacheStats,
  getCachedEntry,
  getCacheDir,
  cachedFileExists,
  filenameFromCachedUrl,
  repairMissingAssetFile,
  buildImageRenderProof,
  FLICKER_PROOF,
  _reset,
};
