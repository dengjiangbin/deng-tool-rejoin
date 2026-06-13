'use strict';

/** Canonical public totem display + image mapping. */
const TOTEM_PUBLIC_MAP = {
  2: {
    itemId: '2',
    canonicalName: 'Mutation Totem',
    displayName: 'Mutation Totem',
    imageFilename: 'totem_mutation_totem.webp',
    rejectGameIconAssetIds: ['75593774049916'],
  },
  501: {
    itemId: '501',
    canonicalName: 'Mutation Totem',
    displayName: 'Mutation Totem',
    imageFilename: 'totem_mutation_totem.webp',
    rejectGameIconAssetIds: ['75593774049916'],
  },
  502: {
    itemId: '502',
    canonicalName: 'Shiny Totem',
    displayName: 'Shiny Totem',
    imageFilename: 'totem_shiny_totem.png',
  },
  503: {
    itemId: '503',
    canonicalName: 'Ancient Totem',
    displayName: 'Ancient Totem',
    imageFilename: 'totem_ancient_totem.webp',
  },
  777: {
    itemId: '777',
    canonicalName: 'Future Lucky Totem',
    displayName: 'Future Lucky Totem',
    imageFilename: 'totem_future_lucky_totem.webp',
  },
};

const TOTEM_PUBLIC_BY_NAME = Object.fromEntries(
  Object.values(TOTEM_PUBLIC_MAP).map((row) => [normalizeTotemName(row.canonicalName), row]),
);

function normalizeTotemName(name) {
  return String(name || '').trim().toLowerCase();
}

function resolvePublicTotemMeta(item) {
  const name = normalizeTotemName(item?.name || item?.displayName);
  if (name && TOTEM_PUBLIC_BY_NAME[name]) return TOTEM_PUBLIC_BY_NAME[name];
  const idKey = item?.itemId != null ? String(item.itemId).trim() : '';
  if (idKey && TOTEM_PUBLIC_MAP[idKey]) return TOTEM_PUBLIC_MAP[idKey];
  return null;
}

function publicTotemDisplayName(item) {
  const meta = resolvePublicTotemMeta(item);
  if (meta) return meta.displayName;
  return String(item?.displayName || item?.name || '').trim() || 'Totem';
}

function publicTotemImageFilename(item) {
  const meta = resolvePublicTotemMeta(item);
  return meta ? meta.imageFilename : null;
}

function isRejectedTotemGameIcon(item, assetId) {
  const id = assetId != null ? String(assetId).trim() : '';
  if (!id) return true;
  const meta = resolvePublicTotemMeta(item);
  if (meta && Array.isArray(meta.rejectGameIconAssetIds)) {
    if (meta.rejectGameIconAssetIds.includes(id)) return true;
  }
  return false;
}

module.exports = {
  TOTEM_PUBLIC_MAP,
  TOTEM_PUBLIC_BY_NAME,
  normalizeTotemName,
  resolvePublicTotemMeta,
  publicTotemDisplayName,
  publicTotemImageFilename,
  isRejectedTotemGameIcon,
};
