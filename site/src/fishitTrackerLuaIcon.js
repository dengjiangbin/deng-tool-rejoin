'use strict';

const robloxThumbnails = require('./fishitRobloxThumbnails');
const { extractAssetIdFromItem } = require('./fishitInventoryImageResolver');
const TRACKER_LUA_ICON_SOURCE = 'tracker_lua_game_asset';
const GAMEITEMDB_ICON_SOURCE = 'gameitemdb_icon';
const GAME_FISH_ICON_SOURCE = 'game_fish_icon_catalog';

function parseTrackerLuaIcon(raw) {
  if (raw == null || raw === '') return null;
  if (typeof raw === 'number') {
    if (raw <= 0) return null;
    return {
      icon: `rbxassetid://${raw}`,
      assetId: String(raw),
      imageSource: TRACKER_LUA_ICON_SOURCE,
    };
  }
  const s = String(raw).trim();
  if (!s || s === '0' || s.toLowerCase() === 'rbxassetid://0') return null;
  const prefixed = s.match(/^rbxassetid:\/\/(\d+)$/i);
  if (prefixed) {
    if (prefixed[1] === '0') return null;
    return {
      icon: s,
      assetId: prefixed[1],
      imageSource: TRACKER_LUA_ICON_SOURCE,
    };
  }
  if (/^\d+$/.test(s)) {
    if (s === '0') return null;
    return {
      icon: `rbxassetid://${s}`,
      assetId: s,
      imageSource: TRACKER_LUA_ICON_SOURCE,
    };
  }
  return null;
}

function extractIconAssetId(row) {
  if (!row || typeof row !== 'object') return null;
  const extracted = extractAssetIdFromItem(row);
  if (extracted?.assetId) return extracted.assetId;
  const direct = robloxThumbnails.sanitiseAssetId(
    row.iconAssetId ?? row.IconAssetId ?? row.imageAssetId,
  );
  if (direct) return direct;
  const parsed = parseTrackerLuaIcon(row.icon ?? row.Icon ?? row.iconRaw ?? row.debugIcon);
  return parsed?.assetId || null;
}

function normaliseTrackerLuaIconFields(row) {
  const extracted = extractAssetIdFromItem(row || {});
  const iconRaw = row?.icon ?? row?.Icon ?? row?.iconRaw ?? row?.image ?? row?.Image ?? extracted?.rawImageValue ?? null;
  const parsed = parseTrackerLuaIcon(iconRaw);
  const iconAssetId = extracted?.assetId || extractIconAssetId(row) || parsed?.assetId || null;  const hasIcon = !!iconAssetId;
  const icon = parsed?.icon || (iconAssetId ? `rbxassetid://${iconAssetId}` : null);
  const iconSource = hasIcon
    ? TRACKER_LUA_ICON_SOURCE
    : (row?.iconSource || null);
  const imageSource = hasIcon
    ? (row?.imageSource === GAME_FISH_ICON_SOURCE
      ? GAME_FISH_ICON_SOURCE
      : GAMEITEMDB_ICON_SOURCE)
    : (row?.imageSource || null);
  return {
    icon,
    iconRaw,
    iconAssetId,
    iconSource,
    imageAssetId: iconAssetId,
    imageSource,
    imageFieldUsed: extracted?.imageFieldUsed || null,
    rawImageValue: extracted?.rawImageValue || iconRaw || null,
  };
}
function hasUsableTrackerIcon(item) {
  return !!extractIconAssetId(item);
}

function isTrackerLuaIconItem(item) {
  if (!item) return false;
  if (item.iconSource === TRACKER_LUA_ICON_SOURCE) return true;
  if (hasUsableTrackerIcon(item) && (
    item.imageSource === GAMEITEMDB_ICON_SOURCE
    || item.imageSource === GAME_FISH_ICON_SOURCE
    || item.source === 'playerdata_gameitemdb'
    || item.source === 'playerdata_itemutility'
  )) {
    return true;
  }
  return false;
}

function resolveImageMetaFromTrackerUpload(item) {
  const assetId = extractIconAssetId(item);
  if (!assetId) return null;
  return {
    assetId,
    sourceUrl: null,
    searchedSources: ['tracker_lua_icon'],
    triedAliases: [item?.baseFishName, item?.name, item?.displayName].filter(Boolean),
    imageSource: TRACKER_LUA_ICON_SOURCE,
    iconDebug: item?.icon || item?.iconRaw || null,
  };
}

module.exports = {
  TRACKER_LUA_ICON_SOURCE,
  GAMEITEMDB_ICON_SOURCE,
  GAME_FISH_ICON_SOURCE,
  parseTrackerLuaIcon,
  extractIconAssetId,
  normaliseTrackerLuaIconFields,
  hasUsableTrackerIcon,
  isTrackerLuaIconItem,
  resolveImageMetaFromTrackerUpload,
};
