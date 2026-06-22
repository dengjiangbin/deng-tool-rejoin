'use strict';

/**
 * Central /tracker item image overrides — one source of truth for fixed local art.
 * Top grid + item grid + search/detail must all resolve through this module.
 */

const RUNIC_STONE_TRACKER_TOP_GRID_IMAGE = '/public/assets/tracker-top-grid/runic-stone.png';
const TRACKER_ITEM_IMAGE_OVERRIDE_SOURCE = 'tracker_top_grid_item_override';
const TRACKER_RUNIC_STONE_IMAGE_RESOLVER = 'tracker_top_grid_runic_stone';

function normalizeTrackerItemName(name) {
  return String(name || '')
    .trim()
    .toLowerCase()
    .replace(/[^\w\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function isRunicStoneItemName(normalizedName) {
  const normalized = normalizedName != null
    ? normalizeTrackerItemName(normalizedName)
    : '';
  if (!normalized) return false;
  if (normalized === 'runic stone') return true;
  if (normalized === 'runic enchant stone') return true;
  if (/^runic(\s+enchant)?\s+stone\b/.test(normalized)) return true;
  return false;
}

function isRunicStoneItem(item) {
  if (!item || typeof item !== 'object') return false;
  const itemId = item.itemId != null ? String(item.itemId).trim() : '';
  if (itemId === '929') return true;
  const stoneType = String(item.stoneType || item.StoneType || '').trim();
  if (stoneType === 'Runic') return true;
  const names = [
    item.name,
    item.displayName,
    item.baseFishName,
    item.originalName,
  ];
  for (const name of names) {
    if (isRunicStoneItemName(name)) return true;
  }
  return false;
}

function resolveTrackerItemImageOverride(item) {
  if (!isRunicStoneItem(item)) return null;
  return {
    imageUrl: RUNIC_STONE_TRACKER_TOP_GRID_IMAGE,
    imageUrlPresent: true,
    imageSource: TRACKER_ITEM_IMAGE_OVERRIDE_SOURCE,
    imageResolver: TRACKER_RUNIC_STONE_IMAGE_RESOLVER,
  };
}

function getTrackerItemImageUrl(nameOrItem) {
  if (nameOrItem && typeof nameOrItem === 'object') {
    const resolved = resolveTrackerItemImageOverride(nameOrItem);
    return resolved ? resolved.imageUrl : null;
  }
  if (isRunicStoneItemName(nameOrItem)) return RUNIC_STONE_TRACKER_TOP_GRID_IMAGE;
  return null;
}

function applyTrackerItemImageOverrideToItem(item) {
  if (!item || typeof item !== 'object') return item;
  const override = resolveTrackerItemImageOverride(item);
  if (!override) return item;
  return {
    ...item,
    ...override,
    imageResolved: true,
  };
}

function applyTrackerItemImageOverridesToItems(items) {
  if (!Array.isArray(items)) return [];
  return items.map((item) => applyTrackerItemImageOverrideToItem(item));
}

function buildTrackerItemImageOverridesJsBootstrap() {
  return [
    `const TRACKER_RUNIC_STONE_IMAGE = ${JSON.stringify(RUNIC_STONE_TRACKER_TOP_GRID_IMAGE)};`,
    'function normalizeTrackerItemName(name) {',
    "  return String(name || '').trim().toLowerCase().replace(/[^\\w\\s]/g, ' ').replace(/\\s+/g, ' ').trim();",
    '}',
    'function isRunicStoneItemName(normalizedName) {',
    '  const normalized = normalizedName != null ? normalizeTrackerItemName(normalizedName) : \'\';',
    '  if (!normalized) return false;',
    "  if (normalized === 'runic stone') return true;",
    "  if (normalized === 'runic enchant stone') return true;",
    "  if (/^runic(\\s+enchant)?\\s+stone\\b/.test(normalized)) return true;",
    '  return false;',
    '}',
    'function isRunicStoneTrackerItem(item) {',
    '  if (!item || typeof item !== \'object\') return false;',
    "  const itemId = item.itemId != null ? String(item.itemId).trim() : '';",
    "  if (itemId === '929') return true;",
    "  const stoneType = String(item.stoneType || item.StoneType || '').trim();",
    "  if (stoneType === 'Runic') return true;",
    '  const names = [item.name, item.displayName, item.baseFishName, item.originalName];',
    '  for (let i = 0; i < names.length; i += 1) {',
    '    if (isRunicStoneItemName(names[i])) return true;',
    '  }',
    '  return false;',
    '}',
    'function resolveTrackerItemImageOverride(item) {',
    '  if (!isRunicStoneTrackerItem(item)) return null;',
    '  return TRACKER_RUNIC_STONE_IMAGE;',
    '}',
    'function getTrackerItemImage(nameOrItem) {',
    '  if (nameOrItem && typeof nameOrItem === \'object\') return resolveTrackerItemImageOverride(nameOrItem);',
    '  return isRunicStoneItemName(nameOrItem) ? TRACKER_RUNIC_STONE_IMAGE : null;',
    '}',
  ].join('\n');
}

module.exports = {
  RUNIC_STONE_TRACKER_TOP_GRID_IMAGE,
  TRACKER_ITEM_IMAGE_OVERRIDE_SOURCE,
  TRACKER_RUNIC_STONE_IMAGE_RESOLVER,
  normalizeTrackerItemName,
  isRunicStoneItemName,
  isRunicStoneItem,
  resolveTrackerItemImageOverride,
  getTrackerItemImageUrl,
  applyTrackerItemImageOverrideToItem,
  applyTrackerItemImageOverridesToItems,
  buildTrackerItemImageOverridesJsBootstrap,
};
