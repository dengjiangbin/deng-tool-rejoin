'use strict';

const path = require('path');
const robloxThumbnails = require('./fishitRobloxThumbnails');

const INVENTORY_ASSET_AUTO_SOURCE = 'inventory_asset_auto';
const GAMEITEMDB_ICON_SOURCE = 'gameitemdb_icon';
const PLAYERDATA_GAMEITEMDB_SOURCE = 'playerdata_gameitemdb';

const IMAGE_FIELD_SCAN_ORDER = [
  ['icon', 'Icon'],
  ['image', 'Image'],
  ['imageId', 'ImageId', 'ImageID'],
  ['iconId', 'IconId', 'IconID'],
  ['thumbnail', 'Thumbnail'],
  ['texture', 'Texture'],
  ['assetId', 'AssetId', 'AssetID'],
];

function normalizeCategory(category) {
  const c = String(category || 'item').toLowerCase();
  if (c === 'fish') return 'fish';
  if (c === 'totem' || c === 'totems') return 'totems';
  if (c === 'stone' || c === 'stones' || c === 'enchantstone') return 'stones';
  return 'item';
}

function localUrlForCategory(category, localFile) {
  const file = path.basename(String(localFile || ''));
  if (!file) return null;
  const cat = normalizeCategory(category);
  return `/api/fishit-tracker/assets/${cat}/${file}`;
}

