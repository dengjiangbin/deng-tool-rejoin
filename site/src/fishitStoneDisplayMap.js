'use strict';

/** Canonical public stone display + image mapping (BLOCKER10ZP). */
const STONE_PUBLIC_MAP = {
  10: {
    itemId: '10',
    stoneType: 'Normal',
    displayName: 'Normal Enchant Stone',
    imageFilename: 'stone_10_normal.png',
  },
  246: {
    itemId: '246',
    stoneType: 'Double',
    displayName: 'Transcended Stone',
    imageFilename: 'stone_246_transcended.png',
    gameIconAssetId: '73883190545629',
    preferGameDbIcon: true,
    legacyImageFilenames: ['stone_246_double.png', 'stone_246_double_live.png'],
    legacyDisplayNames: ['Double Enchant Stone'],
  },
  558: {
    itemId: '558',
    stoneType: 'Evolved',
    displayName: 'Evolved Enchant Stone',
    imageFilename: 'stone_558_evolved.png',
  },
  873: {
    itemId: '873',
    stoneType: 'Eggy',
    displayName: 'Eggy Enchant Stone',
    imageFilename: 'stone_873_eggy.png',
  },
  929: {
    itemId: '929',
    stoneType: 'Runic',
    displayName: 'Runic Enchant Stone',
    imageFilename: 'stone_929_runic.png',
  },
};

const STONE_PUBLIC_BY_TYPE = Object.fromEntries(
  Object.values(STONE_PUBLIC_MAP).map((row) => [row.stoneType, row]),
);

const ENCHANT_STONES = Object.fromEntries(
  Object.entries(STONE_PUBLIC_MAP).map(([id, row]) => [Number(id), {
    name: row.displayName,
    stoneType: row.stoneType,
  }]),
);

function resolvePublicStoneMeta(item) {
  const idKey = item?.itemId != null ? String(item.itemId).trim() : '';
  if (idKey && STONE_PUBLIC_MAP[idKey]) return STONE_PUBLIC_MAP[idKey];
  const type = String(item?.stoneType || item?.StoneType || '').trim();
  if (type && STONE_PUBLIC_BY_TYPE[type]) return STONE_PUBLIC_BY_TYPE[type];
  return null;
}

function publicStoneDisplayName(item) {
  const meta = resolvePublicStoneMeta(item);
  if (meta) return meta.displayName;
  const raw = String(item?.displayName || item?.name || '').trim();
  if (/double enchant/i.test(raw)) return 'Transcended Stone';
  return raw || 'Enchant Stone';
}

function publicStoneImageFilename(item) {
  const meta = resolvePublicStoneMeta(item);
  return meta ? meta.imageFilename : null;
}

function isLegacyStoneImageFilename(filename) {
  const base = pathBasename(filename);
  if (!base) return false;
  for (const row of Object.values(STONE_PUBLIC_MAP)) {
    if ((row.legacyImageFilenames || []).includes(base)) return true;
  }
  return false;
}

function pathBasename(filename) {
  if (!filename) return '';
  return String(filename).split(/[/\\]/).pop();
}

function buildTrackerStoneJsBootstrap() {
  const names = {};
  for (const row of Object.values(STONE_PUBLIC_MAP)) {
    if (row.stoneType) names[row.stoneType] = row.displayName;
  }
  return `const STONE_DISPLAY_NAMES = ${JSON.stringify(names)};`;
}

module.exports = {
  STONE_PUBLIC_MAP,
  STONE_PUBLIC_BY_TYPE,
  ENCHANT_STONES,
  resolvePublicStoneMeta,
  publicStoneDisplayName,
  publicStoneImageFilename,
  isLegacyStoneImageFilename,
  buildTrackerStoneJsBootstrap,
};