function parseRobloxAssetValue(raw) {
  if (raw == null || raw === '') return null;
  if (typeof raw === 'number') {
    if (raw <= 0) return null;
    const assetId = robloxThumbnails.sanitiseAssetId(String(raw));
    if (!assetId) return null;
    return {
      assetId,
      icon: `rbxassetid://${assetId}`,
      rawImageValue: String(raw),
    };
  }
  const s = String(raw).trim();
  if (!s || s === '0' || s.toLowerCase() === 'rbxassetid://0') return null;

  const rbxAsset = s.match(/^rbxassetid:\/\/(\d+)$/i);
  if (rbxAsset) {
    const assetId = robloxThumbnails.sanitiseAssetId(rbxAsset[1]);
    if (!assetId) return null;
    return { assetId, icon: s, rawImageValue: s };
  }

  if (/^\d+$/.test(s)) {
    const assetId = robloxThumbnails.sanitiseAssetId(s);
    if (!assetId) return null;
    return { assetId, icon: `rbxassetid://${assetId}`, rawImageValue: s };
  }

  const rbxThumb = s.match(/rbxthumb:\/\/[^?]*\?[^#]*(?:&|^)id=(\d+)/i)
    || s.match(/rbxthumb:\/\/[^?]*\?[^#]*id=(\d+)/i);
  if (rbxThumb) {
    const assetId = robloxThumbnails.sanitiseAssetId(rbxThumb[1]);
    if (!assetId) return null;
    return { assetId, icon: `rbxassetid://${assetId}`, rawImageValue: s };
  }

  const storeAsset = s.match(/create\.roblox\.com\/store\/asset\/(\d+)/i)
    || s.match(/roblox\.com\/asset\/\?id=(\d+)/i)
    || s.match(/roblox\.com\/library\/(\d+)/i)
    || s.match(/\/asset\/(\d{10,22})(?:\/|$|\?)/i);
  if (storeAsset) {
    const assetId = robloxThumbnails.sanitiseAssetId(storeAsset[1]);
    if (!assetId) return null;
    return { assetId, icon: `rbxassetid://${assetId}`, rawImageValue: s };
  }

  if (/^https?:\/\//i.test(s)) {
    const thumbId = s.match(/[?&]assetId=(\d{10,22})/i)
      || s.match(/[?&]id=(\d{10,22})/i)
      || s.match(/\/(\d{10,22})\/Image\//i);
    if (thumbId) {
      const assetId = robloxThumbnails.sanitiseAssetId(thumbId[1]);
      if (!assetId) return null;
      return { assetId, icon: `rbxassetid://${assetId}`, rawImageValue: s };
    }
    return { assetId: null, sourceUrl: s, rawImageValue: s };
  }

  return null;
}

function extractAssetIdFromItem(item) {
  if (!item || typeof item !== 'object') return null;

  for (const keys of IMAGE_FIELD_SCAN_ORDER) {
    for (const key of keys) {
      if (item[key] == null || item[key] === '') continue;
      const parsed = parseRobloxAssetValue(item[key]);
      if (parsed?.assetId) {
        return {
          assetId: parsed.assetId,
          icon: parsed.icon,
          rawImageValue: parsed.rawImageValue,
          imageFieldUsed: key,
          sourceUrl: parsed.sourceUrl || null,
        };
      }
    }
  }

  for (const key of ['imageAssetId', 'iconAssetId', 'ImageAssetId', 'IconAssetId']) {
    const assetId = robloxThumbnails.sanitiseAssetId(item[key]);
    if (assetId) {
      return {
        assetId,
        icon: `rbxassetid://${assetId}`,
        rawImageValue: String(item[key]),
        imageFieldUsed: key,
        sourceUrl: null,
      };
    }
  }

  if (item.imageUrl && /^https?:\/\//i.test(String(item.imageUrl))) {
    const parsed = parseRobloxAssetValue(item.imageUrl);
    if (parsed?.assetId) {
      return {
        assetId: parsed.assetId,
        icon: parsed.icon,
        rawImageValue: parsed.rawImageValue,
        imageFieldUsed: 'imageUrl',
        sourceUrl: parsed.sourceUrl || item.imageUrl,
      };
    }
  }

  return null;
}

function buildAssetImageResolveProof(item, extracted, cached, imageUrl, category) {
  return {
    name: item?.name || item?.displayName || null,
    type: item?.type || item?.kind || category || null,
    itemId: item?.itemId != null ? String(item.itemId) : null,
    imageFieldUsed: extracted?.imageFieldUsed || null,
    rawImageValue: extracted?.rawImageValue || null,
    resolvedAssetId: extracted?.assetId || null,
    imageResolved: !!(cached?.cached),
    imageUrlPresent: !!imageUrl,
    imageUrl: imageUrl || null,
    imageCacheHit: cached?.cached === true && cached?.imageStatus === 'cached',
    imageCacheWrite: cached?.cached === true,
    quantity: item?.quantity != null ? item.quantity : null,
    source: item?.source || PLAYERDATA_GAMEITEMDB_SOURCE,
    imageResolver: INVENTORY_ASSET_AUTO_SOURCE,
    imageSource: INVENTORY_ASSET_AUTO_SOURCE,
  };
}

async function resolveInventoryItemImage(item, opts = {}) {
  if (!item || typeof item !== 'object') return item;
  const category = normalizeCategory(opts.category || item.category || item.kind || item.type);
  const fishImageCache = opts.fishImageCache;
  const extracted = extractAssetIdFromItem(item);

  if (!extracted?.assetId && !extracted?.sourceUrl) {
    return {
      ...item,
      imageResolved: false,
      imageUrlPresent: Boolean(item.imageUrl),
      assetImageResolveProof: buildAssetImageResolveProof(item, null, null, item.imageUrl || null, category),
    };
  }

  if (!fishImageCache) {
    const proxy = extracted.assetId ? robloxThumbnails.proxyImageUrl(extracted.assetId) : extracted.sourceUrl;
    return {
      ...item,
      icon: extracted.icon || item.icon,
      imageAssetId: extracted.assetId || item.imageAssetId || null,
      iconAssetId: extracted.assetId || item.iconAssetId || null,
      imageUrl: proxy || item.imageUrl || null,
      imageUrlPresent: !!(proxy || item.imageUrl),
      imageResolved: false,
      imageSource: GAMEITEMDB_ICON_SOURCE,
      imageResolver: 'inventory_asset_proxy',
      assetImageResolveProof: buildAssetImageResolveProof(item, extracted, null, proxy, category),
    };
  }

  let cached = null;
  if (extracted.assetId) {
    cached = await fishImageCache.ensureCachedAsset(extracted.assetId, {
      itemId: item.itemId,
      baseFishName: item.baseFishName || item.name,
      displayName: item.displayName || item.name,
      category,
    });
  } else if (extracted.sourceUrl && typeof fishImageCache.ensureCachedFromUrl === 'function') {
    cached = await fishImageCache.ensureCachedFromUrl(extracted.sourceUrl, {
      itemId: item.itemId,
      baseFishName: item.baseFishName || item.name,
      displayName: item.displayName || item.name,
    });
  }

  let imageUrl = null;
  if (cached?.cached && cached.localFile) {
    imageUrl = localUrlForCategory(category, cached.localFile);
  } else if (cached?.localUrl) {
    imageUrl = localUrlForCategory(category, cached.localUrl.split('/').pop());
  } else if (extracted.assetId) {
    imageUrl = robloxThumbnails.proxyImageUrl(extracted.assetId);
  } else if (extracted.sourceUrl) {
    imageUrl = extracted.sourceUrl;
  }

  const proof = buildAssetImageResolveProof(item, extracted, cached, imageUrl, category);

  return {
    ...item,
    icon: extracted.icon || item.icon,
    imageAssetId: extracted.assetId || item.imageAssetId || null,
    iconAssetId: extracted.assetId || item.iconAssetId || null,
    imageUrl: imageUrl || item.imageUrl || null,
    imageUrlPresent: !!(imageUrl || item.imageUrl),
    imageResolved: !!(cached?.cached),
    imageStatus: cached?.imageStatus || (imageUrl ? 'proxy' : 'missing'),
    imageSource: cached?.cached ? INVENTORY_ASSET_AUTO_SOURCE : GAMEITEMDB_ICON_SOURCE,
    imageResolver: cached?.cached ? INVENTORY_ASSET_AUTO_SOURCE : 'inventory_asset_proxy',
    assetImageResolveProof: proof,
  };
}

async function attachInventoryImagesToItems(items, category, opts = {}) {
  if (!Array.isArray(items)) return [];
  const out = [];
  for (const item of items) {
    out.push(await resolveInventoryItemImage(item, { ...opts, category }));
  }
  return out;
}

function buildAssetImageResolveProofList(items = []) {
  return (items || []).map((item) => item?.assetImageResolveProof || {
    name: item?.name || null,
    itemId: item?.itemId || null,
    imageResolved: !!(item?.imageResolved),
    imageUrlPresent: !!(item?.imageUrlPresent || item?.imageUrl),
    imageUrl: item?.imageUrl || null,
  });
}

function mergeImageFieldsPreferValid(existing, incoming) {
  const rank = (item) => {
    const src = item?.imageSource;
    if (src === 'manual_override') return 5;
    if (src === 'inventory_asset_auto') return 4;
    if (src === 'gameitemdb_icon') return 4;
    if (src === 'totem_manual_asset' || src === 'stone_manual_asset') return 3;
    return 1;
  };
  const existingRank = rank(existing);
  const incomingRank = rank(incoming);
  if (incomingRank > existingRank) return { ...(existing || {}), ...(incoming || {}) };
  if (existingRank > incomingRank && existing?.imageUrl) {
    return {
      ...(existing || {}),
      ...(incoming || {}),
      imageUrl: existing.imageUrl,
      imageAssetId: existing.imageAssetId,
      iconAssetId: existing.iconAssetId,
      imageResolved: existing.imageResolved,
      imageSource: existing.imageSource,
      imageResolver: existing.imageResolver,
      assetImageResolveProof: existing.assetImageResolveProof || incoming?.assetImageResolveProof,
    };
  }
  const base = { ...(existing || {}), ...(incoming || {}) };
  const existingOk = !!(existing?.imageUrl && existing?.imageResolved !== false);
  const incomingOk = !!(incoming?.imageUrl && incoming?.imageResolved !== false);
  if (incomingOk) return base;
  if (existingOk && !incomingOk) {
    return {
      ...base,
      imageUrl: existing.imageUrl,
      imageAssetId: existing.imageAssetId || base.imageAssetId,
      iconAssetId: existing.iconAssetId || base.iconAssetId,
      imageResolved: existing.imageResolved,
      imageUrlPresent: existing.imageUrlPresent,
      imageSource: existing.imageSource || base.imageSource,
      assetImageResolveProof: existing.assetImageResolveProof || base.assetImageResolveProof,
    };
  }
  return base;
}

module.exports = {
  INVENTORY_ASSET_AUTO_SOURCE,
  IMAGE_FIELD_SCAN_ORDER,
  normalizeCategory,
  localUrlForCategory,
  parseRobloxAssetValue,
  extractAssetIdFromItem,
  resolveInventoryItemImage,
  attachInventoryImagesToItems,
  buildAssetImageResolveProof,
  buildAssetImageResolveProofList,
  mergeImageFieldsPreferValid,
};
